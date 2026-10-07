#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import re
import urllib.request
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import psycopg

DATABASE_URL=os.environ.get("DATABASE_URL")
URL="https://washingtonian.com/sections/news/"
USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/154 Safari/537.36"

if not DATABASE_URL:
    raise SystemExit("DATABASE_URL is required")

def fetch():
    req=urllib.request.Request(URL,headers={
        "User-Agent":USER_AGENT,
        "Accept":"text/html,application/xhtml+xml",
    })
    with urllib.request.urlopen(req,timeout=25) as resp:
        return resp.read()

def parse(html):
    soup=BeautifulSoup(html,"html.parser")
    out=[]
    seen=set()

    # Washingtonian's section page exposes current stories as heading links.
    for h in soup.find_all(["h2","h3"]):
        a=h.find("a",href=True)
        if not a:
            continue
        title=" ".join(a.stripped_strings).strip()
        href=urljoin(URL,a.get("href",""))
        if not title or len(title)<15:
            continue
        host=urlparse(href).netloc.lower()
        if "washingtonian.com" not in host:
            continue
        low=title.lower()
        if low in {"news & politics","most popular in news & politics","latest in news & politics"}:
            continue
        if href in seen:
            continue
        seen.add(href)

        summary=""
        # Prefer the short dek immediately following the headline if available.
        parent=h.parent
        if parent:
            for p in parent.find_all("p",limit=2):
                text=" ".join(p.stripped_strings).strip()
                if text and text.lower()!=title.lower():
                    summary=text
                    break

        out.append((title,href,summary[:1000]))
        if len(out)>=25:
            break

    return out

def main():
    stories=parse(fetch())
    inserted=0

    with psycopg.connect(DATABASE_URL,autocommit=False,prepare_threshold=None) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sources
                  (source_id,name,beat,source_type,url,priority,editorial_type,enabled,updated_at)
                VALUES
                  ('washingtonian_news','Washingtonian — News & Politics','dc_local','web',%s,'primary','reported_news',TRUE,NOW())
                ON CONFLICT (source_id) DO UPDATE SET
                  name=EXCLUDED.name,beat=EXCLUDED.beat,source_type=EXCLUDED.source_type,
                  url=EXCLUDED.url,priority=EXCLUDED.priority,
                  editorial_type=EXCLUDED.editorial_type,enabled=TRUE,updated_at=NOW()
            """,(URL,))

            for title,url,summary in stories:
                fp=hashlib.sha256(f"washingtonian_news|{url}|{title}".encode()).hexdigest()
                cur.execute("""
                    INSERT INTO items (
                        fingerprint,source_id,source_name,beat,editorial_type,
                        title,url,author,published_at,fetched_at,summary,raw_id,metadata
                    )
                    VALUES (%s,'washingtonian_news','Washingtonian','dc_local','reported_news',
                            %s,%s,'Washingtonian',NULL,NOW(),%s,%s,'{}'::jsonb)
                    ON CONFLICT (fingerprint) DO NOTHING
                """,(fp,title,url,summary,url))
                inserted+=cur.rowcount

        conn.commit()

    print(f"WASHINGTONIAN parsed={len(stories)} inserted={inserted}",flush=True)

if __name__=="__main__":
    main()
