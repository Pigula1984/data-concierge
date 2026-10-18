"""Checks of architecture section 7.3: one SELECT, result columns, truncated sources."""

from collections.abc import Callable

import pytest

from data_concierge.core.errors import QueryExecutionError, QueryValidationError
from data_concierge.query.service import QueryService

type SaveRows = Callable[..., str]

pytestmark = pytest.mark.usefixtures("sales")

ALLOWED = [
    'SELECT c."City", sum(o."SubTotal") AS net_sales FROM r1 o JOIN r2 c USING ("CustomerID") '
    "GROUP BY ALL",
    'SELECT * FROM r1 WHERE "CustomerID" IN '
    '(SELECT "CustomerID" FROM r2 WHERE "City" = \'Bothell\')',
    'SELECT "City" FROM r2 UNION SELECT "Territory" FROM r1',
    "SELECT CAST(range AS DATE) AS month "
    "FROM range(DATE '2024-01-01', DATE '2025-01-01', INTERVAL 1 MONTH)",
    'SELECT "Territory", "CustomerID", sum("SubTotal") AS net_sales, '
    'rank() OVER (PARTITION BY "Territory" ORDER BY sum("SubTotal") DESC) AS position '
    "FROM r1 GROUP BY 1, 2 QUALIFY position <= 2",
    'SELECT "SalesOrderID" FROM r1 ORDER BY "SubTotal" DESC, "SalesOrderID" LIMIT 5 OFFSET 1',
    # Not checked since v3.6: the model avoids them unless the user asks (section 7.2).
    'SELECT "SalesOrderID", row_number() OVER (ORDER BY "OrderDate") AS n FROM r1',
    'SELECT "SalesOrderID" FROM r1 ORDER BY random() LIMIT 3',
    'SELECT string_agg("CustomerName", \', \' ORDER BY "CustomerName") AS names FROM r2',
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allowed_query_runs(queries: QueryService, sql: str) -> None:
    assert queries.run(sql).row_count >= 1


@pytest.mark.parametrize(
    ("sql", "got"),
    [
        ("SELECT 1 AS a; SELECT 2 AS b", "SELECT, SELECT"),
        ("SELECT 1 AS a; DROP TABLE r1", "SELECT, DROP"),
        ("EXPLAIN SELECT 1 AS a", "EXPLAIN"),
        ("-- nothing but a comment", "nothing"),
    ],
)
def test_query_must_be_exactly_one_select(queries: QueryService, sql: str, got: str) -> None:
    with pytest.raises(QueryValidationError, match=f"exactly one SELECT statement; got: {got}"):
        queries.run(sql)


def test_syntax_error_returns_the_duckdb_message(queries: QueryService) -> None:
    with pytest.raises(QueryValidationError, match='syntax error at or near "SELEC"'):
        queries.run("SELEC 1")


def test_every_column_problem_is_reported_at_once(queries: QueryService) -> None:
    sql = 'SELECT sum("SubTotal"), 1 AS Net_Sales, 2 AS net_sales, avg("SubTotal") AS a FROM r1'

    with pytest.raises(QueryValidationError) as error:
        queries.run(sql)

    message = str(error.value)
    assert "result column sum(SubTotal) needs an alias" in message
    assert "must differ ignoring case: Net_Sales, net_sales" in message
    assert "column a is DOUBLE; CAST it to DECIMAL(38,6)" in message


def test_unknown_handle_returns_the_duckdb_catalog_message(queries: QueryService) -> None:
    with pytest.raises(QueryExecutionError, match="r9 does not exist"):
        queries.run("SELECT * FROM r9")


def test_query_reading_a_truncated_source_asks_to_narrow_the_procedure(
    queries: QueryService, save_orders: SaveRows, save_customers: SaveRows
) -> None:
    truncated_orders = save_orders(truncated=True)
    truncated_customers = save_customers(truncated=True)
    reading = [
        f"SELECT * FROM {truncated_orders}",
        f'SELECT sum("SubTotal") AS net_sales FROM {truncated_orders}',
        f'SELECT c."City", sum(o."SubTotal") AS net_sales FROM r1 o '
        f'JOIN {truncated_customers} c USING ("CustomerID") GROUP BY ALL',
        f'SELECT * FROM r1 WHERE "CustomerID" IN (SELECT "CustomerID" FROM {truncated_customers})',
        f"WITH t AS (SELECT * FROM {truncated_orders}) SELECT count(*) AS n FROM t",
        f"SELECT count(*) AS n FROM query('SELECT * FROM {truncated_orders}')",
    ]

    for sql in reading:
        with pytest.raises(QueryValidationError, match=r"cut at max_rows.*narrower parameters"):
            queries.run(sql)
    # A truncated handle the query does not read does not block it.
    assert queries.run('SELECT sum("SubTotal") AS net_sales FROM r1').row_count == 1
