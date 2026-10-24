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
from data_concierge.core.settings import ModelDataAccess, ModelExposure, Settings

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
        "candidates_from": {"handle": "r1", "id_column": "CustomerID"},
    }
    model = scripted(
        ToolCallPart("customer_list", {"city": "Berlin"}),
        present("needs_clarification"),
        present("needs_clarification", clarification=question),
    )

    turn = await service(model).run("Sprzedaż klienta Mohamed Pal", [], deps)

    assert len(retry_prompts(turn.messages)) == 1
    assert turn.answer.clarification is not None
    assert turn.answer.clarification.candidates_from is not None
    assert turn.answer.clarification.candidates_from.handle == "r1"


@pytest.mark.parametrize(
    ("candidates_from", "problem"),
    [
        pytest.param(
            {"handle": "r9", "id_column": "CustomerID"},
            "candidates_from: unknown handle r9; available handles: r1, r2",
            id="handle",
        ),
        pytest.param(
            {"handle": "r1", "id_column": "ProductID"},
            "candidates_from: r1 has no column ProductID; columns: CustomerID, AccountNumber",
            id="column",
        ),
        pytest.param(
            {"handle": "r2", "id_column": "CustomerID"},
            "candidates_from: r2 has 51 rows; at most 50 can be candidates",
            id="rows",
        ),
        pytest.param(
            {"handle": "r2", "id_column": "first_order"},
            "candidates_from: id_column must be an int or a string column",
            id="type",
        ),
        pytest.param(
            {"handle": "r1", "id_column": "AccountNumber"},
            "candidates_from: AccountNumber is not shown to you; use a technical key",
            id="sensitive",
        ),
    ],
)
async def test_candidates_from_a_wrong_handle_are_sent_back(
    service: Callable[[FunctionModel], AgentService],
    scripted: Callable[..., FunctionModel],
    deps: AgentDeps,
    candidates_from: dict[str, str],
    problem: str,
) -> None:
    question = {"question": "Którego klienta wybrać?", "candidates_from": candidates_from}
    model = scripted(
        ToolCallPart("customer_list", {"city": "Berlin"}),
        ToolCallPart(
            "query",
            {
                "sql": "SELECT \"CustomerID\", DATE '2024-01-01' AS first_order "
                "FROM r1, range(25) UNION ALL SELECT 1, DATE '2024-01-01' ORDER BY 1"
            },
        ),
        present("needs_clarification", clarification=question),
        present("no_data"),
    )

    turn = await service(model).run("Sprzedaż klienta Mohamed Pal", [], deps)

    (retry,) = retry_prompts(turn.messages)
    assert problem in str(retry.content)


TOTAL_SQL = 'SELECT CAST(sum("SubTotal") AS DECIMAL(38,2)) AS net_sales FROM r1'


async def test_wrong_anchors_in_every_text_are_sent_back_in_one_correction(
    service: Callable[[FunctionModel], AgentService],
    scripted: Callable[..., FunctionModel],
    deps: AgentDeps,
) -> None:
    question = {"question": "Który z {r7#rows} okresów?"}
    model = scripted(
        ToolCallPart("sales_order_list", YEAR_2024),
        ToolCallPart("query", {"sql": TOTAL_SQL}),
        present(
            message="Suma: {r1.SubTotal}, zamówień: {r1#rows}.",
            tables=[{"handle": "r2", "title": "Sprzedaż {r9.total}"}],
            assumptions=["okres {r1[4].OrderDate}"],
            clarification=question,
        ),
        present(
            message="Suma: {r2.net_sales}, zamówień: {r1#rows}.",
            tables=[{"handle": "r2", "title": "Sprzedaż netto"}],
        ),
    )

    turn = await service(model).run("Sprzedaż w 2024", [], deps)

    (retry,) = retry_prompts(turn.messages)
    for problem in (
        "{r1.SubTotal}: r1 has 3 rows",
        "{r9.total}: unknown handle r9",
        "{r1[4].OrderDate}: r1 has 3 rows",
        "{r7#rows}: unknown handle r7",
    ):
        assert problem in str(retry.content)
    assert turn.answer.message == "Suma: {r2.net_sales}, zamówień: {r1#rows}."
    # The history keeps the template, never the value.
    assert "{r2.net_sales}" in str(turn.messages[-2])
    assert "159,75" not in str(turn.messages)


async def test_error_answer_is_not_checked_for_anchors(
    service: Callable[[FunctionModel], AgentService],
    scripted: Callable[..., FunctionModel],
    deps: AgentDeps,
) -> None:
    model = scripted(present("error", message="Nie udało się: {r9.total}."))

    turn = await service(model).run("Sprzedaż w 2024", [], deps)

    assert turn.answer.status == "error"
    assert not retry_prompts(turn.messages)


async def test_anchors_still_wrong_after_the_retries_end_the_turn_with_an_error(
    service: Callable[[FunctionModel], AgentService],
    scripted: Callable[..., FunctionModel],
    deps: AgentDeps,
) -> None:
    wrong = present(message="Suma: {r9.total}.")
    model = scripted(wrong, wrong, wrong, wrong)

    turn = await service(model).run("Sprzedaż w 2024", PRIOR_TURN, deps)

    assert turn.answer.status == "error"
    assert turn.messages == tuple(PRIOR_TURN)


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


@pytest.mark.parametrize("mode", ["none", "minimal"])
async def test_instructions_of_both_modes_teach_anchors_candidates_and_amount_scale(
    catalog: Catalog, data_ranges: dict[str, DataRange], mode: ModelDataAccess
) -> None:
    text = AgentInstructions(catalog, data_ranges, ModelExposure.for_mode(mode)).text()

    for hint in ("{r3.net_sales}", "{r2[1].month}", "{r2#rows}", "candidates_from"):
        assert hint in text
    assert "cast amounts, averages and ratios to DECIMAL(38,2)" in text
    assert "{r4[1].CustomerName}" in text
    assert "CAST((SELECT sum(net_sales) FROM r2) AS DECIMAL(38,2)) AS annual_total" in text
    assert "at most 50 rows" in text
    assert "preview" not in text


async def test_instructions_of_mode_none_promise_no_values(
    catalog: Catalog, data_ranges: dict[str, DataRange]
) -> None:
    text = AgentInstructions(catalog, data_ranges, ModelExposure.for_mode("none")).text()

    assert "You never see names, amounts or quantities" in text
    assert "PIVOT only over a dimension whose values the procedure description lists" in text
    assert "dimension values" not in text
    assert "statistics" not in text


async def test_instructions_of_mode_minimal_name_the_dimension_values(
    catalog: Catalog, data_ranges: dict[str, DataRange]
) -> None:
    text = AgentInstructions(catalog, data_ranges, ModelExposure.for_mode("minimal")).text()

    assert "the values of dimension columns with at most 20 distinct values" in text
    assert "or from the dimension values of the handle" in text
    assert "You never see names, amounts or quantities" not in text


async def test_instructions_follow_the_model_row_limit(
    catalog: Catalog, data_ranges: dict[str, DataRange]
) -> None:
    text = AgentInstructions(
        catalog, data_ranges, ModelExposure.for_mode("none"), model_max_rows=30
    ).text()

    assert "at most 30 rows" in text
