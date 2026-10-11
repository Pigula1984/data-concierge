"""Identity of the caller on whose behalf a request runs."""

from uuid import uuid4

from pydantic import BaseModel, Field


class RequestContext(BaseModel, frozen=True):
    """Caller identity passed to procedures through injected parameters such as `@UserId`."""

    user_id: int
    role: str
    correlation_id: str = Field(default_factory=lambda: uuid4().hex)
