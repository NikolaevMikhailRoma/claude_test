#!/usr/bin/env python3
"""Step 2: saved page → JSONL. One command, no agent, no browser.

Usage (working directory = the repository root, the folder holding .venv/):
    .venv/bin/python3 linkedin_scraper/parsing/run_parsing.py
    .venv/bin/python3 linkedin_scraper/parsing/run_parsing.py linkedin_scraper/data/pages/linkedin_feed_X.html   # a specific page

Runs parse → validate → save in order, stopping at the first failure. Deterministic — same page
in, same JSONL out — so it needs no agent, unlike step 1 which has to drive Chrome. Exit code is
non-zero if any stage fails, so it can be scripted or scheduled.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import settings

HERE = Path(__file__).parent

# validate.py sits in the middle so a bad batch cannot reach data/scraped/, where it would be
# deduped against forever after.
STAGES = [
    ("parse", "parse_page.py"),
    ("validate", "validate.py"),
    ("save", "save_run.py"),
]


def main():
    page_args = [a for a in sys.argv[1:] if not a.startswith("-")]

    for i, (label, script) in enumerate(STAGES, 1):
        cmd = [sys.executable, str(HERE / script)]
        # Only the parse stage takes a page argument; validate and save find their own inputs.
        # Resolved against OUR cwd before handing it over — the child runs in settings.ROOT, so a
        # relative path would otherwise be looked up in the wrong directory.
        if label == "parse" and page_args:
            cmd.append(str(Path(page_args[0]).resolve()))

        print(f"\n===== {i}/{len(STAGES)}  {label} =====")
        result = subprocess.run(cmd, cwd=settings.ROOT)
        if result.returncode != 0:
            print(f"\nFAILED at stage {i} ({label}), exit {result.returncode}.")
            print("Nothing was saved. Fix the cause and re-run — this step is repeatable.")
            return result.returncode

    print("\n===== done =====")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
