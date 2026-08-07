"""ML scores. A post carries a list of them: several models, several tasks."""

from pydantic import BaseModel


class Score(BaseModel):
    model_config = {"extra": "forbid"}

    model_name: str  # bare `model` collides with pydantic reserved names
    task: str  # binary relevance, rerank against resume, ...
    value: float
