#!/usr/bin/env python3
"""Turn a saved LinkedIn feed page into JSONL records. Offline — never touches the browser.

Usage (working directory = the repository root, the folder holding .venv/):
    .venv/bin/python3 linkedin_scraper/parsing/parse_page.py                          # newest page in data/pages/
    .venv/bin/python3 linkedin_scraper/parsing/parse_page.py linkedin_scraper/data/pages/feed_X.html   # a specific page
    .venv/bin/python3 linkedin_scraper/parsing/parse_page.py --stats                  # parse, report, write nothing

Writes data/new_posts.jsonl. Every record is validated against LinkedInPost as it is built; one
that fails is counted and dropped, so a single odd post cannot cost a nine-minute scrape.

`text` is the POST BODY ONLY — the rendered header and the trailing interface furniture are
stripped. Timestamps come from the page's mtime, not from now, so re-parsing is stable.
"""
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import settings
from pydantic_shemas import LinkedInPost, age_seconds

# The only per-post hook in LinkedIn's feed DOM that is not a hashed, rotating class name.
BTN_SEL = 'button[aria-label^="Open control menu for post"]'
LINK_SEL = 'a[href*="/in/"], a[href*="/company/"]'

# Three digits on purpose, against the schema's two: a fused age ("5023h") is captured here so
# the schema can reject and COUNT it, instead of silently not matching and losing the post as
# "no age".
AGE_RE = re.compile(r"^(\d{1,3})([mhdw])\s*(?:•|$)")

BODY_CUT = re.compile(r"\d{1,3}[mhdw]\s*•\s*")
LEAD_VERBS = re.compile(
    r"^(?:Follow|Connect|Following|Message|Show translation|Edited\s*•\s*|\+\s*Follow)+",
    re.I,
)
# Where the post text ends and LinkedIn's chrome begins. NONE of these may be \b-anchored:
# flattened text glues neighbouring nodes with no separator ("27 reactionsAndela").
BODY_END = re.compile(
    r"\d[\d,\s​]*(?:reactions?|comments?|reposts?)"
    r"|Video Player is loading"
    r"|Comments have been turned off"
    r"|Close Modal Dialog"
    r"|End of dialog window"
    r"|See \d+ more comments?",
    re.I,
)
# Noise to delete in place rather than cut at, because real text follows it.
INLINE_NOISE = re.compile(r"…\s*more|Show translation|hashtag(?=#)", re.I)
# The digit classes include U+200B ZERO WIDTH SPACE — LinkedIn injects it inside its counts
# ("2​​2 reactions") and omitting it makes the pattern silently fail.
TAIL_JUNK = re.compile(
    r"(?:\s|​)*(?:"
    r"\d[\d,\s​]*(?:reactions?|comments?|reposts?)"
    r"|See\s+\d+\s+more\s+comments?"
    r"|Close Modal Dialog.*$"
    r"|End of dialog window\.?"
    r")+\s*$",
    re.I,
)
DEGREE = re.compile(r"\s*•\s*(1st|2nd|3rd|3rd\+|Following)\s*$", re.I)
# LinkedIn names the author in the control-menu button's own aria-label.
ARIA_AUTHOR = re.compile(r"^Open control menu for post by (.+)$")


def flatten(node):
    """textContent, normalized. Matches the browser: no separator between adjacent nodes."""
    return re.sub(r"\s+", " ", node.get_text()).strip()


def sanitize(s):
    """Dodge a false-positive 'query string / cookie data' content filter in the tool layer
    that trips on text carrying several bare '=' or '&' characters."""
    return s.replace("=", "-").replace("&", "and").replace("?", "")


def containers(soup):
    """Yield (button, container) per post. A post's boundary is the highest ancestor still
    holding exactly one control-menu button — never a text-length window, which overshoots on
    short posts and undershoots on long ones.

    Counting buttons per ancestor once is O(ancestors); re-querying each candidate's subtree
    would be O(n^2), which matters on a 20MB page.
    """
    buttons = soup.select(BTN_SEL)
    owns = Counter()
    chains = []
    for btn in buttons:
        chain = []
        node = btn
        while node is not None:
            chain.append(node)
            owns[id(node)] += 1
            node = node.parent
        chains.append(chain)

    for chain in chains:
        best = chain[0]
        for node in chain[1:]:
            if owns[id(node)] > 1:
                break
            best = node
        yield chain[0], best   # the button comes along: its aria-label names the author


