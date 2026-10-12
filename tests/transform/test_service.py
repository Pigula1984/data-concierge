"""TransformService on a real locked-down DuckDB store; expected values are computed in Python."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

import pyarrow as pa
import pytest

from data_concierge.core.errors import SpecValidationError
from data_concierge.core.types import Column, ColumnRole, ColumnType, TableSchema
from data_concierge.results.models import ProcedureLineage, ResultTable, TransformLineage
from data_concierge.results.repository import DuckDBResultStore
from data_concierge.results.service import ResultService
from data_concierge.transform.executor import DuckDBExecutor
from data_concierge.transform.service import TransformService, TransformSummary
from data_concierge.transform.spec import MAX_PIVOT_COLUMNS, TransformSpec
from data_concierge.transform.validator import SpecValidator

type Order = tuple[int, date, str, str, Decimal]

ORDERS_SCHEMA = TableSchema(
    columns=(
        Column(name="SalesOrderID", type=ColumnType.parse("int"), role=ColumnRole.ID),
        Column(name="OrderDate", type=ColumnType.parse("date"), role=ColumnRole.TIME),
        Column(name="CustomerName", type=ColumnType.parse("string"), role=ColumnRole.DIMENSION),
        Column(name="Territory", type=ColumnType.parse("string"), role=ColumnRole.DIMENSION),
        Column(name="SubTotal", type=ColumnType.decimal(19, 4), role=ColumnRole.MEASURE),
    )
)
TERRITORIES = ("Northwest", "Canada", "France")
MONTHLY_REPORT: dict[str, Any] = {
    "steps": [
        {
            "op": "derive",
            "name": "month",
            "fn": "date_trunc",
            "unit": "month",
            "column": "OrderDate",
        },
        {
            "op": "aggregate",
            "group_by": ["month"],
            "measures": [
                {"name": "net_sales", "fn": "sum", "column": "SubTotal"},
                {"name": "order_count", "fn": "count_distinct", "column": "SalesOrderID"},
            ],
        },
        {
            "op": "fill_periods",
            "column": "month",
            "unit": "month",
            "from": "2024-01-01",
            "to": "2025-01-01",
            "fill": {"net_sales": 0, "order_count": 0},
        },
        {"op": "sort", "by": [{"column": "month", "dir": "asc"}]},
    ],
    "facts": [
        {"name": "annual_total", "fn": "sum", "column": "net_sales"},
        {"name": "best_month", "fn": "argmax", "column": "net_sales", "return": "month"},
    ],
}


def _orders_2024() -> list[Order]:
    """Orders in every month of 2024 except February and December."""
    orders: list[Order] = []
    for month in (1, 3, 4, 5, 6, 7, 8, 9, 10, 11):
        for day in (3, 17, 28):
            number = len(orders) + 1
            amount = Decimal(number * 1013 % 9973) + Decimal("0.0125") * number
            territory = TERRITORIES[number % 3]
            orders.append(
                (number, date(2024, month, day), f"Customer {number % 7}", territory, amount)
            )
    return orders


def _table(
    orders: Sequence[tuple[object, ...]],
    schema: TableSchema = ORDERS_SCHEMA,
    truncated: bool = False,
) -> ResultTable:
    columns = list(zip(*orders, strict=True))
    data = pa.table(
        [
            pa.array(values, type=column.type.arrow_type)
            for values, column in zip(columns, schema.columns, strict=True)
        ],
        names=list(schema.names),
    )
    lineage = ProcedureLineage(procedure="api.SalesOrder_List", params={}, truncated=truncated)
    return ResultTable(data=data, schema=schema, lineage=lineage)


class Session:
    def __init__(self, max_pivot_columns: int = MAX_PIVOT_COLUMNS) -> None:
        self.store = DuckDBResultStore()
        self.results = ResultService(self.store)
        self.service = TransformService(
            self.results,
            DuckDBExecutor(self.results, max_pivot_columns=max_pivot_columns),
            SpecValidator(max_pivot_columns=max_pivot_columns),
        )

    def save(self, table: ResultTable) -> str:
        return self.results.save(table)

    def apply(self, source: str, **spec: Any) -> TransformSummary:
        return self.service.apply(TransformSpec.model_validate({"source": source, **spec}))

    def rows(self, handle: str) -> list[tuple[Any, ...]]:
        return [tuple(row.values()) for row in self.store.fetch(handle).to_pylist()]

    def lineage(self, handle: str) -> TransformLineage:
        lineage = self.store.describe(handle).lineage
        assert isinstance(lineage, TransformLineage)
        return lineage


def test_monthly_report_has_twelve_months_with_exact_sums_and_zero_gaps() -> None:
    orders = _orders_2024()
    expected: dict[date, Decimal] = defaultdict(Decimal)
    for _, day, _, _, amount in orders:
        expected[day.replace(day=1)] += amount
    session = Session()

    summary = session.apply(session.save(_table(orders)), **MONTHLY_REPORT)

    rows = session.rows(summary.result.handle)
    assert [row[0] for row in rows] == [date(2024, month, 1) for month in range(1, 13)]
    assert {row[0]: row[1] for row in rows} == {
        date(2024, m, 1): expected.get(date(2024, m, 1), Decimal("0.0000")) for m in range(1, 13)
    }
    assert rows[1][1:] == (Decimal("0.0000"), 0)
    assert rows[11][1:] == (Decimal("0.0000"), 0)
    assert summary.facts["annual_total"] == sum(expected.values())
    assert summary.facts["best_month"] == max(expected, key=lambda month: expected[month])


def test_monthly_report_sql_quotes_identifiers_and_passes_dates_as_parameters() -> None:
    session = Session()
    summary = session.apply(session.save(_table(_orders_2024())), **MONTHLY_REPORT)

    query = session.lineage(summary.result.handle).queries[-1]

    assert '"OrderDate"' in query.sql
    assert "2024-01-01" not in query.sql
    assert "2024-01-01" in query.params
    assert "2025-01-01" in query.params


def test_same_spec_on_reversed_rows_gives_identical_result() -> None:
    orders = _orders_2024()
    session = Session()

    first = session.apply(session.save(_table(orders)), **MONTHLY_REPORT)
    second = session.apply(session.save(_table(list(reversed(orders)))), **MONTHLY_REPORT)

    assert session.rows(first.result.handle) == session.rows(second.result.handle)


def test_without_sort_rows_are_ordered_by_group_keys_then_remaining_columns() -> None:
    session = Session()
    aggregate = {
        "op": "aggregate",
        "group_by": ["Territory"],
        "measures": [{"name": "net_sales", "fn": "sum", "column": "SubTotal"}],
    }

    summary = session.apply(session.save(_table(_orders_2024())), steps=[aggregate])

    sql = session.lineage(summary.result.handle).queries[-1].sql
    assert sql.endswith('ORDER BY "Territory" ASC, "net_sales" ASC')
    assert [row[0] for row in session.rows(summary.result.handle)] == sorted(TERRITORIES)


def test_column_name_with_quote_and_semicolon_does_not_change_the_sql_structure() -> None:
    odd = 'Amount"; DROP TABLE r1; --'
    schema = TableSchema(
        columns=(Column(name=odd, type=ColumnType.decimal(19, 4), role=ColumnRole.MEASURE),)
    )
    session = Session()
    source = session.save(_table([(Decimal("1.5000"),), (Decimal("2.2500"),)], schema))

    summary = session.apply(
        source,
        steps=[{"op": "aggregate", "measures": [{"name": "total", "fn": "sum", "column": odd}]}],
    )

    assert session.rows(summary.result.handle) == [(Decimal("3.7500"),)]
    assert session.rows(source) == [(Decimal("1.5000"),), (Decimal("2.2500"),)]


def test_pivot_month_by_territory_has_a_column_per_territory_and_zero_gaps() -> None:
    orders = _orders_2024()
    expected: dict[tuple[date, str], Decimal] = defaultdict(Decimal)
    for _, day, _, territory, amount in orders:
        expected[(day.replace(day=1), territory)] += amount
    session = Session()
    pivot_steps = [
        MONTHLY_REPORT["steps"][0],
        {"op": "pivot", "rows": "month", "columns": "Territory", "measure": "SubTotal"},
    ]

    summary = session.apply(session.save(_table(orders)), steps=pivot_steps)

    names = [column.name for column in summary.result.columns]
    assert names == ["month", *sorted(TERRITORIES)]
    for row in session.rows(summary.result.handle):
        month, *cells = row
        assert cells == [expected.get((month, t), Decimal("0.0000")) for t in sorted(TERRITORIES)]


def test_pivot_values_with_quotes_and_semicolons_become_columns_through_parameters() -> None:
    names = ("Margie's Travel", 'Say "Hi" Bikes', "A; B")
    orders = [(i, date(2024, 1, 1 + i), names[i % 3], "T", Decimal(i)) for i in range(6)]
    session = Session()

    summary = session.apply(
        session.save(_table(orders)),
        steps=[
            {"op": "pivot", "rows": "OrderDate", "columns": "CustomerName", "measure": "SubTotal"}
        ],
    )

    assert [column.name for column in summary.result.columns][1:] == sorted(names)
    final = session.lineage(summary.result.handle).queries[-1]
    # Values are compared through parameters; names become quoted identifiers only.
    assert final.sql.count('"CustomerName" = ?') == 3
    assert "'Margie" not in final.sql
    assert '"Say ""Hi"" Bikes"' in final.sql
    assert set(names) <= set(final.params)


def test_pivot_by_derived_year_records_values_and_final_query() -> None:
    orders = [*_orders_2024(), (999, date(2025, 3, 1), "Customer 1", "Canada", Decimal("10.0000"))]
    session = Session()

    summary = session.apply(
        session.save(_table(orders)),
        steps=[
            {"op": "derive", "name": "year", "fn": "year", "column": "OrderDate"},
            {"op": "pivot", "rows": "Territory", "columns": "year", "measure": "SubTotal"},
        ],
    )

    assert [column.name for column in summary.result.columns] == ["Territory", "2024", "2025"]
    queries = session.lineage(summary.result.handle).queries
    assert len(queries) == 2
    assert "SELECT DISTINCT" in queries[0].sql


def test_pivot_with_more_values_than_the_limit_is_rejected() -> None:
    session = Session(max_pivot_columns=5)

    with pytest.raises(SpecValidationError, match="more than 5 values"):
        session.apply(
            session.save(_table(_orders_2024())),
            steps=[
                {
                    "op": "derive",
                    "name": "day",
                    "fn": "date_trunc",
                    "unit": "day",
                    "column": "OrderDate",
                },
                {"op": "pivot", "rows": "Territory", "columns": "day", "measure": "SubTotal"},
            ],
        )


def test_sort_descending_with_limit_gives_the_top_customers() -> None:
    orders = _orders_2024()
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for _, _, customer, _, amount in orders:
        totals[customer] += amount
    session = Session()

    summary = session.apply(
        session.save(_table(orders)),
        steps=[
            {
                "op": "aggregate",
                "group_by": ["CustomerName"],
                "measures": [{"name": "net_sales", "fn": "sum", "column": "SubTotal"}],
            },
            {"op": "sort", "by": [{"column": "net_sales", "dir": "desc"}]},
            {"op": "limit", "n": 3},
        ],
    )

    top = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:3]
    assert session.rows(summary.result.handle) == top


def test_ties_in_sort_with_limit_give_identical_rows_for_any_input_order() -> None:
    orders = [(i, date(2024, 1, 1), f"Customer {i}", "T", Decimal("5.0000")) for i in range(1, 9)]
    spec: dict[str, Any] = {
        "steps": [
            {"op": "sort", "by": [{"column": "SubTotal", "dir": "desc"}]},
            {"op": "limit", "n": 3},
        ]
    }
    session = Session()

    first = session.apply(session.save(_table(orders)), **spec)
    second = session.apply(session.save(_table(list(reversed(orders)))), **spec)

    assert session.rows(first.result.handle) == session.rows(second.result.handle)
    assert [row[0] for row in session.rows(first.result.handle)] == [1, 2, 3]


def test_argmax_tie_returns_the_earlier_month() -> None:
    orders = [
        (1, date(2024, 3, 5), "A", "T", Decimal("7.0000")),
        (2, date(2024, 1, 9), "B", "T", Decimal("7.0000")),
        (3, date(2024, 2, 1), "C", "T", Decimal("1.0000")),
    ]
    session = Session()

    summary = session.apply(
        session.save(_table(orders)),
        steps=MONTHLY_REPORT["steps"][:2],
        facts=[{"name": "best_month", "fn": "argmax", "column": "net_sales", "return": "month"}],
    )

    assert summary.facts["best_month"] == date(2024, 1, 1)


def test_avg_fact_has_scale_six() -> None:
    orders = [(i, date(2024, 1, 1), "A", "T", Decimal(i)) for i in (1, 2, 4)]
    session = Session()

    summary = session.apply(
        session.save(_table(orders)),
        facts=[{"name": "avg_order", "fn": "avg", "column": "SubTotal"}],
    )

    assert summary.facts["avg_order"] == Decimal("2.333333")
    assert str(summary.facts["avg_order"]) == "2.333333"


PIVOT_BY_TERRITORY = {
    "op": "pivot",
    "rows": "OrderDate",
    "columns": "Territory",
    "measure": "SubTotal",
}


@pytest.mark.parametrize(
    ("spec", "operation"),
    [
        ({"steps": MONTHLY_REPORT["steps"][:2]}, "aggregate"),
        ({"steps": MONTHLY_REPORT["steps"]}, "fill_periods"),
        ({"steps": [PIVOT_BY_TERRITORY]}, "pivot"),
        ({"facts": [{"name": "orders", "fn": "count"}]}, "facts"),
    ],
)
def test_aggregating_a_truncated_result_asks_to_narrow_the_procedure(
    spec: dict[str, Any], operation: str
) -> None:
    session = Session()
    source = session.save(_table(_orders_2024(), truncated=True))
    derived = session.apply(source, steps=[{"op": "sort", "by": [{"column": "OrderDate"}]}])

    for handle in (source, derived.result.handle):
        with pytest.raises(SpecValidationError, match=f"{operation}.*narrower parameters"):
            session.apply(handle, **spec)


def test_non_aggregating_steps_on_a_truncated_result_keep_the_flag() -> None:
    session = Session()
    source = session.save(_table(_orders_2024(), truncated=True))

    sorted_result = session.apply(source, steps=[{"op": "sort", "by": [{"column": "OrderDate"}]}])
    assert sorted_result.result.truncated is True


def test_division_by_zero_gives_null() -> None:
    schema = TableSchema(
        columns=(
            Column(name="Amount", type=ColumnType.decimal(19, 4), role=ColumnRole.MEASURE),
            Column(name="Count", type=ColumnType.parse("int"), role=ColumnRole.MEASURE),
        )
    )
    session = Session()
    source = session.save(_table([(Decimal("10.0000"), 4), (Decimal("3.0000"), 0)], schema))

    summary = session.apply(
        source,
        steps=[
            {
                "op": "derive",
                "name": "per_item",
                "fn": "arithmetic",
                "operator": "/",
                "left": "Amount",
                "right": "Count",
            }
        ],
    )

    assert session.rows(summary.result.handle) == [
        (Decimal("3.0000"), 0, None),
        (Decimal("10.0000"), 4, Decimal("2.500000")),
    ]


def test_lineage_holds_source_spec_and_sql() -> None:
    session = Session()
    source = session.save(_table(_orders_2024()))

    summary = session.apply(source, **MONTHLY_REPORT)

    lineage = session.lineage(summary.result.handle)
    assert lineage.source == source
    assert lineage.spec["steps"][2]["from"] == "2024-01-01"  # type: ignore[index, call-overload]
    assert lineage.queries[-1].sql.startswith("WITH s1 AS")
    assert [handle for handle, _ in session.results.lineage_chain(summary.result.handle)] == [
        source,
        summary.result.handle,
    ]


def test_select_keeps_renames_and_orders_columns() -> None:
    orders = [
        (2, date(2024, 1, 2), "Bea", "T", Decimal("2.0000")),
        (1, date(2024, 1, 1), "Al", "T", Decimal("1.0000")),
    ]
    session = Session()

    summary = session.apply(
        session.save(_table(orders)),
        steps=[
            {"op": "sort", "by": [{"column": "SubTotal", "dir": "desc"}]},
            {
                "op": "select",
                "columns": [{"column": "SubTotal", "alias": "amount"}, {"column": "CustomerName"}],
            },
        ],
    )

    assert [column.name for column in summary.result.columns] == ["amount", "CustomerName"]
    assert session.rows(summary.result.handle) == [
        (Decimal("2.0000"), "Bea"),
        (Decimal("1.0000"), "Al"),
    ]


def test_count_min_and_max_facts() -> None:
    orders = _orders_2024()
    session = Session()

    summary = session.apply(
        session.save(_table(orders)),
        facts=[
            {"name": "orders", "fn": "count"},
            {"name": "first_order", "fn": "min", "column": "OrderDate"},
            {"name": "largest", "fn": "max", "column": "SubTotal"},
        ],
    )

    assert summary.facts == {
        "orders": len(orders),
        "first_order": min(order[1] for order in orders),
        "largest": max(order[4] for order in orders),
    }


@pytest.mark.parametrize(
    "columns",
    [
        [{"column": "SubTotal"}, {"column": "SubTotal", "alias": "amount"}],
        [{"column": "SubTotal", "alias": "net"}, {"column": "SubTotal", "alias": "gross"}],
    ],
)
def test_select_can_repeat_a_source_column(columns: list[dict[str, str]]) -> None:
    orders = [(1, date(2024, 1, 1), "Al", "T", Decimal("1.5000"))]
    session = Session()

    summary = session.apply(
        session.save(_table(orders)), steps=[{"op": "select", "columns": columns}]
    )

    assert len(summary.result.columns) == 2
    assert session.rows(summary.result.handle) == [(Decimal("1.5000"), Decimal("1.5000"))]


def test_pivot_value_that_cannot_be_a_column_name_is_a_spec_error() -> None:
    orders = [(1, date(2024, 1, 1), "", "T", Decimal("1.0000"))]
    session = Session()

    with pytest.raises(SpecValidationError, match="empty value"):
        session.apply(
            session.save(_table(orders)),
            steps=[
                {
                    "op": "pivot",
                    "rows": "OrderDate",
                    "columns": "CustomerName",
                    "measure": "SubTotal",
                }
            ],
        )


def test_pivot_after_limit_is_not_rejected_by_source_statistics() -> None:
    # 30 customers in the source, more than the limit of 5, but only 3 rows reach the pivot.
    orders = [
        (i, date(2024, 1, 1 + i % 3), f"Customer {i:02d}", "T", Decimal(i)) for i in range(30)
    ]
    session = Session(max_pivot_columns=5)

    summary = session.apply(
        session.save(_table(orders)),
        steps=[
            {"op": "sort", "by": [{"column": "SalesOrderID"}]},
            {"op": "limit", "n": 3},
            {"op": "pivot", "rows": "OrderDate", "columns": "CustomerName", "measure": "SubTotal"},
        ],
    )

    assert [column.name for column in summary.result.columns][1:] == [
        "Customer 00",
        "Customer 01",
        "Customer 02",
    ]


@pytest.mark.parametrize(
    "after_pivot",
    [
        [{"op": "sort", "by": [{"column": "month", "dir": "desc"}]}],
        [{"op": "limit", "n": 3}],
        [{"op": "sort", "by": [{"column": "month", "dir": "desc"}]}, {"op": "limit", "n": 3}],
    ],
)
def test_sort_and_limit_after_pivot_keep_the_pivot_columns(
    after_pivot: list[dict[str, Any]],
) -> None:
    session = Session()
    pivot = {"op": "pivot", "rows": "month", "columns": "Territory", "measure": "SubTotal"}

    summary = session.apply(
        session.save(_table(_orders_2024())),
        steps=[MONTHLY_REPORT["steps"][0], pivot, *after_pivot],
    )

    assert [column.name for column in summary.result.columns] == ["month", *sorted(TERRITORIES)]
    months = [row[0] for row in session.rows(summary.result.handle)]
    if after_pivot[0]["op"] == "sort":
        assert months == sorted(months, reverse=True)


@pytest.mark.parametrize("period", ["OrderDate", "week"])
def test_fill_periods_rejects_periods_not_aligned_to_the_unit(period: str) -> None:
    session = Session()
    week = {
        "op": "derive",
        "name": "week",
        "fn": "date_trunc",
        "unit": "week",
        "column": "OrderDate",
    }
    steps = [
        week,
        {
            "op": "aggregate",
            "group_by": [period],
            "measures": [{"name": "net_sales", "fn": "sum", "column": "SubTotal"}],
        },
        {
            "op": "fill_periods",
            "column": period,
            "unit": "month",
            "from": "2024-01-01",
            "to": "2025-01-01",
            "fill": {"net_sales": 0},
        },
    ]

    with pytest.raises(SpecValidationError, match="not the first day of a month"):
        session.apply(session.save(_table(_orders_2024())), steps=steps)
