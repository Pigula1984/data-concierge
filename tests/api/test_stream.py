"""TurnStream over a real AskService: the steps and the response of a turn, and a turn that
outlives its client."""

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import anyio
import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from data_concierge.agent.progress import ProgressEvent
from data_concierge.api.schemas import Response
from data_concierge.api.service import AskService
from data_concierge.api.stream import StreamItem, TurnStream
from data_concierge.audit.repository import JsonlAuditLog

pytestmark = pytest.mark.anyio

YEAR_2024 = {"date_from": "2024-01-01", "date_to": "2025-01-01"}


def present(**fields: object) -> ToolCallPart:
    return ToolCallPart("present_result", {"status": "ok", "message": "Gotowe.", **fields})


def orders_turn(scripted: Callable[..., FunctionModel]) -> FunctionModel:
    return scripted(
        ToolCallPart("sales_order_list", YEAR_2024),
        present(tables=[{"handle": "r1", "title": "Zamówienia 2024"}]),
        present(),
    )


async def collect(stream: TurnStream, prompt: str, session_id: str | None = None) -> list[Any]:
    return [item async for item in stream.events(prompt, 7, session_id)]


async def finished(stream: TurnStream) -> None:
    with anyio.fail_after(5):
        while stream.running:
            await anyio.sleep(0.01)


def first_prompts(model: FunctionModel) -> list[str]:
    """The first line of every user prompt in the last request the scripted model received."""
    requests: list[list[ModelMessage]] = cast(Any, model.function).requests
    return [
        str(part.content).split("\n")[0]
        for message in requests[-1]
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]


async def test_the_stream_sends_the_steps_then_the_response_of_ask(
    ask_service: Callable[..., AskService], scripted: Callable[..., FunctionModel]
) -> None:
    stream = TurnStream(ask_service(orders_turn(scripted)))
    expected = await ask_service(orders_turn(scripted)).ask("Zamówienia z 2024", user_id=7)

    items = await collect(stream, "Zamówienia z 2024")

    *steps, last = items
    assert [step.step for step in steps] == ["guard", "model", "procedure", "model"]
    assert all(isinstance(step, ProgressEvent) for step in steps)
    assert isinstance(last, Response)
    shown = {"status", "message", "tables", "chart", "assumptions"}
    assert last.model_dump(include=shown) == expected.model_dump(include=shown)


async def test_a_turn_outlives_a_client_that_disconnects(
    ask_service: Callable[..., AskService],
    scripted: Callable[..., FunctionModel],
    tmp_path: Path,
) -> None:
    audit_log = JsonlAuditLog(tmp_path / "audit.jsonl")
    model = scripted(
        present(),
        ToolCallPart("sales_order_list", YEAR_2024),
        present(tables=[{"handle": "r1", "title": "Zamówienia 2024"}]),
        present(),
    )
    service = ask_service(model, audit_log=audit_log)
    stream = TurnStream(service)
    session_id = (await service.ask("Pierwsza", 7)).session_id

    events = stream.events("Zamówienia z 2024", 7, session_id)
    first: StreamItem = await anext(events)
    await events.aclose()
    await finished(stream)
    await service.ask("Trzecia", 7, session_id)

    assert isinstance(first, ProgressEvent)
    assert first_prompts(model) == ["Pierwsza", "Zamówienia z 2024", "Trzecia"]
    responses = [e for e in audit_log.events(session_id) if e.event == "response"]
    assert len(responses) == 3


async def test_no_turn_keeps_running_after_its_response(
    ask_service: Callable[..., AskService], scripted: Callable[..., FunctionModel]
) -> None:
    stream = TurnStream(ask_service(orders_turn(scripted)))

    await collect(stream, "Zamówienia z 2024")
    await finished(stream)

    assert stream.running == 0


async def test_an_unknown_session_gets_only_the_error_response(
    ask_service: Callable[..., AskService], scripted: Callable[..., FunctionModel]
) -> None:
    stream = TurnStream(ask_service(scripted(present())))

    [item] = await collect(stream, "Raport", session_id="gone")

    assert isinstance(item, Response)
    assert item.status == "error"
