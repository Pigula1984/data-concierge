"""One event of a turn, as the audit log stores it."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, JsonValue

type AuditEventKind = Literal[
    "request",
    "model_response",
    "tool_executed",
    "model_input",
    "comment",
    "turn_failed",
    "response",
]


class AuditEvent(BaseModel, frozen=True):
    """A step of a turn; the events of one turn share the correlation id (architecture 8.1)."""

    ts: datetime
    correlation_id: str
    session_id: str
    user_id: int
    event: AuditEventKind
    data: dict[str, JsonValue]

    def concerns(self, turn_or_session_id: str) -> bool:
        return turn_or_session_id in (self.correlation_id, self.session_id)
