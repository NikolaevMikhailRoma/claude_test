"""Shared access to config.json and the two directories both halves of the project use.

Keep it to that. Anything one half alone cares about belongs in that half — the scraper's
tunables are read and validated by scraper/start_cmd.py, and this module never looks inside.
"""
import json
from pathlib import Path

# config.json sits next to this file, so the project root is this file's own directory.
ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"

# Every path the project reads or writes. Defined once so a rename cannot leave one module
# pointing at a directory the others stopped using.
PAGES = ROOT / "data" / "pages"          # the scraper writes here, the parser reads
SCRAPED = ROOT / "data" / "scraped"      # the dataset, one file per run
NEW_POSTS = ROOT / "data" / "new_posts.jsonl"   # parse -> save handoff, deleted after save


def load():
    """Return the config dict. A missing file or bad JSON stops the run rather than falling back
    to defaults — a silent fallback once hid a mislocated config for an hour."""
    if not CONFIG_PATH.exists():
        raise SystemExit(f"config.json not found at {CONFIG_PATH} — create it before running.")
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"config.json is not valid JSON: {e}")
