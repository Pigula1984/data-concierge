"""Loading the agent catalog from its YAML file and the procedure metadata from SQL Server."""

from pathlib import Path
from typing import Protocol

import yaml
from pydantic import ValidationError
from pydantic_core import ErrorDetails

from data_concierge.catalog.models import Catalog, DataRange, ProcedureMetadata, SqlField
from data_concierge.core.errors import CatalogError
from data_concierge.core.sqlserver import SqlExecutor

# A user-defined type such as dbo.Name is described by its system base type.
_PARAMETERS_SQL = """
SELECT p.name,
       CASE WHEN t.type_name IN ('decimal', 'numeric')
            THEN CONCAT(t.type_name, '(', p.precision, ',', p.scale, ')')
            ELSE t.type_name END
FROM sys.parameters AS p
CROSS APPLY (SELECT TYPE_NAME(p.system_type_id) AS type_name) AS t
WHERE p.object_id = ?
ORDER BY p.parameter_id
"""
_COLUMNS_SQL = """
SELECT name, system_type_name, error_message
FROM sys.dm_exec_describe_first_result_set_for_object(?, 0)
ORDER BY column_ordinal
"""


class CatalogRepository(Protocol):
    def load(self) -> Catalog: ...


class ProcedureMetadataRepository(Protocol):
    def describe(self, procedure: str) -> ProcedureMetadata | None:
        """None when the procedure does not exist or the account cannot execute it."""
        ...

    def read_data_range(self, procedure: str) -> DataRange: ...


class SqlServerMetadataRepository:
    """Reads the metadata with the agent's own account: EXECUTE is enough to see
    sys.parameters and the result set description of a procedure (plan KTD16)."""

    def __init__(self, client: SqlExecutor) -> None:
        self._client = client

    def describe(self, procedure: str) -> ProcedureMetadata | None:
        object_id = self._client.fetch("SELECT OBJECT_ID(?, 'P')", [procedure]).rows[0][0]
        if object_id is None:
            return None
        params = self._client.fetch(_PARAMETERS_SQL, [object_id]).rows
        columns = self._client.fetch(_COLUMNS_SQL, [object_id]).rows
        # No rows, or one row with an error, when the result set cannot be described.
        describable = bool(columns) and all(error is None for _, _, error in columns)
        return ProcedureMetadata(
            params=tuple(
                SqlField(str(name).removeprefix("@"), str(sql_type)) for name, sql_type in params
            ),
            columns=(
                tuple(SqlField(str(name), str(sql_type)) for name, sql_type, _ in columns)
                if describable
                else None
            ),
        )

    def read_data_range(self, procedure: str) -> DataRange:
        """The first row of the range procedure: the earliest and the latest date."""
        first, last = self._client.fetch_procedure(procedure).rows[0]
        return DataRange.model_validate({"first": first, "last": last})


class YamlCatalogRepository:
    """Reads the catalog from a YAML file whose root is the list of procedure entries."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> Catalog:
        try:
            data = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise CatalogError(f"cannot read catalog file '{self._path}': {exc}") from exc
        if not isinstance(data, list):
            raise CatalogError(f"catalog file '{self._path}' must contain a list of procedures")
        try:
            return Catalog.model_validate({"entries": data})
        except ValidationError as exc:
            problems = "; ".join(self._describe(error, data) for error in exc.errors())
            raise CatalogError(f"invalid catalog file '{self._path}': {problems}") from exc

    @staticmethod
    def _describe(error: ErrorDetails, entries: list[object]) -> str:
        """Formats one Pydantic error as `<procedure>.<field path>: <message>`."""
        location = list(error["loc"])
        if location[:1] == ["entries"]:
            location.pop(0)
        index = location[0] if location else None
        if isinstance(index, int):
            entry = entries[index] if index < len(entries) else None
            procedure = entry.get("procedure") if isinstance(entry, dict) else None
            location[0] = procedure or f"entry {index + 1}"
        cause = error.get("ctx", {}).get("error")
        message = str(cause) if isinstance(cause, ValueError) else error["msg"]
        return f"{'.'.join(str(part) for part in location)}: {message}"
