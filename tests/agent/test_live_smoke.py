"""Checks on the real Claude API (plan U14, KTD4): the configured model accepts the strict
catalog tools and `effort`, writes the total of its comment as an anchor in the configured
mode (architecture 002, section 6.1), and continues a conversation without dropping thinking
blocks; in mode minimal also after the comment phase replaced the draft in the history (plan
U8, KTD8).

Procedures still run against the fake repository, so no SQL Server is needed.
"""

import logging
from collections.abc import Callable, Sequence

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from data_concierge.agent.anchors import ANCHOR, Anchors
from data_concierge.agent.comment import CommentService
from data_concierge.agent.deps import AgentDeps
from data_concierge.agent.models import OUTPUT_TOOL, AgentTurn
from data_concierge.agent.prompts import AgentInstructions
from data_concierge.agent.registry import ProcedureToolFactory, ToolRegistry
from data_concierge.agent.service import AgentService
from data_concierge.api.service import AskService
from data_concierge.api.sessions import InMemorySessionRepository
from data_concierge.bootstrap import DuckDBSessionFactory
from data_concierge.catalog.models import Catalog, DataRange
from data_concierge.core.settings import ModelExposure, Settings
from data_concierge.procedures.repository import ProcedureRepository

pytestmark = [pytest.mark.eval, pytest.mark.anyio]


@pytest.fixture
def model(settings: Settings) -> AnthropicModel:
    if settings.anthropic_api_key is None:
        pytest.skip("ANTHROPIC_API_KEY is not set")
    provider, name = settings.model.split(":", 1)
    assert provider == "anthropic", "the live check covers the Anthropic settings only"
    return AnthropicModel(
        name, provider=AnthropicProvider(api_key=settings.anthropic_api_key.get_secret_value())
    )


@pytest.fixture
def agent_for(
    model: AnthropicModel, catalog: Catalog, data_ranges: dict[str, DataRange]
) -> Callable[[Settings], AgentService]:
    """The tools phase as the composition root builds it for the given settings."""

    def build(settings: Settings) -> AgentService:
        strict = settings.strict_tools
        exposure = ModelExposure.from_settings(settings)
        tools = ToolRegistry(catalog, ProcedureToolFactory(data_ranges, strict), strict, exposure)
        instructions = AgentInstructions(catalog, data_ranges, exposure, settings.model_max_rows)
        return AgentService(model, tools.tools(), instructions.text(), settings)

    return build


@pytest.fixture
def service(agent_for: Callable[[Settings], AgentService], settings: Settings) -> AgentService:
    return agent_for(settings)


def dropped_thinking(turn: AgentTurn, since: int) -> list[object]:
    return [
        message.provider_details["input_transformations"]
        for message in turn.messages[since:]
        if isinstance(message, ModelResponse)
        and message.provider_details
        and "input_transformations" in message.provider_details
    ]


async def test_model_answers_and_continues_with_history(
    service: AgentService,
    deps: AgentDeps,
    procedure_calls: list[tuple[str, dict[str, object]]],
) -> None:
    first = await service.run(
        "Zrób raport sprzedaży po miesiącach za 2024 i podaj w komentarzu sumę roczną. "
        "Dzisiejsza data: 2026-10-18.",
        [],
        deps,
    )

    assert first.answer.status == "ok", first.answer
    assert procedure_calls, "the model did not call a procedure"
    assert first.answer.tables
    assert ANCHOR.search(first.answer.message), first.answer.message
    assert not ANCHOR.search(Anchors(deps.results).fill(first.answer.message))

    second = await service.run("A teraz tylko pierwszy kwartał 2024.", first.messages, deps)

    assert second.answer.status in ("ok", "no_data"), second.answer
    assert len(second.messages) > len(first.messages)
    assert dropped_thinking(second, len(first.messages)) == []


def answer_message(messages: Sequence[ModelMessage]) -> str:
    """The `message` of the last `present_result` call in the history."""
    calls = [
        part
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart) and part.tool_name == OUTPUT_TOOL
    ]
    return str(calls[-1].args_as_dict()["message"])


async def test_mode_minimal_comments_and_continues_with_the_comment_in_history(
    model: AnthropicModel,
    agent_for: Callable[[Settings], AgentService],
    settings: Settings,
    repository: ProcedureRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    minimal = settings.model_copy(update={"model_data_access": "minimal"})
    sessions = InMemorySessionRepository()
    service = AskService(
        agent_for(minimal),
        CommentService(model, minimal),
        sessions,
        DuckDBSessionFactory(repository, minimal),
        ModelExposure.from_settings(minimal),
    )

    with caplog.at_level(logging.WARNING, logger="data_concierge"):
        first = await service.ask(
            "Zrób raport sprzedaży po miesiącach za 2024 i skomentuj go.", user_id=7
        )
        session = sessions.get(first.session_id)
        assert session is not None
        template = answer_message(session.history)
        second = await service.ask(
            "A teraz tylko pierwszy kwartał 2024.", user_id=7, session_id=first.session_id
        )

    assert first.status == "ok", first
    assert first.tables
    # The history holds the template of the comment the user read, not filled values.
    assert Anchors(session.results).fill(template) == first.message
    assert second.status in ("ok", "no_data"), second
    warnings = [record.getMessage() for record in caplog.records]
    assert not [w for w in warnings if "comment phase failed" in w], warnings
    assert not [w for w in warnings if "dropped thinking blocks" in w], warnings
