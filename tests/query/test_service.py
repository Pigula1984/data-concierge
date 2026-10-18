"""QueryService on a real locked-down DuckDB session; expected values are computed in Python."""

from collections import defaultdict
from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest

from data_concierge.core.errors import QueryExecutionError, QueryValidationError
from data_concierge.query.rules import QueryPolicy
from data_concierge.query.service import QueryService
from data_concierge.results.models import DescribedColumn, QueryLineage, ResultTable
from data_concierge.results.service import ResultService

type Order = tuple[int, date, int, str, Decimal]
type SaveRows = Callable[..., str]

MONTHLY_REPORT = """
WITH months AS (
  SELECT CAST(range AS DATE) AS month
  FROM range(DATE '2024-01-01', DATE '2025-01-01', INTERVAL 1 MONTH)
), sales AS (
  SELECT CAST(date_trunc('month', "OrderDate") AS DATE) AS month,
         sum("SubTotal")                AS net_sales,
         count(DISTINCT "SalesOrderID") AS order_count
  FROM r1
  GROUP BY 1
)
SELECT m.month,
       COALESCE(s.net_sales, 0)   AS net_sales,
       COALESCE(s.order_count, 0) AS order_count
FROM months m LEFT JOIN sales s USING (month)
ORDER BY m.month
"""
BOTHELL_SALES = """
SELECT c."CustomerID", c."CustomerName", sum(o."SubTotal") AS net_sales
FROM r1 o JOIN r2 c ON o."CustomerID" = c."CustomerID"
WHERE c."City" = 'Bothell'
GROUP BY ALL
ORDER BY net_sales DESC
"""


def _monthly_totals(orders: list[Order]) -> list[tuple[str, str, int]]:
    sums: defaultdict[int, Decimal] = defaultdict(lambda: Decimal("0.0000"))
    counts: defaultdict[int, int] = defaultdict(int)
    for _, ordered, _, _, amount in orders:
        sums[ordered.month] += amount
        counts[ordered.month] += 1
    return [
        (date(2024, month, 1).isoformat(), format(sums[month], "f"), counts[month])
        for month in range(1, 13)
    ]


@pytest.mark.usefixtures("sales")
def test_monthly_report_has_exact_sums_and_zero_months(
    queries: QueryService, results: ResultService, orders: list[Order]
) -> None:
    summary = queries.run(MONTHLY_REPORT)
    exported = results.export(summary.handle, max_rows=100)

    assert [list(row) for row in exported.rows] == [list(row) for row in _monthly_totals(orders)]
    assert exported.rows[1][1:] == ("0.0000", 0)
    assert [(column.name, str(column.type)) for column in summary.columns] == [
        ("month", "date"),
        ("net_sales", "decimal(38,4)"),
        ("order_count", "int"),
    ]
    assert summary.sql.endswith(
        'ORDER BY m."month", 1 ASC NULLS LAST, 2 ASC NULLS LAST, 3 ASC NULLS LAST'
    )


@pytest.mark.usefixtures("sales")
def test_join_with_a_customer_list_sums_only_listed_customers(
    queries: QueryService, results: ResultService, orders: list[Order]
) -> None:
    expected: defaultdict[int, Decimal] = defaultdict(Decimal)
    for _, _, customer, _, amount in orders:
        if customer % 2 == 0:
            expected[customer] += amount

    summary = queries.run(BOTHELL_SALES)
    rows = results.export(summary.handle, max_rows=100).rows

    assert summary.sources == ("r1", "r2")
    assert {row[0]: row[2] for row in rows} == {k: format(v, "f") for k, v in expected.items()}
    totals = [Decimal(str(row[2])) for row in rows]
    assert totals == sorted(totals, reverse=True)


