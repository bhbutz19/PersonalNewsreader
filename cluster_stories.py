#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import os
import re
from collections import defaultdict
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent
GUIDE_PATH = ROOT / "taste_guide.json"
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise SystemExit("DATABASE_URL is required")

STOP = {
    "the","a","an","and","or","but","of","to","in","on","for","with","at","by",
    "from","as","is","are","was","were","be","been","being","this","that","these",
    "those","it","its","after","before","about","over","into","new","says","say"
}

def norm_title(s):
    s=(s or "").lower()
    s=re.sub(r"https?://\S+"," ",s)
    s=re.sub(r"[^a-z0-9áéíóúñüàèìòùç'’ -]+"," ",s)
    s=re.sub(r"\s+"," ",s).strip()
    return s

def tokens(s):
    return {x for x in re.findall(r"[a-z0-9áéíóúñüàèìòùç']+", norm_title(s))
            if len(x)>2 and x not in STOP}

def similarity(a,b):
    ta,tb=tokens(a),tokens(b)
    if not ta or not tb:
        return 0.0
    inter=len(ta & tb)
    union=len(ta | tb)
    j=inter/union if union else 0.0
    # Reward strong containment for slightly rewritten newsletter/RSS headlines.
    contain=max(inter/len(ta), inter/len(tb))
    return max(j, contain*0.88)

def ensure_tables(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS story_clusters (
                id BIGSERIAL PRIMARY KEY,
                cluster_key TEXT NOT NULL UNIQUE,
                beat TEXT NOT NULL,
                canonical_item_id BIGINT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                score INTEGER NOT NULL,
                item_count INTEGER NOT NULL DEFAULT 1,
                source_count INTEGER NOT NULL DEFAULT 1,
                first_seen TIMESTAMPTZ,
                latest_seen TIMESTAMPTZ,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS story_cluster_items (
                cluster_id BIGINT NOT NULL REFERENCES story_clusters(id) ON DELETE CASCADE,
                item_id BIGINT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                similarity NUMERIC(5,4) NOT NULL DEFAULT 1.0,
                PRIMARY KEY (cluster_id,item_id)
            )
        """)
    conn.commit()

def main():
    now=dt.datetime.now(dt.timezone.utc)
    with psycopg.connect(DATABASE_URL, autocommit=False, prepare_threshold=None) as conn:
        ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT i.id,i.beat,i.title,i.source_id,i.source_name,
                       COALESCE(i.published_at,i.fetched_at) AS ts,
                       es.score,i.editorial_type,i.url
                FROM items i
                JOIN editorial_scores es ON es.item_id=i.id
                WHERE COALESCE(i.published_at,i.fetched_at) >= NOW() - INTERVAL '4 days'
                  AND NOT (
                    COALESCE(i.metadata->>'channel','')='email'
                    AND COALESCE(i.metadata->>'extraction_version','1') <> '2'
                  )
                ORDER BY i.beat, es.score DESC, ts DESC
            """)
            rows=cur.fetchall()

        bybeat=defaultdict(list)
        for r in rows:
            bybeat[r[1]].append(r)

        clusters=[]
        for beat,items in bybeat.items():
            beat_clusters=[]
            for row in items:
                item_id,_,title,source_id,source_name,ts,score,etype,url=row
                best=None
                best_sim=0.0
                for c in beat_clusters:
                    sim=similarity(title,c["title"])
                    if sim>best_sim:
                        best,best_sim=c,sim
                threshold=0.66 if beat in {"us_politics","spain"} else 0.62
                if best and best_sim>=threshold:
                    best["items"].append((row,best_sim))
                    best["sources"].add(source_id)
                    best["first_seen"]=min(best["first_seen"],ts)
                    best["latest_seen"]=max(best["latest_seen"],ts)
                    if score>best["score"]:
                        best["canonical"]=row
                        best["title"]=title
                        best["score"]=score
                else:
                    beat_clusters.append({
                        "beat":beat,"title":title,"score":score,"canonical":row,
                        "items":[(row,1.0)],"sources":{source_id},
                        "first_seen":ts,"latest_seen":ts
                    })
            clusters.extend(beat_clusters)

        with conn.cursor() as cur:
            cur.execute("TRUNCATE story_cluster_items, story_clusters RESTART IDENTITY")
            for c in clusters:
                canonical=c["canonical"]
                key=f"{c['beat']}|{norm_title(c['title'])[:220]}"
                meta={"canonical_source":canonical[4],"editorial_type":canonical[7],"url":canonical[8]}
                cur.execute("""
                    INSERT INTO story_clusters (
                        cluster_key,beat,canonical_item_id,title,score,item_count,source_count,
                        first_seen,latest_seen,metadata,updated_at
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,NOW())
                    RETURNING id
                """,(key,c["beat"],canonical[0],c["title"],c["score"],len(c["items"]),
                     len(c["sources"]),c["first_seen"],c["latest_seen"],json.dumps(meta)))
                cid=cur.fetchone()[0]
                for row,sim in c["items"]:
                    cur.execute("""
                        INSERT INTO story_cluster_items(cluster_id,item_id,similarity)
                        VALUES (%s,%s,%s)
                    """,(cid,row[0],sim))
        conn.commit()

        multi=sum(1 for c in clusters if len(c["items"])>1)
        print(f"CLUSTERED {len(rows)} items into {len(clusters)} stories; {multi} multi-source/repeat clusters", flush=True)
        for beat in sorted(bybeat):
            n=sum(1 for c in clusters if c["beat"]==beat)
            print(f"CLUSTER {beat}: {len(bybeat[beat])} items -> {n} stories", flush=True)

if __name__=="__main__":
    main()
