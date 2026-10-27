"""The command line with a stub AskService: JSON output, exit codes and the chat session;
the audit command over the audit log of real turns."""

import io
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import JsonValue
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.models.function import FunctionModel

from data_concierge.api.cli import AuditTrailPrinter, CommandLine
from data_concierge.api.schemas import Response
from data_concierge.api.service import AskService
from data_concierge.audit.models import AuditEvent, AuditEventKind
from data_concierge.audit.repository import JsonlAuditLog
from data_concierge.audit.service import AuditTrail
from data_concierge.core.settings import Settings

pytestmark = pytest.mark.anyio


class StubAskService(AskService):
    """Replays the given responses in order and records the calls."""

    def __init__(self, *responses: Response, drop_on_error: bool = False) -> None:
        self.responses = list(responses)
        self.drop_on_error = drop_on_error
        self.dropped: set[str] = set()
        self.calls: list[tuple[str, int, str | None]] = []

    async def ask(self, prompt: str, user_id: int, session_id: str | None = None) -> Response:
        """With `drop_on_error`, an error response drops its session, as after an engine failure."""
        self.calls.append((prompt, user_id, session_id))
        response = self.responses.pop(0)
        if self.drop_on_error and response.status == "error":
            self.dropped.add(response.session_id)
        return response

    def has_session(self, session_id: str) -> bool:
        return session_id not in self.dropped


def ok(session_id: str = "s1") -> Response:
    return Response(status="ok", session_id=session_id, message="Gotowe.")


def documents(output: str) -> list[dict[str, object]]:
    """The JSON documents written one after another, one per turn."""
    decoder, found, rest = json.JSONDecoder(), [], output.lstrip()
    while rest:
        document, end = decoder.raw_decode(rest)
        found.append(document)
        rest = rest[end:].lstrip()
    return found


async def run(service: AskService, *argv: str, stdin: str = "") -> tuple[int, str]:
    stdout = io.StringIO()
    command_line = CommandLine(service, io.StringIO(stdin), stdout)
    code = await command_line.run(CommandLine.parser().parse_args(argv))
    return code, stdout.getvalue()


async def test_ask_prints_the_response_json_and_exits_with_0() -> None:
    service = StubAskService(ok())

    code, output = await run(service, "ask", "Raport sprzedaży", "--user", "7")

    assert code == 0
    assert json.loads(output) == ok().model_dump(mode="json")
    assert service.calls == [("Raport sprzedaży", 7, None)]


async def test_ask_with_status_error_exits_with_a_nonzero_code() -> None:
    error = Response(status="error", session_id="s1", message="Nie udało się.")

    code, output = await run(StubAskService(error), "ask", "Raport", "--user", "7")

    assert code != 0
    assert json.loads(output)["status"] == "error"


async def test_chat_passes_the_session_id_of_the_first_turn_to_the_next_turns() -> None:
    service = StubAskService(ok("s1"), ok("s1"))

    code, output = await run(
        service, "chat", "--user", "7", stdin="Sprzedaż klienta Mohamed Pal\n\nTen pierwszy\n"
    )

    assert code == 0
    assert service.calls == [
        ("Sprzedaż klienta Mohamed Pal", 7, None),
        ("Ten pierwszy", 7, "s1"),
    ]
    assert [response["session_id"] for response in documents(output)] == ["s1", "s1"]


async def test_chat_starts_a_new_session_after_its_session_was_dropped() -> None:
    dropped = Response(status="error", session_id="s1", message="Nie udało się.")
    service = StubAskService(ok("s1"), dropped, ok("s2"), drop_on_error=True)

    code, _ = await run(service, "chat", "--user", "7", stdin="Pierwsza\nDruga\nTrzecia\n")

    assert code == 0
    assert [session_id for _, _, session_id in service.calls] == [None, "s1", None]


YEAR_2024 = {"date_from": "2024-01-01", "date_to": "2025-01-01"}
MONTHLY_SQL = (
    "SELECT CAST(date_trunc('month', \"OrderDate\") AS DATE) AS month, "
    'sum("SubTotal") AS net_sales FROM r1 GROUP BY 1 ORDER BY month'
)


def present(**fields: object) -> ToolCallPart:
    return ToolCallPart("present_result", {"status": "ok", "message": "Gotowe.", **fields})