def test_reversed_source_rows_give_the_same_values_in_the_same_order(
    new_services: Callable[..., tuple[ResultService, QueryService]],
    orders_table: Callable[..., ResultTable],
    orders: list[Order],
) -> None:
    # Two customers tie at the top: the appended ORDER BY must break the tie the same way.
    tied = [
        *orders,
        (31, date(2024, 1, 5), 8, "Canada", Decimal("99999.0000")),
        (32, date(2024, 1, 6), 9, "Canada", Decimal("99999.0000")),
    ]
    top = 'SELECT "SalesOrderID", "SubTotal" FROM r1 ORDER BY "SubTotal" DESC LIMIT 1'
    outputs = []
    for rows in (tied, list(reversed(tied))):
        results, queries = new_services()
        results.save(orders_table(rows))
        outputs.append(
            [
                results.export(queries.run(sql).handle, max_rows=100).rows
                for sql in (top, MONTHLY_REPORT)
            ]
        )

    assert outputs[0] == outputs[1]
    assert outputs[0][0] == ((31, "99999.0000"),)


@pytest.mark.usefixtures("sales")
def test_query_without_order_by_gets_every_column_position(queries: QueryService) -> None:
    summary = queries.run('SELECT "Territory", count(*) AS order_count FROM r1 GROUP BY ALL')

    assert summary.sql.endswith("ORDER BY 1 ASC NULLS LAST, 2 ASC NULLS LAST")


@pytest.mark.usefixtures("sales")
@pytest.mark.parametrize(
    "sql",
    [
        'SELECT DISTINCT "Territory" FROM r1',
        'SELECT "City" AS place FROM r2 UNION SELECT "Territory" FROM r1',
    ],
)
def test_distinct_and_union_run_with_the_appended_order(
    queries: QueryService, results: ResultService, sql: str
) -> None:
    summary = queries.run(sql)
    values = [str(row[0]) for row in results.export(summary.handle, max_rows=100).rows]

    assert values == sorted(values)
    assert summary.sql.endswith("ORDER BY 1 ASC NULLS LAST")


def test_pivot_gives_a_column_per_listed_value_including_an_apostrophe(
    queries: QueryService, results: ResultService, save_orders: SaveRows, orders: list[Order]
) -> None:
    save_orders(
        [
            (*order[:3], "Margie's Travel" if order[0] % 2 else "Canada", order[4])
            for order in orders
        ]
    )
    sql = (
        'SELECT * FROM (SELECT "Territory", "SubTotal" FROM r1) '
        'PIVOT (sum("SubTotal") FOR "Territory" '
        "IN ('Margie''s Travel' AS margies, 'Canada' AS canada))"
    )

    summary = queries.run(sql)
    row = results.export(summary.handle, max_rows=10).rows[0]

    assert [column.name for column in summary.columns] == ["margies", "canada"]
    assert row == (
        format(sum(order[4] for order in orders if order[0] % 2), "f"),
        format(sum(order[4] for order in orders if not order[0] % 2), "f"),
    )


@pytest.mark.usefixtures("sales")
def test_average_and_ratio_cast_to_decimal_and_zero_divisor_gives_null(
    queries: QueryService, results: ResultService, orders: list[Order]
) -> None:
    sql = (
        'SELECT CAST(avg("SubTotal") AS DECIMAL(38,6)) AS average_order, '
        'CAST(sum("SubTotal") / NULLIF(sum("SubTotal") - sum("SubTotal"), 0) AS DECIMAL(38,6)) '
        "AS ratio FROM r1"
    )

    summary = queries.run(sql)
    row = results.export(summary.handle, max_rows=1).rows[0]

    total = sum((order[4] for order in orders), Decimal(0))
    average = (total / len(orders)).quantize(Decimal("0.000001"))
    assert [str(column.type) for column in summary.columns] == ["decimal(38,6)", "decimal(38,6)"]
    assert row == (format(average, "f"), None)


@pytest.mark.usefixtures("sales")
def test_sum_of_an_integer_column_is_an_int(queries: QueryService, results: ResultService) -> None:
    summary = queries.run('SELECT sum("CustomerID") AS customer_sum, 1 AS one FROM r1')

    assert [str(column.type) for column in summary.columns] == ["int", "int"]
    assert results.export(summary.handle, max_rows=1).rows == (
        (sum(i % 7 + 1 for i in range(1, 31)), 1),
    )


