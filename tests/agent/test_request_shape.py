"""The request Pydantic AI sends to Anthropic, captured before it leaves the process."""

import json
from collections.abc import Iterator
from typing import Any

import httpx2
import pytest
from pydantic_ai import Tool
from pydantic_ai.models import override_allow_model_requests
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from data_concierge.agent.deps import AgentDeps
from data_concierge.agent.service import AgentService
from data_concierge.core.settings import Settings

pytestmark = pytest.mark.anyio

# A turn that ends at once, so every captured request starts a turn.
ANSWER = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-haiku-5-5",
    "content": [
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "present_result",
            "input": {"status": "no_data", "message": "Brak danych za ten okres."},
        }
    ],
    "stop_reason": "tool_use",
    "stop_sequence": None,
    "usage": {"input_tokens": 10, "output_tokens": 5},
}


@pytest.fixture
def requests() -> list[dict[str, Any]]:
    return []


@pytest.fixture
def service(
    agent_tools: tuple[Tool[AgentDeps], ...],
    instructions: str,
    agent_settings: Settings,
    requests: list[dict[str, Any]],
) -> Iterator[AgentService]:
    def answer(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json=ANSWER)

    # The configured model name: strict is sent only for models that support it.
    model = AnthropicModel(
        "claude-haiku-5-5",
        provider=AnthropicProvider(
            api_key="test-key",
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(answer)),
        ),
    )
    with override_allow_model_requests(True):
        yield AgentService(model, agent_tools, instructions, agent_settings)


async def test_second_turn_starts_with_the_same_bytes(
    service: AgentService, deps: AgentDeps, requests: list[dict[str, Any]]
) -> None:
    first = await service.run("Sprzedaż w 2019", [], deps)
    await service.run("A w 2020?", first.messages, deps)

    def start(request: dict[str, Any]) -> str:
        return json.dumps([request["system"], request["tools"]])

    assert len(requests) == 2
    assert start(requests[0]) == start(requests[1])
    assert len(requests[1]["messages"]) > len(requests[0]["messages"])


async def test_request_has_effort_cache_points_and_strict_tools(
    service: AgentService, deps: AgentDeps, requests: list[dict[str, Any]]
) -> None:
    await service.run("Sprzedaż w 2024", [], deps)

    (request,) = requests
    tools = {tool["name"]: tool for tool in request["tools"]}
    assert request["output_config"]["effort"] == "medium"
    assert request["max_tokens"] == 16_000
    assert request["thinking"]["block_binding"] == {"prefix_mismatch_behavior": "drop_block"}
    assert "cache_control" in request["system"][-1]
    assert "cache_control" in request["tools"][-1]
    assert tools["sales_order_list"]["strict"] is True
    assert tools["query"]["strict"] is True
    assert "strict" not in tools["present_result"]
    assert "UserId" not in json.dumps(tools["sales_order_list"]["input_schema"])
