"""Rules of architecture section 7.3: for each rule an allowed and a refused query."""

from collections.abc import Callable

import pytest

from data_concierge.core.errors import QueryValidationError
from data_concierge.query.service import QueryService

type SaveRows = Callable[..., str]

pytestmark = pytest.mark.usefixtures("sales")

ALLOWED = [
    # Rule 2: joins, subqueries, set operations, CASE, BETWEEN, COALESCE.
    'SELECT c."City", sum(o."SubTotal") AS net_sales FROM r1 o JOIN r2 c USING ("CustomerID") '
    "GROUP BY ALL",
    'SELECT * FROM r1 WHERE "CustomerID" IN '
    '(SELECT "CustomerID" FROM r2 WHERE "City" = \'Bothell\')',
    'SELECT "City" FROM r2 UNION SELECT "Territory" FROM r1',
    "SELECT CASE WHEN \"SubTotal\" BETWEEN 0 AND 100 THEN 'small' ELSE 'large' END AS size, "
    "COALESCE(\"Territory\", 'none') AS territory FROM r1",
    'SELECT * FROM r1 SEMI JOIN r2 USING ("CustomerID")',
    # Rule 4: range builds a grid of periods.
    "SELECT CAST(range AS DATE) AS month "
    "FROM range(DATE '2024-01-01', DATE '2025-01-01', INTERVAL 1 MONTH)",
    "SELECT generate_series AS n FROM generate_series(1, 3)",
    # Rule 5: rank and dense_rank with QUALIFY, aggregates over a window.
    'SELECT "Territory", "CustomerID", sum("SubTotal") AS net_sales, '
    'rank() OVER (PARTITION BY "Territory" ORDER BY sum("SubTotal") DESC) AS position '
    "FROM r1 GROUP BY 1, 2 QUALIFY position <= 2",
    'SELECT "SalesOrderID", dense_rank() OVER (ORDER BY "OrderDate") AS day_number, '
    'sum("SubTotal") OVER (PARTITION BY "Territory") AS territory_total FROM r1',
    'SELECT lower("City") AS city, "CustomerName" ILIKE \'%1\' AS ends_with_one FROM r2',
    'SELECT date_part(\'year\', "OrderDate") AS year, extract(month FROM "OrderDate") AS month '
    "FROM r1",
    # Rule 6: LIMIT and OFFSET at the top level.
    'SELECT "SalesOrderID" FROM r1 ORDER BY "SubTotal" DESC LIMIT 5 OFFSET 1',
    # Rule 7: division outside aggregates, cast to DECIMAL in the same expression.
    'SELECT CAST(sum("SubTotal") / NULLIF(count(*), 0) AS DECIMAL(38,6)) AS average_order FROM r1',
    'WITH shares AS (SELECT "SalesOrderID", '
    'CAST("SubTotal" / NULLIF(sum("SubTotal") OVER (), 0) AS DECIMAL(38,6)) AS share FROM r1) '
    "SELECT sum(share) AS total_share FROM shares",
    # Division in a condition or a sort key yields no column, so it needs no cast.
    'SELECT "SalesOrderID" FROM r1 WHERE "SubTotal" / 2 > 100',
    'SELECT "SalesOrderID", rank() OVER (ORDER BY "SubTotal" / "CustomerID") AS position FROM r1',
]