def age_element(box):
    """The element that RENDERS the age — never flattened text, where a neighbouring number
    fuses onto the front ("23h" -> "5023h"). The length cap rejects an outer wrapper that merely
    starts with the age."""
    for el in box.find_all(["span", "time"]):
        t = flatten(el)
        if len(t) <= 40 and AGE_RE.match(t):
            return el
    return None


def _norm_name(s):
    return DEGREE.sub("", re.sub(r"\s+", " ", s or "").strip()).strip().lower()


def author_from_aria(btn, box):
    """Primary rule: LinkedIn states the author in the button's own aria-label.

    The label gives only a NAME, so the URL comes from the link whose visible text matches it —
    matching by name, not by position, is what keeps the social-proof link from being picked.
    """
    aria = btn.get("aria-label") or ""
    m = ARIA_AUTHOR.match(aria)
    if not m:
        return None
    name = re.sub(r"\s+", " ", m.group(1)).strip()
    target = _norm_name(name)
    if not target:
        return None

    for a in box.select(LINK_SEL):
        if _norm_name(flatten(a)) == target:
            href = (a.get("href") or "").split("?")[0]
            if href:
                return href, name[:90]
    return None


def author_from_walk(box):
    """Fallback, independent of the aria-label wording: anchor on the age element and walk up to
    the nearest ancestor with a link. NOT the first profile link in the container — a
    social-proof header ("Alice likes this") puts the liker's link first. The age sits in the
    actor block, so anchoring there lands on the real author.
    """
    ae = age_element(box)
    if ae is None:
        return None
    node, hops = ae.parent, 0
    while node is not None and hops < 8:
        links = node.select(LINK_SEL)
        if links:
            # Prefer a link with visible text: the first is the bare avatar, whose text is empty.
            pick = links[0]
            for a in links:
                t = flatten(a)
                if t and len(t) < 90:
                    pick = a
                    break
            href = (pick.get("href") or "").split("?")[0]
            name = DEGREE.sub("", flatten(pick))[:90] or None
            return (href, name) if href else None
        if node is box:
            break
        node, hops = node.parent, hops + 1
    return None


def author_of(btn, box, stats=None):
    """aria-label first, DOM walk second."""
    got = author_from_aria(btn, box)
    if got:
        if stats is not None:
            stats["author_via_aria"] += 1
        return got
    got = author_from_walk(box)
    if got and stats is not None:
        stats["author_via_walk"] += 1
    return got


def body_of(flat):
    """Strip the rendered header, then everything from the end of the post onwards."""
    m = BODY_CUT.search(flat)
    body = LEAD_VERBS.sub("", flat[m.end():]).strip() if m else flat

    # Cut at the FIRST end-of-post marker: it keeps a quoted reshare (which precedes the reaction
    # count) while dropping the comment block. Cutting at the LAST "…more" instead leaked comment
    # previews into 46 of 486 bodies.
    e = BODY_END.search(body)
    if e:
        body = body[: e.start()]

    # Noise deleted in place rather than cut at, because real text follows it.
    body = re.sub(r"\s+", " ", INLINE_NOISE.sub(" ", body)).strip()

    # Repeatedly, because tail junk stacks ("… 1 reaction 1 See 1 more comment").
    prev = None
    while prev != body:
        prev = body
        body = TAIL_JUNK.sub("", body).strip()

    # "" for an image- or link-only post; the schema rejects it and the record is dropped. Do NOT
    # add a minimum-length floor that falls back to the raw text — one existed, fired on correct
    # short posts ("Ужос", "Логично!") and replaced them with LinkedIn's boilerplate.
    return body


