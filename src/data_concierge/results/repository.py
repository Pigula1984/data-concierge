"""Session result store: tables under handles r1, r2, ... in a locked-down DuckDB connection."""

import json
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Protocol

import duckdb
import pyarrow as pa

from data_concierge.core.errors import (
    QueryError,
    QueryExecutionError,
    QueryTimeoutError,
    QueryValidationError,
    ResultStoreError,
    UnknownHandleError,
)
from data_concierge.core.sql import quote_identifier
from data_concierge.core.types import TableSchema
from data_concierge.results.models import (
    DescribedColumn,
    Handle,
    Lineage,
    ResultTable,
    StoredResult,
)

DEFAULT_MEMORY_LIMIT = "512MB"
DEFAULT_THREADS = 2
DEFAULT_QUERY_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_QUERY_ROWS = 100_000
# DuckDB error texts can list dozens of candidate functions; the model needs the first lines.
MAX_ERROR_LINES = 4
# A bug or a broken state of the engine, not of the query: DuckDB invalidates the database,
# so the session ends instead of the model being asked to fix its SQL (section 11.2).
ENGINE_FAILURES = (duckdb.FatalException, duckdb.InternalException)
# Errors that quote only the model's SQL and the schema, so their text helps the model and
# reaches it; any other error may quote a value from the data (architecture 002, section 6.2).
VERBATIM_ERRORS = (duckdb.ParserException, duckdb.BinderException, duckdb.CatalogException)
# Fixed engine texts of other errors that quote no value, so they also reach the model.
VERBATIM_MESSAGES = ("Invalid Input Error: More than one row returned by a subquery",)
_ERROR_HINTS: dict[type[duckdb.Error], str] = {
    duckdb.ConversionException: (
        "a value could not be converted; use TRY_CAST or filter the column first"
    ),
    duckdb.OutOfRangeException: (
        "a value is out of the range of its type; use a wider type or filter the column first"
    ),
    duckdb.InvalidInputException: (
        "a function got an input it cannot handle; check its arguments or filter the column first"
    ),
}
_DEFAULT_ERROR_HINT = "the query failed while running; change the query"


class ResultStore(Protocol):
    def put(self, table: ResultTable) -> Handle: ...

    def materialize(self, sql: str, schema: TableSchema, lineage: Lineage) -> Handle: ...

    def describe_query(self, sql: str) -> tuple[DescribedColumn, ...]: ...

    def statement_types(self, sql: str) -> tuple[str, ...]: ...

    def read_tables(self, sql: str) -> frozenset[str]: ...

    def describe(self, handle: Handle) -> StoredResult: ...

    def handles(self) -> tuple[Handle, ...]: ...

    def fetch(self, handle: Handle, limit: int | None = None) -> pa.Table: ...

    def query(self, sql: str, params: Sequence[object] = ()) -> pa.Table: ...


