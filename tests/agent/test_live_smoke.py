"""One check on the real Claude API (plan U14, KTD4): the configured model accepts the strict
catalog tools and `effort`, and continues a conversation without dropping thinking blocks.

Procedures still run against the fake repository, so no SQL Server is needed.
"""

import pytest
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from data_concierge.agent.deps import AgentDeps
from data_concierge.agent.models import AgentTurn
from data_concierge.agent.prompts import AgentInstructions
from data_concierge.agent.registry import ProcedureToolFactory, ToolRegistry
from data_concierge.agent.service import AgentService
from data_concierge.catalog.models import Catalog, DataRange
from data_concierge.core.settings import Settings

pytestmark = [pytest.mark.eval, pytest.mark.anyio]


@pytest.fixture
def service(
    catalog: Catalog, data_ranges: dict[str, DataRange], settings: Settings
) -> AgentService:
    if settings.anthropic_api_key is None:
        pytest.skip("ANTHROPIC_API_KEY is not set")
    provider, name = settings.model.split(":", 1)
    assert provider == "anthropic", "the live check covers the Anthropic settings only"
    model = AnthropicModel(
        name, provider=AnthropicProvider(api_key=settings.anthropic_api_key.get_secret_value())
    )
    strict = settings.strict_tools
    tools = ToolRegistry(catalog, ProcedureToolFactory(data_ranges, strict), strict).tools()
    return AgentService(model, tools, AgentInstructions(catalog, data_ranges).text(), settings)


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
        "Zrób raport sprzedaży po miesiącach za 2024. Dzisiejsza data: 2026-10-18.", [], deps
    )

    assert first.answer.status == "ok", first.answer
    assert procedure_calls, "the model did not call a procedure"
    assert first.answer.tables

    second = await service.run("A teraz tylko pierwszy kwartał 2024.", first.messages, deps)

    assert second.answer.status in ("ok", "no_data"), second.answer
    assert len(second.messages) > len(first.messages)
    assert dropped_thinking(second, len(first.messages)) == []
