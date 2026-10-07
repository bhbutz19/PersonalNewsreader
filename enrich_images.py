#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import urllib.request
from html import unescape

import psycopg

DATABASE_URL=os.environ.get("DATABASE_URL")
USER_AGENT="PersonalNewsreader/0.3 (+private personal-use reader)"
if not DATABASE_URL:
    raise SystemExit("DATABASE_URL is required")

META_PATTERNS=[
    re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',re.I),
    re.compile(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',re.I),
]

def fetch_html(url):
    req=urllib.request.Request(url,headers={"User-Agent":USER_AGENT,"Accept":"text/html,application/xhtml+xml"})
    with urllib.request.urlopen(req,timeout=12) as resp:
        ctype=(resp.headers.get("Content-Type") or "").lower()
        if "text/html" not in ctype:
            return ""
        return resp.read(500000).decode("utf-8","replace")

def extract_image(html):
    for p in META_PATTERNS:
        m=p.search(html)
        if m:
            u=unescape(m.group(1)).strip()
            if u.startswith(("http://","https://")):
                return u
    return ""

def main():
    with psycopg.connect(DATABASE_URL,autocommit=False,prepare_threshold=None) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT i.id,i.url,i.metadata
                FROM story_clusters sc
                JOIN items i ON i.id=sc.canonical_item_id
                WHERE sc.latest_seen >= NOW() - INTERVAL '48 hours'
                  AND sc.score >= 70
                ORDER BY i.id DESC
                LIMIT 40
            """)
            rows=cur.fetchall()

        checked=updated=0
        for item_id,url,metadata in rows:
            metadata=metadata or {}
            if metadata.get("image_url") or not url or not url.startswith(("http://","https://")):
                continue
            try:
                html=fetch_html(url)
                checked+=1
                img=extract_image(html)
                if not img:
                    continue
                metadata["image_url"]=img
                metadata["image_source"]="page_meta"
                with conn.cursor() as cur:
                    cur.execute("UPDATE items SET metadata=%s::jsonb WHERE id=%s",(json.dumps(metadata),item_id))
                conn.commit()
                updated+=1
            except Exception:
                conn.rollback()

        print(f"IMAGES checked={checked} updated={updated}",flush=True)

if __name__=="__main__":
    main()
