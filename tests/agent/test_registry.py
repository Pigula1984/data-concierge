"""Procedure tools generated from the catalog, and the fixed order the model sees them in."""

import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import date

import pytest
from pydantic_ai import Agent, Tool
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.tools import ToolDefinition

from data_concierge.agent.deps import AgentDeps
from data_concierge.agent.registry import ProcedureToolFactory, ToolRegistry
from data_concierge.catalog.models import Catalog, DataRange
from data_concierge.core.errors import ProcedureTimeoutError
from data_concierge.results.models import ResultSummary

pytestmark = pytest.mark.anyio

type ToolDefinitions = Callable[[Sequence[Tool[AgentDeps]]], Awaitable[list[ToolDefinition]]]

# Anthropic limits per request, counted over all strict tools (architecture, section 6.1).
MAX_STRICT_TOOLS = 20
MAX_STRICT_OPTIONAL_PARAMS = 24
MAX_STRICT_UNION_PARAMS = 16


@pytest.fixture
def registry(catalog: Catalog, data_ranges: dict[str, DataRange]) -> ToolRegistry:
    return ToolRegistry(catalog, ProcedureToolFactory(data_ranges, strict=True), strict=True)


def returned(messages: Sequence[ModelMessage]) -> list[ToolReturnPart | RetryPromptPart]:
    """What the model got back from every tool call, in order."""
    return [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart | RetryPromptPart)
    ]


async def definition(tools: ToolDefinitions, registry: ToolRegistry, name: str) -> ToolDefinition:
    return next(d for d in await tools(registry.tools()) if d.name == name)


