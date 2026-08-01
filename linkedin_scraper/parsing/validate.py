#!/usr/bin/env python3
"""Sanity-check a scrape before committing it to data/scraped/.

Usage (working directory = the repository root, the folder holding .venv/):
    .venv/bin/python3 linkedin_scraper/parsing/validate.py                          # checks data/new_posts.jsonl
    .venv/bin/python3 linkedin_scraper/parsing/validate.py linkedin_scraper/data/scraped/posts_X.jsonl

Per-record field rules live in LinkedInPost and are enforced at creation. This re-checks them
against the file (catching records written by an older parser) and adds the AGGREGATE checks
that cannot be expressed one record at a time. Exit code is non-zero on a hard failure, so it
gates the save step.

Prints ONLY counts, lengths and timestamps — never post text. The whole pipeline exists so feed
content never reaches a model's context, and a validator that dumped records would defeat it.

What it catches:
  * multi-post blobs   - the container walk overshot and one record swallowed several posts.
                         Symptom is a text length near the clamp.
  * repeated keys      - informational: parse_page.py re-reads posts as it scrolls and dedup
                         lives in save_run.py. Only a near-total repeat rate is suspicious — it
                         would mean the scroll never advanced.
  * empty bodies       - a fingerprint body that strips to nothing. Since the schema rejects
                         letter-less text this is now impossible for a fresh parse, so any hit
                         is a record from an older parser and a hard failure.
  * order inversions   - the feed is Recent-sorted, so ages must increase down the page. Catches
                         the "5023h" class of bug without punishing a genuinely deep run.
  * time span          - how far back the run actually reached.
"""
import json
import sys
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import settings
from pydantic_shemas import LinkedInPost, age_seconds, read_jsonl

from save_run import norm_key  # single source of truth for the fingerprint

TEXT_CLAMP = 6000  # must match the [:6000] slice in parse_page.py — change both together


def main():
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else settings.NEW_POSTS
    if not target.exists():
        print(f"FAIL: {target.name} does not exist.")
        return 1

    try:
        records = read_jsonl(target)
    except json.JSONDecodeError as e:
        print(f"FAIL: {target.name} is not valid JSONL — {e}")
        return 1

    # Re-checking the per-record rules against the file catches records written by an older
    # parser, which validation at creation time cannot.
    invalid = []
    for i, r in enumerate(records):
        try:
            LinkedInPost(**r)
        except ValidationError as e:
            invalid.append((i, e.errors()[0]["msg"][:70]))

    if not records:
        print(f"FAIL: {target.name} parsed to zero records.")
        return 1

    n = len(records)
    lengths = sorted(len(r.get("text") or "") for r in records)
    near_clamp = sum(1 for L in lengths if L >= TEXT_CLAMP - 50)
    no_url = sum(1 for r in records if not r.get("author_profile_url"))
    no_time = sum(1 for r in records if not r.get("post_time_iso"))
    missing_field = sum(
        1
        for r in records
        if not all(
            k in r
            for k in (
                "author_profile_url",
                "author_name",
                "text",
                "post_relative_age",
                "post_time_iso",
                "scraped_at",
            )
        )
    )

    keys = [norm_key(r) for r in records]
    dupes = sum(c - 1 for c in Counter(keys).values() if c > 1)
    empty_body = sum(1 for k in keys if not k[1])

    times = sorted(r["post_time_iso"] for r in records if r.get("post_time_iso"))
    authors = len({r.get("author_profile_url") for r in records})

    units = Counter((r.get("post_relative_age") or "?")[-1] for r in records)

    # ORDER INVERSIONS — the real test that ages were read correctly. Records come in DOM order
    # and the feed is Recent-sorted, so age must increase down the page; a fused age ("5023h")
    # plants a 7-month-old post in the middle of a run of minutes. Do NOT go back to failing on
    # week-aged records: LinkedIn really does serve two-week-old posts deep in a run, and the
    # old rule was measuring depth and calling it corruption.
    seq = [s for s in (age_seconds(r.get("post_relative_age")) for r in records) if s is not None]
    inversions = sum(1 for i in range(1, len(seq)) if seq[i] < seq[i - 1])
    inv_pct = (100.0 * inversions / len(seq)) if seq else 0.0

    print(f"file:              {target.name}")
    print(f"records:           {n}")
    print(f"distinct authors:  {authors}")
    print(f"schema-invalid:    {len(invalid)}   (should be 0 — every field rule lives in LinkedInPost)")
    print(f"text length:       min {lengths[0]}  median {lengths[n // 2]}  max {lengths[-1]}")
    print(f"near {TEXT_CLAMP} clamp:   {near_clamp}   <- possible multi-post blobs")
    print(f"repeated keys:     {dupes}   (expected — save_run.py collapses these)")
    print(f"unique posts:      {n - dupes}   <- what save_run.py will actually consider")
    print(f"empty-body keys:   {empty_body}   <- must be 0")
    hist = "  ".join(f"{u}:{c}" for u, c in sorted(units.items(), key=lambda kv: -kv[1]))
    print(f"age units:         {hist}   (m/h/d/w, ?=none)")
    print(f"order inversions:  {inversions}/{len(seq)} ({inv_pct:.1f}%)   <- ages must increase down the feed")
    if times:
        print(f"oldest post:       {times[0]}")
        print(f"newest post:       {times[-1]}")
        # More honest than the single oldest record, which one mis-parsed age drags back months.
        print(f"5th pct oldest:    {times[max(0, len(times) // 20)]}   <- trust this over 'oldest'")

    fails = []
    if invalid:
        detail = "; ".join(f"#{i}: {m}" for i, m in invalid[:3])
        fails.append(f"{len(invalid)} record(s) fail the schema — {detail}")
    # The schema rejects letter-less text, so a fresh parse cannot produce one of these.
    if empty_body:
        fails.append(
            f"{empty_body} record(s) fingerprint to an empty body — the dedup key degrades to "
            f"(author, '') and collapses them; written by a parser older than the letters rule"
        )
    # Repeats are normal (see the docstring). But if almost everything is a repeat, the scroll
    # loop was spinning without advancing and the run is worthless.
    if n >= 20 and dupes > n * 0.9:
        fails.append(f"{dupes}/{n} records are repeats — the scroll almost certainly never advanced")
    # A couple of long posts are normal; a cluster at the clamp is not.
    if near_clamp > max(3, n * 0.05):
        fails.append(f"{near_clamp} record(s) at the text clamp - container walk may be overshooting")
    # A few inversions are normal: "Recent" is not perfectly strict, and the age is coarse (two
    # posts 90 minutes apart both read "1h"). A large share means ages are being mis-read.
    if len(seq) >= 20 and inv_pct > 10:
        fails.append(
            f"{inversions}/{len(seq)} ages ({inv_pct:.0f}%) are out of order — the feed is "
            f"Recent-sorted, so ages should increase down the page; they are likely being read "
            f"from the wrong element"
        )

    print()
    if fails:
        print("FAIL:")
        for f_ in fails:
            print(f"  - {f_}")
        return 1
    print("OK - safe to run save_run.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
