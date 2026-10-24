"""Calling catalog procedures in SQL Server (Adapter, docs/standards/wzorce-projektowe.md)."""

from collections.abc import Mapping, Sequence
from typing import Protocol

import pyarrow as pa

from data_concierge.catalog.models import CatalogEntry
from data_concierge.core.errors import ProcedureError
from data_concierge.core.sqlserver import SqlExecutor
from data_concierge.core.types import TableSchema
from data_concierge.procedures.models import ProcedureResult


class ProcedureRepository(Protocol):
    def execute(self, entry: CatalogEntry, params: Mapping[str, object]) -> ProcedureResult:
        """Runs the procedure with values keyed by database parameter name (without `@`)."""
        ...


class SqlServerProcedureRepository:
    """Runs `EXEC` with every catalog parameter bound by name and builds the Arrow table
    from the catalog types (plan KTD14)."""

    def __init__(self, client: SqlExecutor) -> None:
        self._client = client

    def execute(self, entry: CatalogEntry, params: Mapping[str, object]) -> ProcedureResult:
        rows = self._client.fetch_procedure(
            entry.procedure,
            {param.name: params.get(param.name) for param in entry.params},
            max_rows=entry.max_rows,
        )
        schema = entry.table_schema
        # Checked on every call: the only check for procedures the database cannot describe.
        if [name.casefold() for name in rows.columns] != [n.casefold() for n in schema.names]:
            raise ProcedureError(
                f"{entry.procedure} returned columns {', '.join(rows.columns)}; "
                f"the catalog expects {', '.join(schema.names)}"
            )
        return ProcedureResult(
            data=self._arrow_table(entry.procedure, schema, rows.rows), truncated=rows.has_more
        )

    @staticmethod
    def _arrow_table(
        procedure: str, schema: TableSchema, rows: Sequence[tuple[object, ...]]
    ) -> pa.Table:
        """Builds each column with its catalog type; pyodbc returns Decimal for money/decimal."""
        arrays = []
        for index, column in enumerate(schema.columns):
            try:
                arrays.append(pa.array([row[index] for row in rows], type=column.type.arrow_type))
            except (pa.ArrowInvalid, pa.ArrowTypeError, OverflowError) as exc:
                raise ProcedureError(
                    f"{procedure}: column {column.name} does not fit the catalog type "
                    f"{column.type}",
                    detail=str(exc),
                ) from exc
        return pa.Table.from_arrays(arrays, schema=pa.schema(schema.arrow_fields))
