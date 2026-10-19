"""The agent loop offline: scripted model turns over real tools and a real DuckDB session."""

from collections.abc import Callable
from datetime import date

import pytest
from pydantic_ai import Tool
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from data_concierge.agent.deps import AgentDeps
from data_concierge.agent.prompts import AgentInstructions
from data_concierge.agent.service import AgentService
from data_concierge.catalog.models import Catalog, DataRange
from data_concierge.core.errors import ProcedureTimeoutError
from data_concierge.core.settings import Settings

pytestmark = pytest.mark.anyio

YEAR_2024 = {"date_from": "2024-01-01", "date_to": "2025-01-01"}
MONTHLY_SQL = (
    "SELECT CAST(date_trunc('month', \"OrderDate\") AS DATE) AS month, "
    'sum("SubTotal") AS net_sales FROM r1 GROUP BY 1 ORDER BY month'
)
# A finished earlier turn; a failed turn must leave it as it was.
PRIOR_TURN: list[ModelMessage] = [
    ModelRequest(parts=[UserPromptPart("Klienci z Berlina")]),
    ModelResponse(parts=[TextPart("Gotowe.")]),
]


@pytest.fixture
def service(
    agent_tools: tuple[Tool[AgentDeps], ...], instructions: str, agent_settings: Settings
) -> Callable[[FunctionModel], AgentService]:
    return lambda model: AgentService(model, agent_tools, instructions, agent_settings)


def present(status: str = "ok", **fields: object) -> ToolCallPart:
    return ToolCallPart("present_result", {"status": status, "message": "Gotowe.", **fields})


def retry_prompts(messages: tuple[ModelMessage, ...]) -> list[RetryPromptPart]:
    return [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, RetryPromptPart)
    ]


async def test_procedure_then_query_then_answer_returns_the_query_handle(
    service: Callable[[FunctionModel], AgentService],
    scripted: Callable[..., FunctionModel],
    deps: AgentDeps,
) -> None:
    model = scripted(
        ToolCallPart("sales_order_list", YEAR_2024),
        ToolCallPart("query", {"sql": MONTHLY_SQL}),
        present(tables=[{"handle": "r2", "title": "Sprzedaż netto po miesiącach, 2024"}]),
    )

    turn = await service(model).run("Raport sprzedaży po miesiącach za 2024", [], deps)

    assert turn.answer.status == "ok"
    assert [table.handle for table in turn.answer.tables] == ["r2"]
    assert deps.results.handles() == ("r1", "r2")
    assert not retry_prompts(turn.messages)


async def test_join_of_two_procedures_is_corrected_after_a_rejected_query(
    service: Callable[[FunctionModel], AgentService],
    scripted: Callable[..., FunctionModel],
    deps: AgentDeps,
) -> None:
    join = (
        'SELECT c."CustomerID" AS customer_id, {measure} AS avg_order FROM r2 AS o '
        'JOIN r1 AS c ON c."CustomerID" = o."CustomerID" GROUP BY 1 ORDER BY 1'
    )
    model = scripted(
        ToolCallPart("customer_list", {"city": "Berlin"}),
        ToolCallPart("sales_order_list", YEAR_2024),
        ToolCallPart("query", {"sql": join.format(measure='avg(o."SubTotal")')}),
        ToolCallPart(
            "query", {"sql": join.format(measure='CAST(avg(o."SubTotal") AS DECIMAL(38,6))')}
        ),
        present(tables=[{"handle": "r3", "title": "Średnie zamówienie klientów z Berlina"}]),
    )

    turn = await service(model).run("Średnie zamówienie klientów z Berlina w 2024", [], deps)

    (rejected,) = retry_prompts(turn.messages)
    assert "avg_order is DOUBLE" in str(rejected.content)
    assert turn.answer.status == "ok"
    assert [table.handle for table in turn.answer.tables] == ["r3"]
    assert deps.results.describe("r3").lineage.sources == ("r1", "r2")


async def test_answer_with_an_unknown_handle_is_sent_back_for_correction(
    service: Callable[[FunctionModel], AgentService],
    scripted: Callable[..., FunctionModel],
    deps: AgentDeps,
) -> None:
    model = scripted(
        ToolCallPart("sales_order_list", YEAR_2024),
        present(tables=[{"handle": "r9", "title": "Zamówienia"}]),
        present(tables=[{"handle": "r1", "title": "Zamówienia"}]),
    )

    turn = await service(model).run("Zamówienia z 2024", [], deps)

    (retry,) = retry_prompts(turn.messages)
    assert "unknown handles r9; available handles: r1" in str(retry.content)
    assert [table.handle for table in turn.answer.tables] == ["r1"]


