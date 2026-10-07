#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

import psycopg

DATABASE_URL=os.environ.get("DATABASE_URL")
DEEPL_API_KEY=os.environ.get("DEEPL_API_KEY")
DEEPL_API_URL=os.environ.get("DEEPL_API_URL","https://api-free.deepl.com/v2/translate")

if not DATABASE_URL:
    raise SystemExit("DATABASE_URL is required")

TARGET_BEATS={"spain","menorca","logrono"}

def translate(text):
    if not text or not text.strip():
        return ""
    data=urllib.parse.urlencode({
        "auth_key":DEEPL_API_KEY,
        "text":text,
        "source_lang":"ES",
        "target_lang":"EN-US",
    }).encode("utf-8")
    req=urllib.request.Request(
        DEEPL_API_URL,
        data=data,
        headers={"Content-Type":"application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req,timeout=25) as resp:
        payload=json.loads(resp.read().decode("utf-8"))
    translations=payload.get("translations") or []
    return translations[0].get("text","").strip() if translations else ""

def main():
    if not DEEPL_API_KEY:
        print("SKIP translate: DEEPL_API_KEY not configured",flush=True)
        return

    with psycopg.connect(DATABASE_URL,autocommit=False,prepare_threshold=None) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id,beat,title,summary,metadata
                FROM items
                WHERE beat = ANY(%s)
                  AND (
                    COALESCE(metadata->>'translation_version','') <> '1'
                    OR COALESCE(metadata->>'translated_title','') = ''
                  )
                ORDER BY COALESCE(published_at,fetched_at) DESC
                LIMIT 120
            """,(list(TARGET_BEATS),))
            rows=cur.fetchall()

        translated=failed=0

        for item_id,beat,title,summary,metadata in rows:
            metadata=metadata or {}
            try:
                en_title=translate(title)
                en_summary=translate(summary) if summary else ""
                metadata.update({
                    "translation_version":"1",
                    "original_language":"es",
                    "original_title":title,
                    "original_summary":summary or "",
                    "translated_title":en_title or title,
                    "translated_summary":en_summary or "",
                    "translation_provider":"deepl",
                })
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE items
                        SET title=%s,
                            summary=%s,
                            language='en',
                            metadata=%s::jsonb
                        WHERE id=%s
                    """,(
                        en_title or title,
                        en_summary or summary or "",
                        json.dumps(metadata),
                        item_id
                    ))
                conn.commit()
                translated+=1
            except Exception as exc:
                conn.rollback()
                failed+=1
                print(f"ERR translate item={item_id}: {exc}",flush=True)

        print(f"TRANSLATE translated={translated} failed={failed}",flush=True)

if __name__=="__main__":
    main()
