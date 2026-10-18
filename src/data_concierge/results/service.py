"""Saving results under handles and describing them for the model and the response."""

import pyarrow as pa

from data_concierge.core.sql import quote_identifier
from data_concierge.core.types import Column, ColumnRole, TableSchema
from data_concierge.results.models import (
    ColumnInfo,
    ColumnStats,
    DescribedColumn,
    ExportedTable,
    Handle,
    JsonScalar,
    Lineage,
    ProcedureLineage,
    ResultSummary,
    ResultTable,
    Rows,
    StoredResult,
    handle_number,
    to_json_scalar,
)
from data_concierge.results.repository import ResultStore

PREVIEW_ROWS = 10
FULL_PREVIEW_MAX_ROWS = 50
MAX_LISTED_VALUES = 20


class ResultService:
    """Facade over the session's result store used by procedures, queries and the agent."""

    def __init__(
        self,
        store: ResultStore,
        preview_rows: int = PREVIEW_ROWS,
        full_preview_max_rows: int = FULL_PREVIEW_MAX_ROWS,
        max_listed_values: int = MAX_LISTED_VALUES,
    ) -> None:
        self._store = store
        self._preview_rows = preview_rows
        self._full_preview_max_rows = full_preview_max_rows
        self._max_listed_values = max_listed_values

    def save(self, table: ResultTable) -> Handle:
        return self._store.put(table)

    def describe(self, handle: Handle) -> StoredResult:
        return self._store.describe(handle)

    def handles(self) -> tuple[Handle, ...]:
        return self._store.handles()

    def describe_query(self, sql: str) -> tuple[DescribedColumn, ...]:
        """Names and DuckDB types of a query's result columns, without running the query."""
        return self._store.describe_query(sql)

    def statement_types(self, sql: str) -> tuple[str, ...]:
        return self._store.statement_types(sql)

    def sources(self, sql: str) -> tuple[Handle, ...]:
        """The session handles a query reads, each once, in creation order."""
        read = self._store.read_tables(sql)
        return tuple(handle for handle in self._store.handles() if handle in read)

    def materialize(self, sql: str, schema: TableSchema, lineage: Lineage) -> Handle:
        """Stores the result of a query over existing handles under a new handle."""
        return self._store.materialize(sql, schema, lineage)

    def lineage(self, handle: Handle) -> tuple[StoredResult, ...]:
        """The handle and all its ancestors, each once, oldest first, to reproduce the result.

        Handles are numbered in creation order and a source always exists before its result,
        so ordering by number is a topological order of the lineage graph.
        """
        found: dict[Handle, StoredResult] = {}
        pending = [handle]
        while pending:
            current = pending.pop()
            if current not in found:
                found[current] = self._store.describe(current)
                pending.extend(found[current].lineage.sources)
        return tuple(found[key] for key in sorted(found, key=handle_number))

    def is_truncated(self, handle: Handle) -> bool:
        """True when the handle or any ancestor came from a procedure result cut at max_rows."""
        return any(_cut_by_procedure(stored.lineage) for stored in self.lineage(handle))

    def summarize(self, handle: Handle) -> ResultSummary:
        stored = self._store.describe(handle)
        limit = None if stored.row_count <= self._full_preview_max_rows else self._preview_rows
        return ResultSummary(
            handle=handle,
            row_count=stored.row_count,
            truncated=self.is_truncated(handle),
            columns=_column_infos(stored),
            stats=self._stats(stored),
            preview=_rows(self._store.fetch(handle, limit)),
        )

    def export(self, handle: Handle, max_rows: int) -> ExportedTable:
        stored = self._store.describe(handle)
        return ExportedTable(
            handle=handle,
            columns=_column_infos(stored),
            rows=_rows(self._store.fetch(handle, max_rows)),
            row_count=stored.row_count,
            truncated=stored.row_count > max_rows or self.is_truncated(handle),
        )

    def _stats(self, stored: StoredResult) -> dict[str, ColumnStats]:
        table = quote_identifier(stored.handle)
        stats: dict[str, ColumnStats] = {}
        for column in stored.table_schema.columns:
            name = quote_identifier(column.name)
            if column.role in (ColumnRole.TIME, ColumnRole.MEASURE):
                low, high = self._single_row(f"SELECT min({name}), max({name}) FROM {table}")
                stats[column.name] = ColumnStats(min=low, max=high)
            elif column.role is ColumnRole.DIMENSION:
                stats[column.name] = self._dimension_stats(table, column)
        return stats

    def _dimension_stats(self, table: str, column: Column) -> ColumnStats:
        name = quote_identifier(column.name)
        (count,) = self._single_row(f"SELECT count(DISTINCT {name}) FROM {table}")
        assert isinstance(count, int)
        if count > self._max_listed_values:
            return ColumnStats(distinct_count=count)
        values = self._store.query(
            f"SELECT DISTINCT {name} FROM {table} WHERE {name} IS NOT NULL ORDER BY {name}"
        )
        return ColumnStats(values=tuple(to_json_scalar(v) for v in values.column(0).to_pylist()))

    def _single_row(self, sql: str) -> tuple[JsonScalar, ...]:
        return _rows(self._store.query(sql))[0]


def _cut_by_procedure(lineage: Lineage) -> bool:
    return isinstance(lineage, ProcedureLineage) and lineage.truncated


def _column_infos(stored: StoredResult) -> tuple[ColumnInfo, ...]:
    return tuple(
        ColumnInfo(name=column.name, type=column.type, role=column.role)
        for column in stored.table_schema.columns
    )


def _rows(table: pa.Table) -> Rows:
    columns = [column.to_pylist() for column in table.columns]
    return tuple(
        tuple(to_json_scalar(value) for value in row) for row in zip(*columns, strict=True)
    )
