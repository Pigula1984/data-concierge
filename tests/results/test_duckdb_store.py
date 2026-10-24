from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import duckdb
import pyarrow as pa
import pytest

from data_concierge.core.errors import (
    QueryExecutionError,
    QueryTimeoutError,
    ResultStoreError,
    UnknownHandleError,
)
from data_concierge.core.types import Column, ColumnRole, ColumnType, TableSchema
from data_concierge.results.models import ProcedureLineage, ResultTable
from data_concierge.results.repository import DuckDBResultStore
from data_concierge.results.service import ResultService

AMOUNTS = [Decimal("1234567.8900"), Decimal("0.0000"), Decimal("0.0001"), Decimal("-5.1200")]


def _amount_table() -> ResultTable:
    schema = TableSchema(
        columns=(Column(name="Amount", type=ColumnType.decimal(19, 4), role=ColumnRole.MEASURE),)
    )
    data = pa.table({"Amount": pa.array(AMOUNTS, type=pa.decimal128(19, 4))})
    lineage = ProcedureLineage(procedure="api.Test", params={}, truncated=False)
    return ResultTable(data=data, schema=schema, lineage=lineage)


def test_decimal_keeps_type_and_scale_from_arrow_through_duckdb_to_text() -> None:
    store = DuckDBResultStore()
    handle = store.put(_amount_table())

    duckdb_type = store.query(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = 'Amount'",
        [handle],
    ).column(0)[0]
    round_trip = store.fetch(handle)
    exported = ResultService(store).export(handle, max_rows=10)

    assert duckdb_type.as_py() == "DECIMAL(19,4)"
    assert round_trip.schema.field("Amount").type == pa.decimal128(19, 4)
    assert round_trip.column("Amount").to_pylist() == AMOUNTS
    assert [row[0] for row in exported.rows] == ["1234567.8900", "0.0000", "0.0001", "-5.1200"]


def _int_table(count: int) -> ResultTable:
    schema = TableSchema(
        columns=(Column(name="Value", type=ColumnType.parse("int"), role=ColumnRole.MEASURE),)
    )
    data = pa.table({"Value": pa.array(range(count), type=pa.int64())})
    lineage = ProcedureLineage(procedure="api.Test", params={}, truncated=False)
    return ResultTable(data=data, schema=schema, lineage=lineage)


def test_handles_are_numbered_per_session() -> None:
    first_session = DuckDBResultStore()
    handles = [first_session.put(_int_table(1)) for _ in range(3)]

    assert handles == ["r1", "r2", "r3"]
    assert DuckDBResultStore().put(_int_table(1)) == "r1"


def test_unknown_handle_lists_existing_handles() -> None:
    store = DuckDBResultStore()
    store.put(_int_table(1))
    store.put(_int_table(1))

    with pytest.raises(UnknownHandleError, match="'r9'; available handles: r1, r2"):
        store.describe("r9")


FILE_ACCESS_DISABLED = "file system operations are disabled by configuration"
CONFIGURATION_LOCKED = "the configuration has been locked"


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("SELECT * FROM read_csv('{existing}')", FILE_ACCESS_DISABLED),
        ("COPY (SELECT 1) TO '{new_file}'", FILE_ACCESS_DISABLED),
        ("INSTALL httpfs", FILE_ACCESS_DISABLED),
        ("SET enable_external_access = true", CONFIGURATION_LOCKED),
        ("SET memory_limit = '64GB'", CONFIGURATION_LOCKED),
        ("SET threads = 64", CONFIGURATION_LOCKED),
    ],
)
def test_locked_connection_rejects_file_network_and_config_access(
    tmp_path: Path, statement: str, expected: str
) -> None:
    existing = tmp_path / "data.csv"
    existing.write_text("a\n1\n", encoding="utf-8")
    sql = statement.format(existing=existing, new_file=tmp_path / "leak.csv")

    with pytest.raises(QueryExecutionError) as raised:
        DuckDBResultStore().query(sql)

    assert raised.value.detail is not None
    assert expected in raised.value.detail

    assert not (tmp_path / "leak.csv").exists()


