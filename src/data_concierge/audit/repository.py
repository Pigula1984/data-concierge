"""Where audit events are kept: a JSONL file, or nowhere when the log is off."""

import logging
import threading
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from data_concierge.audit.models import AuditEvent

logger = logging.getLogger(__name__)


class AuditLog(Protocol):
    def record(self, event: AuditEvent) -> None: ...


class JsonlAuditLog:
    """Appends one event per line; a failed write is logged and never fails the turn.

    The lock keeps lines whole within one process: sync tools write from worker threads, and
    HTTP turns run concurrently.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def record(self, event: AuditEvent) -> None:
        # ValueError: text the JSON encoder rejects, e.g. a lone surrogate in a prompt.
        try:
            line = event.model_dump_json() + "\n"
            with self._lock:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as file:
                    file.write(line)
        except (OSError, ValueError) as exc:
            logger.warning(
                "audit event %s not written (correlation id %s): %s",
                event.event,
                event.correlation_id,
                exc,
            )

    def events(self, turn_or_session_id: str) -> tuple[AuditEvent, ...]:
        """The events of a turn (correlation id) or a conversation (session id), in file
        order; none when the file does not exist. A torn line, e.g. from a killed process,
        is skipped, so it hides no other turn."""
        if not self._path.exists():
            return ()
        events: list[AuditEvent] = []
        with self._path.open(encoding="utf-8", errors="replace") as file:
            for number, line in enumerate(file, start=1):
                if not line.strip():
                    continue
                try:
                    event = AuditEvent.model_validate_json(line)
                except ValidationError:
                    logger.warning("audit line %d of %s is unreadable", number, self._path)
                    continue
                if event.concerns(turn_or_session_id):
                    events.append(event)
        return tuple(events)


class NullAuditLog:
    """The log when auditing is off, and the default for components built without one."""

    def record(self, event: AuditEvent) -> None:
        pass
