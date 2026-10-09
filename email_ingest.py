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
from email.utils import parsedate_to_datetime, parseaddr
from html.parser import HTMLParser
from bs4 import BeautifulSoup
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import psycopg

ROOT = Path(__file__).resolve().parent
RULES_PATH = ROOT / "email_rules.json"

DATABASE_URL = os.environ.get("DATABASE_URL")
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
GMAIL_LOOKBACK_DAYS = int(os.environ.get("GMAIL_LOOKBACK_DAYS", "7"))

if not DATABASE_URL:
    raise SystemExit("DATABASE_URL is required")

def now_utc():
    return dt.datetime.now(dt.timezone.utc)

def decode_mime(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value

def strip_html(value):
    if not value:
        return ""
    value = re.sub(r"<script.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()

def decode_part(part):
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except Exception:
        return payload.decode("utf-8", errors="replace")

def message_parts(msg):
    plain, html_parts = [], []
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
            plain.append(text)
        elif ctype == "text/html":
            html_parts.append(text)
    return plain, html_parts

def body_text(msg):
    plain, html_parts = message_parts(msg)
    out = "\n".join(x.strip() for x in plain if x.strip())
    if not out:
        out = "\n".join(strip_html(x) for x in html_parts if x.strip())
    return out[:200000]

class AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href:
            title = re.sub(r"\s+", " ", html.unescape(" ".join(self._text))).strip()
            self.links.append((title, self._href))
            self._href = None
            self._text = []

def normalize_url(url):
    if not url or not url.startswith(("http://", "https://")):
        return ""
    try:
        p = urlparse(url)
        return urlunparse((p.scheme, p.netloc.lower(), p.path, "", p.query, ""))
    except Exception:
        return url

def extract_links(msg, filters):
    _, html_parts = message_parts(msg)
    candidates = []

    # Prefer BeautifulSoup because many newsletters use deeply nested tables,
    # tracking redirects, encoded entities, and image/title link pairs.
    for h in html_parts:
        try:
            soup = BeautifulSoup(h, "html.parser")
            for a in soup.find_all("a", href=True):
                title = " ".join(a.stripped_strings).strip()
                href = a.get("href", "")
                if not title:
                    img = a.find("img")
                    if img and img.get("alt"):
                        title = img.get("alt", "").strip()
                candidates.append((title, href))
        except Exception:
            # Fallback to the simple stdlib parser.
            parser = AnchorParser()
            try:
                parser.feed(h)
                candidates.extend(parser.links)
            except Exception:
                pass

    reject_text = [x.lower() for x in filters.get("reject_text", [])]
    reject_domains = [x.lower() for x in filters.get("reject_domains", [])]
    min_len = int(filters.get("min_title_length", 8))
    seen = set()
    out = []

    for title, href in candidates:
        title = re.sub(r"\\s+", " ", html.unescape(title or "")).strip()
        href = normalize_url(href)
        if len(title) < min_len or not href:
            continue
        if not headline_like(title):
            continue
        lower = title.lower()
        if any(x in lower for x in reject_text):
            continue
        host = urlparse(href).netloc.lower()
        if any(host == d or host.endswith("." + d) for d in reject_domains):
            continue
        key = (title.lower(), href)
        if key in seen:
            continue
        seen.add(key)
        out.append((title, href))
    return out

def parse_received(value):
    if not value:
        return None
    try:
        d = parsedate_to_datetime(value)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(dt.timezone.utc)
    except Exception:
        return None

def detect_forwarded(text, subject):
    forwarded = bool(re.match(r"^(Fwd:|FW:|Forward:)", subject or "", re.I))
    original_sender = ""
    for p in (r"(?:^|\n)From:\s*(.+?)(?:\n|$)", r"(?:^|\n)De:\s*(.+?)(?:\n|$)"):
        m = re.search(p, text or "", re.I)
        if m:
            original_sender = m.group(1).strip()[:500]
            forwarded = True
            break
    return forwarded, original_sender

def classify(subject, sender, rules):
    subject = subject or ""
    sender = sender or ""
    for rule in rules:
        subject_pattern = rule.get("subject_regex")
        sender_pattern = rule.get("sender_regex")
        exclude_pattern = rule.get("subject_exclude_regex")

        if exclude_pattern and re.search(exclude_pattern, subject, re.I):
            continue

        subject_match = bool(subject_pattern and re.search(subject_pattern, subject, re.I))
        sender_match = bool(sender_pattern and re.search(sender_pattern, sender, re.I))

        # A rule may match by subject, by sender, or by either when both are supplied.
        if subject_match or sender_match:
            return rule
    return None

def message_fingerprint(message_id, uid, sender, subject, received_at):
    basis = "|".join([message_id or "", uid or "", sender or "", subject or "",
                      received_at.isoformat() if received_at else ""])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()

def normalize_title(value):
    value = html.unescape(value or "")
    value = re.sub(r"\\s+", " ", value).strip().lower()
    value = re.sub(r"[^a-z0-9áéíóúñüàèìòùç'’ -]+", "", value)
    return value

def headline_like(title):
    t = re.sub(r"\\s+", " ", html.unescape(title or "")).strip()
    low = t.lower()
    if len(t) < 14 or len(t) > 220:
        return False
    if len(re.findall(r"[A-Za-zÀ-ÿ0-9]+", t)) < 3:
        return False
    reject_exact = {
        "read more", "read the full article", "learn more", "click here",
        "view in browser", "view this email in your browser", "subscribe",
        "unsubscribe", "manage preferences", "sign up", "donate",
        "advertise", "contact us", "follow us", "instagram", "facebook",
        "twitter", "x", "youtube", "linkedin", "privacy policy"
    }
    if low in reject_exact:
        return False
    reject_contains = (
        "unsubscribe", "manage preferences", "view in browser",
        "privacy policy", "advertise with", "email preferences",
        "forward to a friend", "sponsored by", "paid for by"
    )
    if any(x in low for x in reject_contains):
        return False
    return True

def article_beat(rule, title):
    beat = rule.get("beat") if rule else "unclassified_email"
    for override in (rule or {}).get("beat_overrides", []):
        pattern = override.get("title_regex")
        if pattern and re.search(pattern, title or "", re.I):
            return override.get("beat", beat)
    return beat

def source_title_allowed(rule, title):
    if not rule:
        return True
    pattern = rule.get("title_reject_regex")
    if pattern and re.search(pattern, title or "", re.I):
        return False
    # Newsletter story anchors should normally read like headlines, not sentence fragments.
    m = re.search(r"[A-Za-zÀ-ÿ]", title or "")
    if m and (title or "")[m.start()].islower():
        return False
    return True

def content_count_limit(rule, text, default_limit):
    if not rule or not rule.get("use_email_content_counts"):
        return default_limit
    allowed = set(rule.get("allowed_content_types", []))
    counts = {}
    for key in ("articles","bulletins","press releases","events","property sales"):
        m = re.search(rf"(\\d+)\\s+{re.escape(key)}", text or "", re.I)
        if m:
            counts[key] = int(m.group(1))
    if not counts:
        return default_limit
    total = sum(counts.get(k, 0) for k in allowed)
    return total if total > 0 else default_limit

def item_fingerprint(source_id, title, url):
    # Newsletter tracking URLs change from send to send.  A stable source +
    # normalized headline fingerprint prevents the same story from becoming
    # multiple items just because it appeared in another newsletter.
    basis = f"{source_id}|{normalize_title(title)}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()

def ensure_tables(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS email_messages (
                id BIGSERIAL PRIMARY KEY,
                fingerprint TEXT NOT NULL UNIQUE,
                gmail_uid TEXT,
                message_id TEXT,
                sender TEXT,
                sender_name TEXT,
                subject TEXT NOT NULL,
                received_at TIMESTAMPTZ,
                fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                beat TEXT NOT NULL DEFAULT 'unclassified_email',
                editorial_type TEXT NOT NULL DEFAULT 'newsletter',
                body_text TEXT,
                original_sender TEXT,
                is_forwarded BOOLEAN NOT NULL DEFAULT FALSE,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb
            )
        """)
    conn.commit()

def sync_email_sources(conn, rules):
    with conn.cursor() as cur:
        for rule in rules:
            source_id = rule.get("source_id")
            if not source_id:
                continue
            cur.execute("""
                INSERT INTO sources
                  (source_id,name,beat,source_type,url,priority,editorial_type,enabled,updated_at)
                VALUES (%s,%s,%s,'email',%s,%s,%s,TRUE,NOW())
                ON CONFLICT (source_id) DO UPDATE SET
                  name=EXCLUDED.name, beat=EXCLUDED.beat, url=EXCLUDED.url,
                  editorial_type=EXCLUDED.editorial_type, enabled=TRUE, updated_at=NOW()
            """, (source_id, rule["name"], rule["beat"], rule.get("homepage","https://mail.google.com/"),
                  "preferred" if rule.get("must_carry") else "primary",
                  rule.get("editorial_type","newsletter")))
            # Keep previously extracted newsletter stories aligned with
            # source-rule changes, including article-level beat overrides.
            cur.execute("""
                SELECT id,title
                FROM items
                WHERE source_id=%s
            """, (source_id,))
            existing_items = cur.fetchall()
            for item_id, item_title in existing_items:
                desired_beat = article_beat(rule, item_title)
                cur.execute("""
                    UPDATE items
                    SET beat=%s,
                        editorial_type=%s
                    WHERE id=%s
                      AND (beat IS DISTINCT FROM %s OR editorial_type IS DISTINCT FROM %s)
                """, (
                    desired_beat,
                    rule.get("editorial_type","newsletter"),
                    item_id,
                    desired_beat,
                    rule.get("editorial_type","newsletter")
                ))
    conn.commit()

def main():
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("SKIP gmail: GMAIL_ADDRESS/GMAIL_APP_PASSWORD not configured")
        return

    config = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    rules = config.get("rules", [])
    filters = config.get("link_filters", {})
    mail = imaplib.IMAP4_SSL("imap.gmail.com")

    try:
        mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        mail.select("INBOX", readonly=True)

        parsed = inserted_messages = inserted_articles = 0

        with psycopg.connect(DATABASE_URL, autocommit=False, prepare_threshold=None) as conn:
            ensure_tables(conn)
            sync_email_sources(conn, rules)

            # Normal operation is incremental: Gmail UIDs increase monotonically
            # within the mailbox, so only inspect messages newer than the highest
            # UID already persisted. The date lookback is bootstrap-only.
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT MAX(gmail_uid::bigint)
                    FROM email_messages
                    WHERE gmail_uid ~ '^[0-9]+$'
                """)
                row = cur.fetchone()
                last_uid = int(row[0]) if row and row[0] is not None else None

            if last_uid is not None:
                start_uid = last_uid + 1
                status, data = mail.uid("search", None, f"(UID {start_uid}:*)")
                mode = f"incremental UID>{last_uid}"
            else:
                cutoff = (now_utc() - dt.timedelta(days=GMAIL_LOOKBACK_DAYS)).strftime("%d-%b-%Y")
                status, data = mail.uid("search", None, f'(SINCE "{cutoff}")')
                mode = f"bootstrap last {GMAIL_LOOKBACK_DAYS} days"

            if status != "OK":
                raise RuntimeError("Gmail IMAP search failed")

            uids = data[0].split() if data and data[0] else []
            if last_uid is not None:
                uids = [u for u in uids if u.isdigit() and int(u) > last_uid]

            # Revisit a bounded set of recent messages that were previously
            # unclassified. This lets improved source rules recover newsletters
            # we already fetched without resetting the mailbox checkpoint.
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT gmail_uid
                    FROM email_messages
                    WHERE beat='unclassified_email'
                      AND received_at >= NOW() - INTERVAL '7 days'
                      AND gmail_uid ~ '^[0-9]+$'
                    ORDER BY received_at DESC
                    LIMIT 100
                """)
                retry_uids = [str(r[0]).encode() for r in cur.fetchall() if r and r[0]]

            existing_uid_keys = {
                u.decode() if isinstance(u, bytes) else str(u)
                for u in uids
            }
            retry_added = 0
            for retry_uid in retry_uids:
                key = retry_uid.decode() if isinstance(retry_uid, bytes) else str(retry_uid)
                if key not in existing_uid_keys:
                    uids.append(retry_uid)
                    existing_uid_keys.add(key)
                    retry_added += 1

            print(
                f"GMAIL {mode}: {len(uids)} message(s) to inspect"
                + (f" ({retry_added} recent unclassified retries)" if retry_added else ""),
                flush=True
            )

            for uidb in uids:
                uid = uidb.decode()
                status, fetched = mail.uid("fetch", uid, "(BODY.PEEK[])")
                if status != "OK" or not fetched:
                    continue

                raw = next(
                    (part[1] for part in fetched if isinstance(part, tuple) and len(part) > 1),
                    None
                )
                if not raw:
                    continue

                msg = email.message_from_bytes(raw)
                subject = decode_mime(msg.get("Subject", "")).strip() or "(no subject)"
                sender_name, sender_addr = parseaddr(decode_mime(msg.get("From", "")))
                sender = sender_addr or decode_mime(msg.get("From", ""))
                received = parse_received(msg.get("Date"))
                message_id = (msg.get("Message-ID") or "").strip()
                text = body_text(msg)
                is_forwarded, original_sender = detect_forwarded(text, subject)
                rule = classify(subject, sender, rules)

                beat = rule.get("beat") if rule else "unclassified_email"
                editorial_type = rule.get("editorial_type") if rule else "newsletter"
                rule_name = rule.get("name") if rule else None
                fp = message_fingerprint(message_id, uid, sender, subject, received)

                metadata = {
                    "rule_name": rule_name,
                    "to": decode_mime(msg.get("To", "")),
                    "reply_to": decode_mime(msg.get("Reply-To", "")),
                }

                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO email_messages (
                            fingerprint,gmail_uid,message_id,sender,sender_name,subject,
                            received_at,fetched_at,beat,editorial_type,body_text,
                            original_sender,is_forwarded,metadata
                        )
                        VALUES (%s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s,%s,%s,%s,%s::jsonb)
                        ON CONFLICT (fingerprint) DO UPDATE SET
                            beat=EXCLUDED.beat,
                            editorial_type=EXCLUDED.editorial_type,
                            body_text=EXCLUDED.body_text,
                            original_sender=EXCLUDED.original_sender,
                            is_forwarded=EXCLUDED.is_forwarded,
                            metadata=EXCLUDED.metadata,
                            fetched_at=NOW()
                    """, (
                        fp,uid,message_id,sender,sender_name,subject,received,beat,
                        editorial_type,text,original_sender,is_forwarded,json.dumps(metadata)
                    ))
                    inserted_messages += cur.rowcount

                if rule and rule.get("extract_articles"):
                    source_id = rule["source_id"]
                    links = extract_links(msg, filters)
                    max_links = int(rule.get("max_article_links", 30))
                    max_links = content_count_limit(rule, text, max_links)
                    links = links[:max_links]
                    print(
                        f"EMAIL {rule['name']}: {subject!r} -> {len(links)} headline candidates",
                        flush=True
                    )

                    for link_index, (title, href) in enumerate(links, start=1):
                        if title.lower() == subject.lower():
                            continue
                        if not source_title_allowed(rule, title):
                            continue

                        article_beat_value = article_beat(rule, title)
                        item_fp = item_fingerprint(source_id, title, href)
                        item_meta = {
                            "channel": "email",
                            "email_message_id": message_id,
                            "gmail_uid": uid,
                            "newsletter_subject": subject,
                            "forwarded": is_forwarded,
                            "original_sender": original_sender,
                            "extraction_version": 3,
                            "must_carry": bool(rule.get("must_carry")),
                            "newsletter_link_index": link_index,
                        }

                        with conn.cursor() as cur:
                            cur.execute("""
                                INSERT INTO items (
                                    fingerprint,source_id,source_name,beat,editorial_type,
                                    title,url,author,published_at,fetched_at,summary,raw_id,metadata
                                )
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s,%s::jsonb)
                                ON CONFLICT (fingerprint) DO UPDATE SET
                                    url=EXCLUDED.url,
                                    author=EXCLUDED.author,
                                    published_at=EXCLUDED.published_at,
                                    fetched_at=NOW(),
                                    metadata=EXCLUDED.metadata
                            """, (
                                item_fp,source_id,rule["name"],article_beat_value,editorial_type,title,href,
                                sender_name or sender,received,"",message_id,json.dumps(item_meta)
                            ))
                            inserted_articles += cur.rowcount

                parsed += 1

            conn.commit()

        print(
            f"OK gmail: {parsed} inspected, {inserted_messages} new messages, "
            f"{inserted_articles} article candidates"
        )

    finally:
        try:
            mail.logout()
        except Exception:
            pass

if __name__ == "__main__":
    main()

                    ORDER BY received_at DESC
                    LIMIT 100
                """)
                retry_uids = [str(r[0]).encode() for r in cur.fetchall() if r and r[0]]

            before = {u.decode() if isinstance(u, bytes) else str(u) for u in uids}
            retry_added = 0
            for u in retry_uids:
                key = u.decode() if isinstance(u, bytes) else str(u)
                if key not in before:
                    uids.append(u)
                    before.add(key)
                    retry_added += 1

            print(
                f"GMAIL {mode}: {len(uids)} message(s) to inspect"
                + (f" ({retry_added} recent unclassified retries)" if retry_added else ""),
                flush=True
            )

            for uidb in uids:
                uid = uidb.decode()
                status, fetched = mail.uid("fetch", uid, "(BODY.PEEK[])")
                if status != "OK" or not fetched:
                    continue

                raw = next(
                    (part[1] for part in fetched if isinstance(part, tuple) and len(part) > 1),
                    None
                )
                if not raw:
                    continue

                msg = email.message_from_bytes(raw)
                subject = decode_mime(msg.get("Subject", "")).strip() or "(no subject)"
                sender_name, sender_addr = parseaddr(decode_mime(msg.get("From", "")))
                sender = sender_addr or decode_mime(msg.get("From", ""))
                received = parse_received(msg.get("Date"))
                message_id = (msg.get("Message-ID") or "").strip()
                text = body_text(msg)
                is_forwarded, original_sender = detect_forwarded(text, subject)
                rule = classify(subject, sender, rules)

                beat = rule.get("beat") if rule else "unclassified_email"
                editorial_type = rule.get("editorial_type") if rule else "newsletter"
                rule_name = rule.get("name") if rule else None
                fp = message_fingerprint(message_id, uid, sender, subject, received)

                metadata = {
                    "rule_name": rule_name,
                    "to": decode_mime(msg.get("To", "")),
                    "reply_to": decode_mime(msg.get("Reply-To", "")),
                }

                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO email_messages (
                            fingerprint,gmail_uid,message_id,sender,sender_name,subject,
                            received_at,fetched_at,beat,editorial_type,body_text,
                            original_sender,is_forwarded,metadata
                        )
                        VALUES (%s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s,%s,%s,%s,%s::jsonb)
                        ON CONFLICT (fingerprint) DO NOTHING
                    """, (
                        fp,uid,message_id,sender,sender_name,subject,received,beat,
                        editorial_type,text,original_sender,is_forwarded,json.dumps(metadata)
                    ))
                    inserted_messages += cur.rowcount

                if rule and rule.get("extract_articles"):
                    source_id = rule["source_id"]
                    links = extract_links(msg, filters)
                    max_links = int(rule.get("max_article_links", 30))
                    links = links[:max_links]
                    print(
                        f"EMAIL {rule['name']}: {subject!r} -> {len(links)} headline candidates",
                        flush=True
                    )

                    for title, href in links:
                        if title.lower() == subject.lower():
                            continue
                        if not source_title_allowed(rule, title):
                            continue

                        article_beat_value = article_beat(rule, title)
                        item_fp = item_fingerprint(source_id, title, href)
                        item_meta = {
                            "channel": "email",
                            "email_message_id": message_id,
                            "gmail_uid": uid,
                            "newsletter_subject": subject,
                            "forwarded": is_forwarded,
                            "original_sender": original_sender,
                            "extraction_version": 3,
                            "must_carry": bool(rule.get("must_carry")),
                        }

                        with conn.cursor() as cur:
                            cur.execute("""
                                INSERT INTO items (
                                    fingerprint,source_id,source_name,beat,editorial_type,
                                    title,url,author,published_at,fetched_at,summary,raw_id,metadata
                                )
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s,%s::jsonb)
                                ON CONFLICT (fingerprint) DO UPDATE SET
                                    url=EXCLUDED.url,
                                    author=EXCLUDED.author,
                                    published_at=EXCLUDED.published_at,
                                    fetched_at=NOW(),
                                    metadata=EXCLUDED.metadata
                            """, (
                                item_fp,source_id,rule["name"],article_beat_value,editorial_type,title,href,
                                sender_name or sender,received,"",message_id,json.dumps(item_meta)
                            ))
                            inserted_articles += cur.rowcount

                parsed += 1

            conn.commit()

        print(
            f"OK gmail: {parsed} inspected, {inserted_messages} new messages, "
            f"{inserted_articles} article candidates"
        )

    finally:
        try:
            mail.logout()
        except Exception:
            pass

if __name__ == "__main__":
    main()
