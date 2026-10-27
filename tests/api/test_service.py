"""AskService over real sessions: scripted model turns, real tools and DuckDB, fake SQL Server."""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import JsonValue
from pydantic_ai import capture_run_messages
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from data_concierge.api.service import AskService
from data_concierge.audit.models import AuditEvent
from data_concierge.audit.repository import JsonlAuditLog
from data_concierge.core.errors import ResultStoreError
from data_concierge.core.settings import Settings
from data_concierge.results.repository import DuckDBResultStore

pytestmark = pytest.mark.anyio

YEAR_2024 = {"date_from": "2024-01-01", "date_to": "2025-01-01"}
MONTHLY_SQL = (
    "SELECT CAST(date_trunc('month', \"OrderDate\") AS DATE) AS month, "
    'sum("SubTotal") AS net_sales FROM r1 GROUP BY 1 ORDER BY month'
)


def present(status: str = "ok", **fields: object) -> ToolCallPart:
    return ToolCallPart("present_result", {"status": status, "message": "Gotowe.", **fields})


def comment(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(text)])


def requests(model: FunctionModel) -> list[list[ModelMessage]]:
    """Messages of every request the scripted model received (ScriptedModel.requests)."""
    seen: list[list[ModelMessage]] = cast(Any, model.function).requests
    return seen


def user_prompts(messages: list[ModelMessage]) -> list[str]:
    return [
        str(part.content)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]


async def test_second_ask_continues_the_conversation_with_its_history_and_handles(
    ask_service: Callable[..., AskService], scripted: Callable[..., FunctionModel]
) -> None:
    model = scripted(
        ToolCallPart("sales_order_list", YEAR_2024),
        present(tables=[{"handle": "r1", "title": "Zamówienia 2024"}]),
        ToolCallPart("query", {"sql": MONTHLY_SQL}),
        present(tables=[{"handle": "r2", "title": "Sprzedaż po miesiącach"}]),
    )
    service = ask_service(model)

    first = await service.ask("Zamówienia z 2024", user_id=7)
    second = await service.ask("A teraz po miesiącach", user_id=7, session_id=first.session_id)

    assert first.status == "ok"
    assert second.status == "ok"
    assert second.session_id == first.session_id
    assert [table.handle for table in second.tables] == ["r2"]
    assert [step.handle for step in second.provenance] == ["r1", "r2"]
    # The first request of the second turn carries the whole first turn.
    second_turn_start = requests(model)[2]
    assert [prompt.split("\n")[0] for prompt in user_prompts(second_turn_start)] == [
        "Zamówienia z 2024",
        "A teraz po miesiącach",
    ]


async def test_response_has_the_value_and_the_next_turn_sees_the_anchor(
    ask_service: Callable[..., AskService], scripted: Callable[..., FunctionModel]
) -> None:
    total = 'SELECT CAST(sum("SubTotal") AS DECIMAL(38,2)) AS net_sales FROM r1'
    model = scripted(
        ToolCallPart("sales_order_list", YEAR_2024),
        ToolCallPart("query", {"sql": total}),
        present(message="Sprzedaż netto w 2024: {r2.net_sales}."),
        present("no_data"),
    )
    service = ask_service(model)

    first = await service.ask("Sprzedaż w 2024", user_id=7)
    await service.ask("A w 2019?", user_id=7, session_id=first.session_id)

    assert first.message == "Sprzedaż netto w 2024: 159,75."
    second_turn_start = str(requests(model)[3])
    assert "{r2.net_sales}" in second_turn_start
    assert "159,75" not in second_turn_start


async def test_ask_without_session_id_starts_a_new_conversation(
    ask_service: Callable[..., AskService], scripted: Callable[..., FunctionModel]
) -> None:
    service = ask_service(scripted(present(), present()))

    first = await service.ask("Pierwsza rozmowa", user_id=7)
    second = await service.ask("Druga rozmowa", user_id=7)

    assert first.session_id != second.session_id


