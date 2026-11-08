"""ChartPolicy: a chart the model asks for is drawable from its table (plan 3B, KTD3, KTD4)."""

from datetime import date
from decimal import Decimal

import pyarrow as pa
import pytest

from data_concierge.agent.charts import ChartPolicy
from data_concierge.agent.models import AgentAnswer, ChartRequest, TableRef
from data_concierge.api.schemas import AnswerStatus, ChartKind
from data_concierge.core.types import Column, ColumnRole, ColumnType, TableSchema
from data_concierge.query.policy import QueryPolicy
from data_concierge.query.service import QueryService
from data_concierge.results.models import ProcedureLineage, ResultTable
from data_concierge.results.repository import DuckDBResultStore
from data_concierge.results.service import ResultService

MAX_ROWS = 10_000
# A value that must never appear in a message to the model (architecture 002, section 6.2).
SECRET = "Tajny Sklep Sp. z o.o."

MONTHS_SQL = (
    "SELECT CAST(range AS DATE) AS month, CAST(1000 * month(range) AS DECIMAL(38,2)) AS "
    "net_sales, CAST(month(range) AS INTEGER) AS order_count, 'm' || month(range) AS label "
    "FROM range(DATE '2024-01-01', DATE '2025-01-01', INTERVAL 1 MONTH) ORDER BY month"
)
CATEGORIES_SQL = (
    "SELECT * FROM (VALUES ('Bikes', 10.50), ('Components', 4.25), ('Clothing', 1.00), "
    f"('Accessories', 0.75), ('{SECRET}', 0.10)) AS t(category, net_sales) ORDER BY category"
)
LONG_SQL = (
    "SELECT CAST(range AS DATE) AS month, 'c' || c AS category, CAST(c AS DECIMAL(38,2)) AS "
    "net_sales FROM range(DATE '2024-01-01', DATE '2025-01-01', INTERVAL 1 MONTH), "
    "range(13) AS r(c) ORDER BY month, category"
)
MANY_CATEGORIES_SQL = (
    "SELECT 'k' || range AS category, CAST(range AS DECIMAL(38,2)) AS net_sales "
    "FROM range(60) ORDER BY category"
)
MANY_POINTS_SQL = (
    "SELECT range AS n, CAST(range AS DECIMAL(38,2)) AS net_sales FROM range(1001) ORDER BY n"
)
ORDERS_SCHEMA = TableSchema(
    columns=(
        Column(name="OrderDate", type=ColumnType.parse("date"), role=ColumnRole.TIME),
        Column(name="SubTotal", type=ColumnType.decimal(19, 4), role=ColumnRole.MEASURE),
    )
)


@pytest.fixture
def results() -> ResultService:
    """r1 months, r2 categories, r3 months x 13 categories, r4 60 categories, r5 1001 points,
    r6 orders cut at the procedure's row limit."""
    service = ResultService(DuckDBResultStore())
    queries = QueryService(service, QueryPolicy())
    for sql in (MONTHS_SQL, CATEGORIES_SQL, LONG_SQL, MANY_CATEGORIES_SQL, MANY_POINTS_SQL):
        queries.run(sql)
    orders = pa.table(
        {
            "OrderDate": pa.array([date(2024, 1, 1)], type=pa.date32()),
            "SubTotal": pa.array([Decimal("1.0000")], type=pa.decimal128(19, 4)),
        }
    )
    lineage = ProcedureLineage(procedure="api.SalesOrder_List", params={}, truncated=True)
    service.save(ResultTable(data=orders, schema=ORDERS_SCHEMA, lineage=lineage))
    return service


def answer(
    kind: ChartKind,
    handle: str,
    x: str,
    y: tuple[str, ...],
    series: str | None = None,
    status: AnswerStatus = "ok",
    tables: tuple[str, ...] | None = None,
) -> AgentAnswer:
    returned = (handle,) if tables is None else tables
    return AgentAnswer(
        status=status,
        message="Gotowe.",
        tables=tuple(TableRef(handle=h, title="Tabela") for h in returned),
        chart=ChartRequest(handle=handle, kind=kind, x=x, y=y, series=series, title="Wykres"),
    )


def problems(results: ResultService, chart_answer: AgentAnswer) -> list[str]:
    return ChartPolicy(results, MAX_ROWS).problems(chart_answer)