@pytest.mark.usefixtures("sales")
@pytest.mark.parametrize(
    ("sql", "message"),
    [
        # Lists and structs have no allowed constructor, so rule 5 stops them first.
        ('SELECT ["City"] AS cities FROM r2', "`list_value` is not allowed"),
        ("SELECT {'a': 1} AS pair", "`struct_pack` is not allowed"),
        ("SELECT INTERVAL 1 DAY AS span", "span has the unsupported type INTERVAL"),
    ],
)
def test_result_columns_of_unsupported_types_are_refused(
    queries: QueryService, sql: str, message: str
) -> None:
    with pytest.raises(QueryValidationError) as error:
        queries.run(sql)

    assert message in str(error.value)


def test_double_result_column_is_refused_with_a_cast_hint() -> None:
    # Rules 5 and 7 stop every allowed DOUBLE source first; the type check is the backstop.
    with pytest.raises(
        QueryValidationError, match=r"seconds is DOUBLE; CAST it to DECIMAL\(38,6\)"
    ):
        QueryPolicy.result_types([DescribedColumn("seconds", "DOUBLE")])


@pytest.mark.usefixtures("sales")
def test_result_roles_come_from_source_columns_or_from_the_type(queries: QueryService) -> None:
    summary = queries.run(BOTHELL_SALES)

    assert [(column.name, column.role.value) for column in summary.columns] == [
        ("CustomerID", "id"),
        ("CustomerName", "dimension"),
        ("net_sales", "measure"),
    ]


@pytest.mark.usefixtures("sales")
def test_unknown_column_returns_the_duckdb_binder_message(queries: QueryService) -> None:
    with pytest.raises(QueryExecutionError, match='"Discount" not found') as error:
        queries.run('SELECT sum("Discount") AS discount FROM r1')

    assert "Candidate bindings" in str(error.value)


@pytest.mark.usefixtures("sales")
def test_handle_names_ignore_ascii_case(queries: QueryService) -> None:
    summary = queries.run("WITH Bothell AS (SELECT * FROM R2) SELECT count(*) AS n FROM bothell")

    assert summary.sources == ("r2",)


@pytest.mark.usefixtures("sales")
def test_query_lineage_keeps_submitted_and_executed_sql_and_sources(
    queries: QueryService, results: ResultService
) -> None:
    summary = queries.run(BOTHELL_SALES)
    lineage = results.describe(summary.handle).lineage

    assert isinstance(lineage, QueryLineage)
    assert lineage.submitted_sql == BOTHELL_SALES
    assert lineage.sql == summary.sql
    assert lineage.sources == ("r1", "r2")


@pytest.mark.usefixtures("sales")
def test_lineage_lists_every_ancestor_once_in_handle_order(
    queries: QueryService, results: ResultService
) -> None:
    joined = queries.run(BOTHELL_SALES).handle
    combined = queries.run(
        f'SELECT j."CustomerID", j.net_sales, c."City" '
        f'FROM {joined} j JOIN r2 c USING ("CustomerID")'
    ).handle

    assert (joined, combined) == ("r3", "r4")
    assert [stored.handle for stored in results.lineage(combined)] == ["r1", "r2", "r3", "r4"]


@pytest.mark.usefixtures("sales")
def test_executed_sql_reproduces_the_result_in_a_new_session(
    queries: QueryService,
    results: ResultService,
    new_services: Callable[..., tuple[ResultService, QueryService]],
    orders_table: Callable[..., ResultTable],
    customers_table: Callable[..., ResultTable],
) -> None:
    original = queries.run(MONTHLY_REPORT)
    joined = queries.run(BOTHELL_SALES)
    replay_results, replay_queries = new_services()
    replay_results.save(orders_table())
    replay_results.save(customers_table())

    for summary in (original, joined):
        replayed = replay_queries.run(summary.sql)
        assert (
            replay_results.export(replayed.handle, max_rows=100).rows
            == results.export(summary.handle, max_rows=100).rows
        )
