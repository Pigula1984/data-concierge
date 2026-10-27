import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import JsonValue

from data_concierge.audit.models import AuditEvent, AuditEventKind
from data_concierge.audit.repository import JsonlAuditLog

TS = datetime(2026, 10, 26, 17, 0, tzinfo=UTC)


def event(
    correlation_id: str = "c1",
    session_id: str = "s1",
    kind: AuditEventKind = "request",
    **data: JsonValue,
) -> AuditEvent:
    return AuditEvent(
        ts=TS,
        correlation_id=correlation_id,
        session_id=session_id,
        user_id=7,
        event=kind,
        data=dict(data),
    )


def test_events_are_appended_as_json_lines_in_order(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = JsonlAuditLog(path)

    log.record(event(kind="request", prompt="sprzedaż w Łodzi"))
    log.record(event(kind="response", status="ok"))

    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event"] for line in lines] == ["request", "response"]
    first = json.loads(lines[0])
    assert first["correlation_id"] == "c1"
    assert first["session_id"] == "s1"
    assert first["user_id"] == 7
    assert first["ts"].startswith("2026-10-26T17:00:00")
    # Polish text stays readable in the file.
    assert "sprzedaż w Łodzi" in lines[0]


def test_a_missing_directory_is_created(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "nested" / "audit.jsonl"

    JsonlAuditLog(path).record(event())

    assert path.exists()


def test_events_are_read_by_correlation_id_or_session_id(tmp_path: Path) -> None:
    log = JsonlAuditLog(tmp_path / "audit.jsonl")
    log.record(event("c1", "s1", "request"))
    log.record(event("c2", "s2", "request"))
    log.record(event("c3", "s1", "request"))
    log.record(event("c1", "s1", "response"))

    turn = log.events("c1")
    conversation = log.events("s1")

    assert [(e.correlation_id, e.event) for e in turn] == [("c1", "request"), ("c1", "response")]
    assert [e.correlation_id for e in conversation] == ["c1", "c3", "c1"]


def test_unknown_id_or_missing_file_gives_no_events(tmp_path: Path) -> None:
    log = JsonlAuditLog(tmp_path / "audit.jsonl")
    assert log.events("c1") == ()

    log.record(event())
    assert log.events("unknown") == ()


def test_concurrent_writers_keep_lines_whole(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = JsonlAuditLog(path)
    payload = "x" * 5_000

    def write(writer: int) -> None:
        for _ in range(100):
            log.record(event(correlation_id=f"c{writer}", payload=payload))

    threads = [threading.Thread(target=write, args=(writer,)) for writer in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 400
    assert all(json.loads(line)["data"]["payload"] == payload for line in lines)


def test_a_failed_write_is_logged_and_does_not_raise(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A directory where the file should be: opening it for appending fails.
    path = tmp_path / "audit.jsonl"
    path.mkdir()

    with caplog.at_level(logging.WARNING):
        JsonlAuditLog(path).record(event(correlation_id="c9"))

    assert any("c9" in record.getMessage() for record in caplog.records)


def test_a_torn_line_is_skipped_and_hides_no_other_event(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = JsonlAuditLog(path)
    log.record(event(kind="request"))
    with path.open("a", encoding="utf-8") as file:
        file.write('{"ts": "2026-10-26T1\n')
    log.record(event(kind="response"))

    assert [e.event for e in log.events("c1")] == ["request", "response"]
