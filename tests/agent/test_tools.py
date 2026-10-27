"""The fixed tools as the model sees them: thin controllers over the session's services."""

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import JsonValue
from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.messages import ModelRequest, RetryPromptPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.tools import ToolDefinition

from data_concierge.agent.deps import AgentDeps
from data_concierge.agent.tools import describe_result, query, retry_on_domain_error
from data_concierge.audit.repository import JsonlAuditLog
from data_concierge.audit.service import TurnAudit
from data_concierge.catalog.models import Catalog
from data_concierge.core.errors import (
    ParameterValidationError,
    ProcedureError,
    ProcedureTimeoutError,
)
from data_concierge.query.models import QuerySummary
from data_concierge.query.policy import QueryPolicy
from data_concierge.query.service import QueryService
from data_concierge.results.models import ResultSummary
from data_concierge.results.repository import DuckDBResultStore
from data_concierge.results.service import ResultService

pytestmark = pytest.mark.anyio

FIXED_TOOLS = (Tool(query), Tool(describe_result))

type Part = ToolReturnPart | RetryPromptPart


async def tool_results(
    model: FunctionModel, deps: AgentDeps, tools: Sequence[Tool[AgentDeps]] = FIXED_TOOLS
) -> list[Part]:
    """What the model got back from every tool call of the run, in order."""
    result = await Agent(model, deps_type=AgentDeps, tools=tools).run("test", deps=deps)
    return [
        part
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart | RetryPromptPart)
    ]


@pytest.fixture
def orders(deps: AgentDeps, catalog: Catalog) -> None:
    """Orders of 2024 saved as r1 through the procedure service."""
    deps.procedures.call(
        catalog.entry("api.SalesOrder_List"),
        {"date_from": "2024-01-01", "date_to": "2025-01-01"},
        deps.context,
    )


@pytest.mark.usefixtures("orders")
async def test_query_returns_the_new_handle_with_its_sources_and_sql(
    deps: AgentDeps, scripted: Callable[..., FunctionModel]
) -> None:
    sql = 'SELECT sum("SubTotal") AS net_sales FROM r1'

    (part,) = await tool_results(scripted(ToolCallPart("query", {"sql": sql})), deps)

    assert isinstance(part, ToolReturnPart)
    assert isinstance(part.content, QuerySummary)
    assert (part.content.handle, part.content.sources, part.content.sql) == ("r2", ("r1",), sql)
    assert (part.content.row_count, part.content.stats, part.content.ids) == (1, {}, {})


@pytest.mark.usefixtures("orders")
async def test_query_with_a_double_column_asks_the_model_to_cast(
    deps: AgentDeps, scripted: Callable[..., FunctionModel]
) -> None:
    sql = 'SELECT avg("SubTotal") AS avg_order FROM r1'

    (part,) = await tool_results(scripted(ToolCallPart("query", {"sql": sql})), deps)

    assert isinstance(part, RetryPromptPart)
    assert "column avg_order is DOUBLE; CAST it to DECIMAL(38,6)" in str(part.content)


@pytest.mark.usefixtures("orders")
async def test_query_with_an_unknown_column_returns_the_duckdb_binder_message(
    deps: AgentDeps, scripted: Callable[..., FunctionModel]
) -> None:
    sql = 'SELECT sum("Subtotal2") AS net_sales FROM r1'

    (part,) = await tool_results(scripted(ToolCallPart("query", {"sql": sql})), deps)

    assert isinstance(part, RetryPromptPart)
    assert '"Subtotal2" not found' in str(part.content)


@pytest.mark.usefixtures("orders")
async def test_query_error_quoting_a_data_value_is_logged_but_not_shown_to_the_model(
    deps: AgentDeps, scripted: Callable[..., FunctionModel], caplog: pytest.LogCaptureFixture
) -> None:
    sql = 'SELECT CAST("CustomerName" AS INTEGER) AS customer FROM r1'

    with caplog.at_level(logging.WARNING, logger="data_concierge.agent.tools"):
        (part,) = await tool_results(scripted(ToolCallPart("query", {"sql": sql})), deps)

    assert isinstance(part, RetryPromptPart)
    assert "ConversionException" in str(part.content)
    assert "Mohamed Pal" not in str(part.content)
    assert "Mohamed Pal" in caplog.text
    assert deps.context.correlation_id in caplog.text


@pytest.mark.usefixtures("orders")
async def test_query_on_a_missing_handle_names_it_for_the_model(
    deps: AgentDeps, scripted: Callable[..., FunctionModel]
) -> None:
    (part,) = await tool_results(scripted(ToolCallPart("query", {"sql": "SELECT * FROM r9"})), deps)

    assert isinstance(part, RetryPromptPart)
    assert "r9" in str(part.content)


