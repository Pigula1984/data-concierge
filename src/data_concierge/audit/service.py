"""The audit module's services: recording the turns and reading them back."""

import logging
from collections.abc import Mapping
from datetime import UTC, datetime

from pydantic import JsonValue

from data_concierge.audit.models import AuditEvent, AuditEventKind
from data_concierge.audit.repository import AuditLog, JsonlAuditLog, NullAuditLog
from data_concierge.core.context import RequestContext

logger = logging.getLogger(__name__)


class AuditService:
    """Gives each turn its TurnAudit over the process's audit log (architecture 8.1)."""

    def __init__(self, log: AuditLog) -> None:
        self._log = log

    def turn(self, context: RequestContext, session_id: str) -> TurnAudit:
        return TurnAudit(self._log, context, session_id)

    @classmethod
    def disabled(cls) -> AuditService:
        """Audits that keep nothing, when the log is off."""
        return cls(NullAuditLog())


class AuditTrail:
    """The recorded events read back, for the `audit` command."""

    def __init__(self, log: JsonlAuditLog) -> None:
        self._log = log

    @property
    def location(self) -> str:
        return str(self._log.path)

    def events(self, turn_or_session_id: str) -> tuple[AuditEvent, ...]:
        return self._log.events(turn_or_session_id)


class TurnAudit:
    """Created by AskService for each turn and handed to the agent and its tools, so every
    event of the turn carries the same correlation id, session and user. Recording never
    raises: the audit must not fail the turn it describes (architecture, section 8.1)."""

    def __init__(self, log: AuditLog, context: RequestContext, session_id: str) -> None:
        self._log = log
        self._context = context
        self._session_id = session_id

    def record(self, event: AuditEventKind, data: Mapping[str, JsonValue]) -> None:
        """`data` must already be JSON values, e.g. from `model_dump(mode="json")`."""
        try:
            self._log.record(
                AuditEvent(
                    ts=datetime.now(UTC),
                    correlation_id=self._context.correlation_id,
                    session_id=self._session_id,
                    user_id=self._context.user_id,
                    event=event,
                    data=dict(data),
                )
            )
        except (OSError, ValueError) as exc:
            logger.warning(
                "audit event %s not recorded (correlation id %s): %s",
                event,
                self._context.correlation_id,
                exc,
            )

    @staticmethod
    def failure(exc: Exception) -> dict[str, str]:
        """The data of a failure: the error class and its message."""
        return {"error": type(exc).__name__, "message": str(exc)}