def test_query_over_time_limit_is_stopped() -> None:
    store = DuckDBResultStore(query_timeout_seconds=0.5)

    with pytest.raises(QueryTimeoutError, match=r"0.5 s"):
        store.query("SELECT sum(a.range * b.range) FROM range(100000) a, range(100000) b")


def test_parallel_puts_from_threads_get_distinct_handles() -> None:
    store = DuckDBResultStore()

    with ThreadPoolExecutor(max_workers=2) as pool:
        handles = list(pool.map(lambda count: store.put(_int_table(count)), [5, 7]))

    assert sorted(handles) == ["r1", "r2"]
    assert sorted(store.describe(handle).row_count for handle in handles) == [5, 7]


def test_result_table_rejects_arrow_types_that_differ_from_the_schema() -> None:
    schema = TableSchema(
        columns=(Column(name="Value", type=ColumnType.decimal(19, 4), role=ColumnRole.MEASURE),)
    )
    lineage = ProcedureLineage(procedure="api.Test", params={}, truncated=False)

    with pytest.raises(ValueError, match="do not match the schema"):
        ResultTable(data=pa.table({"Value": [1.5]}), schema=schema, lineage=lineage)


SENTINEL = "SENTINEL-NAME-7Q"


def _name_table() -> ResultTable:
    schema = TableSchema(
        columns=(Column(name="Name", type=ColumnType.parse("string"), role=ColumnRole.DIMENSION),)
    )
    data = pa.table({"Name": pa.array([SENTINEL], type=pa.string())})
    lineage = ProcedureLineage(procedure="api.Test", params={}, truncated=False)
    return ResultTable(data=data, schema=schema, lineage=lineage)


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        pytest.param(
            'SELECT CAST("Name" AS INTEGER) AS n FROM r1',
            "ConversionException: a value could not be converted; "
            "use TRY_CAST or filter the column first",
            id="conversion",
        ),
        pytest.param(
            """SELECT strptime("Name", '%Y') AS year FROM r1""",
            "InvalidInputException: a function got an input it cannot handle; "
            "check its arguments or filter the column first",
            id="invalid input",
        ),
    ],
)
def test_errors_that_may_quote_data_reach_the_model_as_class_and_hint(
    sql: str, message: str
) -> None:
    store = DuckDBResultStore()
    store.put(_name_table())

    with pytest.raises(QueryExecutionError) as raised:
        store.query(sql)

    assert str(raised.value) == message
    assert raised.value.detail is not None
    assert SENTINEL in raised.value.detail


def test_scalar_subquery_with_several_rows_reaches_the_model_unchanged() -> None:
    store = DuckDBResultStore()
    store.put(_name_table())

    with pytest.raises(QueryExecutionError, match="More than one row returned by a subquery"):
        store.query('SELECT (SELECT "Name" FROM r1 UNION ALL SELECT "Name" FROM r1) AS name')


def test_binder_error_reaches_the_model_unchanged_without_detail() -> None:
    store = DuckDBResultStore()
    store.put(_name_table())

    with pytest.raises(QueryExecutionError, match='"Nme" not found') as raised:
        store.query('SELECT "Nme" FROM r1')

    assert raised.value.detail is None


class BrokenCursor:
    """A cursor of a connection DuckDB has invalidated after an internal error."""

    def __init__(self, error: duckdb.Error) -> None:
        self._error = error

    def execute(self, sql: str, params: list[object]) -> None:
        raise self._error

    def interrupt(self) -> None:
        pass


@pytest.mark.parametrize(
    "error", [duckdb.InternalException("assertion failed"), duckdb.FatalException("invalidated")]
)
def test_engine_failure_is_not_a_query_error_the_model_could_fix(error: duckdb.Error) -> None:
    store = DuckDBResultStore()

    with pytest.raises(ResultStoreError, match="failed internally"):
        store._execute(BrokenCursor(error), "SELECT 1", ())  # type: ignore[arg-type]
