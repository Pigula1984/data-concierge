"""Saving results under handles and describing them for the model and the response."""

from datetime import date, datetime
from decimal import Decimal

import pyarrow as pa

from data_concierge.core.settings import DEFAULT_EXPOSURE, ModelExposure
from data_concierge.core.sql import quote_identifier
from data_concierge.core.types import Column, ColumnRole, TableSchema
from data_concierge.results.models import (
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
    TableExcerpt,
)
from data_concierge.results.repository import ResultStore

MODEL_MAX_ROWS = 50
MAX_LISTED_VALUES = 20


class ResultService:
    """Facade over the session's result store used by procedures, queries and the agent.

    Summaries show the model what the process's mode allows (architecture 002, sections 4-5).
    """

    def __init__(
        self,
        store: ResultStore,
        exposure: ModelExposure = DEFAULT_EXPOSURE,
        model_max_rows: int = MODEL_MAX_ROWS,
        max_listed_values: int = MAX_LISTED_VALUES,
    ) -> None:
        self._store = store
        self._exposure = exposure
        self._model_max_rows = model_max_rows
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

        The store lists handles in creation order and a source always exists before its
        result, so that order is a topological order of the lineage graph.
        """
        found: dict[Handle, StoredResult] = {}
        pending = [handle]
        while pending:
            current = pending.pop()
            if current not in found:
                found[current] = self._store.describe(current)
                pending.extend(found[current].lineage.sources)
        return tuple(found[key] for key in self._store.handles() if key in found)

    def is_truncated(self, handle: Handle) -> bool:
        """True when the handle or any ancestor came from a procedure result cut at max_rows."""
        return any(self._cut_by_procedure(stored.lineage) for stored in self.lineage(handle))

    def summarize(self, handle: Handle) -> ResultSummary:
        stored = self._store.describe(handle)
        return ResultSummary(
            handle=handle,
            row_count=stored.row_count,
            truncated=self.is_truncated(handle),
            columns=stored.column_infos,
            stats=self._stats(stored),
            ids=self._ids(stored),
        )

    def cell(self, handle: Handle, row: int, column: str) -> JsonScalar:
        """The value in a row (from 1, in the handle's row order) of an existing column."""
        (value,) = self._single_row(
            f"SELECT {quote_identifier(column)} FROM {quote_identifier(handle)} "
            f"LIMIT 1 OFFSET {row - 1}"
        )
        return value

    def excerpt(self, handle: Handle) -> TableExcerpt:
        """The table for the comment phase: its rows up to model_max_rows, never the values
        of sensitive columns (architecture 002, section 5.1)."""
        stored = self._store.describe(handle)
        columns = zip(stored.table_schema.columns, stored.column_infos, strict=True)
        shown = [(column, info) for column, info in columns if not column.sensitive]
        rows = None
        if shown and stored.row_count <= self._model_max_rows:
            select = ", ".join(quote_identifier(column.name) for column, _ in shown)
            rows = self._rows(self._store.query(f"SELECT {select} FROM {quote_identifier(handle)}"))
        return TableExcerpt(
            handle=handle,
            row_count=stored.row_count,
            truncated=self.is_truncated(handle),
            columns=tuple(info for _, info in shown),
            hidden_columns=tuple(c.name for c in stored.table_schema.columns if c.sensitive),
            rows=rows,
        )

    def export(self, handle: Handle, max_rows: int) -> ExportedTable:
        stored = self._store.describe(handle)
        return ExportedTable(
            handle=handle,
            columns=stored.column_infos,
            rows=self._rows(self._store.fetch(handle, max_rows)),
            row_count=stored.row_count,
            truncated=stored.row_count > max_rows or self.is_truncated(handle),
        )

    @staticmethod
    def to_json_scalar(value: object) -> JsonScalar:
        """Converts one Arrow value to JSON without passing decimals through float."""
        if value is None or isinstance(value, bool | int | str):
            return value
        if isinstance(value, datetime | date):
            return value.isoformat()
        if isinstance(value, Decimal):
            # format(..., "f") keeps the scale and never switches to exponent notation.
            return format(value, "f")
        raise TypeError(f"unsupported value type {type(value).__name__}")

    def _stats(self, stored: StoredResult) -> dict[str, ColumnStats]:
        """min/max of time columns; dimension values only when the mode shows them."""
        table = quote_identifier(stored.handle)
        stats: dict[str, ColumnStats] = {}
        for column in self._visible(stored, ColumnRole.TIME):
            name = quote_identifier(column.name)
            low, high = self._single_row(f"SELECT min({name}), max({name}) FROM {table}")
            stats[column.name] = ColumnStats(min=low, max=high)
        if self._exposure.dimension_values:
            for column in self._visible(stored, ColumnRole.DIMENSION):
                stats[column.name] = self._dimension_stats(table, column)
        return stats

    def _ids(self, stored: StoredResult) -> dict[str, tuple[JsonScalar, ...]]:
        """Values of the id columns, which carry keys from one procedure to the next (D1)."""
        columns = self._visible(stored, ColumnRole.ID)
        if not columns or stored.row_count > self._model_max_rows:
            return {}
        select = ", ".join(quote_identifier(column.name) for column in columns)
        table = self._store.query(f"SELECT {select} FROM {quote_identifier(stored.handle)}")
        return {
            column.name: tuple(self.to_json_scalar(v) for v in values.to_pylist())
            for column, values in zip(columns, table.columns, strict=True)
        }

    @staticmethod
    def _visible(stored: StoredResult, role: ColumnRole) -> list[Column]:
        """Columns of the role that the model may see values of, in schema order."""
        return [c for c in stored.table_schema.columns if c.role is role and not c.sensitive]

    def _dimension_stats(self, table: str, column: Column) -> ColumnStats:
        name = quote_identifier(column.name)
        (count,) = self._single_row(f"SELECT count(DISTINCT {name}) FROM {table}")
        assert isinstance(count, int)
        if count > self._max_listed_values:
            return ColumnStats(distinct_count=count)
        values = self._store.query(
            f"SELECT DISTINCT {name} FROM {table} WHERE {name} IS NOT NULL ORDER BY {name}"
        )
        return ColumnStats(
            values=tuple(self.to_json_scalar(v) for v in values.column(0).to_pylist())
        )

    def _single_row(self, sql: str) -> tuple[JsonScalar, ...]:
        return self._rows(self._store.query(sql))[0]

    @staticmethod
    def _cut_by_procedure(lineage: Lineage) -> bool:
        return isinstance(lineage, ProcedureLineage) and lineage.truncated

    @classmethod
    def _rows(cls, table: pa.Table) -> Rows:
        columns = [column.to_pylist() for column in table.columns]
        return tuple(
            tuple(cls.to_json_scalar(value) for value in row) for row in zip(*columns, strict=True)
        )
