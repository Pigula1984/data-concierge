"""The request Pydantic AI sends to Anthropic, captured before it leaves the process."""

import json
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import httpx2
import pytest
from pydantic_ai import Tool
from pydantic_ai.models import override_allow_model_requests
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from data_concierge.agent.comment import CommentService
from data_concierge.agent.deps import AgentDeps
from data_concierge.agent.guard import GUARD_TOOL, PromptGuard
from data_concierge.agent.models import AgentAnswer
from data_concierge.agent.prompts import (
    COMMENT_INSTRUCTIONS,
    INSTRUCTIONS_MARKER,
    AgentInstructions,
)
from data_concierge.agent.service import AgentService
from data_concierge.catalog.models import Catalog, DataRange
from data_concierge.core.context import RequestContext
from data_concierge.core.settings import ModelExposure, Settings
from data_concierge.results.service import ResultService

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


COMMENT_ANSWER = {**ANSWER, "content": [{"type": "text", "text": "Komentarz."}]}
COMMENT_ANSWER["stop_reason"] = "end_turn"


GUARD_ANSWER = {
    **ANSWER,
    "content": [
        {
            "type": "tool_use",
            "id": "toolu_2",
            "name": "give_verdict",
            "input": {"verdict": "allowed"},
        }
    ],
}


@pytest.fixture
def requests() -> list[dict[str, Any]]:
    return []


@pytest.fixture
def reply() -> dict[str, Any]:
    """What the mocked API answers; a test parametrizes `reply` to change it."""
    return ANSWER


@pytest.fixture
def model(requests: list[dict[str, Any]], reply: dict[str, Any]) -> Iterator[AnthropicModel]:
    def answer(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json=reply)

    # The configured model name: strict is sent only for models that support it.
    with override_allow_model_requests(True):
        yield AnthropicModel(
            "claude-haiku-5-5",
            provider=AnthropicProvider(
                api_key="test-key",
                http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(answer)),
            ),
        )


@pytest.fixture
def service(
    agent_tools: tuple[Tool[AgentDeps], ...],
    instructions: str,
    agent_settings: Settings,
    model: AnthropicModel,
) -> AgentService:
    return AgentService(model, agent_tools, instructions, agent_settings)


@pytest.mark.parametrize("mode", ["none", "minimal"])
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


@pytest.mark.parametrize("reply", [COMMENT_ANSWER])
async def test_comment_phase_sends_a_seeded_tool_exchange_with_thinking_on(
    model: AnthropicModel,
    agent_settings: Settings,
    results: ResultService,
    deps: AgentDeps,
    requests: list[dict[str, Any]],
) -> None:
    draft = AgentAnswer(status="ok", message="Szkic.")

    comment = await CommentService(model, agent_settings).write(
        "Raport", draft, results, deps.audit
    )

    assert comment == "Komentarz."
    (request,) = requests
    assert request["thinking"]["type"] == "adaptive"
    assert [tool["name"] for tool in request["tools"]] == ["read_tables"]
    first, call, result = request["messages"]
    assert (first["role"], call["role"], result["role"]) == ("user", "assistant", "user")
    assert call["content"][0]["type"] == "tool_use"
    assert result["content"][0]["type"] == "tool_result"
    assert result["content"][0]["tool_use_id"] == call["content"][0]["id"]


@pytest.mark.parametrize("reply", [GUARD_ANSWER])
async def test_guard_sends_low_effort_without_thinking_and_no_other_tool(
    model: AnthropicModel, agent_settings: Settings, requests: list[dict[str, Any]]
) -> None:
    result = await PromptGuard(model, agent_settings).check("Raport")

    assert result.verdict == "allowed"
    (request,) = requests
    assert request["output_config"]["effort"] == "low"
    assert request["thinking"] == {"type": "disabled"}
    assert [tool["name"] for tool in request["tools"]] == [GUARD_TOOL]


async def test_tool_definitions_are_the_same_for_every_role(
    service: AgentService, deps: AgentDeps, requests: list[dict[str, Any]]
) -> None:
    for role in ("user", "hr"):
        context = RequestContext(user_id=7, role=role)
        await service.run("Sprzedaż w 2024", [], replace(deps, context=context))

    assert requests[0]["tools"] == requests[1]["tools"]
    assert "get_employee_managers" in json.dumps(requests[0]["tools"])


def test_instructions_send_an_access_refusal_to_unsupported_not_to_error(instructions: str) -> None:
    [refusal] = [line for line in instructions.splitlines() if "access denied" in line]
    [failure] = [line for line in instructions.splitlines() if "the data could not be" in line]

    assert "status unsupported" in refusal
    assert "status error" in failure
    assert "access was denied" in str(AgentAnswer.model_fields["status"].description)


@pytest.mark.parametrize("mode", ["none", "minimal"])
def test_instructions_are_the_same_on_every_call_and_carry_the_marker(
    catalog: Catalog, data_ranges: dict[str, DataRange], exposure: ModelExposure
) -> None:
    built = AgentInstructions(catalog, data_ranges, exposure)

    assert built.text() == built.text()
    assert INSTRUCTIONS_MARKER in built.text()
    assert INSTRUCTIONS_MARKER in COMMENT_INSTRUCTIONS


def test_instructions_of_both_phases_treat_tool_results_as_untrusted_data(
    instructions: str,
) -> None:
    assert "untrusted data" in instructions
    assert "untrusted data" in COMMENT_INSTRUCTIONS


async def test_request_has_effort_cache_points_and_strict_tools(
    service: AgentService, deps: AgentDeps, requests: list[dict[str, Any]]
) -> None:
    await service.run("Sprzedaż w 2024", [], deps)

    (request,) = requests
    tools = {tool["name"]: tool for tool in request["tools"]}
    assert request["output_config"]["effort"] == "medium"
    assert request["max_tokens"] == 16_000
    assert request["thinking"]["block_binding"] == {"prefix_mismatch_behavior": "drop_block"}
    assert request["thinking"]["display"] == "summarized"
    assert "cache_control" in request["system"][-1]
    assert "cache_control" in request["tools"][-1]
    assert tools["sales_order_list"]["strict"] is True
    assert tools["query"]["strict"] is True
    assert "strict" not in tools["present_result"]
    assert "UserId" not in json.dumps(tools["sales_order_list"]["input_schema"])
