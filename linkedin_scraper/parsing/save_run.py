#!/usr/bin/env python3
"""Finalize a scrape run: filter out already-known posts, save as a new timestamped JSONL file.

Usage (working directory = the repository root, the folder holding .venv/):
    .venv/bin/python3 linkedin_scraper/parsing/save_run.py

Reads data/new_posts.jsonl, drops anything already present by content (author + normalized
body — see norm_key), writes the survivors to data/scraped/posts_<UTC timestamp>.jsonl and
deletes new_posts.jsonl. Content dedup is the ONLY filter; see the note in main().

Safe to re-run: if new_posts.jsonl doesn't exist, it's a no-op.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import settings
from pydantic_shemas import read_jsonl


def norm_key(entry):
    """Dedup fingerprint: author + the letters of the body.

    Content-based because author + time cannot dedup — post_time_iso comes from a coarse
    relative age, so the same post scraped on two days carries two timestamps. Digits are
    dropped because reaction counts drift between passes. str.isalpha(), never [a-z]: the feed
    is heavily Russian/Ukrainian and an ASCII filter collapsed 3 pairs of different posts.
    """
    url = entry.get("author_profile_url") or "~"
    text = re.sub(r"\s+", " ", entry.get("text") or "").strip().lower()
    body = "".join(ch for ch in text if ch.isalpha())
    return (url, body[:120])


def main():
    settings.SCRAPED.mkdir(parents=True, exist_ok=True)
    new_records = read_jsonl(settings.NEW_POSTS)
    if not new_records:
        print("No new_posts.jsonl found (or it's empty) — nothing to save.")
        return

    existing_files = sorted(settings.SCRAPED.glob("posts_*.jsonl"))
    existing_keys = set()
    for path in existing_files:
        for rec in read_jsonl(path):
            existing_keys.add(norm_key(rec))

    # DEDUP IS THE ONLY FILTER. Never re-add a time cutoff: the feed is an algorithmic sample,
    # not an ordered stream, so "older than what I have" does not mean "already seen" — that
    # filter discarded 84 never-before-seen posts when simulated over the real dataset.
    #
    # `seen` GROWS as records are kept, so it collapses repeats within this batch as well as
    # against previous runs. parse_page.py re-reads posts as it scrolls, so a raw file of 424
    # records holds ~415 distinct ones; seeding from disk alone let every intra-batch repeat in.
    seen = set(existing_keys)
    kept, skipped_dup = [], 0
    for rec in new_records:
        key = norm_key(rec)
        if key in seen:
            skipped_dup += 1
            continue
        seen.add(key)
        kept.append(rec)

    now = datetime.now(timezone.utc)
    out_path = settings.SCRAPED / f"posts_{now.strftime('%Y-%m-%dT%H-%M-%SZ')}.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for rec in kept:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    settings.NEW_POSTS.unlink()

    print(f"known already:      {len(existing_keys)} record(s) in {len(existing_files)} previous run(s)")
    print(f"kept (new):         {len(kept)}")
    print(f"skipped (dup):      {skipped_dup}")
    print(f"written to:         {out_path.relative_to(settings.ROOT)}")
    if not kept:
        out_path.unlink()
        print("(no new posts — empty file removed)")


if __name__ == "__main__":
    main()
