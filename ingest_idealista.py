#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import email
import hashlib
import html
import imaplib
import json
import os
import re
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import psycopg
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
PREFS_PATH = ROOT / "real_estate_preferences.json"

DATABASE_URL = os.environ.get("DATABASE_URL")
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
LOOKBACK_DAYS = int(os.environ.get("GMAIL_LOOKBACK_DAYS", "7"))

if not DATABASE_URL:
    raise SystemExit("DATABASE_URL is required")


def decode_mime(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def parse_date(value):
    if not value:
        return None
    try:
        d = parsedate_to_datetime(value)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(dt.timezone.utc)
    except Exception:
        return None


def decode_part(part):
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except Exception:
        return payload.decode("utf-8", errors="replace")


def message_content(msg):
    plain_parts, html_parts = [], []
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        disp = str(part.get("Content-Disposition", ""))
        if "attachment" in disp.lower():
            continue
        ctype = part.get_content_type()
        text = decode_part(part)
        if not text:
            continue
        if ctype == "text/plain":
            plain_parts.append(text)
        elif ctype == "text/html":
            html_parts.append(text)

    plain = "\n".join(plain_parts)
    raw_html = "\n".join(html_parts)
    if raw_html:
        soup = BeautifulSoup(raw_html, "html.parser")
        visible = soup.get_text("\n", strip=True)
    else:
        soup = None
        visible = plain
    return plain or visible, raw_html, soup


def canonical_listing_url(url, listing_id):
    if not url:
        return f"https://www.idealista.com/en/inmueble/{listing_id}/"
    p = urlparse(url)
    return urlunparse((p.scheme or "https", p.netloc or "www.idealista.com",
                       f"/en/inmueble/{listing_id}/", "", "", ""))


def normalize_number(value):
    if not value:
        return None
    clean = re.sub(r"[^0-9]", "", value)
    return int(clean) if clean else None


def first_match(patterns, text, flags=re.I):
    for pattern in patterns:
        m = re.search(pattern, text or "", flags)
        if m:
            return m
    return None


def search_name_from_subject(subject):
    m = re.search(r"your search:\s*(.+?)(?:!|$)", subject or "", re.I)
    return (m.group(1).strip() if m else "")


def market_from_search(search_name, title, text):
    hay = f"{search_name} {title} {text}".lower()
    if "menorca" in hay or "alaior" in hay or "mercadal" in hay:
        return "menorca"
    if "cantabria" in hay:
        return "northern_spain"
    return ""


def listing_anchor(soup):
    if soup is None:
        return None, None, ""
    seen = set()
    for a in soup.find_all("a", href=True):
        href = html.unescape(a.get("href", ""))
        m = re.search(r"idealista\.com/(?:en/)?inmueble/(\d+)", href, re.I)
        if not m:
            continue
        listing_id = m.group(1)
        if listing_id in seen:
            continue
        title = " ".join(a.stripped_strings).strip()
        low = title.lower()
        if not title or re.match(r"^(see \d+ photo|contact|view|more info)", low):
            continue
        if " in " not in low and not re.search(r"\b(house|home|apartment|flat|estate|villa|chalet|farm|property)\b", low):
            continue

        # Idealista places a photo link before the title link for the same
        # listing. Only mark the ID as seen after we have a usable title.
        seen.add(listing_id)
        return listing_id, href, title
    return None, None, ""


def listing_from_text(text):
    url_m = re.search(r"https?://[^\s)>\]]*idealista\.com/(?:en/)?inmueble/(\d+)[^\s)>\]]*", text or "", re.I)
    listing_id = url_m.group(1) if url_m else None
    url = url_m.group(0) if url_m else ""

    title_m = first_match([
        r"(?m)^\s*((?:Detached|Terraced|Semi-detached|Village|Country|Rustic)\s+(?:house|home|property)\s+in\s+[^\n]{2,140})\s*$",
        r"(?m)^\s*((?:Apartment|Flat|Estate|Villa|Chalet|Farmhouse|Finca|House)\s+in\s+[^\n]{2,140})\s*$",
    ], text)
    title = title_m.group(1).strip() if title_m else ""
    return listing_id, url, title


def nearby_image(soup, listing_id):
    if soup is None or not listing_id:
        return ""
    link = soup.find("a", href=re.compile(rf"/inmueble/{re.escape(listing_id)}"))
    if not link:
        return ""
    node = link
    for _ in range(6):
        if node is None:
            break
        imgs = node.find_all("img") if hasattr(node, "find_all") else []
        for img in imgs:
            src = img.get("src") or img.get("data-src") or ""
            if src.startswith("http"):
                return html.unescape(src)
        node = getattr(node, "parent", None)
    return ""


def property_fit(market, search_name, title, description, price_eur, bedrooms, area_m2,
                 event_type, drop_pct, prefs):
    score = 35
    reasons = []
    hay = f"{title} {description}".lower()
    search_low = (search_name or "").lower()

    if "priority search" in search_low:
        score += 18
        reasons.append("priority search")
    elif "value" in search_low:
        score += 10
        reasons.append("value scan")

    if bedrooms is not None and bedrooms >= 3:
        score += 12
        reasons.append("3+ bedrooms")
    elif bedrooms is not None:
        score -= 20

    target_eur = prefs.get("target_max_eur") or 400000
    if price_eur is not None:
        if price_eur <= target_eur:
            score += 12
            reasons.append("within €400K search ceiling")
            if price_eur <= 300000:
                score += 6
                reasons.append("strong price")
        else:
            score -= 18

    if event_type == "price_reduction":
        score += min(12, 5 + int((drop_pct or 0) / 2))
        reasons.append("price reduction")

    risk_terms = (
        "assignment of credit", "investors only", "occupied property",
        "without possession", "no access", "auction", "tenant in situ"
    )
    if any(term in hay for term in risk_terms):
        score -= 40
        reasons.append("legal/possession risk")

    if market == "menorca":
        if any(loc.lower() in hay for loc in ("alaior", "es mercadal", "mercadal")):
            score += 15
            reasons.append("priority Menorca location")
        character_terms = ("farmhouse", "country house", "rustic", "finca", "stone", "land", "plot")
        if any(term in hay for term in character_terms):
            score += 10
            reasons.append("farmhouse/rustic character")
        if ("apartment" in hay or "flat" in hay) and any(loc.lower() in hay for loc in ("alaior", "es mercadal", "mercadal")):
            score += 7
            reasons.append("priority-town apartment")

    elif market == "northern_spain":
        coast_terms = ("sea", "beach", "coast", "estuary", "ria", "marina", "ocean")
        character_terms = ("farmhouse", "country house", "stone", "casona", "casa rural", "finca", "land", "plot")
        if any(term in hay for term in coast_terms):
            score += 10
            reasons.append("coastal signal")
        if any(term in hay for term in character_terms):
            score += 10
            reasons.append("country/stone/land signal")
        priority_locations = [
            "san vicente de la barquera", "comillas", "santillana del mar",
            "suances", "santander", "santoña", "santona", "laredo", "castro urdiales"
        ]
        if any(loc in hay for loc in priority_locations):
            score += 12
            reasons.append("priority northern-coast location")
        fixer_terms = ("to renovate", "needs renovation", "needs reform", "to reform", "fixer", "rehabilitation")
        if any(term in hay for term in fixer_terms):
            score += 5
            reasons.append("fixer-upper opportunity")

    if area_m2 and area_m2 >= 180:
        score += 4
        reasons.append("large property")

    return max(0, min(100, score)), reasons


def ensure_sources(conn):
    sources = [
        ("idealista_menorca", "Idealista — Menorca", "https://www.idealista.com/en/venta-viviendas/balears-illes/menorca/", "Menorca"),
        ("idealista_northern_spain", "Idealista — Northern Spain", "https://www.idealista.com/", "Northern Spain / Cantabria Coast"),
    ]
    with conn.cursor() as cur:
        for source_id, name, url, _ in sources:
            cur.execute("""
                INSERT INTO sources
                  (source_id,name,beat,source_type,url,priority,editorial_type,enabled,updated_at)
                VALUES (%s,%s,'real_estate','email',%s,'primary','listing',TRUE,NOW())
                ON CONFLICT (source_id) DO UPDATE SET
                  name=EXCLUDED.name, beat='real_estate', source_type='email',
                  url=EXCLUDED.url, priority='primary', editorial_type='listing',
                  enabled=TRUE, updated_at=NOW()
            """, (source_id, name, url))
    conn.commit()


def main():
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("SKIP idealista: Gmail credentials not configured")
        return

    prefs_all = json.loads(PREFS_PATH.read_text(encoding="utf-8")).get("markets", {})
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=LOOKBACK_DAYS)).strftime("%d-%b-%Y")

    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    parsed = updated = skipped = 0
    try:
        mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        mail.select("INBOX", readonly=True)
        status, data = mail.uid("search", None, f'(SINCE "{cutoff}")')
        if status != "OK":
            raise RuntimeError("Idealista Gmail search failed")
        uids = data[0].split() if data and data[0] else []

        with psycopg.connect(DATABASE_URL, autocommit=False, prepare_threshold=None) as conn:
            ensure_sources(conn)

            for uidb in uids:
                uid = uidb.decode()
                status, fetched = mail.uid("fetch", uid, "(BODY.PEEK[])")
                if status != "OK" or not fetched:
                    continue
                raw = next((part[1] for part in fetched if isinstance(part, tuple) and len(part) > 1), None)
                if not raw:
                    continue

                msg = email.message_from_bytes(raw)
                subject = decode_mime(msg.get("Subject", "")).strip()
                sender = decode_mime(msg.get("From", "")).lower()

                if "idealista" not in sender and "your search:" not in subject.lower():
                    continue
                if "daily roundup" in subject.lower():
                    skipped += 1
                    continue
                if not re.search(r"(new .+ in your search:|price reduction in your search:)", subject, re.I):
                    continue

                plain, raw_html, soup = message_content(msg)
                text = (soup.get_text("\n", strip=True) if soup is not None else plain)

                listing_id, href, title = listing_anchor(soup)
                if not listing_id:
                    listing_id, href, title = listing_from_text(plain or text)
                if not listing_id or not title:
                    print(f"IDEALISTA skip uid={uid}: could not identify listing from {subject!r}", flush=True)
                    skipped += 1
                    continue

                search_name = search_name_from_subject(subject)
                market = market_from_search(search_name, title, text)
                if market not in prefs_all:
                    skipped += 1
                    continue

                price_matches = re.findall(r"([0-9][0-9.,]*)\s*€", text)
                prices = [normalize_number(x) for x in price_matches]
                prices = [x for x in prices if x]
                current_price = prices[-1] if prices else None
                previous_price = None
                drop_pct = None

                drop_m = re.search(
                    r"dropped from\s*([0-9][0-9.,]*)\s*€\s*to\s*([0-9][0-9.,]*)\s*€",
                    text, re.I
                )
                if drop_m:
                    previous_price = normalize_number(drop_m.group(1))
                    current_price = normalize_number(drop_m.group(2))
                pct_m = re.search(r"↓\s*(\d{1,2})\s*%", text)
                if pct_m:
                    drop_pct = int(pct_m.group(1))

                bed_m = re.search(r"(\d+)\s*bed\.", text, re.I)
                bedrooms = int(bed_m.group(1)) if bed_m else None
                area_m = re.search(r"([0-9][0-9.,]*)\s*m(?:2|²)", text, re.I)
                area_m2 = normalize_number(area_m.group(1)) if area_m else None

                event_type = "price_reduction" if "price reduction" in subject.lower() else "new_listing"

                # Pull a concise description after the beds/area line when possible.
                description = ""
                lines = [re.sub(r"\s+", " ", x).strip() for x in text.splitlines()]
                for i, line in enumerate(lines):
                    if re.search(r"\d+\s*bed\.", line, re.I):
                        for candidate in lines[i+1:i+8]:
                            low = candidate.lower()
                            if len(candidate) >= 35 and not low.startswith(("contact", "see all listings", "does this listing")):
                                if "idealista.com" not in low and "€/m" not in low:
                                    description = candidate[:500]
                                    break
                        break

                fit, fit_reasons = property_fit(
                    market, search_name, title, description, current_price, bedrooms,
                    area_m2, event_type, drop_pct, prefs_all.get(market, {})
                )

                source_id = "idealista_menorca" if market == "menorca" else "idealista_northern_spain"
                source_name = "Idealista — Menorca" if market == "menorca" else "Idealista — Northern Spain"
                canonical_url = canonical_listing_url(href, listing_id)
                image_url = nearby_image(soup, listing_id)
                received = parse_date(msg.get("Date")) or dt.datetime.now(dt.timezone.utc)

                price_label = f"€{current_price:,}" if current_price is not None else "Price unavailable"
                details = [price_label]
                if bedrooms is not None:
                    details.append(f"{bedrooms} bed")
                if area_m2 is not None:
                    details.append(f"{area_m2} m²")
                if event_type == "price_reduction" and drop_pct:
                    details.append(f"↓{drop_pct}%")
                summary = " · ".join(details)
                if description:
                    summary += f" — {description}"

                metadata = {
                    "channel": "idealista_email",
                    "listing_id": listing_id,
                    "property_market": market,
                    "search_name": search_name,
                    "event_type": event_type,
                    "price_eur": current_price,
                    "previous_price_eur": previous_price,
                    "price_drop_pct": drop_pct,
                    "bedrooms": bedrooms,
                    "area_m2": area_m2,
                    "property_fit_score": fit,
                    "property_fit_reasons": fit_reasons,
                    "image_url": image_url,
                    "gmail_uid": uid,
                }
                fingerprint = hashlib.sha256(f"idealista|{listing_id}".encode("utf-8")).hexdigest()

                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO items (
                            fingerprint,source_id,source_name,beat,editorial_type,
                            title,url,author,published_at,fetched_at,summary,raw_id,language,metadata
                        )
                        VALUES (%s,%s,%s,'real_estate','listing',%s,%s,'Idealista',%s,NOW(),%s,%s,'en',%s::jsonb)
                        ON CONFLICT (fingerprint) DO UPDATE SET
                            source_id=EXCLUDED.source_id,
                            source_name=EXCLUDED.source_name,
                            title=EXCLUDED.title,
                            url=EXCLUDED.url,
                            published_at=GREATEST(items.published_at, EXCLUDED.published_at),
                            fetched_at=NOW(),
                            summary=EXCLUDED.summary,
                            metadata=items.metadata || EXCLUDED.metadata
                    """, (
                        fingerprint, source_id, source_name, title, canonical_url,
                        received, summary, listing_id, json.dumps(metadata)
                    ))
                    updated += cur.rowcount
                parsed += 1

            conn.commit()

        print(f"IDEALISTA parsed={parsed} updated={updated} skipped={skipped}", flush=True)

    finally:
        try:
            mail.logout()
        except Exception:
            pass


if __name__ == "__main__":
    main()
