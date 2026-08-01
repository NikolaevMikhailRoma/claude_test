"""The record contract. Both halves of the project import this and nothing else from each other.

A post has no link: LinkedIn exposes a permalink for ~6% of feed posts and a URN for ~21%, so
there is nothing reliable to store. A post is identified by AUTHOR + TIME.

That is for a human. It is NOT the dedup key — post_time_iso is derived from a coarse relative
age ("1d" covers 24 hours), so the same post scraped on two days gets two different timestamps.
Dedup is content-based; see norm_key() in parsing/save_run.py.

Every validator below is a bug that already cost real time. They are cheap to keep and expensive
to rediscover.
"""
import re
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

# Two digits, never more: LinkedIn rolls 60m -> 1h, so a longer number is a neighbouring number
# fused onto the age by textContent concatenation ("...502" + "3h" -> "5023h").
AGE_RE = re.compile(r"\d{1,2}[mhdw]")
AGE_UNITS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
PROFILE_RE = re.compile(r"https://www\.linkedin\.com/(in|company)/")

# A Recent-sorted feed has served up to 14 days. 30 gives room without admitting a parse error.
MAX_POST_AGE = timedelta(days=30)


def age_seconds(age):
    """"3h" -> 10800. None if it does not parse. The one place the unit table is read."""
    if not age or age[-1] not in AGE_UNITS:
        return None
    try:
        return int(age[:-1]) * AGE_UNITS[age[-1]]
    except ValueError:
        return None


class LinkedInPost(BaseModel):
    # extra="forbid" so a typo'd field name fails loudly instead of being silently dropped;
    # frozen because records are written once and never edited.
    model_config = ConfigDict(extra="forbid", frozen=True)

    author_profile_url: str
    author_name: str
    text: str
    post_relative_age: str  # "17m", "3h", "2d", "1w"
    post_time_iso: datetime
    scraped_at: datetime

    @field_validator("post_relative_age")
    @classmethod
    def _age(cls, v):
        if not AGE_RE.fullmatch(v):
            raise ValueError(f"implausible relative age {v!r} — likely digits fused onto it")
        return v

    @field_validator("author_profile_url")
    @classmethod
    def _url(cls, v):
        if not PROFILE_RE.match(v):
            raise ValueError(f"not a LinkedIn profile or company URL: {v!r}")
        return v

    @field_validator("author_name")
    @classmethod
    def _name(cls, v):
        if not v.strip():
            raise ValueError("empty author_name")
        return v

    @field_validator("text")
    @classmethod
    def _text(cls, v):
        if not v.strip():
            raise ValueError("empty text")
        # No message here may quote the text: validate.py surfaces these, and feed content must
        # never reach a model's context. Report shape, not content.
        #
        # The marker means body cleaning fell through and this is LinkedIn's chrome, not a post.
        if v.startswith("Feed post"):
            raise ValueError(f"uncleaned body: starts with the feed marker ({len(v)} chars)")
        # norm_key() in parsing/save_run.py fingerprints on letters alone, so a letter-less body
        # degrades the key to (author, "") and collapses every such post by that author into
        # one. isalpha(), never [a-z] — the feed is heavily Russian/Ukrainian.
        if not any(ch.isalpha() for ch in v):
            raise ValueError(f"no letters in text ({len(v)} chars) — dedup key would degrade")
        return v

    @model_validator(mode="after")
    def _time(self):
        delta = self.scraped_at - self.post_time_iso
        if delta < timedelta(0):
            raise ValueError(f"post_time_iso is after scraped_at by {-delta}")
        if delta > MAX_POST_AGE:
            raise ValueError(f"post is {delta.days}d old — beyond anything the feed serves")
        return self

    def to_json_line(self):
        """One JSONL line. mode='json' so datetimes serialize as ISO strings."""
        return self.model_dump_json()


def read_jsonl(path):
    """Every raw JSONL read in the project goes through here — dicts, not models.

    Deliberately NOT validating: callers read files written by older parser versions and by
    save_run.py's own output, and a hard failure there would block a run over a historical
    record nobody is looking at. Validation belongs at the point records are CREATED.
    """
    import json

    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
