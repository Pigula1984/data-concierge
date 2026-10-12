"""Result tables stored under handles, their lineage, and what the model sees of them."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

import pyarrow as pa
from pydantic import BaseModel, Field, JsonValue

from data_concierge.core.types import ColumnRole, ColumnType, TableSchema, arrow_fields

type Handle = str
type JsonScalar = str | int | bool | None
type Rows = tuple[tuple[JsonScalar, ...], ...]


class ProcedureLineage(BaseModel, frozen=True):
    """A result produced by calling a stored procedure."""

    step: Literal["procedure"] = "procedure"
    procedure: str
    params: dict[str, JsonValue]
    # The procedure returned more rows than the catalog's max_rows allowed.
    truncated: bool


class QueryRecord(BaseModel, frozen=True):
    """One executed query with its parameters, enough to rerun it without the model."""

    sql: str
    params: tuple[JsonValue, ...] = ()


class TransformLineage(BaseModel, frozen=True):
    """A result computed from another handle by a transform specification."""

    step: Literal["transform"] = "transform"
    source: Handle
    spec: dict[str, JsonValue]
    # Every query the step ran, in order (pivot runs a values query before the final one).
    queries: tuple[QueryRecord, ...]


type Lineage = Annotated[ProcedureLineage | TransformLineage, Field(discriminator="step")]


@dataclass(frozen=True, slots=True)
class ResultTable:
    """Arrow data whose column names and types match the schema exactly."""

    data: pa.Table
    schema: TableSchema
    lineage: Lineage

    def __post_init__(self) -> None:
        expected = self.schema.arrow_fields
        actual = arrow_fields(self.data.schema)
        if actual != expected:
            raise ValueError(f"arrow columns {actual} do not match the schema {expected}")


class StoredResult(BaseModel, frozen=True):
    """What the store keeps about a handle besides its data."""

    handle: Handle
    table_schema: TableSchema
    lineage: Lineage
    row_count: int


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