async def test_query_over_the_time_limit_asks_for_a_retry_not_a_failure(
    deps: AgentDeps, scripted: Callable[..., FunctionModel]
) -> None:
    results = ResultService(DuckDBResultStore(query_timeout_seconds=0.5))
    slow = replace(deps, results=results, queries=QueryService(results, QueryPolicy()))
    sql = "SELECT sum(a.range * b.range) AS total FROM range(100000) a, range(100000) b"

    (part,) = await tool_results(scripted(ToolCallPart("query", {"sql": sql})), slow)

    assert isinstance(part, RetryPromptPart)
    assert "time limit" in str(part.content)


async def test_query_schema_has_one_required_sql_string(
    tool_definitions: Callable[[Sequence[Tool[AgentDeps]]], Awaitable[list[ToolDefinition]]],
) -> None:
    (definition,) = await tool_definitions([Tool(query)])

    schema = definition.parameters_json_schema
    assert schema["required"] == ["sql"]
    assert schema["properties"]["sql"]["type"] == "string"
    assert schema["additionalProperties"] is False
    assert "single SELECT" in schema["properties"]["sql"]["description"]


@pytest.mark.usefixtures("orders")
async def test_describe_result_returns_the_summary_without_values(
    deps: AgentDeps, scripted: Callable[..., FunctionModel]
) -> None:
    (part,) = await tool_results(scripted(ToolCallPart("describe_result", {"handle": "r1"})), deps)

    assert isinstance(part, ToolReturnPart)
    assert isinstance(part.content, ResultSummary)
    assert part.content.row_count == 3
    assert [column.name for column in part.content.columns][:3] == [
        "SalesOrderID",
        "OrderDate",
        "CustomerID",
    ]
    assert part.content.stats["OrderDate"].max == "2024-03-02"
    assert "SubTotal" not in part.content.stats
    assert part.content.ids["CustomerID"] == (12698, 20550, 11000)


@pytest.mark.usefixtures("orders")
async def test_describe_result_of_an_unknown_handle_lists_the_existing_ones(
    deps: AgentDeps, scripted: Callable[..., FunctionModel]
) -> None:
    (part,) = await tool_results(scripted(ToolCallPart("describe_result", {"handle": "r9"})), deps)

    assert isinstance(part, RetryPromptPart)
    assert "available handles: r1" in str(part.content)


@retry_on_domain_error
def slow_procedure(ctx: RunContext[AgentDeps]) -> str:
    raise ProcedureTimeoutError("the call exceeded the 30 s time limit and was stopped")


async def test_procedure_failure_is_reported_without_using_the_retry_budget(
    deps: AgentDeps, scripted: Callable[..., FunctionModel]
) -> None:
    # With no retries left, a ModelRetry would end the run; ToolFailed lets it continue.
    tool = Tool(slow_procedure, max_retries=0)
    model = scripted(ToolCallPart("slow_procedure", {}), ToolCallPart("slow_procedure", {}))

    parts = await tool_results(model, deps, [tool])

    assert [type(part) for part in parts] == [ToolReturnPart, ToolReturnPart]
    assert all(isinstance(p, ToolReturnPart) and p.outcome == "failed" for p in parts)
    assert "30 s time limit" in str(parts[0].content)


@retry_on_domain_error
def unconvertible_procedure(ctx: RunContext[AgentDeps]) -> str:
    raise ProcedureError(
        "api.Test: column CustomerID does not fit the catalog type int",
        detail="Could not convert 'SENTINEL-NAME-7Q' with type str",
    )


async def test_procedure_failure_detail_is_logged_but_not_shown_to_the_model(
    deps: AgentDeps, scripted: Callable[..., FunctionModel], caplog: pytest.LogCaptureFixture
) -> None:
    model = scripted(ToolCallPart("unconvertible_procedure", {}))

    with caplog.at_level(logging.WARNING, logger="data_concierge.agent.tools"):
        (part,) = await tool_results(model, deps, [Tool(unconvertible_procedure)])

    assert isinstance(part, ToolReturnPart)
    assert part.outcome == "failed"
    assert "SENTINEL-NAME-7Q" not in str(part.content)
    assert "SENTINEL-NAME-7Q" in caplog.text
    assert deps.context.correlation_id in caplog.text


@retry_on_domain_error
def broken_tool(ctx: RunContext[AgentDeps]) -> str:
    raise RuntimeError("bug")


async def test_unexpected_errors_pass_through_unchanged(
    deps: AgentDeps, scripted: Callable[..., FunctionModel]
) -> None:
    with pytest.raises(RuntimeError, match="bug"):
        await tool_results(scripted(ToolCallPart("broken_tool", {})), deps, [Tool(broken_tool)])


@retry_on_domain_error
def strict_procedure(ctx: RunContext[AgentDeps]) -> str:
    raise ParameterValidationError("invalid arguments for sales_order_list: date_from: missing")


