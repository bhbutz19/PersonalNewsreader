#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import math
import os
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent
GUIDE_PATH = ROOT / "taste_guide.json"
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise SystemExit("DATABASE_URL is required")

def utcnow():
    return dt.datetime.now(dt.timezone.utc)

def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS editorial_scores (
                item_id BIGINT PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
                score INTEGER NOT NULL,
                personal_relevance INTEGER NOT NULL,
                importance INTEGER NOT NULL,
                novelty INTEGER NOT NULL,
                source_quality INTEGER NOT NULL,
                local_specificity INTEGER NOT NULL,
                conversation_value INTEGER NOT NULL,
                reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
                scored_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                scoring_version INTEGER NOT NULL DEFAULT 1
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_editorial_scores_score ON editorial_scores(score DESC)")
    conn.commit()

def clamp(n, lo=0, hi=100):
    return max(lo, min(hi, int(round(n))))

def recency_points(ts, now):
    if not ts:
        return 5
    age_h = max(0.0, (now - ts).total_seconds() / 3600)
    if age_h <= 6: return 15
    if age_h <= 12: return 13
    if age_h <= 24: return 11
    if age_h <= 48: return 8
    if age_h <= 72: return 5
    if age_h <= 168: return 2
    return 0

def local_points(beat):
    if beat in {"dc_local","dc_politics","dc_dining","menorca","logrono","cedarburg"}:
        return 10
    if beat in {"milwaukee","wisconsin","wisconsin_politics","uwm"}:
        return 7
    return 2

def editorially_usable(source_id, beat, title, url):
    t=(title or "").strip()
    low=t.lower()
    if not t:
        return False

    exact_junk={
        "download the app","learn more about openweb","interview with politico",
        "her fourth hot wing","100,000 protesters"
    }
    if low in exact_junk:
        return False

    fragment_prefixes=(
        "according to ","per the ","per ","to learn more ","from ",
        "overturned the ","wrote to ","federal documents show ",
        "aquifer breach on ","died oct.","interview with "
    )
    if low.startswith(fragment_prefixes):
        return False

    if beat=="dc_dining":
        if any(x in low for x in (
            "best new restaurants in manhattan",
            "best new restaurants in los angeles",
            "portland, oregon",
            "openweb"
        )):
            return False

    if beat=="cooking" and any(x in low for x in ("openweb","portland, oregon")):
        return False

    # Guard against sentence fragments masquerading as headlines.
    if len(t) < 18:
        return False
    first_alpha = next((ch for ch in t if ch.isalpha()), "")
    if first_alpha and first_alpha.islower():
        return False

    return True

def score_item(row, guide, now):
    (item_id, source_id, source_name, beat, editorial_type, title,
     published_at, fetched_at, metadata, priority) = row
    metadata = metadata or {}
    reasons = []

    personal = guide["beat_relevance"].get(beat, 12)
    importance = 12
    novelty = recency_points(published_at or fetched_at, now)
    source_q = guide["source_quality"].get(priority or "", 8)
    local = local_points(beat)
    conversation = 5 if editorial_type == "social" else 1

    eq = guide["editorial_quality"].get(editorial_type or "", 2)
    importance += eq

    if editorial_type == "primary_source":
        importance += 2
        reasons.append("primary source")
    if priority == "official":
        reasons.append("official source")
    if metadata.get("must_carry"):
        personal += 25
        reasons.append("must carry")
    if metadata.get("manual_pick"):
        personal += 10
        reasons.append("manual pick")
    if metadata.get("original_reporting"):
        importance += 5
        reasons.append("original reporting")

    low = (title or "").lower()
    if any(x in low for x in ("breaking", "announces", "approved", "votes", "wins", "loss", "injury", "opens", "opening", "closes", "closed")):
        importance += 4
    if any(x in low for x in ("opinion", "sponsored", "advertisement")):
        importance -= 10
        reasons.append("promotional/opinion penalty")
    if source_id.startswith("govinfo_") and (
        low.startswith("federal register vol.")
        or low.startswith("congressional record volume")
        or low.startswith("serial no.")
    ):
        importance -= 8
        reasons.append("raw government compilation penalty")

    components = {
        "personal_relevance": clamp(personal,0,30),
        "importance": clamp(importance,0,25),
        "novelty": clamp(novelty,0,15),
        "source_quality": clamp(source_q,0,15),
        "local_specificity": clamp(local,0,10),
        "conversation_value": clamp(conversation,0,5),
    }
    total = sum(components.values())
    return item_id, total, components, reasons

def main():
    guide = json.loads(GUIDE_PATH.read_text(encoding="utf-8"))
    now = utcnow()
    with psycopg.connect(DATABASE_URL, autocommit=False, prepare_threshold=None) as conn:
        ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT i.id,i.source_id,i.source_name,i.beat,i.editorial_type,i.title,
                       i.published_at,i.fetched_at,i.metadata,s.priority
                FROM items i
                LEFT JOIN sources s ON s.source_id=i.source_id
                WHERE COALESCE(i.published_at,i.fetched_at) >= NOW() - INTERVAL '8 days'
                  AND NOT (
                    COALESCE(i.metadata->>'channel','')='email'
                    AND COALESCE(i.metadata->>'extraction_version','1') <> '2'
                  )
            """)
            rows = [r for r in cur.fetchall() if editorially_usable(r[1], r[3], r[5], None)]

        scored = 0
        with conn.cursor() as cur:
            for row in rows:
                item_id,total,comp,reasons = score_item(row, guide, now)
                cur.execute("""
                    INSERT INTO editorial_scores (
                        item_id,score,personal_relevance,importance,novelty,
                        source_quality,local_specificity,conversation_value,
                        reasons,scored_at,scoring_version
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,NOW(),1)
                    ON CONFLICT (item_id) DO UPDATE SET
                        score=EXCLUDED.score,
                        personal_relevance=EXCLUDED.personal_relevance,
                        importance=EXCLUDED.importance,
                        novelty=EXCLUDED.novelty,
                        source_quality=EXCLUDED.source_quality,
                        local_specificity=EXCLUDED.local_specificity,
                        conversation_value=EXCLUDED.conversation_value,
                        reasons=EXCLUDED.reasons,
                        scored_at=NOW(),
                        scoring_version=1
                """, (
                    item_id,total,comp["personal_relevance"],comp["importance"],
                    comp["novelty"],comp["source_quality"],comp["local_specificity"],
                    comp["conversation_value"],json.dumps(reasons)
                ))
                scored += 1
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("""
                SELECT i.beat, COUNT(*), ROUND(AVG(es.score),1), MAX(es.score)
                FROM editorial_scores es
                JOIN items i ON i.id=es.item_id
                WHERE es.scored_at >= NOW() - INTERVAL '15 minutes'
                GROUP BY i.beat
                ORDER BY MAX(es.score) DESC, i.beat
            """)
            summary = cur.fetchall()
        print(f"SCORED {scored} candidate items", flush=True)
        for beat,count,avg_score,max_score in summary:
            print(f"SCORE {beat}: {count} items avg={avg_score} max={max_score}", flush=True)

if __name__ == "__main__":
    main()