@pytest.fixture
def audit_log(tmp_path: Path) -> JsonlAuditLog:
    return JsonlAuditLog(tmp_path / "audit.jsonl")


def audit(log: JsonlAuditLog, turn_or_session_id: str) -> tuple[int, str]:
    stdout = io.StringIO()
    code = AuditTrailPrinter(AuditTrail(log), stdout).print(turn_or_session_id)
    return code, stdout.getvalue()


def turn_ids(log: JsonlAuditLog, session_id: str) -> list[str]:
    return [e.correlation_id for e in log.events(session_id) if e.event == "request"]


async def test_audit_prints_one_turn_step_by_step(
    ask_service: Callable[..., AskService],
    scripted: Callable[..., FunctionModel],
    audit_log: JsonlAuditLog,
) -> None:
    model = scripted(
        ToolCallPart("sales_order_list", YEAR_2024, "call-1"),
        ToolCallPart("query", {"sql": MONTHLY_SQL}, "call-2"),
        present(tables=[{"handle": "r2", "title": "Sprzedaż po miesiącach"}]),
        present(),
    )
    service = ask_service(model, audit_log=audit_log)
    first = await service.ask("Raport sprzedaży po miesiącach za 2024", user_id=7)
    await service.ask("Dziękuję", user_id=7, session_id=first.session_id)
    first_turn, _ = turn_ids(audit_log, first.session_id)

    code, output = audit(audit_log, first_turn)

    assert code == 0
    assert output.count("Turn ") == 1
    assert first_turn in output
    assert "prompt: Raport sprzedaży po miesiącach za 2024" in output
    assert "Dziękuję" not in output
    lines = output.splitlines()
    steps = [line.split()[1] for line in lines if line[:3].strip().isdigit()]
    assert steps == [
        "request",
        "model_response",
        "tool_executed",
        "model_input",
        "model_response",
        "tool_executed",
        "model_input",
        "model_response",
        "response",
    ]
    assert "call sales_order_list [call-1]" in output
    assert "sales_order_list [call-1]" in output
    assert "-> r1," in output
    assert "procedure api.SalesOrder_List" in output
    assert "date_trunc('month'" in output
    assert "call present_result" in output
    assert "status ok" in output
    assert "table r2: Sprzedaż po miesiącach (" in output


async def test_audit_by_session_prints_every_turn_of_the_conversation(
    ask_service: Callable[..., AskService],
    scripted: Callable[..., FunctionModel],
    audit_log: JsonlAuditLog,
) -> None:
    service = ask_service(scripted(present(), present()), audit_log=audit_log)
    first = await service.ask("Pierwsze", user_id=7)
    await service.ask("Drugie", user_id=7, session_id=first.session_id)

    code, output = audit(audit_log, first.session_id)

    assert code == 0
    assert output.count("Turn ") == 2
    assert output.index("prompt: Pierwsze") < output.index("prompt: Drugie")


async def test_audit_shows_why_a_turn_stopped(
    ask_service: Callable[..., AskService],
    scripted: Callable[..., FunctionModel],
    agent_settings: Settings,
    audit_log: JsonlAuditLog,
) -> None:
    steps = agent_settings.max_agent_steps
    model = scripted(*(ToolCallPart("sales_order_list", YEAR_2024) for _ in range(steps + 2)))
    response = await ask_service(model, audit_log=audit_log).ask("Raport", user_id=7)

    _, output = audit(audit_log, response.session_id)

    assert output.count("call sales_order_list") == steps
    assert "turn_failed" in output
    assert "UsageLimitExceeded" in output
    assert "status error" in output


async def test_audit_prints_table_sizes_not_rows(
    ask_service: Callable[..., AskService],
    scripted: Callable[..., FunctionModel],
    audit_log: JsonlAuditLog,
) -> None:
    model = scripted(
        ToolCallPart("sales_order_list", YEAR_2024),
        present(tables=[{"handle": "r1", "title": "Zamówienia 2024"}]),
    )
    response = await ask_service(model, audit_log=audit_log).ask("Zamówienia", user_id=7)
    (table,) = response.tables

    _, output = audit(audit_log, response.session_id)

    assert f"table r1: Zamówienia 2024 ({table.row_count} rows)" in output
    order_ids = [str(row[0]) for row in table.rows]
    assert not any(order_id in output for order_id in order_ids if len(order_id) > 3)