async def test_invalid_procedure_arguments_ask_the_model_to_correct_them(
    deps: AgentDeps, scripted: Callable[..., FunctionModel]
) -> None:
    (part,) = await tool_results(
        scripted(ToolCallPart("strict_procedure", {})), deps, [Tool(strict_procedure)]
    )

    assert isinstance(part, RetryPromptPart)
    assert "date_from: missing" in str(part.content)


@pytest.fixture
def audit_log(tmp_path: Path) -> JsonlAuditLog:
    return JsonlAuditLog(tmp_path / "audit.jsonl")


@pytest.fixture
def audited(deps: AgentDeps, audit_log: JsonlAuditLog) -> AgentDeps:
    """The session's deps with an audit of the turn written to `audit_log`."""
    return replace(deps, audit=TurnAudit(audit_log, deps.context, session_id="s1"))


def executions(audit_log: JsonlAuditLog) -> list[dict[str, JsonValue]]:
    return [event.data for event in audit_log.events("s1") if event.event == "tool_executed"]


async def test_procedure_execution_is_audited_with_its_result_and_lineage(
    audited: AgentDeps,
    audit_log: JsonlAuditLog,
    agent_tools: tuple[Tool[AgentDeps], ...],
    scripted: Callable[..., FunctionModel],
) -> None:
    call = ToolCallPart(
        "sales_order_list", {"date_from": "2024-01-01", "date_to": "2025-01-01"}, "call-1"
    )

    (part,) = await tool_results(scripted(call), audited, agent_tools)

    assert isinstance(part, ToolReturnPart)
    assert isinstance(part.content, ResultSummary)
    (execution,) = executions(audit_log)
    assert execution["tool"] == "sales_order_list"
    assert execution["tool_call_id"] == "call-1"
    assert execution["handle"] == "r1"
    assert execution["rows"] == part.content.row_count
    assert execution["truncated"] is False
    assert execution["lineage"] == {
        "step": "procedure",
        "procedure": "api.SalesOrder_List",
        # The injected @UserId identifies the caller, not the data, so it is left out.
        "params": {"DateFrom": "2024-01-01", "DateTo": "2025-01-01", "CustomerID": None},
        "truncated": False,
    }
    duration = execution["duration_ms"]
    assert isinstance(duration, int)
    assert duration >= 0


@pytest.mark.usefixtures("orders")
async def test_query_execution_is_audited_with_the_submitted_and_executed_sql(
    audited: AgentDeps, audit_log: JsonlAuditLog, scripted: Callable[..., FunctionModel]
) -> None:
    sql = 'SELECT sum("SubTotal") AS net_sales FROM r1'

    await tool_results(scripted(ToolCallPart("query", {"sql": sql})), audited)

    (execution,) = executions(audit_log)
    assert execution["tool"] == "query"
    assert execution["handle"] == "r2"
    assert execution["rows"] == 1
    lineage = execution["lineage"]
    assert isinstance(lineage, dict)
    assert lineage["step"] == "query"
    assert lineage["sources"] == ["r1"]
    assert lineage["submitted_sql"] == sql
    assert lineage["sql"]


@pytest.mark.usefixtures("orders")
async def test_refused_query_is_audited_with_its_error(
    audited: AgentDeps, audit_log: JsonlAuditLog, scripted: Callable[..., FunctionModel]
) -> None:
    sql = "SELECT 1 FROM r1; SELECT 2 FROM r1"

    (part,) = await tool_results(scripted(ToolCallPart("query", {"sql": sql})), audited)

    assert isinstance(part, RetryPromptPart)
    (execution,) = executions(audit_log)
    assert execution["error"] == "QueryValidationError"
    assert execution["message"] == str(part.content)
    assert "handle" not in execution


async def test_procedure_failure_is_audited_with_the_detail_the_model_does_not_see(
    audited: AgentDeps, audit_log: JsonlAuditLog, scripted: Callable[..., FunctionModel]
) -> None:
    model = scripted(ToolCallPart("unconvertible_procedure", {}))

    await tool_results(model, audited, [Tool(unconvertible_procedure)])

    (execution,) = executions(audit_log)
    assert execution["error"] == "ProcedureError"
    assert execution["message"] == "api.Test: column CustomerID does not fit the catalog type int"
    assert execution["detail"] == "Could not convert 'SENTINEL-NAME-7Q' with type str"


@pytest.mark.usefixtures("orders")
async def test_describe_result_is_audited_without_a_new_handle(
    audited: AgentDeps, audit_log: JsonlAuditLog, scripted: Callable[..., FunctionModel]
) -> None:
    await tool_results(scripted(ToolCallPart("describe_result", {"handle": "r1"})), audited)

    (execution,) = executions(audit_log)
    assert (execution["tool"], execution["handle"]) == ("describe_result", "r1")
    assert audited.results.handles() == ("r1",)
