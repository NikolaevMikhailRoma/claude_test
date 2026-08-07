"""Canonical post — the common envelope for all sources."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from src.schemas.scoring import Score


class SourceRef(BaseModel):
    model_config = {"extra": "forbid"}

    path: str  # source type lives in the path: "linkedin/in/john-doe", "telegram/python_jobs"
    url: str | None = None
    name: str | None = None


class Status(BaseModel):
    model_config = {"extra": "forbid"}

    state: Literal["parsed", "scored", "irrelevant", "sent"] = "parsed"


class PostMeta(BaseModel):
    """Bookkeeping — rarely looked at in queries."""

    model_config = {"extra": "forbid"}

    source: SourceRef
    user_id: str  # from .env, "test" for now
    parsed_at: datetime
    raw_file: str  # which raw file the post was built from
    platform_data: dict = {}  # source specifics, kept lossless
    schema_version: int = 1


class Post(BaseModel):
    model_config = {"extra": "forbid"}

    id: str  # "{platform}:{hash}", computed by the source parser
    platform: str
    text: str
    url: str | None = None  # permalink; linkedin has none, author link goes here instead
    published_at: datetime | None = None  # UTC; some sources have no date at all
    scoring: list[Score] = []
    status: Status
    feedback: int | None = Field(default=None, ge=0, le=10)  # set via the bot; None = not rated
    metadata: PostMeta
