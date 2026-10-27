from datetime import UTC, datetime
from pathlib import Path

from data_concierge.audit.repository import JsonlAuditLog
from data_concierge.audit.service import TurnAudit
from data_concierge.core.context import RequestContext


def test_turn_audit_records_events_with_the_fields_of_the_turn(tmp_path: Path) -> None:
    log = JsonlAuditLog(tmp_path / "audit.jsonl")
    context = RequestContext(user_id=7, role="user")
    audit = TurnAudit(log, context, session_id="s1")

    before = datetime.now(UTC)
    audit.record("request", {"prompt": "raport"})
    after = datetime.now(UTC)

    (recorded,) = log.events(context.correlation_id)
    assert before <= recorded.ts <= after
    assert recorded.session_id == "s1"
    assert recorded.user_id == 7
    assert recorded.event == "request"
    assert recorded.data == {"prompt": "raport"}


def test_failure_names_the_error_class_and_message() -> None:
    assert TurnAudit.failure(ValueError("bad date")) == {
        "error": "ValueError",
        "message": "bad date",
    }
