#!/usr/bin/env python3
"""Print the `await SCR.start({...})` line that configures a scrape run.

Usage (working directory = the repository root, the folder holding .venv/):
    .venv/bin/python3 linkedin_scraper/scraper/start_cmd.py

stdout is exactly the line to paste after scroll.js. The stop-boundary breakdown goes to stderr.
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import settings

# config.json key -> the CFG field in scroll.js. max_age_days / overlap_hours are absent on
# purpose: they are folded into stopBeforeMs, the only form the page understands.
FIELDS = {
    "max_scroll_cycles": "maxCycles",
    "pg_down_delay": "delayMs",
    "scroll_fraction": "scrollFrac",
    "settle_ms": "settleMs",
    "load_wait_ms": "loadWaitMs",
    "stuck_limit": "stuckLimit",
    "max_minutes": "maxMinutes",
    "max_posts": "maxPosts",
    "stop_streak": "stopStreak",
    "sort_wait_ms": "sortWaitMs",
    "hidden_grace_ms": "hiddenGraceMs",
}
INT_KEYS = (
    "max_scroll_cycles", "settle_ms", "load_wait_ms", "stuck_limit", "max_minutes",
    "max_posts", "max_age_days", "overlap_hours", "stop_streak", "sort_wait_ms",
    "hidden_grace_ms",
)
PAIR_KEYS = ("pg_down_delay", "scroll_fraction")


def params():
    """Validate config.json -> scraper_params. Nothing is defaulted: a partial config must fail
    at startup rather than run at numbers nobody chose."""
    p = settings.load().get("scraper_params")
    if not isinstance(p, dict):
        raise SystemExit("config.json: scraper_params must be an object")

    missing = [k for k in INT_KEYS + PAIR_KEYS if k not in p]
    if missing:
        raise SystemExit(f"config.json: scraper_params is missing: {', '.join(missing)}")

    out = {}
    for k in INT_KEYS:
        try:
            out[k] = int(p[k])
        except (TypeError, ValueError):
            raise SystemExit(f"config.json: scraper_params.{k} must be a number, got {p[k]!r}")
        if out[k] < 1:
            raise SystemExit(f"config.json: scraper_params.{k} must be at least 1")

    for k in PAIR_KEYS:
        v = p[k]
        if not isinstance(v, list) or len(v) != 2:
            raise SystemExit(f"config.json: scraper_params.{k} must be a [min, max] pair")
        try:
            lo, hi = float(v[0]), float(v[1])
        except (TypeError, ValueError):
            raise SystemExit(f"config.json: scraper_params.{k} must hold two numbers, got {v!r}")
        if lo > hi:
            raise SystemExit(f"config.json: scraper_params.{k} has min > max: {v!r}")
        out[k] = [int(x) if x.is_integer() else x for x in (lo, hi)]

    # A step longer than the container skips whatever fell in the gap. This bound is what
    # guarantees no post is scrolled past unseen.
    if out["scroll_fraction"][1] > 1.0:
        raise SystemExit("config.json: scroll_fraction must not exceed 1.0 — posts would be skipped")
    return out


def newest_page_mtime():
    """Capture time of the most recent collected page, or None on a first run. The mtime, not the
    filename: collect.py preserves it and parse_page.py derives every post_time_iso from it."""
    pages = sorted(settings.PAGES.glob("linkedin_feed_*.html"), key=lambda p: p.stat().st_mtime)
    return pages[-1].stat().st_mtime if pages else None


def iso(epoch_s):
    return datetime.fromtimestamp(epoch_s, timezone.utc).isoformat(timespec="seconds")


def err(line=""):
    print(line, file=sys.stderr)


def main():
    p = params()
    now = time.time()
    prev = newest_page_mtime()
    floor = now - p["max_age_days"] * 86400

    # The later of the two wins, so the run stops as soon as either is satisfied. The overlap is
    # not optional: a feed age is coarse ("1d" covers a full day), so cutting exactly at the last
    # run's timestamp would leave a gap. Re-reading a few hours costs nothing — save_run.py
    # dedups by content.
    boundary = max(prev, floor) if prev else floor
    stop_before_ms = int((boundary - p["overlap_hours"] * 3600) * 1000)

    err("stop boundary")
    err(f"  last collected page: {iso(prev) if prev else '(none — first run)'}")
    err(f"  now - {p['max_age_days']}d:{' ' * 9}{iso(floor)}")
    err(f"  later of the two:    {iso(boundary)}")
    err(f"  minus {p['overlap_hours']}h overlap:   {iso(stop_before_ms / 1000)}   <- baked in")
    err("  -> incremental: stops where the last run ended" if prev and prev > floor
        else f"  -> depth-limited: stops at {p['max_age_days']} days back")
    err(f"\n  max_scroll_cycles: {p['max_scroll_cycles']}")

    cfg = {FIELDS[k]: p[k] for k in FIELDS}
    cfg["stopBeforeMs"] = stop_before_ms
    print("await SCR.start(%s)" % json.dumps(cfg, separators=(",", ":")))


if __name__ == "__main__":
    main()
