"""Hostile SQL from the model: the locked-down connection and the one-SELECT check stop it."""

import pytest

from data_concierge.core.errors import QueryError, QueryExecutionError, QueryTimeoutError
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
]
TEXT_TRICKS = [
    "SELECT 1 AS a; /* harmless */ DROP TABLE r1",
    "SELECT * FROM READ_CSV('C:/Windows/win.ini')",
    "SELECT * FROM r1 WHERE \"CustomerID\" IN (SELECT * FROM read_csv('ids.csv'))",
    "SELECT * FROM query('DROP TABLE r1')",
    "SELECT * FROM query('SET enable_external_access = true')",
    "SELECT * FROM duckdb_secrets()",
    "SELECT * FROM duckdb_extensions()",
]


@pytest.mark.usefixtures("sales")
@pytest.mark.parametrize(
    "sql",
    DDL_AND_DML + CONFIGURATION_AND_EXTENSIONS + FILES_AND_NETWORK + TEXT_TRICKS,
)
def test_hostile_query_fails_and_leaves_the_session_intact(
    queries: QueryService, results: ResultService, sql: str
) -> None:
    with pytest.raises(QueryError):
        queries.run(sql)

    _assert_orders_intact(queries, results)


@pytest.mark.usefixtures("sales")
def test_system_views_show_only_the_session_itself(
    queries: QueryService, results: ResultService
) -> None:
    # Readable since v3.6 (architecture, section 7.3): nothing beyond the session's own
    # tables, here r1, r2 and r3, the result table being created.
    summary = queries.run("SELECT table_name FROM duckdb_tables() ORDER BY table_name")

    assert results.export(summary.handle, max_rows=10).rows == (("r1",), ("r2",), ("r3",))


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