REFUSED = [
    ("SELECT 1 AS a; SELECT 2 AS b", 1, "exactly one SELECT"),
    ("SELECT * FROM r1 POSITIONAL JOIN r2", 2, "POSITIONAL JOIN"),
    ('SELECT * FROM r1 ASOF JOIN r2 ON r1."CustomerID" >= r2."CustomerID"', 2, "ASOF JOIN"),
    ('SELECT "City" COLLATE nocase AS city FROM r2', 2, "COLLATE"),
    ("SELECT * FROM (VALUES (1)) AS t(a)", 2, "EXPRESSION_LIST"),
    ("SELECT * FROM r9", 3, "unknown handle 'r9'; available handles: r1, r2"),
    ("SELECT * FROM orders", 3, "'orders' is not a result handle"),
    ("SELECT * FROM read_csv('x.csv')", 4, "read_csv"),
    (
        'SELECT row_number() OVER (ORDER BY "OrderDate") AS n FROM r1',
        5,
        "`row_number` is not allowed; use `rank` or `dense_rank` with QUALIFY",
    ),
    ('SELECT lag("SubTotal") OVER (ORDER BY "OrderDate") AS previous FROM r1', 5, "`lag`"),
    ('SELECT any_value("Territory") AS territory FROM r1', 5, "`any_value`"),
    ("SELECT string_agg(\"City\", ',') AS cities FROM r2", 5, "`string_agg`"),
    ("SELECT random() AS r", 5, "`random`"),
    ("SELECT now() AS t", 5, "`now`"),
    (
        'SELECT sum("SubTotal") OVER (ORDER BY "OrderDate" ROWS BETWEEN 1 PRECEDING AND '
        "CURRENT ROW) AS moving FROM r1",
        5,
        "ROWS frame",
    ),
    ("WITH top AS (SELECT * FROM r1 LIMIT 5) SELECT * FROM top", 6, "LIMIT"),
    ('SELECT DISTINCT ON ("Territory") "Territory", "SubTotal" FROM r1', 6, "DISTINCT ON"),
    ("SELECT * FROM r1 USING SAMPLE 10", 6, "SAMPLE"),
    ("SELECT * FROM r1 TABLESAMPLE 10%", 6, "SAMPLE"),
    (
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 3) "
        "SELECT i FROM n",
        6,
        "WITH RECURSIVE",
    ),
    ('SELECT CAST("SubTotal" AS DOUBLE) AS amount FROM r1', 7, "DOUBLE"),
    ('SELECT TRY_CAST("SubTotal" AS REAL) AS amount FROM r1', 7, "FLOAT"),
    ('SELECT * FROM r1 WHERE "SubTotal" > 1e-3', 7, "exponent"),
    ('SELECT sum("SubTotal" / 2) AS half FROM r1', 7, "division"),
    (
        'WITH t AS (SELECT "SubTotal" / 3 AS third FROM r1) '
        "SELECT CAST(sum(third) AS DECIMAL(38,6)) AS total FROM t",
        7,
        "division gives DOUBLE",
    ),
    (
        'SELECT CAST(sum(x) AS DECIMAL(38,6)) AS total FROM (SELECT avg("SubTotal") AS x FROM r1)',
        7,
        "`avg` gives DOUBLE",
    ),
    (
        'WITH t AS (SELECT avg("SubTotal") OVER (PARTITION BY "Territory") AS a FROM r1) '
        "SELECT CAST(sum(a) AS DECIMAL(38,6)) AS total FROM t",
        7,
        "`avg` gives DOUBLE",
    ),
    (
        "SELECT CAST(sum(date_part('epoch', \"OrderDate\")) AS DECIMAL(38,6)) AS s FROM r1",
        5,
        "date_part('epoch')",
    ),
    ('SELECT extract(julian FROM "OrderDate") AS j FROM r1', 5, "date_part('julian')"),
    ('SELECT CAST(sum(avg("SubTotal")) OVER () AS DECIMAL(38,6)) AS a FROM r1', 7, "`avg`"),
    (
        'SELECT sum((SELECT max("SubTotal") FROM r1)) AS s FROM r2',
        7,
        "subquery",
    ),
    ('SELECT * FROM r1 WHERE "CustomerID" = ?', 8, "parameter"),
    ('SELECT * FROM r1 WHERE "CustomerID" = $1', 8, "parameter"),
    ('SELECT sum("SubTotal") FROM r1', 9, "sum(SubTotal)"),
    (
        'SELECT sum("SubTotal") AS "Net_Sales", count(*) AS net_sales FROM r1',
        9,
        "net_sales",
    ),
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allowed_query_runs(queries: QueryService, sql: str) -> None:
    assert queries.run(sql).row_count >= 1


@pytest.mark.parametrize(("sql", "rule", "message"), REFUSED)
def test_refused_query_names_the_rule(
    queries: QueryService, sql: str, rule: int, message: str
) -> None:
    with pytest.raises(QueryValidationError) as error:
        queries.run(sql)

    assert f"Rule {rule}:" in str(error.value)
    assert message in str(error.value)


def test_all_violations_are_reported_together(queries: QueryService) -> None:
    sql = 'SELECT row_number() OVER () AS n, random() AS r FROM r1 WHERE "CustomerID" = ?'

    with pytest.raises(QueryValidationError) as error:
        queries.run(sql)

    message = str(error.value)
    assert "`row_number`" in message
    assert "`random`" in message
    assert "Rule 8:" in message


def test_aggregating_over_a_truncated_source_asks_to_narrow_the_procedure(
    queries: QueryService, save_orders: SaveRows, save_customers: SaveRows
) -> None:
    truncated_orders = save_orders(truncated=True)
    truncated_customers = save_customers(truncated=True)
    derived = queries.run(f"SELECT * FROM {truncated_orders}")
    aggregating = [
        f'SELECT sum("SubTotal") AS net_sales FROM {derived.handle}',
        f'SELECT c."City", sum(o."SubTotal") AS net_sales FROM r1 o '
        f'JOIN {truncated_customers} c USING ("CustomerID") GROUP BY ALL',
        f'SELECT "Territory" FROM {truncated_orders} GROUP BY "Territory"',
        f'SELECT sum("SubTotal") OVER () AS total FROM {truncated_orders}',
        f"SELECT * FROM {truncated_orders} "
        'PIVOT (sum("SubTotal") FOR "Territory" IN (\'Canada\' AS canada))',
    ]

    grandchild = queries.run(f"SELECT * FROM {derived.handle}")
    aggregating.append(f'SELECT sum("SubTotal") AS net_sales FROM {grandchild.handle}')

    assert derived.truncated is True
    assert grandchild.truncated is True
    for sql in aggregating:
        with pytest.raises(QueryValidationError, match=r"Rule 10:.*narrower parameters"):
            queries.run(sql)
    # A truncated handle the query does not read does not block it.
    assert queries.run('SELECT sum("SubTotal") AS net_sales FROM r1').row_count == 1