class DuckDBResultStore:
    """Owns one in-memory DuckDB connection per session.

    The connection has no file, network or extension access and its configuration is locked
    (architecture, section 7.4a). Tools run in worker threads, so every operation uses its own
    cursor under the store lock, and a timer interrupts queries that exceed the time limit.
    """

    def __init__(
        self,
        memory_limit: str = DEFAULT_MEMORY_LIMIT,
        threads: int = DEFAULT_THREADS,
        query_timeout_seconds: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
        max_query_rows: int = DEFAULT_MAX_QUERY_ROWS,
    ) -> None:
        self._connection = self._connect_locked(
            f"SET memory_limit = '{memory_limit}'",
            f"SET threads = {int(threads)}",
            # The ORDER BY of the model's query must survive CREATE TABLE AS and fetch.
            "SET preserve_insertion_order = true",
            # EXPLAIN on this connection also returns the plan before optimization (read_tables).
            "SET explain_output = 'all'",
        )
        self._timeout = query_timeout_seconds
        self._max_query_rows = max_query_rows
        self._lock = threading.Lock()
        self._results: dict[Handle, StoredResult] = {}

    def put(self, table: ResultTable) -> Handle:
        """Stores Arrow data; the locked connection does not scan Python variables, so the
        table is registered explicitly, copied into a DuckDB table and unregistered."""
        with self._lock:
            handle = self._next_handle()
            incoming = f"_incoming_{handle}"
            with self._engine_guard():
                cursor = self._connection.cursor()
                cursor.register(incoming, table.data)
                try:
                    cursor.execute(
                        f"CREATE TABLE {quote_identifier(handle)} AS SELECT * FROM {incoming}"
                    )
                finally:
                    cursor.unregister(incoming)
            return self._record(handle, table.schema, table.lineage, table.data.num_rows)

    def materialize(self, sql: str, schema: TableSchema, lineage: Lineage) -> Handle:
        """Stores the result of a query over existing handles under a new handle.

        A result over the row limit is dropped, so a cartesian product cannot fill the session.
        At most one row past the limit is stored, and DuckDB stops reading there unless the
        query itself sorts every row first.
        """
        with self._lock:
            handle = self._next_handle()
            table = quote_identifier(handle)
            created = self._execute(
                self._connection.cursor(),
                # The newline ends a trailing -- comment in the model's text.
                f"CREATE TABLE {table} AS SELECT * FROM ({sql}\n) LIMIT {self._max_query_rows + 1}",
                (),
            )
            # CREATE TABLE AS returns one row with the number of rows it inserted.
            row_count = int(created.column(0)[0].as_py())
            # A fresh cursor: a timer firing as the query ends must not interrupt these checks.
            with self._engine_guard():
                cursor = self._connection.cursor()
                actual = cursor.execute(f"SELECT * FROM {table} LIMIT 0").to_arrow_table()
                if not schema.matches_arrow(actual.schema):
                    cursor.execute(f"DROP TABLE {table}")
                    raise TypeError(
                        f"query result {actual.schema} does not match {schema.arrow_fields}"
                    )
                if row_count > self._max_query_rows:
                    cursor.execute(f"DROP TABLE {table}")
                    raise QueryExecutionError(
                        f"the query result has more than the limit of {self._max_query_rows} "
                        "rows; aggregate the data or narrow the query"
                    )
            return self._record(handle, schema, lineage, row_count)

    def describe_query(self, sql: str) -> tuple[DescribedColumn, ...]:
        """Binds a query without running it: names and DuckDB types of its result columns."""
        with self._lock:
            described = self._execute(self._connection.cursor(), f"DESCRIBE {sql}", ())
            names = described.column("column_name").to_pylist()
            types = described.column("column_type").to_pylist()
            return tuple(
                DescribedColumn(name, duckdb_type)
                for name, duckdb_type in zip(names, types, strict=True)
            )

    def statement_types(self, sql: str) -> tuple[str, ...]:
        """The types of the statements in the text, e.g. ("SELECT",), without running them."""
        with self._lock:
            try:
                statements = self._connection.cursor().extract_statements(sql)
            except ENGINE_FAILURES as exc:
                raise self._engine_failure(exc) from exc
            except duckdb.Error as exc:
                raise self._query_error(QueryValidationError, exc) from exc
            return tuple(statement.type.name for statement in statements)

    def read_tables(self, sql: str) -> frozenset[str]:
        """The tables the bound query reads, from DuckDB's logical plan before optimization.

        The binder has resolved CTEs, letter case and query('...') there, and the optimizer
        has not yet dropped a table that the statistics prove irrelevant.
        """
        with self._lock:
            # explain_output is local to a connection, so a new cursor would not have it.
            plans = self._execute(self._connection, f"EXPLAIN (FORMAT json) {sql}", ())
        by_kind = dict(
            zip(
                plans.column("explain_key").to_pylist(),
                plans.column("explain_value").to_pylist(),
                strict=True,
            )
        )
        return frozenset(self._scanned_tables(json.loads(by_kind["logical_plan"])))

    def describe(self, handle: Handle) -> StoredResult:
        with self._lock:
            return self._require(handle)

    def handles(self) -> tuple[Handle, ...]:
        with self._lock:
            return tuple(self._results)

    def fetch(self, handle: Handle, limit: int | None = None) -> pa.Table:
        with self._lock:
            self._require(handle)
            sql = f"SELECT * FROM {quote_identifier(handle)}"
            params: list[object] = []
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
            return self._execute(self._connection.cursor(), sql, params)

    def query(self, sql: str, params: Sequence[object] = ()) -> pa.Table:
        """Runs a read query over stored handles, subject to the time limit."""
        with self._lock:
            return self._execute(self._connection.cursor(), sql, params)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _execute(
        self, cursor: duckdb.DuckDBPyConnection, sql: str, params: Sequence[object]
    ) -> pa.Table:
        timer = threading.Timer(self._timeout, cursor.interrupt)
        timer.start()
        try:
            return cursor.execute(sql, list(params)).to_arrow_table()
        except duckdb.InterruptException as exc:
            raise QueryTimeoutError(
                f"query exceeded the {self._timeout:g} s time limit and was stopped"
            ) from exc
        except ENGINE_FAILURES as exc:
            raise self._engine_failure(exc) from exc
        except duckdb.Error as exc:
            raise self._query_error(QueryExecutionError, exc) from exc
        finally:
            timer.cancel()

    def _next_handle(self) -> Handle:
        # Handles are r1, r2, ... in creation order.
        return f"r{len(self._results) + 1}"

    def _record(
        self, handle: Handle, schema: TableSchema, lineage: Lineage, row_count: int
    ) -> Handle:
        self._results[handle] = StoredResult(
            handle=handle, table_schema=schema, lineage=lineage, row_count=row_count
        )
        return handle

    def _require(self, handle: Handle) -> StoredResult:
        result = self._results.get(handle)
        if result is None:
            available = ", ".join(self._results) or "none"
            raise UnknownHandleError(f"unknown handle '{handle}'; available handles: {available}")
        return result

    @staticmethod
    def _connect_locked(*settings: str) -> duckdb.DuckDBPyConnection:
        """An in-memory connection without file, network or extension access.

        The given settings run before `lock_configuration`, after which no setting can change.
        """
        connection = duckdb.connect(":memory:")
        for statement in (
            "SET enable_external_access = false",
            "SET autoinstall_known_extensions = false",
            "SET autoload_known_extensions = false",
            *settings,
            "SET lock_configuration = true",
        ):
            connection.execute(statement)
        return connection

    @classmethod
    def _scanned_tables(cls, node: object) -> Iterator[str]:
        """Names of the tables a plan scans, e.g. r1 from memory.main.r1."""
        if isinstance(node, dict):
            info = node.get("extra_info")
            if isinstance(info, dict) and isinstance(info.get("Table"), str):
                yield info["Table"].rsplit(".", 1)[-1]
            for value in node.values():
                yield from cls._scanned_tables(value)
        elif isinstance(node, list):
            for value in node:
                yield from cls._scanned_tables(value)

    @contextmanager
    def _engine_guard(self) -> Iterator[None]:
        try:
            yield
        except ENGINE_FAILURES as exc:
            raise self._engine_failure(exc) from exc

    @staticmethod
    def _engine_failure(exc: duckdb.Error) -> ResultStoreError:
        return ResultStoreError(f"the session's DuckDB failed internally: {exc}")

    @classmethod
    def _query_error[E: QueryError](cls, error_type: type[E], exc: duckdb.Error) -> E:
        """The error for the model: a verbatim error shortened, any other as its class and a
        hint, with the full text in `detail`."""
        text = str(exc)
        if isinstance(exc, VERBATIM_ERRORS) or text.startswith(VERBATIM_MESSAGES):
            return error_type(cls._brief(text))
        hint = next(
            (hint for kind, hint in _ERROR_HINTS.items() if isinstance(exc, kind)),
            _DEFAULT_ERROR_HINT,
        )
        return error_type(f"{type(exc).__name__}: {hint}", detail=text)

    @staticmethod
    def _brief(message: str) -> str:
        lines = [line for line in message.splitlines() if line.strip()]
        if len(lines) > MAX_ERROR_LINES:
            lines = [*lines[:MAX_ERROR_LINES], "..."]
        return "\n".join(lines)
