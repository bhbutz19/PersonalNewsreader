#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import os
from collections import defaultdict
from pathlib import Path

import psycopg

ROOT=Path(__file__).resolve().parent
GUIDE_PATH=ROOT/"taste_guide.json"
DATABASE_URL=os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise SystemExit("DATABASE_URL is required")

SECTION_ORDER=[
    "dc_local","dc_politics","us_politics","dc_dining",
    "milwaukee","wisconsin","wisconsin_politics","cedarburg",
    "packers","brewers","uwm","f1","spain","menorca","logrono",
    "real_estate","cooking","sailing"
]

SECTION_NAMES={
    "dc_local":"Washington",
    "dc_politics":"D.C. Government & Politics",
    "us_politics":"National Politics",
    "dc_dining":"D.C. Dining",
    "milwaukee":"Milwaukee",
    "wisconsin":"Wisconsin",
    "wisconsin_politics":"Wisconsin Politics",
    "cedarburg":"Cedarburg",
    "packers":"Packers",
    "brewers":"Brewers",
    "uwm":"UW–Milwaukee",
    "f1":"Formula 1",
    "spain":"Spain",
    "menorca":"Menorca",
    "logrono":"Logroño / La Rioja",
    "real_estate":"Property",
    "cooking":"Cooking",
    "sailing":"Sailing"
}

def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS editions (
                id BIGSERIAL PRIMARY KEY,
                edition_type TEXT NOT NULL,
                edition_date DATE NOT NULL,
                generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                payload JSONB NOT NULL,
                UNIQUE (edition_type,edition_date)
            )
        """)
    conn.commit()

def main():
    guide=json.loads(GUIDE_PATH.read_text(encoding="utf-8"))
    caps=guide.get("beat_caps",{})
    now=dt.datetime.now(dt.timezone.utc)
    edition_type=os.environ.get("EDITION_TYPE","morning")
    edition_date=now.date()

    with psycopg.connect(DATABASE_URL, autocommit=False, prepare_threshold=None) as conn:
        ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT sc.id,sc.beat,sc.title,sc.score,sc.item_count,sc.source_count,
                       sc.first_seen,sc.latest_seen,sc.metadata,
                       i.source_name,i.editorial_type,i.url
                FROM story_clusters sc
                JOIN items i ON i.id=sc.canonical_item_id
                WHERE sc.latest_seen >= NOW() - INTERVAL '48 hours'
                ORDER BY sc.score DESC, sc.latest_seen DESC
            """)
            rows=cur.fetchall()

        grouped=defaultdict(list)
        for r in rows:
            grouped[r[1]].append(r)

        sections=[]
        total=0
        for beat in SECTION_ORDER:
            cap=int(caps.get(beat,4))
            selected=grouped.get(beat,[])[:cap]
            if not selected:
                continue
            stories=[]
            for r in selected:
                cid,_,title,score,item_count,source_count,first_seen,latest_seen,meta,source_name,etype,url=r
                stories.append({
                    "cluster_id":cid,
                    "title":title,
                    "score":score,
                    "source":source_name,
                    "editorial_type":etype,
                    "url":url,
                    "item_count":item_count,
                    "source_count":source_count,
                    "first_seen":first_seen.isoformat() if first_seen else None,
                    "latest_seen":latest_seen.isoformat() if latest_seen else None
                })
            sections.append({"beat":beat,"name":SECTION_NAMES.get(beat,beat),"stories":stories})
            total+=len(stories)

        payload={
            "edition_type":edition_type,
            "edition_date":edition_date.isoformat(),
            "generated_at":now.isoformat(),
            "story_count":total,
            "sections":sections
        }

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO editions(edition_type,edition_date,payload)
                VALUES (%s,%s,%s::jsonb)
                ON CONFLICT (edition_type,edition_date) DO UPDATE SET
                    generated_at=NOW(),payload=EXCLUDED.payload
            """,(edition_type,edition_date,json.dumps(payload)))
        conn.commit()

        print(f"EDITION {edition_type} {edition_date}: {total} stories across {len(sections)} sections", flush=True)
        for section in sections:
            print(f"SECTION {section['beat']}: {len(section['stories'])} stories", flush=True)
            for story in section["stories"][:3]:
                print(f"  {story['score']:>3} | {story['title']}", flush=True)

if __name__=="__main__":
    main()
