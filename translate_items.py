#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

import psycopg

DATABASE_URL=os.environ.get("DATABASE_URL")
DEEPL_API_KEY=(os.environ.get("DEEPL_API_KEY") or "").strip()
DEEPL_API_URL=(os.environ.get("DEEPL_API_URL") or "").strip()

if not DATABASE_URL:
    raise SystemExit("DATABASE_URL is required")

TARGET_BEATS={"spain","menorca","logrono"}

class DeepLAuthError(RuntimeError):
    pass

def api_url():
    if DEEPL_API_URL:
        return DEEPL_API_URL
    # DeepL API Free keys conventionally end in :fx.
    host="api-free.deepl.com" if DEEPL_API_KEY.endswith(":fx") else "api.deepl.com"
    return f"https://{host}/v2/translate"

def translate_pair(title, summary):
    texts=[title or ""]
    if summary:
        texts.append(summary)

    body=json.dumps({
        "text":texts,
        "source_lang":"ES",
        "target_lang":"EN-US",
    }).encode("utf-8")

    req=urllib.request.Request(
        api_url(),
        data=body,
        headers={
            "Authorization":f"DeepL-Auth-Key {DEEPL_API_KEY}",
            "Content-Type":"application/json",
            "User-Agent":"PersonalNewsreader/0.4",
        },
        method="POST",
    )

    attempts=0
    while True:
        attempts+=1
        try:
            with urllib.request.urlopen(req,timeout=25) as resp:
                payload=json.loads(resp.read().decode("utf-8"))
            out=[x.get("text","").strip() for x in payload.get("translations",[])]
            return (
                out[0] if out else (title or ""),
                out[1] if summary and len(out)>1 else ""
            )
        except urllib.error.HTTPError as exc:
            if exc.code in (401,403):
                raise DeepLAuthError(
                    f"DeepL authentication failed ({exc.code}). "
                    "Check that DEEPL_API_KEY is an API key and that the Free/Pro endpoint matches the key."
                )
            if exc.code==429 and attempts<4:
                wait=2 ** attempts
                print(f"WAIT DeepL rate limit; retrying in {wait}s",flush=True)
                time.sleep(wait)
                continue
            raise

def main():
    if not DEEPL_API_KEY:
        print("SKIP translate: DEEPL_API_KEY not configured",flush=True)
        return

    print(f"TRANSLATE endpoint={api_url().split('/v2/')[0]}",flush=True)

    with psycopg.connect(DATABASE_URL,autocommit=False,prepare_threshold=None) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id,beat,title,summary,metadata
                FROM items
                WHERE beat = ANY(%s)
                  AND (
                    COALESCE(metadata->>'translation_version','') <> '2'
                    OR COALESCE(metadata->>'translated_title','') = ''
                  )
                ORDER BY COALESCE(published_at,fetched_at) DESC
                LIMIT 60
            """,(list(TARGET_BEATS),))
            rows=cur.fetchall()

        translated=failed=0

        for item_id,beat,title,summary,metadata in rows:
            metadata=metadata or {}

            # Avoid translating an already-English replacement title on reruns:
            # if an original Spanish title was previously stored, use that.
            original_title=metadata.get("original_title") or title
            original_summary=metadata.get("original_summary")
            if original_summary is None:
                original_summary=summary or ""

            try:
                en_title,en_summary=translate_pair(original_title,original_summary)
                metadata.update({
                    "translation_version":"2",
                    "original_language":"es",
                    "original_title":original_title,
                    "original_summary":original_summary,
                    "translated_title":en_title or original_title,
                    "translated_summary":en_summary or "",
                    "translation_provider":"deepl",
                })

                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE items
                        SET title=%s,
                            summary=%s,
                            metadata=%s::jsonb
                        WHERE id=%s
                    """,(
                        en_title or original_title,
                        en_summary or original_summary,
                        json.dumps(metadata),
                        item_id
                    ))
                conn.commit()
                translated+=1

            except DeepLAuthError as exc:
                conn.rollback()
                print(f"AUTH {exc}",flush=True)
                print("TRANSLATE aborted after authentication failure",flush=True)
                break
            except Exception as exc:
                conn.rollback()
                failed+=1
                print(f"ERR translate item={item_id}: {exc}",flush=True)

        print(f"TRANSLATE translated={translated} failed={failed}",flush=True)

if __name__=="__main__":
    main()