async def test_clarification_candidates_come_from_the_handle_and_the_choice_continues(
    ask_service: Callable[..., AskService],
    scripted: Callable[..., FunctionModel],
    procedure_calls: list[tuple[str, dict[str, object]]],
) -> None:
    candidates_sql = (
        'SELECT "CustomerID", "AccountNumber", "StateProvince" FROM r1 ORDER BY "CustomerID"'
    )
    clarification = {
        "question": "Znaleziono {r2#rows} klientów o tej nazwie. Którego wybierasz?",
        "candidates_from": {"handle": "r2", "id_column": "CustomerID"},
    }
    model = scripted(
        ToolCallPart("customer_list", {"city": "Berlin"}),
        ToolCallPart("query", {"sql": candidates_sql}),
        present("needs_clarification", clarification=clarification),
        ToolCallPart("sales_order_list", {**YEAR_2024, "customer_id": 12698}),
        present(tables=[{"handle": "r3", "title": "Zamówienia klienta 12698"}]),
    )
    service = ask_service(model)

    first = await service.ask("Sprzedaż klienta Mohamed Pal z Berlina w 2024", user_id=7)
    second = await service.ask("Ten z kontem AW00012698", user_id=7, session_id=first.session_id)

    assert first.status == "needs_clarification"
    assert first.clarification is not None
    assert first.clarification.question == "Znaleziono 2 klientów o tej nazwie. Którego wybierasz?"
    assert [(c.id, c.description) for c in first.clarification.candidates] == [
        (12698, "AW00012698, Brandenburg"),
        (20550, "AW00020550, Hessen"),
    ]
    # The model never saw the account numbers it asked the user to choose by.
    assert "AW00012698" not in str(requests(model)[:3])
    assert second.status == "ok"
    assert [name for name, _ in procedure_calls] == ["api.Customer_List", "api.SalesOrder_List"]
    assert procedure_calls[1][1]["CustomerID"] == 12698
    assert [params["UserId"] for _, params in procedure_calls] == [7, 7]


async def test_failure_in_the_agent_layer_gives_an_error_with_the_correlation_id(
    ask_service: Callable[..., AskService],
    scripted: Callable[..., FunctionModel],
    fail_procedures: Callable[[Exception], None],
    caplog: pytest.LogCaptureFixture,
) -> None:
    model = scripted(ToolCallPart("sales_order_list", YEAR_2024), present())
    service = ask_service(model)
    fail_procedures(RuntimeError("driver crashed"))

    with caplog.at_level(logging.ERROR, logger="data_concierge.api.service"):
        failed = await service.ask("Zamówienia z 2024", user_id=7)

    assert failed.status == "error"
    assert failed.tables == ()
    [record] = caplog.records
    correlation_id = str(record.args[0]) if isinstance(record.args, tuple) else ""
    assert correlation_id
    assert correlation_id in failed.message
    assert record.exc_info is not None
    assert "driver crashed" in str(record.exc_info[1])

    # The session continues from the history before the failed turn.
    retried = await service.ask("Spróbuj jeszcze raz", user_id=7, session_id=failed.session_id)

    assert retried.status == "ok"
    assert user_prompts(requests(model)[-1])[0].startswith("Spróbuj jeszcze raz")


async def test_unknown_session_is_not_continued_and_the_agent_is_not_called(
    ask_service: Callable[..., AskService], scripted: Callable[..., FunctionModel]
) -> None:
    model = scripted(present())

    response = await ask_service(model).ask("Dalej", user_id=7, session_id="no-such-session")

    assert response.status == "error"
    assert response.session_id == "no-such-session"
    assert "wygasła" in response.message
    assert requests(model) == []


async def test_prompt_for_the_agent_carries_the_date_from_the_injected_clock(
    ask_service: Callable[..., AskService], scripted: Callable[..., FunctionModel]
) -> None:
    model = scripted(present())

    await ask_service(model).ask("Sprzedaż w ostatnim kwartale", user_id=7)

    assert user_prompts(requests(model)[0]) == [
        "Sprzedaż w ostatnim kwartale\n\nCurrent date: 2025-04-15"
    ]


