#!/usr/bin/env python3
import subprocess
import sys

def run_stage(name, command):
    print(f"=== START {name} ===", flush=True)
    result = subprocess.run(command, check=False)
    print(f"=== END {name} exit={result.returncode} ===", flush=True)
    if result.returncode != 0:
        sys.exit(result.returncode)

run_stage("RSS_WEB", [sys.executable, "-u", "ingest.py"])
run_stage("GMAIL", [sys.executable, "-u", "email_ingest.py"])
run_stage("SCORE", [sys.executable, "-u", "score_items.py"])
run_stage("CLUSTER", [sys.executable, "-u", "cluster_stories.py"])
run_stage("IMAGES", [sys.executable, "-u", "enrich_images.py"])
run_stage("EDITION", [sys.executable, "-u", "build_edition.py"])
print("=== ALL COLLECTORS COMPLETE ===", flush=True)
