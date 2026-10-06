#!/usr/bin/env python3
import os
import psycopg

url = os.environ["DATABASE_URL"]
with psycopg.connect(url) as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT source_id, enabled, last_success_at,
                   consecutive_failures, COALESCE(last_error,'')
            FROM sources
            ORDER BY source_id
        """)
        for row in cur.fetchall():
            print(" | ".join(str(v) for v in row))
