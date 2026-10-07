#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import os
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg

ROOT=Path(__file__).resolve().parent
GUIDE_PATH=ROOT/"taste_guide.json"
DATABASE_URL=os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise SystemExit("DATABASE_URL is required")

EASTERN=ZoneInfo("America/New_York")

SECTION_ORDER=[
    "dc_local","weather","dc_politics","us_politics","dc_dining",
    "milwaukee","wisconsin","wisconsin_politics","cedarburg",
    "packers","brewers","uwm","f1","spain","menorca","logrono",
    "real_estate","cooking","sailing"
]

SECTION_NAMES={
    "dc_local":"Washington",
    "weather":"Weather",
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

def fetch_rows(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT sc.id,sc.beat,sc.title,sc.score,sc.item_count,sc.source_count,
                   sc.first_seen,sc.latest_seen,sc.metadata,
                   i.source_name,i.editorial_type,i.url,i.summary,i.metadata
            FROM story_clusters sc
            JOIN items i ON i.id=sc.canonical_item_id
            WHERE sc.latest_seen >= NOW() - INTERVAL '48 hours'
            ORDER BY sc.score DESC, sc.latest_seen DESC
        """)
        return cur.fetchall()

def row_to_story(r):
    cid,beat,title,score,item_count,source_count,first_seen,latest_seen,cluster_meta,source_name,etype,url,summary,item_meta=r
    item_meta=item_meta or {}
    return {
        "cluster_id":cid,
        "title":title,
        "score":score,
        "source":source_name,
        "editorial_type":etype,
        "url":url,
        "summary":summary or "",
        "image_url":item_meta.get("image_url") or "",
        "item_count":item_count,
        "source_count":source_count,
        "first_seen":first_seen.isoformat() if first_seen else None,
        "latest_seen":latest_seen.isoformat() if latest_seen else None
    }


def diversify_by_source(rows, cap, per_source_cap=None):
    """Keep score ordering while preventing one outlet from monopolizing a section."""
    if not rows or cap <= 0:
        return []
    if not per_source_cap:
        return rows[:cap]

    chosen=[]
    counts=defaultdict(int)

    # First pass enforces the cap.
    for r in rows:
        source=r[9] or "Unknown"
        if counts[source] >= per_source_cap:
            continue
        chosen.append(r)
        counts[source]+=1
        if len(chosen) >= cap:
            return chosen

    # If there are not enough alternative sources, fill remaining slots by score.
    if len(chosen) < cap:
        chosen_ids={r[0] for r in chosen}
        for r in rows:
            if r[0] in chosen_ids:
                continue
            chosen.append(r)
            if len(chosen) >= cap:
                break

    return chosen

def build_payload(rows, caps, edition_type, edition_date, now_utc, cutoff_utc=None):
    grouped=defaultdict(list)

    for r in rows:
        if edition_type=="evening" and cutoff_utc is not None:
            latest=r[7]
            if latest is None or latest < cutoff_utc:
                continue
        grouped[r[1]].append(r)

    # If the evening pool is too thin, include top recent carryovers from the last 24h.
    if edition_type=="evening":
        evening_count=sum(len(v) for v in grouped.values())
        if evening_count < 10:
            for r in rows:
                beat=r[1]
                if r in grouped.get(beat,[]):
                    continue
                if r[7] and r[7] >= now_utc - dt.timedelta(hours=24):
                    grouped[beat].append(r)

    sections=[]
    total=0

    for beat in SECTION_ORDER:
        cap=int(caps.get(beat,4))
        if edition_type=="evening":
            cap=min(cap, 3)
        pool=grouped.get(beat,[])
        reported=[r for r in pool if r[10] != "primary_source"]
        primary=[r for r in pool if r[10] == "primary_source"]
        primary_cap = 1

        # Washington should read like a newspaper section, not a single-source feed.
        # Keep no more than half the section from one outlet when alternatives exist.
        source_cap = max(2, cap // 2) if beat == "dc_local" else None
        reported_selected = diversify_by_source(
            reported,
            max(0, cap - min(primary_cap, len(primary))),
            source_cap
        )
        selected=(reported_selected + primary[:primary_cap])[:cap]
        if not selected:
            continue

        stories=[row_to_story(r) for r in selected]
        sections.append({
            "beat":beat,
            "name":SECTION_NAMES.get(beat,beat),
            "stories":stories
        })
        total+=len(stories)

    return {
        "edition_type":edition_type,
        "edition_date":edition_date.isoformat(),
        "generated_at":now_utc.isoformat(),
        "story_count":total,
        "sections":sections
    }

def upsert_edition(conn, edition_type, edition_date, payload):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO editions(edition_type,edition_date,payload)
            VALUES (%s,%s,%s::jsonb)
            ON CONFLICT (edition_type,edition_date) DO UPDATE SET
                generated_at=NOW(),payload=EXCLUDED.payload
        """,(edition_type,edition_date,json.dumps(payload)))
    conn.commit()

def edition_exists(conn, edition_type, edition_date):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM editions WHERE edition_type=%s AND edition_date=%s",
            (edition_type,edition_date)
        )
        return cur.fetchone() is not None

def print_payload(payload):
    print(
        f"EDITION {payload['edition_type']} {payload['edition_date']}: "
        f"{payload['story_count']} stories across {len(payload['sections'])} sections",
        flush=True
    )
    for section in payload["sections"]:
        print(f"SECTION {section['beat']}: {len(section['stories'])} stories", flush=True)
        for story in section["stories"][:3]:
            print(f"  {story['score']:>3} | {story['title']}", flush=True)

def main():
    guide=json.loads(GUIDE_PATH.read_text(encoding="utf-8"))
    caps=guide.get("beat_caps",{})
    now_utc=dt.datetime.now(dt.timezone.utc)
    now_et=now_utc.astimezone(EASTERN)
    edition_date=now_et.date()

    # Noon Eastern freezes the morning issue and starts the evening-change window.
    noon_et=dt.datetime.combine(edition_date, dt.time(12,0), tzinfo=EASTERN)
    noon_utc=noon_et.astimezone(dt.timezone.utc)

    with psycopg.connect(DATABASE_URL, autocommit=False, prepare_threshold=None) as conn:
        ensure_table(conn)
        rows=fetch_rows(conn)

        if now_et.hour < 12 or not edition_exists(conn,"morning",edition_date):
            morning=build_payload(rows,caps,"morning",edition_date,now_utc)
            upsert_edition(conn,"morning",edition_date,morning)
            print_payload(morning)
        else:
            print(f"EDITION morning {edition_date}: frozen after noon ET", flush=True)

        if now_et.hour >= 12:
            evening=build_payload(rows,caps,"evening",edition_date,now_utc,cutoff_utc=noon_utc)
            upsert_edition(conn,"evening",edition_date,evening)
            print_payload(evening)

if __name__=="__main__":
    main()
