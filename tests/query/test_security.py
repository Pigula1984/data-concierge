"""Hostile SQL from the model: every case is refused before DuckDB runs anything."""

import pytest

from data_concierge.core.errors import QueryExecutionError, QueryTimeoutError, QueryValidationError
from data_concierge.query.service import QueryService
from data_concierge.results.service import ResultService

DDL_AND_DML = [
    "CREATE TABLE stolen AS SELECT 1 AS a",
    "DROP TABLE r1",
    "INSERT INTO r1 SELECT * FROM r1",
    'UPDATE r1 SET "SubTotal" = 0',
    "DELETE FROM r1",
    "ALTER TABLE r1 ADD COLUMN extra INTEGER",
    "CREATE MACRO leak(a) AS a",
]
CONFIGURATION_AND_EXTENSIONS = [
    "PRAGMA database_list",
    "SET enable_external_access = true",
    "RESET memory_limit",
    "INSTALL httpfs",
    "LOAD httpfs",
    "ATTACH 'stolen.db' AS stolen",
    "DETACH memory",
    "USE memory",
    "CALL duckdb_settings()",
    "EXPORT DATABASE 'dump'",
    "COPY r1 TO 'leak.csv'",
]
FILES_AND_NETWORK = [
    "SELECT * FROM read_csv('C:/Windows/win.ini')",
    "SELECT * FROM read_parquet('data.parquet')",
    "SELECT * FROM read_json('data.json')",
    "SELECT * FROM read_text('notes.txt')",
    "SELECT * FROM read_blob('notes.txt')",
    "SELECT * FROM glob('*')",
    "SELECT * FROM 'data.csv'",
    "SELECT * FROM 'https://example.com/data.csv'",
    "SELECT * FROM query_table('r1')",
    "SELECT * FROM query('SELECT 1')",
]
SYSTEM_CATALOG_AND_ENVIRONMENT = [
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM duckdb_tables()",
    "SELECT * FROM duckdb_settings()",
    "SELECT * FROM pg_catalog.pg_tables",
    "SELECT * FROM sqlite_master",
    "SELECT current_setting('memory_limit') AS setting",
    "SELECT getenv('HOME') AS home",
    "SELECT version() AS version",
]
BYPASSES = [
    # A CTE named like a handle would shadow it.
    'WITH r1 AS (SELECT 1 AS "SubTotal") SELECT * FROM r1',
    "SELECT * FROM main.r1",
    "SELECT * FROM memory.main.r1",
    "SELECT 1 AS a; /* harmless */ DROP TABLE r1",
    "SELECT * FROM READ_CSV('C:/Windows/win.ini')",
    "SELECT * FROM r1 WHERE \"CustomerID\" IN (SELECT * FROM read_csv('ids.csv'))",
    # A CTE that references its own name reads the system view of that name.
    "WITH sqlite_master AS (SELECT * FROM sqlite_master) SELECT * FROM sqlite_master",
    # Handles and CTE names compare ignoring ASCII case, as DuckDB resolves them.
    'WITH R1 AS (SELECT 1 AS "SubTotal") SELECT * FROM r1',
    'WITH r1 AS (SELECT 1 AS "SubTotal") SELECT * FROM R1',
    # Python's casefold maps the long s and the Kelvin sign to ASCII letters, DuckDB does not:
    # the CTE would look like it covers the system view, which DuckDB then reads.
    'WITH "\u017fqlite_master" AS (SELECT 1 AS a) SELECT * FROM sqlite_master',
    'WITH "duc\u212adb_tables" AS (SELECT 1 AS a) SELECT * FROM duckdb_tables',
    'WITH "pg_\u017fettings" AS (SELECT 1 AS a) SELECT * FROM pg_settings',
]

