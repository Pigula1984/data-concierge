"""AuditCapability over a real agent turn: what the model decided and what it was sent back."""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import JsonValue
from pydantic_ai import Tool
from pydantic_ai.messages import ModelResponse, ThinkingPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from data_concierge.agent.deps import AgentDeps
from data_concierge.agent.service import AgentService
from data_concierge.audit.models import AuditEvent
from data_concierge.audit.repository import JsonlAuditLog
from data_concierge.audit.service import TurnAudit
from data_concierge.core.settings import Settings

pytestmark = pytest.mark.anyio

YEAR_2024 = {"date_from": "2024-01-01", "date_to": "2025-01-01"}

type Run = Callable[[FunctionModel], Awaitable[tuple[AuditEvent, ...]]]


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.jsonl"


@pytest.fixture
def run(
    agent_tools: tuple[Tool[AgentDeps], ...],
    instructions: str,
    agent_settings: Settings,
    deps: AgentDeps,
    audit_path: Path,
) -> Run:
    """`await run(model)`: one agent turn whose audit goes to `audit_path`, session s1."""
    audited = replace(deps, audit=TurnAudit(JsonlAuditLog(audit_path), deps.context, "s1"))

    async def turn(model: FunctionModel) -> tuple[AuditEvent, ...]:
        service = AgentService(model, agent_tools, instructions, agent_settings)
        await service.run("Raport", (), audited)
        return JsonlAuditLog(audit_path).events("s1")

    return turn


def model_inputs(events: tuple[AuditEvent, ...]) -> list[dict[str, JsonValue]]:
    """Every part of every `model_input` event, in order."""
    found: list[dict[str, JsonValue]] = []
    for event in events:
        parts = event.data["parts"] if event.event == "model_input" else []
        assert isinstance(parts, list)
        found += [part for part in parts if isinstance(part, dict)]
    return found


async def test_thinking_is_audited_without_its_signature(
    run: Run, scripted: Callable[..., FunctionModel], audit_path: Path
) -> None:
    model = scripted(
        ModelResponse(
            parts=[
                ThinkingPart("Potrzebuję zamówień z 2024.", signature="SIGNATURE-7Q"),
                ThinkingPart(""),
                ToolCallPart("sales_order_list", YEAR_2024),
            ]
        ),
        ToolCallPart("present_result", {"status": "ok", "message": "Gotowe."}),
    )

    events = await run(model)

    assert events[0].event == "model_response"
    assert events[0].data["thinking"] == ["Potrzebuję zamówień z 2024."]
    assert "SIGNATURE-7Q" not in audit_path.read_text(encoding="utf-8")


async def test_invalid_arguments_are_audited_as_a_retry_prompt(
    run: Run, scripted: Callable[..., FunctionModel]
) -> None:
    model = scripted(
        ToolCallPart("sales_order_list", {"date_from": "2024", "date_to": "2025-01-01"}),
        ToolCallPart("present_result", {"status": "ok", "message": "Gotowe."}),
    )

    events = await run(model)

    [retry] = model_inputs(events)
    assert (retry["kind"], retry["tool"]) == ("retry", "sales_order_list")
    assert "date_from" in str(retry["content"])
    # Pydantic AI rejects the arguments before the tool runs, so nothing was executed.
    assert [e.event for e in events].count("tool_executed") == 0


async def test_an_answer_with_an_unknown_handle_is_audited_as_a_retry_prompt(
    run: Run, scripted: Callable[..., FunctionModel]
) -> None:
    model = scripted(
        ToolCallPart(
            "present_result",
            {"status": "ok", "message": "Gotowe.", "tables": [{"handle": "r9", "title": "Brak"}]},
        ),
        ToolCallPart("present_result", {"status": "ok", "message": "Gotowe."}),
    )

    events = await run(model)

    [retry] = model_inputs(events)
    assert (retry["kind"], retry["tool"]) == ("retry", "present_result")
    assert "unknown handles r9" in str(retry["content"])