def test_audit_of_an_unknown_id_says_so_and_exits_with_1(
    audit_log: JsonlAuditLog, tmp_path: Path
) -> None:
    code, output = audit(audit_log, "no-such-turn")

    assert code == 1
    assert "No audit events for no-such-turn" in output
    assert str(tmp_path / "audit.jsonl") in output


def test_audit_command_takes_the_id_without_a_user() -> None:
    args = CommandLine.parser().parse_args(["audit", "abc123"])

    assert (args.command, args.id) == ("audit", "abc123")


def test_audit_prints_every_kind_of_detail(audit_log: JsonlAuditLog) -> None:
    ts = datetime(2026, 10, 26, 17, 0, tzinfo=UTC)
    steps: list[tuple[AuditEventKind, dict[str, JsonValue]]] = [
        (
            "model_response",
            {
                "thinking": ["Najpierw zamówienia.\nPotem suma."],
                "text": ["Sprawdzam."],
                "tool_calls": [
                    {"tool": "query", "tool_call_id": "c1", "args": {"sql": "SELECT 1"}}
                ],
                "usage": {
                    "input_tokens": 120,
                    "output_tokens": 30,
                    "cache_read_tokens": 100,
                    "cache_write_tokens": 0,
                },
            },
        ),
        (
            "tool_executed",
            {
                "tool": "sales_order_list",
                "tool_call_id": "c0",
                "duration_ms": 5,
                "error": "ProcedureError",
                "message": "api.Test failed",
                "detail": "Could not convert 'X'",
            },
        ),
        (
            "tool_executed",
            {
                "tool": "query",
                "tool_call_id": "c1",
                "duration_ms": 7,
                "handle": "r2",
                "rows": 3,
                "truncated": True,
                "lineage": {
                    "step": "query",
                    "sources": ["r1"],
                    "submitted_sql": "SELECT 1",
                    "sql": "SELECT 1",
                },
            },
        ),
        (
            "model_input",
            {
                "parts": [
                    {
                        "kind": "retry",
                        "tool": "query",
                        "tool_call_id": "c3",
                        "content": "use a CAST",
                    },
                    {
                        "kind": "tool_return",
                        "tool": "query",
                        "tool_call_id": "c1",
                        "content": {"handle": "r2", "row_count": 3},
                    },
                ]
            },
        ),
        ("comment", {"comment": "Razem {r2.net_sales}."}),
        ("comment", {"failed": "overloaded"}),
        ("turn_failed", {"error": "UsageLimitExceeded", "message": "limit 10"}),
        (
            "response",
            {
                "duration_ms": 42,
                "response": {
                    "status": "needs_clarification",
                    "message": "Którego klienta?",
                    "tables": [],
                    "assumptions": ["Okres: 2024."],
                    "clarification": {"question": "Którego klienta wybierasz?", "candidates": []},
                },
            },
        ),
    ]
    for kind, data in steps:
        audit_log.record(
            AuditEvent(ts=ts, correlation_id="c", session_id="s", user_id=7, event=kind, data=data)
        )

    code, output = audit(audit_log, "c")

    assert code == 0
    for line in [
        "thinking:",
        "  Najpierw zamówienia.",
        "  Potem suma.",
        "text:",
        "  Sprawdzam.",
        'call query [c1] {"sql": "SELECT 1"}',
        "tokens: in 120, out 30, cache read 100",
        "sales_order_list [c0] 5 ms: ProcedureError: api.Test failed",
        "detail: Could not convert 'X'",
        "query [c1] 7 ms -> r2, 3 rows, truncated",
        'sources ["r1"], sql:',
        "retry query [c3]:",
        "  use a CAST",
        "result query [c1]: r2, 3 rows",
        "comment: Razem {r2.net_sales}.",
        "failed, the draft is used: overloaded",
        "UsageLimitExceeded: limit 10",
        "status needs_clarification in 42 ms",
        "message: Którego klienta?",
        "assumption: Okres: 2024.",
        "question: Którego klienta wybierasz?",
    ]:
        assert line in output, line