async def test_clarification_status_without_a_question_is_sent_back(
    service: Callable[[FunctionModel], AgentService],
    scripted: Callable[..., FunctionModel],
    deps: AgentDeps,
) -> None:
    question = {
        "question": "Którego klienta Mohamed Pal wybrać?",
        "candidates": [
            {"id": 12698, "description": "AW00012698, Germany"},
            {"id": 20550, "description": "AW00020550, Germany"},
        ],
    }
    model = scripted(
        present("needs_clarification"), present("needs_clarification", clarification=question)
    )

    turn = await service(model).run("Sprzedaż klienta Mohamed Pal", [], deps)

    assert len(retry_prompts(turn.messages)) == 1
    assert turn.answer.clarification is not None
    assert [c.id for c in turn.answer.clarification.candidates] == [12698, 20550]


async def test_text_instead_of_present_result_ends_in_error_after_the_retries(
    service: Callable[[FunctionModel], AgentService], deps: AgentDeps
) -> None:
    def text_only(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("Sprzedaż wyniosła 100.")])

    turn = await service(FunctionModel(text_only)).run("Sprzedaż w 2024", PRIOR_TURN, deps)

    assert turn.answer.status == "error"
    assert turn.messages == tuple(PRIOR_TURN)


async def test_endless_tool_calls_stop_after_ten_requests(
    service: Callable[[FunctionModel], AgentService], deps: AgentDeps
) -> None:
    requests: list[int] = []

    def endless(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        requests.append(len(messages))
        if len(requests) == 1:
            return ModelResponse(parts=[ToolCallPart("sales_order_list", YEAR_2024)])
        return ModelResponse(parts=[ToolCallPart("describe_result", {"handle": "r1"})])

    turn = await service(FunctionModel(endless)).run("Sprzedaż w 2024", PRIOR_TURN, deps)

    assert turn.answer.status == "error"
    assert turn.messages == tuple(PRIOR_TURN)
    assert len(requests) == 10


async def test_procedure_failure_ends_with_a_logged_error_answer(
    service: Callable[[FunctionModel], AgentService],
    scripted: Callable[..., FunctionModel],
    deps: AgentDeps,
    fail_procedures: Callable[[Exception], None],
    caplog: pytest.LogCaptureFixture,
) -> None:
    fail_procedures(ProcedureTimeoutError("the call exceeded the 30 s time limit"))
    model = scripted(
        ToolCallPart("sales_order_list", YEAR_2024),
        present("error", message="Nie udało się pobrać zamówień: przekroczony czas."),
    )

    turn = await service(model).run("Sprzedaż w 2024", [], deps)

    assert turn.answer.status == "error"
    assert deps.context.correlation_id in caplog.text
    assert "Nie udało się pobrać zamówień" in caplog.text


async def test_answer_in_the_same_response_as_a_query_cannot_name_its_result(
    service: Callable[[FunctionModel], AgentService],
    scripted: Callable[..., FunctionModel],
    deps: AgentDeps,
) -> None:
    blind = ModelResponse(
        parts=[
            ToolCallPart("query", {"sql": MONTHLY_SQL}),
            present(tables=[{"handle": "r2", "title": "Sprzedaż"}]),
        ]
    )
    model = scripted(
        ToolCallPart("sales_order_list", YEAR_2024),
        blind,
        present(tables=[{"handle": "r2", "title": "Sprzedaż"}]),
    )

    turn = await service(model).run("Raport miesięczny za 2024", [], deps)

    (retry,) = retry_prompts(turn.messages)
    assert "unknown handles r2" in str(retry.content)
    assert [table.handle for table in turn.answer.tables] == ["r2"]


async def test_dropped_thinking_is_logged_with_the_correlation_id(
    service: Callable[[FunctionModel], AgentService],
    deps: AgentDeps,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def dropped(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[present("no_data")],
            provider_details={"input_transformations": [{"type": "thinking_block_dropped"}]},
        )

    await service(FunctionModel(dropped)).run("Sprzedaż w 2019", [], deps)

    assert "dropped thinking blocks" in caplog.text
    assert deps.context.correlation_id in caplog.text


async def test_instructions_carry_the_sql_guidance_and_no_date(
    instructions: str, catalog: Catalog
) -> None:
    for hint in (
        'CAST(avg("SubTotal") AS DECIMAL(38,6))',
        "NULLIF",
        "CAST(date_trunc('month', \"OrderDate\") AS DATE)",
        "range(DATE '2024-01-01', DATE '2025-01-01', INTERVAL 1 MONTH)",
        "tie-breaking id column",
        "never current_date",
        "in Polish",
        "snake_case",
    ):
        assert hint in instructions
    assert "sales_order_list: Zamówienia sprzedaży" in instructions
    assert "Data from 2022-05-30 to 2025-06-29." in instructions
    assert "sales_order_data_range" not in instructions
    assert date.today().isoformat() not in instructions
    assert "at most 50)" in instructions


async def test_instructions_follow_the_full_preview_setting(
    catalog: Catalog, data_ranges: dict[str, DataRange]
) -> None:
    text = AgentInstructions(catalog, data_ranges, full_preview_max_rows=30).text()

    assert "at most 30)" in text
