"""Session result store: tables under handles r1, r2, ... in a locked-down DuckDB connection."""

import threading
from collections.abc import Sequence
from typing import Protocol

import duckdb
import pyarrow as pa

from data_concierge.core.errors import ProcedureTimeoutError, UnknownHandleError
from data_concierge.core.sql import quote_identifier
from data_concierge.core.types import TableSchema, arrow_fields
from data_concierge.results.models import Handle, Lineage, ResultTable, StoredResult

DEFAULT_MEMORY_LIMIT = "512MB"
DEFAULT_THREADS = 2
DEFAULT_QUERY_TIMEOUT_SECONDS = 30.0


class ResultStore(Protocol):
    def put(self, table: ResultTable) -> Handle: ...

    def materialize(
        self, sql: str, params: Sequence[object], schema: TableSchema, lineage: Lineage
    ) -> Handle: ...

    def describe(self, handle: Handle) -> StoredResult: ...

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
    ) -> None:
        self._connection = duckdb.connect(":memory:")
        for statement in (
            "SET enable_external_access = false",
            "SET autoinstall_known_extensions = false",
            "SET autoload_known_extensions = false",
            f"SET memory_limit = '{memory_limit}'",
            f"SET threads = {int(threads)}",
            "SET lock_configuration = true",
        ):
            self._connection.execute(statement)
        self._timeout = query_timeout_seconds
        self._lock = threading.Lock()
        self._results: dict[Handle, StoredResult] = {}

    def put(self, table: ResultTable) -> Handle:
        """Stores Arrow data; the locked connection does not scan Python variables, so the
        table is registered explicitly, copied into a DuckDB table and unregistered."""
        with self._lock:
            handle = self._next_handle()
            incoming = f"_incoming_{handle}"
            cursor = self._connection.cursor()
            cursor.register(incoming, table.data)
            try:
                cursor.execute(
                    f"CREATE TABLE {quote_identifier(handle)} AS SELECT * FROM {incoming}"
                )
            finally:
                cursor.unregister(incoming)
            return self._record(handle, table.schema, table.lineage, table.data.num_rows)

    def materialize(
        self, sql: str, params: Sequence[object], schema: TableSchema, lineage: Lineage
    ) -> Handle:
        """Stores the result of a query over existing handles under a new handle."""
        with self._lock:
            handle = self._next_handle()
            created = self._execute(
                self._connection.cursor(),
                f"CREATE TABLE {quote_identifier(handle)} AS {sql}",
                params,
            )
            # A fresh cursor: a timer firing as the query ends must not interrupt these checks.
            cursor = self._connection.cursor()
            actual = cursor.execute(
                f"SELECT * FROM {quote_identifier(handle)} LIMIT 0"
            ).to_arrow_table()
            expected = schema.arrow_fields
            if arrow_fields(actual.schema) != expected:
                cursor.execute(f"DROP TABLE {quote_identifier(handle)}")
                raise TypeError(f"query result {actual.schema} does not match {expected}")
            # CREATE TABLE AS returns one row with the number of rows it inserted.
            return self._record(handle, schema, lineage, int(created.column(0)[0].as_py()))

    def describe(self, handle: Handle) -> StoredResult:
        with self._lock:
            return self._require(handle)

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
            raise ProcedureTimeoutError(
                f"query exceeded the {self._timeout:g} s time limit and was stopped"
            ) from exc
        finally:
            timer.cancel()

    def _next_handle(self) -> Handle:
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
