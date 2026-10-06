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
from pathlib import Path

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

def body_text(msg):
    plain = []
    html_parts = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if "attachment" in disp.lower():
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except Exception:
                text = payload.decode("utf-8", errors="replace")
            if ctype == "text/plain":
                plain.append(text)
            elif ctype == "text/html":
                html_parts.append(strip_html(text))
    else:
        payload = msg.get_payload(decode=True)
        if payload is not None:
            charset = msg.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except Exception:
                text = payload.decode("utf-8", errors="replace")
            if msg.get_content_type() == "text/html":
                html_parts.append(strip_html(text))
            else:
                plain.append(text)
    out = "\n".join(x.strip() for x in plain if x.strip())
    if not out:
        out = "\n".join(x.strip() for x in html_parts if x.strip())
    return out[:200000]

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
    if text:
        patterns = [
            r"(?:^|\n)From:\s*(.+?)(?:\n|$)",
            r"(?:^|\n)De:\s*(.+?)(?:\n|$)",
        ]
        for p in patterns:
            m = re.search(p, text, re.I)
            if m:
                original_sender = m.group(1).strip()[:500]
                forwarded = True
                break
    return forwarded, original_sender

def classify(subject, sender, rules):
    hay = f"{subject}\n{sender}"
    for rule in rules:
        pattern = rule.get("subject_regex")
        if pattern and re.search(pattern, hay, re.I):
            return rule.get("beat", "unclassified_email"), rule.get("editorial_type", "newsletter"), rule.get("name")
    return "unclassified_email", "newsletter", None

def fingerprint(message_id, uid, sender, subject, received_at):
    basis = "|".join([
        message_id or "",
        uid or "",
        sender or "",
        subject or "",
        received_at.isoformat() if received_at else "",
    ])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()

def ensure_table(conn):
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
        cur.execute("CREATE INDEX IF NOT EXISTS idx_email_messages_received_at ON email_messages(received_at DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_email_messages_beat ON email_messages(beat)")
    conn.commit()

def main():
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("SKIP gmail: GMAIL_ADDRESS/GMAIL_APP_PASSWORD not configured")
        return

    rules = json.loads(RULES_PATH.read_text(encoding="utf-8")).get("rules", [])
    cutoff = (now_utc() - dt.timedelta(days=GMAIL_LOOKBACK_DAYS)).strftime("%d-%b-%Y")

    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        mail.select("INBOX", readonly=True)
        status, data = mail.uid("search", None, f'(SINCE "{cutoff}")')
        if status != "OK":
            raise RuntimeError("Gmail IMAP search failed")
        uids = data[0].split()
        inserted = 0
        parsed = 0

        with psycopg.connect(DATABASE_URL, autocommit=False, prepare_threshold=None) as conn:
            ensure_table(conn)
            for uidb in uids[-500:]:
                uid = uidb.decode()
                status, fetched = mail.uid("fetch", uid, "(BODY.PEEK[])")
                if status != "OK" or not fetched:
                    continue
                raw = None
                for part in fetched:
                    if isinstance(part, tuple) and len(part) > 1:
                        raw = part[1]
                        break
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
                beat, editorial_type, rule_name = classify(subject, sender, rules)
                fp = fingerprint(message_id, uid, sender, subject, received)

                metadata = {
                    "rule_name": rule_name,
                    "to": decode_mime(msg.get("To", "")),
                    "reply_to": decode_mime(msg.get("Reply-To", "")),
                }

                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO email_messages (
                            fingerprint, gmail_uid, message_id, sender, sender_name,
                            subject, received_at, fetched_at, beat, editorial_type,
                            body_text, original_sender, is_forwarded, metadata
                        )
                        VALUES (%s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s,%s,%s,%s,%s::jsonb)
                        ON CONFLICT (fingerprint) DO NOTHING
                    """, (
                        fp, uid, message_id, sender, sender_name, subject, received,
                        beat, editorial_type, text, original_sender, is_forwarded,
                        json.dumps(metadata)
                    ))
                    inserted += cur.rowcount
                parsed += 1

            conn.commit()
        print(f"OK gmail: {parsed} parsed, {inserted} new")

    finally:
        try:
            mail.logout()
        except Exception:
            pass

if __name__ == "__main__":
    main()
