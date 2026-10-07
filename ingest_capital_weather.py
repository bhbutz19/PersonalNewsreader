#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import urllib.request
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import psycopg

DATABASE_URL=os.environ.get("DATABASE_URL")
URL="https://www.capitalweather.com/"
USER_AGENT="PersonalNewsreader/0.5 (+private personal-use reader)"

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
    headline_node=None
    for h in soup.find_all(["h2","h3"]):
        text=" ".join(h.stripped_strings)
        if re.search(r"^DC-area forecast:",text,re.I):
            headline_node=h
            break
    if not headline_node:
        raise RuntimeError("Capital Weather daily forecast headline not found")

    title=" ".join(headline_node.stripped_strings)
    link_node=headline_node.find("a",href=True) or headline_node.parent.find("a",href=True)
    url=urljoin(URL,link_node["href"]) if link_node else URL

    summary=""
    # Search nearby text for the homepage's HAPPENING NOW capsule.
    container=headline_node.parent
    for _ in range(4):
        if container is None:
            break
        text=" ".join(container.stripped_strings)
        m=re.search(r"HAPPENING NOW:\s*(.+?)(?:\s+By\s+|\s+/\s+|$)",text,re.I)
        if m:
            summary=m.group(1).strip()
            break
        container=container.parent

    if not summary:
        # Fallback: first short paragraph after headline.
        p=headline_node.find_next("p")
        if p:
            summary=" ".join(p.stripped_strings).strip()

    return title,url,summary[:1200]

def main():
    html=fetch()
    title,url,summary=parse(html)
    now=dt.datetime.now(dt.timezone.utc)
    fp=hashlib.sha256(f"capital_weather_forecast|{title}".encode()).hexdigest()

    metadata={
        "must_carry":True,
        "weather_forecast":True,
        "original_reporting":True,
    }

    with psycopg.connect(DATABASE_URL,autocommit=False,prepare_threshold=None) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sources
                  (source_id,name,beat,source_type,url,priority,editorial_type,enabled,updated_at)
                VALUES
                  ('capital_weather_forecast','Capital Weather','weather','web',%s,'preferred','reported_news',TRUE,NOW())
                ON CONFLICT (source_id) DO UPDATE SET
                  name=EXCLUDED.name,beat=EXCLUDED.beat,source_type=EXCLUDED.source_type,
                  url=EXCLUDED.url,priority=EXCLUDED.priority,
                  editorial_type=EXCLUDED.editorial_type,enabled=TRUE,updated_at=NOW()
            """,(URL,))
            cur.execute("""
                INSERT INTO items (
                    fingerprint,source_id,source_name,beat,editorial_type,
                    title,url,author,published_at,fetched_at,summary,raw_id,metadata
                )
                VALUES (%s,'capital_weather_forecast','Capital Weather','weather','reported_news',
                        %s,%s,'Capital Weather',%s,NOW(),%s,%s,%s::jsonb)
                ON CONFLICT (fingerprint) DO UPDATE SET
                    url=EXCLUDED.url,
                    fetched_at=NOW(),
                    summary=EXCLUDED.summary,
                    metadata=EXCLUDED.metadata
            """,(
                fp,title,url,now,summary,title,json.dumps(metadata)
            ))
        conn.commit()

    print(f"WEATHER {title}",flush=True)
    if summary:
        print(f"WEATHER SUMMARY {summary[:180]}",flush=True)

if __name__=="__main__":
    main()