@pytest.mark.parametrize(
    "chart_answer",
    [
        pytest.param(answer("line", "r1", "month", ("net_sales",)), id="line"),
        pytest.param(answer("line", "r1", "MONTH", ("net_sales", "order_count")), id="two-y"),
        pytest.param(answer("bar", "r2", "category", ("net_sales",)), id="bar"),
        pytest.param(answer("pie", "r2", "category", ("net_sales",)), id="pie"),
        pytest.param(answer("scatter", "r1", "order_count", ("net_sales",)), id="scatter"),
        pytest.param(
            answer("stacked_bar", "r1", "month", ("net_sales", "order_count")), id="stacked-y"
        ),
        pytest.param(answer("bar", "r1", "label", ("order_count",)), id="text-x"),
    ],
)
def test_a_drawable_chart_has_no_problems(
    results: ResultService, chart_answer: AgentAnswer
) -> None:
    assert problems(results, chart_answer) == []


def test_an_answer_without_a_chart_has_no_problems(results: ResultService) -> None:
    plain = AgentAnswer(status="ok", message="Gotowe.", tables=(TableRef(handle="r1", title="T"),))

    assert problems(results, plain) == []


@pytest.mark.parametrize(
    ("chart_answer", "expected"),
    [
        pytest.param(
            answer("line", "r1", "month", ("net_sales",), status="needs_clarification"),
            "chart: only with status ok",
            id="status",
        ),
        pytest.param(
            answer("line", "r1", "month", ("net_sales",), tables=("r2",)),
            "chart: r1 is not one of the returned tables (r2)",
            id="not-returned",
        ),
        pytest.param(
            answer("line", "r1", "month", ("sales",)),
            "chart: r1 has no column sales; columns: month, net_sales, order_count, label",
            id="no-column",
        ),
        pytest.param(
            answer("line", "r1", "month", ("label",)),
            "chart: y column label must be numeric (int or decimal)",
            id="text-y",
        ),
        pytest.param(
            answer("pie", "r1", "label", ("net_sales",)),
            "chart: pie needs at most 8 rows, r1 has 12; aggregate the rest in query or choose bar",
            id="pie-rows",
        ),
        pytest.param(
            answer("pie", "r2", "category", ("net_sales", "net_sales")),
            "chart: pie needs exactly one y column",
            id="pie-two-y",
        ),
        pytest.param(
            answer("pie", "r3", "month", ("net_sales",)),
            "chart: pie needs a text x column, month is date",
            id="pie-date-x",
        ),
        pytest.param(
            answer("bar", "r4", "category", ("net_sales",)),
            "chart: bar needs at most 50 categories in category, r4 has 60; aggregate in query",
            id="bar-categories",
        ),
        pytest.param(
            answer("line", "r3", "month", ("net_sales",), series="category"),
            "chart: at most 12 series, category has 13 values",
            id="series-count",
        ),
        pytest.param(
            answer("line", "r1", "month", ("net_sales", "order_count"), series="label"),
            "chart: with series, give exactly one y column",
            id="series-two-y",
        ),
        pytest.param(
            answer("stacked_bar", "r2", "category", ("net_sales",)),
            "chart: stacked_bar needs series or at least two y columns",
            id="stacked-one-y",
        ),
        pytest.param(
            answer("scatter", "r1", "label", ("net_sales",)),
            "chart: scatter needs a numeric x column, label is string",
            id="scatter-text-x",
        ),
        pytest.param(
            answer("line", "r5", "n", ("net_sales",)),
            "chart: line needs at most 1000 rows, r5 has 1001; aggregate in query",
            id="line-points",
        ),
        pytest.param(
            answer("line", "r6", "OrderDate", ("SubTotal",)),
            "chart: r6 is cut at a row limit, so a chart would miss data; aggregate it in query "
            "first (e.g. by month) and chart that handle",
            id="truncated",
        ),
    ],
)
def test_an_undrawable_chart_is_described_for_the_model(
    results: ResultService, chart_answer: AgentAnswer, expected: str
) -> None:
    assert expected in problems(results, chart_answer)


def test_a_table_over_the_response_limit_has_no_chart(results: ResultService) -> None:
    found = ChartPolicy(results, max_rows=10).problems(
        answer("line", "r1", "month", ("net_sales",))
    )

    assert found == [
        "chart: r1 is cut at a row limit, so a chart would miss data; aggregate it in query "
        "first (e.g. by month) and chart that handle"
    ]


def test_messages_never_carry_values_from_the_data(results: ResultService) -> None:
    found = problems(results, answer("pie", "r2", "category", ("category",), series="category"))

    assert found
    assert not any(SECRET in problem for problem in found)
