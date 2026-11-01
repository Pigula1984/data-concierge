"""Identity of the caller on whose behalf a request runs."""

from collections.abc import Mapping
from uuid import uuid4

from pydantic import BaseModel, Field

DEFAULT_ROLE = "user"


class RequestContext(BaseModel, frozen=True):
    """Caller identity passed to procedures through injected parameters such as `@UserId`."""

    user_id: int
    role: str
    correlation_id: str = Field(default_factory=lambda: uuid4().hex)


class RoleDirectory:
    """The role of each user comes from the backend's configuration, never from a prompt."""

    def __init__(self, roles: Mapping[int, str]) -> None:
        self._roles = roles

    def role_of(self, user_id: int) -> str:
        return self._roles.get(user_id, DEFAULT_ROLE)
