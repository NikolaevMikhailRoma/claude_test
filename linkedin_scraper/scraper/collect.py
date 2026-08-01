#!/usr/bin/env python3
"""Move the saved feed page out of the browser's download folder into data/pages/.

Usage (working directory = the repository root, the folder holding .venv/):
    .venv/bin/python3 linkedin_scraper/scraper/collect.py          # normal
    .venv/bin/python3 linkedin_scraper/scraper/collect.py --force  # accept a page over 30 min old

Safe to re-run; a no-op with no files.
"""
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import settings

DOWNLOADS = Path.home() / "Downloads"
STALE_AFTER_S = 30 * 60


def main():
    force = "--force" in sys.argv
    candidates = sorted(
        DOWNLOADS.glob("linkedin_feed_*.html"), key=lambda p: p.stat().st_mtime
    )
    if not candidates:
        print(f"No linkedin_feed_*.html files found in {DOWNLOADS}.")
        print("Either the scrape never finished, or Chrome is prompting 'Save As' instead of")
        print("downloading silently — see the Fallback section of README.md.")
        return 1

    print(f"Found {len(candidates)} page(s) in {DOWNLOADS}:")
    for p in candidates:
        age_min = (time.time() - p.stat().st_mtime) / 60
        print(f"  {p.name}  ({p.stat().st_size / 1e6:.1f} MB, {age_min:.1f} min old)")

    # A page older than 30 minutes is a leftover from an earlier session; taking it would report
    # success for a run that produced nothing.
    best = candidates[-1]
    age = time.time() - best.stat().st_mtime
    if age > STALE_AFTER_S and not force:
        print(f"\nREFUSING: newest page is {age / 60:.0f} min old (> 30 min).")
        print("Investigate, then re-run with --force if the old page really is the one you want.")
        return 1

    settings.PAGES.mkdir(parents=True, exist_ok=True)
    dest = settings.PAGES / best.name
    shutil.copy2(best, dest)   # copy2 preserves mtime — parse_page.py derives post_time_iso from it
    print(f"\nCollected: {dest.relative_to(settings.ROOT)}  ({dest.stat().st_size / 1e6:.1f} MB)")

    for p in candidates:
        p.unlink()
    print(f"Removed {len(candidates)} file(s) from {DOWNLOADS}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