async def test_procedure_schema_has_model_arguments_only(
    registry: ToolRegistry, tool_definitions: ToolDefinitions
) -> None:
    schema = (
        await definition(tool_definitions, registry, "sales_order_list")
    ).parameters_json_schema

    assert list(schema["properties"]) == ["date_from", "date_to", "customer_id"]
    assert schema["required"] == ["date_from", "date_to"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["date_from"]["format"] == "date"
    assert schema["properties"]["customer_id"]["anyOf"] == [{"type": "integer"}, {"type": "null"}]
    assert "customer_search" in schema["properties"]["customer_id"]["description"]


async def test_procedure_description_has_grain_references_and_data_range(
    registry: ToolRegistry, tool_definitions: ToolDefinitions
) -> None:
    description = (await definition(tool_definitions, registry, "sales_order_list")).description

    assert description == (
        "Zamówienia sprzedaży (nagłówki) w okresie, opcjonalnie dla jednego klienta. "
        "Jeden wiersz = jedno zamówienie, dane nie są zgrupowane. "
        "Używaj do: pytanie dotyczy sprzedaży, obrotów lub zamówień w czasie; potrzebny jest "
        "raport sprzedaży po dniach, miesiącach, latach, klientach lub terytoriach. "
        "Nie używaj do: sprzedaż w podziale na produkty lub kategorie → użyj "
        "sales_order_line_list; wyszukanie klienta po nazwie → najpierw customer_search. "
        "Miary: SubTotal (Sprzedaż netto, bez podatku i frachtu; domyślna dla: sprzedaż), "
        "TotalDue (Kwota z podatkiem i frachtem). "
        "Dane: od 2022-05-30 do 2025-06-29."
    )


async def test_line_description_names_the_default_measure(
    registry: ToolRegistry, tool_definitions: ToolDefinitions
) -> None:
    description = (
        await definition(tool_definitions, registry, "sales_order_line_list")
    ).description

    assert "LineTotal (Wartość netto pozycji; domyślna dla: sprzedaż)" in (description or "")


async def test_tools_come_in_catalog_order_then_query_and_describe_result(
    registry: ToolRegistry, catalog: Catalog, tool_definitions: ToolDefinitions
) -> None:
    names = [d.name for d in await tool_definitions(registry.tools())]

    assert names == [
        *(entry.tool for entry in catalog.entries if entry.exposed),
        "query",
        "describe_result",
    ]
    assert "sales_order_data_range" not in names


async def test_definitions_are_byte_identical_between_registries(
    registry: ToolRegistry,
    catalog: Catalog,
    data_ranges: dict[str, DataRange],
    tool_definitions: ToolDefinitions,
) -> None:
    rebuilt = ToolRegistry(catalog, ProcedureToolFactory(data_ranges, strict=True), strict=True)

    first = await tool_definitions(registry.tools())
    second = await tool_definitions(rebuilt.tools())

    assert json.dumps([d.__dict__ for d in first], default=str) == json.dumps(
        [d.__dict__ for d in second], default=str
    )


async def test_strict_tools_fit_the_anthropic_budget(
    registry: ToolRegistry, tool_definitions: ToolDefinitions
) -> None:
    strict = [d for d in await tool_definitions(registry.tools()) if d.strict]
    properties = [
        (name, prop, d.parameters_json_schema.get("required", []))
        for d in strict
        for name, prop in d.parameters_json_schema["properties"].items()
    ]
    optional = [name for name, _, required in properties if name not in required]
    unions = [name for name, prop, _ in properties if "anyOf" in prop]

    assert [d.name for d in strict][-1] == "query"
    assert len(strict) <= MAX_STRICT_TOOLS
    assert len(optional) <= MAX_STRICT_OPTIONAL_PARAMS
    assert len(unions) <= MAX_STRICT_UNION_PARAMS


async def test_strict_follows_the_setting(
    catalog: Catalog, data_ranges: dict[str, DataRange], tool_definitions: ToolDefinitions
) -> None:
    registry = ToolRegistry(catalog, ProcedureToolFactory(data_ranges, strict=False), strict=False)

    assert not any(d.strict for d in await tool_definitions(registry.tools()))


async def test_invalid_argument_is_sent_back_and_the_corrected_call_runs(
    registry: ToolRegistry,
    deps: AgentDeps,
    scripted: Callable[..., FunctionModel],
    procedure_calls: list[tuple[str, dict[str, object]]],
) -> None:
    model = scripted(
        ToolCallPart("sales_order_list", {"date_from": "2024-13-01", "date_to": "2025-01-01"}),
        ToolCallPart("sales_order_list", {"date_from": "2024-01-01", "date_to": "2025-01-01"}),
    )

    result = await Agent(model, deps_type=AgentDeps, tools=registry.tools()).run("x", deps=deps)

    parts = returned(result.all_messages())
    assert isinstance(parts[0], RetryPromptPart)
    assert isinstance(parts[1], ToolReturnPart)
    assert isinstance(parts[1].content, ResultSummary)
    assert parts[1].content.handle == "r1"
    assert procedure_calls == [
        (
            "api.SalesOrder_List",
            {
                "DateFrom": date(2024, 1, 1),
                "DateTo": date(2025, 1, 1),
                "CustomerID": None,
                "UserId": 7,
            },
        )
    ]


async def test_procedure_timeout_is_a_tool_failure(
    catalog: Catalog,
    data_ranges: dict[str, DataRange],
    deps: AgentDeps,
    scripted: Callable[..., FunctionModel],
    fail_procedures: Callable[[Exception], None],
) -> None:
    fail_procedures(ProcedureTimeoutError("the call exceeded the 30 s time limit"))
    tool = ProcedureToolFactory(data_ranges, strict=True).create(
        catalog.entry("api.SalesOrder_List")
    )
    tool.max_retries = 0
    call = ToolCallPart("sales_order_list", {"date_from": "2024-01-01", "date_to": "2025-01-01"})

    result = await Agent(scripted(call, call), deps_type=AgentDeps, tools=[tool]).run(
        "x", deps=deps
    )

    failed = returned(result.all_messages())
    assert [(type(part), getattr(part, "outcome", None)) for part in failed] == [
        (ToolReturnPart, "failed"),
        (ToolReturnPart, "failed"),
    ]
