"""Anchors in texts for the user and the formatting of the values they insert (plan U4)."""

import pytest

from data_concierge.agent.anchors import Anchors
from data_concierge.agent.formatting import ValueFormatter
from data_concierge.core.errors import AnchorError
from data_concierge.core.types import ColumnRole, ColumnType
from data_concierge.query.policy import QueryPolicy
from data_concierge.query.service import QueryService
from data_concierge.results.models import JsonScalar
from data_concierge.results.repository import DuckDBResultStore
from data_concierge.results.service import ResultService

NBSP = "\N{NO-BREAK SPACE}"
TOTALS_SQL = (
    "SELECT CAST(32145678.12 AS DECIMAL(38,2)) AS annual_total, "
    "count(*) AS row_count, NULL::INT AS missing"
)
MONTHS_SQL = (
    "SELECT CAST(range AS DATE) AS month, CAST(1000 * month(range) AS DECIMAL(38,2)) AS net_sales "
    "FROM range(DATE '2024-01-01', DATE '2025-01-01', INTERVAL 1 MONTH) ORDER BY month"
)


@pytest.fixture
def anchors() -> Anchors:
    """r1: one row of totals; r2: twelve months of 2024."""
    results = ResultService(DuckDBResultStore())
    queries = QueryService(results, QueryPolicy())
    queries.run(TOTALS_SQL)
    queries.run(MONTHS_SQL)
    return Anchors(results)


@pytest.mark.parametrize(
    ("value", "column_type", "role", "text"),
    [
        pytest.param(
            "32145678.12",
            "decimal(38,2)",
            ColumnRole.MEASURE,
            "32\N{NO-BREAK SPACE}145\N{NO-BREAK SPACE}678,12",
            id="sum",
        ),
        pytest.param("1234.5000", "decimal(38,4)", ColumnRole.MEASURE, "1234,5000", id="short"),
        pytest.param(
            "-12345.50",
            "decimal(38,2)",
            ColumnRole.MEASURE,
            "-12\N{NO-BREAK SPACE}345,50",
            id="neg",
        ),
        pytest.param(14244, "int", ColumnRole.MEASURE, "14\N{NO-BREAK SPACE}244", id="int"),
        pytest.param(2024, "int", ColumnRole.MEASURE, "2024", id="year"),
        pytest.param(12650, "int", ColumnRole.ID, "12650", id="id"),
        pytest.param("2024-06-01", "date", ColumnRole.TIME, "2024-06-01", id="date"),
        pytest.param(None, "int", ColumnRole.MEASURE, "brak danych", id="null"),
        pytest.param(True, "bool", ColumnRole.DIMENSION, "tak", id="true"),
        pytest.param(False, "bool", ColumnRole.DIMENSION, "nie", id="false"),
        pytest.param("Bikes", "string", ColumnRole.DIMENSION, "Bikes", id="text"),
    ],
)
def test_values_are_formatted_by_column_type_and_role(
    value: JsonScalar, column_type: str, role: ColumnRole, text: str
) -> None:
    formatted = ValueFormatter().format(value, ColumnType.parse(column_type), role)

    assert formatted == text


def test_cell_of_a_one_row_handle_is_inserted(anchors: Anchors) -> None:
    filled = anchors.fill("Sprzedaż netto w 2024: {r1.annual_total}.")

    assert filled == f"Sprzedaż netto w 2024: 32{NBSP}145{NBSP}678,12."


def test_row_cell_and_row_count_are_inserted(anchors: Anchors) -> None:
    filled = anchors.fill("Szósty miesiąc: {r2[6].month}, miesięcy: {r2#rows}.")

    assert filled == "Szósty miesiąc: 2024-06-01, miesięcy: 12."


def test_a_column_named_row_count_is_not_the_row_count(anchors: Anchors) -> None:
    assert anchors.fill("{r1.row_count} i {r1#rows}") == "1 i 1"
    assert anchors.fill("{r1.missing}") == "brak danych"


def test_column_name_is_matched_regardless_of_case(anchors: Anchors) -> None:
    assert anchors.fill("{r2[1].NET_SALES}") == "1000,00"


def test_text_outside_the_grammar_stays_as_it_is(anchors: Anchors) -> None:
    text = "Wynik {nie kotwica} i {rok 2024} bez zmian."

    assert anchors.problems(text) == []
    assert anchors.fill(text) == text


def test_every_wrong_anchor_is_described_with_the_handle_columns(anchors: Anchors) -> None:
    problems = anchors.problems(
        "{r2.net_sales} {r9.total} {r2[13].month} {r2.Netsales} {r1.annual_total}"
    )

    assert problems == [
        "{r2.net_sales}: r2 has 12 rows; for one of them name the row, e.g. "
        "{r2[1].net_sales}; for a total or a best value run a query that returns it in one row "
        "and anchor that handle",
        "{r9.total}: unknown handle r9; available handles: r1, r2",
        "{r2[13].month}: r2 has 12 rows",
        "{r2.Netsales}: r2 has no column Netsales; columns: month, net_sales",
    ]


def test_row_count_is_formatted_like_other_numbers() -> None:
    results = ResultService(DuckDBResultStore())
    queries = QueryService(results, QueryPolicy())
    queries.run("SELECT range AS n FROM range(12345)")
    queries.run("SELECT 1 AS n WHERE false")

    assert Anchors(results).fill("{r1#rows}") == f"12{NBSP}345"
    assert Anchors(results).problems("{r2.n} {r2[1].n}") == [
        "{r2.n}: r2 has no rows",
        "{r2[1].n}: r2 has no rows",
    ]


@pytest.mark.parametrize("text", ["{r1#count}", "{ r1.annual_total }", "{R1.x}", "{r2}"])
def test_attempted_anchor_outside_the_grammar_is_a_problem(anchors: Anchors, text: str) -> None:
    assert anchors.problems(f"Wynik: {text}.") == [
        f"{text}: not an anchor; write {{rN.column}}, {{rN[1].column}} or {{rN#rows}}"
    ]


def test_filling_a_wrong_anchor_raises(anchors: Anchors) -> None:
    with pytest.raises(AnchorError, match="unknown handle r9"):
        anchors.fill("{r9.total}")


def test_handles_named_in_anchors_are_listed_once(anchors: Anchors) -> None:
    assert anchors.handles("{r2#rows} {r1.annual_total} {r2[1].month} {nie}") == {"r1", "r2"}
