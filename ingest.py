#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent
SOURCES_PATH = ROOT / "sources.json"
SCHEMA_PATH = ROOT / "schema.sql"

DATABASE_URL = os.environ.get("DATABASE_URL")
USER_AGENT = "PersonalNewsreader/0.2 (+private personal-use feed reader)"

if not DATABASE_URL:
    raise SystemExit("DATABASE_URL is required")

def utcnow():
    return dt.datetime.now(dt.timezone.utc)

def strip_html(value):
    if not value:
        return ""
    value = re.sub(r"<script.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()

def parse_date(value):
    if not value:
        return None
    try:
        d = email.utils.parsedate_to_datetime(value)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(dt.timezone.utc)
    except Exception:
        pass
    try:
        return dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except Exception:
        return None

def local_name(tag):
    return tag.split("}", 1)[-1]

def child_text_by_local(el, names):
    names = set(names)
    for ch in list(el):
        if local_name(ch.tag) in names and ch.text:
            return ch.text.strip()
    return ""


def image_url(entry):
    # Prefer explicit RSS/Media RSS image elements already supplied by publishers.
    for ch in entry.iter():
        name = local_name(ch.tag).lower()
        url = ch.attrib.get("url") or ch.attrib.get("href")
        if not url:
            continue
        medium = (ch.attrib.get("medium") or "").lower()
        ctype = (ch.attrib.get("type") or "").lower()
        if name in ("thumbnail", "image"):
            return url
        if name in ("content", "enclosure") and (medium == "image" or ctype.startswith("image/")):
            return url
    return ""

def atom_link(entry):
    for ch in list(entry):
        if local_name(ch.tag) == "link":
            href = ch.attrib.get("href")
            rel = ch.attrib.get("rel", "alternate")
            if href and rel in ("alternate", ""):
                return href
    return ""

def parse_feed(data):
    root = ET.fromstring(data)
    root_name = local_name(root.tag).lower()
    out = []

    if root_name in ("rss", "rdf"):
        candidates = [x for x in root.iter() if local_name(x.tag).lower() == "item"]
        for x in candidates:
            title = child_text_by_local(x, ["title"])
            if not title:
                continue
            out.append({
                "title": strip_html(title),
                "url": child_text_by_local(x, ["link"]),
                "raw_id": child_text_by_local(x, ["guid"]),
                "author": strip_html(child_text_by_local(x, ["author", "creator"])),
                "published_at": parse_date(child_text_by_local(x, ["pubDate", "date", "published", "updated"])),
                "summary": strip_html(child_text_by_local(x, ["description", "summary", "encoded"])),
                "image_url": image_url(x),
            })
    else:
        candidates = [x for x in root.iter() if local_name(x.tag).lower() == "entry"]
        for x in candidates:
            title = child_text_by_local(x, ["title"])
            if not title:
                continue
            author = ""
            for ch in list(x):
                if local_name(ch.tag) == "author":
                    author = child_text_by_local(ch, ["name"])
                    break
            out.append({
                "title": strip_html(title),
                "url": atom_link(x),
                "raw_id": child_text_by_local(x, ["id"]),
                "author": strip_html(author),
                "published_at": parse_date(child_text_by_local(x, ["published", "updated"])),
                "summary": strip_html(child_text_by_local(x, ["summary", "content"])),
                "image_url": image_url(x),
            })
    return out

def fingerprint(source_id, item):
    basis = "|".join([
        source_id,
        item.get("raw_id") or "",
        item.get("url") or "",
        item.get("title") or "",
        item.get("published_at").isoformat() if item.get("published_at") else "",
    ])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()

def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*;q=0.5",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()

def init_db(conn):
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        for statement in [s.strip() for s in sql.split(";") if s.strip()]:
            cur.execute(statement)
    conn.commit()

def sync_sources(conn, config):
    with conn.cursor() as cur:
        for s in config["sources"]:
            cur.execute("""
                INSERT INTO sources
                  (source_id,name,beat,source_type,url,priority,editorial_type,enabled,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (source_id) DO UPDATE SET
                  name=EXCLUDED.name,
                  beat=EXCLUDED.beat,
                  source_type=EXCLUDED.source_type,
                  url=EXCLUDED.url,
                  priority=EXCLUDED.priority,
                  editorial_type=EXCLUDED.editorial_type,
                  enabled=EXCLUDED.enabled,
                  updated_at=NOW()
            """, (
                s["id"], s["name"], s["beat"], s["type"], s["url"],
                s.get("priority"), s.get("editorial_type"),
                bool(s.get("enabled", True))
            ))
    conn.commit()

def main():
    config = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    configured_rss_sources = [
        s for s in config["sources"]
        if s.get("enabled", True) and s.get("type") == "rss"
    ]

    with psycopg.connect(DATABASE_URL, autocommit=False, prepare_threshold=None) as conn:
        init_db(conn)
        sync_sources(conn, config)

        # Respect per-source collection cadence. Render can wake every 30 minutes
        # while noisier/rate-limited feeds (e.g. Reddit, GovInfo) run less often.
        now = utcnow()
        rss_sources = []
        skipped = []
        with conn.cursor() as cur:
            for source in configured_rss_sources:
                min_interval = int(source.get("min_interval_minutes", 30))
                cur.execute(
                    "SELECT last_checked_at FROM sources WHERE source_id=%s",
                    (source["id"],)
                )
                row = cur.fetchone()
                last_checked = row[0] if row else None
                due = (
                    last_checked is None or
                    (now - last_checked) >= dt.timedelta(minutes=min_interval)
                )
                if due:
                    rss_sources.append(source)
                else:
                    skipped.append(source["id"])

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO ingestion_runs (sources_attempted)
                VALUES (%s)
                RETURNING id
            """, (len(rss_sources),))
            run_id = cur.fetchone()[0]
        conn.commit()

        if skipped:
            print(f"SKIP {len(skipped)} not due: {', '.join(skipped)}")

        succeeded = failed = parsed_total = inserted_total = 0

        for source in rss_sources:
            checked = utcnow()
            try:
                payload = fetch(source["url"])
                entries = parse_feed(payload)
                parsed_total += len(entries)
                inserted = 0

                with conn.cursor() as cur:
                    for item in entries:
                        fp = fingerprint(source["id"], item)
                        cur.execute("""
                            INSERT INTO items (
                                fingerprint, source_id, source_name, beat, editorial_type,
                                title, url, author, published_at, fetched_at, summary, raw_id, metadata
                            )
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                            ON CONFLICT (fingerprint) DO UPDATE SET
                                url=EXCLUDED.url,
                                summary=EXCLUDED.summary,
                                metadata=EXCLUDED.metadata
                        """, (
                            fp, source["id"], source["name"], source["beat"],
                            source.get("editorial_type"), item["title"], item.get("url"),
                            item.get("author"), item.get("published_at"), checked,
                            item.get("summary"), item.get("raw_id"),
                            json.dumps({"image_url": item.get("image_url") or ""})
                        ))
                        inserted += cur.rowcount

                    cur.execute("""
                        UPDATE sources
                        SET last_checked_at=%s, last_success_at=%s, last_error=NULL,
                            consecutive_failures=0, updated_at=NOW()
                        WHERE source_id=%s
                    """, (checked, checked, source["id"]))

                conn.commit()
                inserted_total += inserted
                succeeded += 1
                print(f"OK  {source['id']}: {len(entries)} parsed, {inserted} new")

            except Exception as exc:
                failed += 1
                conn.rollback()
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE sources
                        SET last_checked_at=%s, last_error=%s,
                            consecutive_failures=consecutive_failures+1,
                            updated_at=NOW()
                        WHERE source_id=%s
                    """, (checked, str(exc)[:2000], source["id"]))
                conn.commit()
                print(f"ERR {source['id']}: {exc}", file=sys.stderr)

        with conn.cursor() as cur:
            cur.execute("""
                UPDATE ingestion_runs
                SET finished_at=NOW(),
                    status=%s,
                    sources_succeeded=%s,
                    sources_failed=%s,
                    items_parsed=%s,
                    items_inserted=%s
                WHERE id=%s
            """, (
                "success" if failed == 0 else "partial",
                succeeded, failed, parsed_total, inserted_total, run_id
            ))
        conn.commit()

        print(
            f"DONE run={run_id} due_sources={len(rss_sources)} "
            f"configured_sources={len(configured_rss_sources)} "
            f"succeeded={succeeded} failed={failed} "
            f"parsed={parsed_total} inserted={inserted_total}"
        )

if __name__ == "__main__":
    main()