# One forbidden element in every position the walker must visit.
EVERY_POSITION = [
    'SELECT * FROM r1 WHERE "SubTotal" > 0 AND random() > 0.5',
    'SELECT * FROM r1 WHERE "SubTotal" > 0 OR random() > 0.5',
    'SELECT * REPLACE (random() AS "SubTotal") FROM r1',
    "SELECT CASE WHEN random() > 0.5 THEN 1 ELSE 0 END AS c FROM r1",
    'SELECT CASE WHEN "SubTotal" > 0 THEN random() ELSE 0 END AS c FROM r1',
    'SELECT CASE WHEN "SubTotal" > 0 THEN 1 ELSE random() END AS c FROM r1',
    'SELECT * FROM r1 WHERE "SubTotal" BETWEEN random() AND 100',
    'SELECT * FROM r1 WHERE "CustomerID" IN (1, random())',
    'SELECT * FROM r1 WHERE random() = ANY (SELECT "CustomerID" FROM r2)',
    'SELECT * FROM r1 o JOIN r2 c ON o."CustomerID" = c."CustomerID" AND random() > 0.1',
    'SELECT "Territory" FROM r1 GROUP BY "Territory" HAVING random() > 0.5',
    'SELECT "SalesOrderID" FROM r1 QUALIFY random() > 0.5',
    "SELECT count(*) AS n FROM r1 GROUP BY random() > 0.5",
    'SELECT "SalesOrderID" FROM r1 ORDER BY random()',
    'SELECT "SalesOrderID" FROM r1 LIMIT (SELECT count(*) FROM sqlite_master)',
    'SELECT sum("SubTotal") OVER (PARTITION BY random() > 0.5) AS t FROM r1',
    'SELECT sum("SubTotal") OVER (ORDER BY random()) AS t FROM r1',
    "SELECT count(*) FILTER (WHERE random() > 0.5) AS n FROM r1",
    'SELECT min("SubTotal" ORDER BY random()) AS m FROM r1',
    "SELECT * FROM r1 PIVOT (sum(random()) FOR \"Territory\" IN ('Canada' AS canada))",
    "SELECT 1 AS a UNION SELECT count(*) FROM sqlite_master",
    "SELECT count(*) AS a FROM sqlite_master EXCEPT SELECT 1",
    "SELECT 1 AS a INTERSECT SELECT count(*) FROM duckdb_settings()",
    "SELECT (SELECT max(random()) FROM r2) AS m FROM r1",
    "SELECT * FROM (SELECT random() AS x FROM r2) AS s",
]


@pytest.mark.usefixtures("sales")
@pytest.mark.parametrize(
    "sql",
    DDL_AND_DML
    + CONFIGURATION_AND_EXTENSIONS
    + FILES_AND_NETWORK
    + SYSTEM_CATALOG_AND_ENVIRONMENT
    + BYPASSES
    + EVERY_POSITION,
)
def test_hostile_query_is_refused_before_execution(
    queries: QueryService, results: ResultService, sql: str
) -> None:
    with pytest.raises(QueryValidationError, match=r"Rule \d+"):
        queries.run(sql)

    _assert_orders_intact(queries, results)


def _assert_orders_intact(queries: QueryService, results: ResultService) -> None:
    count = queries.run("SELECT count(*) AS order_count FROM r1").handle
    assert results.export(count, max_rows=1).rows == ((30,),)


CARTESIAN = 'SELECT a."SalesOrderID" AS a_id, b."SalesOrderID" AS b_id FROM r1 a, r1 b'


@pytest.mark.usefixtures("sales")
@pytest.mark.parametrize("services", [{"max_query_rows": 100}], indirect=True)
def test_result_over_the_row_limit_is_dropped_and_the_session_keeps_working(
    queries: QueryService, results: ResultService
) -> None:
    with pytest.raises(QueryExecutionError, match="more than the limit of 100 rows"):
        queries.run(CARTESIAN)

    _assert_orders_intact(queries, results)
    assert results.describe("r3").lineage.sources == ("r1",)


@pytest.mark.usefixtures("sales")
@pytest.mark.parametrize("services", [{"timeout": 0.5}], indirect=True)
def test_query_over_the_time_limit_is_stopped_and_the_session_keeps_working(
    queries: QueryService,
) -> None:
    with pytest.raises(QueryTimeoutError, match=r"0.5 s"):
        queries.run(
            "SELECT count(*) AS n FROM range(100000) a, range(100000) b WHERE a.range <> b.range"
        )

    assert queries.run("SELECT count(*) AS order_count FROM r1").row_count == 1
