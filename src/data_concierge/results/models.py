"""Result tables stored under handles, their lineage, and what the model sees of them."""

from dataclasses import dataclass
from typing import Annotated, Literal, NamedTuple

import pyarrow as pa
from pydantic import BaseModel, Field, JsonValue

from data_concierge.core.types import ColumnRole, ColumnType, TableSchema

# Handles are r1, r2, ... in creation order.
type Handle = str
type JsonScalar = str | int | bool | None
type Rows = tuple[tuple[JsonScalar, ...], ...]


class DescribedColumn(NamedTuple):
    """A result column as DuckDB binds it, before it is mapped to a catalog type."""

    name: str
    duckdb_type: str


class ProcedureLineage(BaseModel, frozen=True):
    """A result produced by calling a stored procedure."""

    step: Literal["procedure"] = "procedure"
    procedure: str
    params: dict[str, JsonValue]
    # The procedure returned more rows than the catalog's max_rows allowed.
    truncated: bool

    @property
    def sources(self) -> tuple[Handle, ...]:
        return ()


class QueryLineage(BaseModel, frozen=True):
    """A result computed by a SELECT over other handles, enough to rerun it without the model."""

    step: Literal["query"] = "query"
    sources: tuple[Handle, ...]
    # The SQL as the model wrote it and as the backend ran it (with the full ORDER BY).
    submitted_sql: str
    sql: str


type Lineage = Annotated[ProcedureLineage | QueryLineage, Field(discriminator="step")]


@dataclass(frozen=True, slots=True)
class ResultTable:
    """Arrow data whose column names and types match the schema exactly."""

    data: pa.Table
    schema: TableSchema
    lineage: Lineage

    def __post_init__(self) -> None:
        if not self.schema.matches_arrow(self.data.schema):
            raise ValueError(
                f"arrow columns {self.data.schema} do not match the schema "
                f"{self.schema.arrow_fields}"
            )


class StoredResult(BaseModel, frozen=True):
    """What the store keeps about a handle besides its data."""

    handle: Handle
    table_schema: TableSchema
    lineage: Lineage
    row_count: int

    @property
    def column_infos(self) -> tuple[ColumnInfo, ...]:
        return tuple(
            ColumnInfo(name=column.name, type=column.type, role=column.role)
            for column in self.table_schema.columns
        )


class ColumnInfo(BaseModel, frozen=True):
    name: str
    type: ColumnType
    role: ColumnRole


class ColumnStats(BaseModel, frozen=True):
    """min/max for time and measure columns; values or a count for dimension columns."""

    min: JsonScalar = None
    max: JsonScalar = None
    values: tuple[JsonScalar, ...] | None = None
    distinct_count: int | None = None


class ResultSummary(BaseModel, frozen=True):
    """The model's view of a result: schema, statistics and a preview, never the full data."""

    handle: Handle
    row_count: int
    truncated: bool
    columns: tuple[ColumnInfo, ...]
    stats: dict[str, ColumnStats]
    preview: Rows


class ExportedTable(BaseModel, frozen=True):
    """Full table data for the response; decimals as text, dates in ISO format."""

    handle: Handle
    columns: tuple[ColumnInfo, ...]
    rows: Rows
    row_count: int
    truncated: bool