async def test_broken_session_store_ends_the_turn_and_the_session(
    ask_service: Callable[..., AskService],
    scripted: Callable[..., FunctionModel],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ask_service(
        scripted(
            ToolCallPart("sales_order_list", YEAR_2024), ToolCallPart("query", {"sql": MONTHLY_SQL})
        )
    )

    def broken(self: DuckDBResultStore, sql: str) -> None:
        raise ResultStoreError("the session's DuckDB failed internally: invalidated")

    monkeypatch.setattr(DuckDBResultStore, "describe_query", broken)

    failed = await service.ask("Sprzedaż po miesiącach w 2024", user_id=7)
    continued = await service.ask("Dalej", user_id=7, session_id=failed.session_id)

    assert failed.status == "error"
    assert "Identyfikator zgłoszenia" in failed.message
    assert not service.has_session(failed.session_id)
    assert "wygasła" in continued.message


async def test_model_provider_error_gives_an_error_response(
    ask_service: Callable[..., AskService],
) -> None:
    def overloaded(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(529, "claude-haiku-5-5", {"type": "overloaded_error"})

    response = await ask_service(FunctionModel(overloaded)).ask("Raport", user_id=7)

    assert response.status == "error"
    assert "Identyfikator zgłoszenia" in response.message


TOTAL_SQL = 'SELECT CAST(sum("SubTotal") AS DECIMAL(38,2)) AS net_sales FROM r1'


@pytest.mark.parametrize("mode", ["minimal"])
async def test_comment_of_mode_minimal_is_filled_in_the_response_and_a_template_in_history(
    ask_service: Callable[..., AskService], scripted: Callable[..., FunctionModel]
) -> None:
    model = scripted(
        ToolCallPart("sales_order_list", YEAR_2024),
        ToolCallPart("query", {"sql": MONTHLY_SQL}),
        ToolCallPart("query", {"sql": TOTAL_SQL}),
        present(tables=[{"handle": "r2", "title": "Sprzedaż po miesiącach"}]),
        present("no_data"),
    )
    comments = scripted(
        comment("Najwięcej w {r2[1].month}: {r2[1].net_sales}, razem {r3.net_sales}.")
    )
    service = ask_service(model, comments)

    first = await service.ask("Sprzedaż po miesiącach za 2024", user_id=7)
    await service.ask("A w 2019?", user_id=7, session_id=first.session_id)

    assert first.status == "ok"
    assert first.message == "Najwięcej w 2024-01-01: 149,7500, razem 159,75."
    # r3 is anchored only by the comment, so it joins the provenance.
    assert [step.handle for step in first.provenance] == ["r1", "r2", "r3"]
    # The comment phase saw the draft; the next turn sees the comment as a template.
    assert "Gotowe." in str(requests(comments)[0])
    second_turn_start = str(requests(model)[4])
    assert (
        "Najwięcej w {r2[1].month}: {r2[1].net_sales}, razem {r3.net_sales}." in second_turn_start
    )
    assert "Gotowe." not in second_turn_start
    assert "149,7500" not in second_turn_start


@pytest.mark.parametrize("mode", ["minimal"])
async def test_failed_comment_phase_gives_the_draft_and_a_log_entry(
    ask_service: Callable[..., AskService],
    scripted: Callable[..., FunctionModel],
    caplog: pytest.LogCaptureFixture,
) -> None:
    def overloaded(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(529, "claude-haiku-5-5", {"type": "overloaded_error"})

    model = scripted(
        ToolCallPart("sales_order_list", YEAR_2024),
        ToolCallPart("query", {"sql": TOTAL_SQL}),
        present(message="Sprzedaż netto w 2024: {r2.net_sales}."),
        present("no_data"),
    )
    service = ask_service(model, FunctionModel(overloaded))

    with caplog.at_level(logging.WARNING, logger="data_concierge.api.service"):
        response = await service.ask("Sprzedaż w 2024", user_id=7)
    await service.ask("A w 2019?", user_id=7, session_id=response.session_id)

    assert response.status == "ok"
    assert response.message == "Sprzedaż netto w 2024: 159,75."
    [record] = caplog.records
    assert record.getMessage().startswith("the comment phase failed (correlation id ")
    # The history keeps the draft the user read.
    assert "Sprzedaż netto w 2024: {r2.net_sales}." in str(requests(model)[3])


@pytest.mark.parametrize("mode", ["minimal"])
async def test_comment_with_bad_anchors_after_all_retries_gives_the_draft(
    ask_service: Callable[..., AskService], scripted: Callable[..., FunctionModel]
) -> None:
    model = scripted(
        ToolCallPart("sales_order_list", YEAR_2024),
        ToolCallPart("query", {"sql": TOTAL_SQL}),
        present(message="Sprzedaż netto w 2024: {r2.net_sales}."),
    )
    comments = scripted(*(comment("Suma: {r9.net_sales}.") for _ in range(3)))

    response = await ask_service(model, comments).ask("Sprzedaż w 2024", user_id=7)

    assert response.message == "Sprzedaż netto w 2024: 159,75."
    assert len(requests(comments)) == 3


@pytest.mark.parametrize(
    ("mode", "status"),
    [
        pytest.param("minimal", "needs_clarification", id="minimal-needs_clarification"),
        pytest.param("minimal", "no_data", id="minimal-no_data"),
        pytest.param("minimal", "unsupported", id="minimal-unsupported"),
        pytest.param("none", "ok", id="none-ok"),
    ],
)
async def test_comment_phase_runs_only_for_status_ok_in_mode_minimal(
    ask_service: Callable[..., AskService], scripted: Callable[..., FunctionModel], status: str
) -> None:
    fields = (
        {"clarification": {"question": "Który okres?"}} if status == "needs_clarification" else {}
    )
    comments = scripted()

    response = await ask_service(scripted(present(status, **fields)), comments).ask(
        "Raport", user_id=7
    )

    assert response.status == status
    assert requests(comments) == []


@pytest.fixture
def audit_log(tmp_path: Path) -> JsonlAuditLog:
    return JsonlAuditLog(tmp_path / "audit.jsonl")


def kinds(events: tuple[AuditEvent, ...]) -> list[str]:
    return [event.event for event in events]


async def test_a_turn_is_framed_by_request_and_response_events(
    ask_service: Callable[..., AskService],
    scripted: Callable[..., FunctionModel],
    audit_log: JsonlAuditLog,
) -> None:
    model = scripted(
        ToolCallPart("sales_order_list", YEAR_2024),
        ToolCallPart("query", {"sql": MONTHLY_SQL}),
        present(tables=[{"handle": "r2", "title": "Sprzedaż po miesiącach"}]),
    )

    response = await ask_service(model, audit_log=audit_log).ask("Raport za 2024", user_id=7)

    events = audit_log.events(response.session_id)
    assert kinds(events)[0] == "request"
    assert kinds(events)[-1] == "response"
    assert len({event.correlation_id for event in events}) == 1
    assert {(event.session_id, event.user_id) for event in events} == {(response.session_id, 7)}
    request = events[0].data
    assert request["prompt"] == "Raport za 2024"
    assert request["current_date"] == "2025-04-15"
    assert request["continued_session"] is False
    assert request["model_data_access"] == "none"
    assert request["model"] == model.model_name
    final = events[-1].data
    assert final["response"] == response.model_dump(mode="json")
    assert isinstance(final["duration_ms"], int)


async def test_the_next_turn_of_a_session_has_its_own_correlation_id(
    ask_service: Callable[..., AskService],
    scripted: Callable[..., FunctionModel],
    audit_log: JsonlAuditLog,
) -> None:
    service = ask_service(scripted(present(), present()), audit_log=audit_log)

    first = await service.ask("Pierwsze", user_id=7)
    await service.ask("Drugie", user_id=7, session_id=first.session_id)

    requests_ = [e for e in audit_log.events(first.session_id) if e.event == "request"]
    assert [e.data["continued_session"] for e in requests_] == [False, True]
    assert requests_[0].correlation_id != requests_[1].correlation_id


async def test_an_expired_session_is_audited_without_agent_events(
    ask_service: Callable[..., AskService],
    scripted: Callable[..., FunctionModel],
    audit_log: JsonlAuditLog,
) -> None:
    service = ask_service(scripted(present()), audit_log=audit_log)

    await service.ask("Dalej", user_id=7, session_id="no-such-session")

    events = audit_log.events("no-such-session")
    assert kinds(events) == ["request", "response"]
    response = events[1].data["response"]
    assert isinstance(response, dict)
    assert response["status"] == "error"


async def test_a_broken_result_store_is_audited_as_a_failed_turn(
    ask_service: Callable[..., AskService],
    scripted: Callable[..., FunctionModel],
    audit_log: JsonlAuditLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ask_service(
        scripted(
            ToolCallPart("sales_order_list", YEAR_2024), ToolCallPart("query", {"sql": MONTHLY_SQL})
        ),
        audit_log=audit_log,
    )

    def broken(self: DuckDBResultStore, sql: str) -> None:
        raise ResultStoreError("the session's DuckDB failed internally: invalidated")

    monkeypatch.setattr(DuckDBResultStore, "describe_query", broken)

    failed = await service.ask("Sprzedaż po miesiącach w 2024", user_id=7)

    events = audit_log.events(failed.session_id)
    assert kinds(events)[-3:] == ["tool_executed", "turn_failed", "response"]
    assert events[-3].data["error"] == "ResultStoreError"
    assert events[-2].data == {
        "error": "ResultStoreError",
        "message": "the session's DuckDB failed internally: invalidated",
    }


@pytest.mark.parametrize("mode", ["minimal"])
async def test_the_comment_phase_is_audited(
    ask_service: Callable[..., AskService],
    scripted: Callable[..., FunctionModel],
    audit_log: JsonlAuditLog,
) -> None:
    model = scripted(
        ToolCallPart("sales_order_list", YEAR_2024),
        ToolCallPart("query", {"sql": TOTAL_SQL}),
        present(message="Sprzedaż netto w 2024: {r2.net_sales}."),
    )
    comments = scripted(comment("Razem {r2.net_sales}."))

    response = await ask_service(model, comments, audit_log).ask("Sprzedaż w 2024", user_id=7)

    events = audit_log.events(response.session_id)
    [phase] = [event for event in events if event.event == "comment"]
    assert phase.data == {"comment": "Razem {r2.net_sales}."}
    assert events[-1].data["response"] == response.model_dump(mode="json")


@pytest.mark.parametrize("mode", ["minimal"])
async def test_a_failed_comment_phase_is_audited_with_its_detail(
    ask_service: Callable[..., AskService],
    scripted: Callable[..., FunctionModel],
    audit_log: JsonlAuditLog,
) -> None:
    def overloaded(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(529, "claude-haiku-5-5", {"type": "overloaded_error"})

    model = scripted(
        ToolCallPart("sales_order_list", YEAR_2024),
        ToolCallPart("query", {"sql": TOTAL_SQL}),
        present(message="Sprzedaż netto w 2024: {r2.net_sales}."),
    )
    service = ask_service(model, FunctionModel(overloaded), audit_log)

    response = await service.ask("Sprzedaż w 2024", user_id=7)

    [phase] = [e for e in audit_log.events(response.session_id) if e.event == "comment"]
    assert set(phase.data) == {"failed"}
    assert phase.data["failed"]


def input_parts(event: AuditEvent) -> list[dict[str, JsonValue]]:
    """The tool results and retry prompts of a `model_input` event."""
    parts = event.data["parts"]
    assert isinstance(parts, list)
    return [part for part in parts if isinstance(part, dict)]


def calls(event: AuditEvent) -> list[tuple[object, object]]:
    tool_calls = event.data["tool_calls"]
    assert isinstance(tool_calls, list)
    return [(c["tool"], c["tool_call_id"]) for c in tool_calls if isinstance(c, dict)]


async def test_model_steps_and_executions_are_audited_in_the_order_of_the_flow(
    ask_service: Callable[..., AskService],
    scripted: Callable[..., FunctionModel],
    audit_log: JsonlAuditLog,
) -> None:
    model = scripted(
        ToolCallPart("sales_order_list", YEAR_2024, "call-1"),
        ToolCallPart("query", {"sql": MONTHLY_SQL}, "call-2"),
        present(tables=[{"handle": "r2", "title": "Sprzedaż po miesiącach"}]),
    )

    response = await ask_service(model, audit_log=audit_log).ask("Raport za 2024", user_id=7)

    events = audit_log.events(response.session_id)
    assert kinds(events) == [
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
    assert calls(events[1]) == [("sales_order_list", "call-1")]
    assert (events[2].data["tool"], events[2].data["tool_call_id"]) == (
        "sales_order_list",
        "call-1",
    )
    [returned] = input_parts(events[3])
    assert isinstance(returned, dict)
    assert (returned["kind"], returned["tool_call_id"]) == ("tool_return", "call-1")
    content = returned["content"]
    assert isinstance(content, dict)
    assert content["handle"] == "r1"
    assert [tool for tool, _ in calls(events[7])] == ["present_result"]
    present_args = events[7].data["tool_calls"]
    assert isinstance(present_args, list)
    assert isinstance(present_args[0], dict)
    assert present_args[0]["args"] == {
        "status": "ok",
        "message": "Gotowe.",
        "tables": [{"handle": "r2", "title": "Sprzedaż po miesiącach"}],
    }
    usage = events[1].data["usage"]
    assert isinstance(usage, dict)
    assert isinstance(usage["input_tokens"], int)
    assert usage["input_tokens"] > 0


async def test_a_turn_stopped_by_the_step_limit_keeps_its_steps_in_the_audit(
    ask_service: Callable[..., AskService],
    scripted: Callable[..., FunctionModel],
    agent_settings: Settings,
    audit_log: JsonlAuditLog,
) -> None:
    steps = agent_settings.max_agent_steps
    model = scripted(*(ToolCallPart("sales_order_list", YEAR_2024) for _ in range(steps + 2)))

    response = await ask_service(model, audit_log=audit_log).ask("Raport", user_id=7)

    assert response.status == "error"
    events = audit_log.events(response.session_id)
    assert kinds(events).count("model_response") == steps
    assert kinds(events)[-2:] == ["turn_failed", "response"]
    assert events[-2].data["error"] == "UsageLimitExceeded"


async def test_capture_of_run_messages_still_sees_the_turn(
    ask_service: Callable[..., AskService], scripted: Callable[..., FunctionModel]
) -> None:
    model = scripted(ToolCallPart("sales_order_list", YEAR_2024), present())

    with capture_run_messages() as messages:
        await ask_service(model).ask("Raport", user_id=7)

    calls_seen = [
        part.tool_name
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart)
    ]
    assert calls_seen == ["sales_order_list", "present_result"]


async def test_a_model_provider_error_is_audited_as_a_failed_turn(
    ask_service: Callable[..., AskService], audit_log: JsonlAuditLog
) -> None:
    def overloaded(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(529, "claude-haiku-5-5", {"type": "overloaded_error"})

    response = await ask_service(FunctionModel(overloaded), audit_log=audit_log).ask(
        "Raport", user_id=7
    )

    events = audit_log.events(response.session_id)
    assert kinds(events) == ["request", "turn_failed", "response"]
    assert events[1].data["error"] == "ModelHTTPError"


async def test_an_event_the_log_cannot_write_does_not_fail_the_turn(
    ask_service: Callable[..., AskService],
    scripted: Callable[..., FunctionModel],
    audit_log: JsonlAuditLog,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A lone surrogate is valid in a Python string but cannot be encoded as JSON text.
    prompt = "Raport \ud800"

    with caplog.at_level(logging.WARNING, logger="data_concierge.audit"):
        response = await ask_service(scripted(present()), audit_log=audit_log).ask(
            prompt, user_id=7
        )

    assert response.status == "ok"
    assert any("audit event request not" in r.getMessage() for r in caplog.records)
    assert kinds(audit_log.events(response.session_id))[-1] == "response"
