#!/usr/bin/env python3
import os
import sys
import psycopg

n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
url = os.environ["DATABASE_URL"]

with psycopg.connect(url) as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COALESCE(published_at, fetched_at), beat, source_name, title, url
            FROM items
            ORDER BY COALESCE(published_at, fetched_at) DESC
            LIMIT %s
        """, (n,))
        for row in cur.fetchall():
            print(" | ".join(str(v or "") for v in row))
