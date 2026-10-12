"""Saving results under handles and describing them for the model and the response."""

from collections.abc import Collection, Sequence

import pyarrow as pa

from data_concierge.core.sql import quote_identifier
from data_concierge.core.types import Column, ColumnRole, TableSchema
from data_concierge.results.models import (
    ColumnInfo,
    ColumnStats,
    ExportedTable,
    Handle,
    JsonScalar,
    Lineage,
    ProcedureLineage,
    ResultSummary,
    ResultTable,
    Rows,
    StoredResult,
    to_json_scalar,
)
from data_concierge.results.repository import ResultStore

PREVIEW_ROWS = 10
FULL_PREVIEW_MAX_ROWS = 50
MAX_LISTED_VALUES = 20


class ResultService:
    """Facade over the session's result store used by procedures, transforms and the agent."""

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

    def query(self, sql: str, params: Sequence[object] = ()) -> pa.Table:
        """Runs a read query over the session's handles, subject to the time limit."""
        return self._store.query(sql, params)

    def materialize(
        self, sql: str, params: Sequence[object], schema: TableSchema, lineage: Lineage
    ) -> Handle:
        """Stores the result of a query over existing handles under a new handle."""
        return self._store.materialize(sql, params, schema, lineage)

    def lineage_chain(self, handle: Handle) -> tuple[tuple[Handle, Lineage], ...]:
        """The handle's origin, oldest step first, so the result can be reproduced."""
        chain: list[tuple[Handle, Lineage]] = []
        current: Handle | None = handle
        while current is not None:
            lineage = self._store.describe(current).lineage
            chain.append((current, lineage))
            current = None if isinstance(lineage, ProcedureLineage) else lineage.source
        return tuple(reversed(chain))

    def is_truncated(self, handle: Handle) -> bool:
        """True when the handle or any ancestor came from a procedure result cut at max_rows."""
        return any(
            isinstance(lineage, ProcedureLineage) and lineage.truncated
            for _, lineage in self.lineage_chain(handle)
        )

    def summarize(self, handle: Handle) -> ResultSummary:
        stored = self._store.describe(handle)
        limit = None if stored.row_count <= self._full_preview_max_rows else self._preview_rows
        return ResultSummary(
            handle=handle,
            row_count=stored.row_count,
            truncated=self.is_truncated(handle),
            columns=_column_infos(stored),
            stats=self._stats(stored, stored.table_schema.names),
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

    def column_stats(self, handle: Handle, names: Collection[str]) -> dict[str, ColumnStats]:
        """Statistics of the named columns only, as in summarize."""
        return self._stats(self._store.describe(handle), names)

    def _stats(self, stored: StoredResult, names: Collection[str]) -> dict[str, ColumnStats]:
        table = quote_identifier(stored.handle)
        stats: dict[str, ColumnStats] = {}
        for column in stored.table_schema.columns:
            if column.name not in names:
                continue
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
