"""Telegram scraper record contract, ld.py's counterpart.

Unlike LinkedIn, a message has a reliable identity (channel + message_id), an exact
timestamp and a derivable permalink — dedup is by identity, not content.
"""
import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

# Public username: 5-32 chars, letters/digits/underscore, starts with a letter.
CHANNEL_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,31}")


class TelegramPost(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    channel: str  # username without @
    channel_title: str | None = None
    message_id: int
    text: str
    posted_at: datetime  # exact, UTC
    scraped_at: datetime

    @field_validator("channel")
    @classmethod
    def _channel(cls, v):
        if not CHANNEL_RE.fullmatch(v):
            raise ValueError(f"not a telegram username: {v!r}")
        return v

    @field_validator("message_id")
    @classmethod
    def _message_id(cls, v):
        if v <= 0:
            raise ValueError(f"message_id must be positive, got {v}")
        return v

    @field_validator("text")
    @classmethod
    def _text(cls, v):
        # Empty text = media without a caption, useless for job hunting.
        if not v.strip():
            raise ValueError("empty text")
        return v

    @model_validator(mode="after")
    def _time(self):
        # Only forbid the future: channels serve arbitrarily old history.
        if self.posted_at > self.scraped_at:
            raise ValueError(f"posted_at is after scraped_at by {self.posted_at - self.scraped_at}")
        return self

    @property
    def url(self):
        return f"https://t.me/{self.channel}/{self.message_id}"

    def to_json_line(self):
        return self.model_dump_json()