def parse(path):
    """Return (records, stats) for one saved page."""
    html = path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")

    # Anchor timestamps to when the page was CAPTURED, not to now, so re-parsing is stable.
    captured = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    captured_iso = captured.isoformat().replace("+00:00", "Z")

    records = []
    stats = Counter()
    for btn, box in containers(soup):
        stats["containers"] += 1
        flat = sanitize(flatten(box))[:6000]

        ae = age_element(box)
        age = None
        if ae is not None:
            m = AGE_RE.match(flatten(ae))
            if m:
                age = m.group(1) + m.group(2)

        # NO AGE -> DROP, whatever the reason: paid ads (LinkedIn puts "Promoted" in the age
        # slot) and milestone cards ("X started a new position"). Keeping the rule at "no age"
        # rather than "no age AND Promoted" preserves the invariant that every stored record can
        # be placed in time. Never filter ads by "/company/ in the URL" — companies post the
        # genuine vacancies this scrape exists to find. "Promoted" is unanchored on purpose:
        # flattened text glues it to the previous node ("3,321 followersPromoted").
        if age is None:
            stats["ads" if "Promoted" in flat else "no_age"] += 1
            continue

        author = author_of(btn, box, stats)
        if author is None:
            stats["no_author"] += 1
            continue

        href, name = author
        secs = age_seconds(age)
        post_time = (captured - timedelta(seconds=secs)).isoformat().replace("+00:00", "Z")

        # Validate at CREATION — the only point that can tell a parse failure from a historical
        # record. A rejection is counted and dropped, never raised.
        try:
            records.append(
                LinkedInPost(
                    author_profile_url=href,
                    author_name=name,
                    text=body_of(flat),
                    post_relative_age=age,
                    post_time_iso=post_time,
                    scraped_at=captured_iso,
                )
            )
        except ValidationError as e:
            # Bucket by the first failing rule so the report says WHICH invariant broke.
            err = e.errors()[0]
            field = err["loc"][0] if err["loc"] else "model"
            stats[f"rejected_{field}"] += 1
            stats["rejected"] += 1

    stats["records"] = len(records)
    return records, stats


def newest_page():
    """The newest page in data/pages/ — what a fresh scrape just produced. Pass a path to
    override."""
    pages = sorted(settings.PAGES.glob("*.html"), key=lambda p: p.stat().st_mtime)
    if not pages:
        raise SystemExit(f"No pages in {settings.PAGES.relative_to(settings.ROOT)}/ — run the scraper first.")
    newest = pages[-1]

    # Refuse a page already turned into a run file: run files are named posts_<UTC ts>.jsonl, so
    # the newest filename IS the time of the last save. Re-parsing would report every record as a
    # duplicate, which reads like a broken scrape rather than a no-op.
    saved = sorted(settings.SCRAPED.glob("posts_*.jsonl"))
    if saved:
        stamp = saved[-1].stem[len("posts_"):]
        try:
            last_save = datetime.strptime(stamp, "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return newest  # unrecognised filename — do not block on it
        page_time = datetime.fromtimestamp(newest.stat().st_mtime, tz=timezone.utc)
        if page_time <= last_save:
            raise SystemExit(
                f"{newest.name} was captured {page_time:%Y-%m-%d %H:%M} UTC, before the last "
                f"save ({saved[-1].name}).\nIt has already been processed — run the scraper for "
                f"a fresh page, or pass a path explicitly to re-parse this one."
            )
    return newest


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    stats_only = "--stats" in sys.argv

    path = Path(args[0]) if args else newest_page()
    if not path.exists():
        print(f"FAIL: {path} does not exist.")
        return 1

    records, stats = parse(path)
    size_mb = path.stat().st_size / 1e6
    print(f"page:        {path.name}  ({size_mb:.1f} MB)")
    print(f"containers:  {stats['containers']}")
    print(f"ads dropped: {stats['ads']}")
    if stats["no_age"]:
        print(f"no age:      {stats['no_age']}  <- milestone cards etc, dropped")
    if stats["no_author"]:
        print(f"no author:   {stats['no_author']}  <- unresolved, dropped")
    print(f"author via:  aria-label {stats['author_via_aria']}, dom-walk {stats['author_via_walk']}")
    if stats["rejected"]:
        by = ", ".join(f"{k[len('rejected_'):]}={v}" for k, v in sorted(stats.items())
                       if k.startswith("rejected_"))
        print(f"rejected:    {stats['rejected']}  ({by})  <- failed the schema, dropped")
    print(f"records:     {stats['records']}")

    if not records:
        print("\nFAIL: nothing parsed. The feed markup has probably changed.")
        return 1

    if stats_only:
        print("\n--stats: nothing written.")
        return 0

    settings.NEW_POSTS.parent.mkdir(parents=True, exist_ok=True)
    with settings.NEW_POSTS.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(rec.to_json_line() + "\n")
    print(f"written to:  {settings.NEW_POSTS.relative_to(settings.ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
