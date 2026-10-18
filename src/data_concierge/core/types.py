"""Column types, roles and table schemas shared by the catalog, results and queries."""

import re
from collections import Counter
from collections.abc import Sequence
from enum import StrEnum
from typing import Self

import pyarrow as pa
from pydantic import BaseModel, model_serializer, model_validator

from data_concierge.core.errors import CatalogError, QueryValidationError

MAX_DECIMAL_PRECISION = 38

_TYPE_PATTERN = re.compile(r"^(?P<name>[a-z0-9_]+)(?:\((?P<args>[^)]*)\))?$")


class ColumnKind(StrEnum):
    INT = "int"
    STRING = "string"
    DATE = "date"
    DATETIME = "datetime"
    BOOL = "bool"
    DECIMAL = "decimal"


class ColumnRole(StrEnum):
    ID = "id"
    DIMENSION = "dimension"
    MEASURE = "measure"
    TIME = "time"


_ARROW_TYPES: dict[ColumnKind, pa.DataType] = {
    ColumnKind.INT: pa.int64(),
    ColumnKind.STRING: pa.string(),
    ColumnKind.DATE: pa.date32(),
    ColumnKind.DATETIME: pa.timestamp("us"),
    ColumnKind.BOOL: pa.bool_(),
}


class ColumnType(BaseModel, frozen=True):
    """Catalog column type; `decimal` carries precision and scale, other kinds carry neither."""

    kind: ColumnKind
    precision: int | None = None
    scale: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_text(cls, data: object) -> object:
        """Accepts the text form (`decimal(19,4)`) used in YAML and JSON."""
        if not isinstance(data, str):
            return data
        try:
            parsed = cls.parse(data)
        except CatalogError as exc:
            raise ValueError(str(exc)) from exc
        return {"kind": parsed.kind, "precision": parsed.precision, "scale": parsed.scale}

    @model_validator(mode="after")
    def _check_decimal_arguments(self) -> Self:
        if self.kind is not ColumnKind.DECIMAL:
            if self.precision is not None or self.scale is not None:
                raise ValueError(f"type '{self.kind}' takes no precision or scale")
            return self
        if self.precision is None or self.scale is None:
            raise ValueError("type 'decimal' needs precision and scale")
        bounds_error = _decimal_bounds_error(self.precision, self.scale)
        if bounds_error is not None:
            raise ValueError(f"{self} {bounds_error}")
        return self

    @model_serializer
    def _to_text(self) -> str:
        return str(self)

    @classmethod
    def decimal(cls, precision: int, scale: int) -> ColumnType:
        return cls(kind=ColumnKind.DECIMAL, precision=precision, scale=scale)

    @classmethod
    def parse(cls, text: str) -> ColumnType:
        """Parses a catalog type such as `int` or `decimal(19,4)`."""
        name, args = _split_type(text)
        try:
            kind = ColumnKind(name)
        except ValueError:
            raise CatalogError(
                f"unknown column type '{text}'; expected one of: {', '.join(ColumnKind)}"
            ) from None
        if kind is ColumnKind.DECIMAL:
            return _parse_decimal(text, args)
        if args is not None:
            raise CatalogError(f"column type '{text}' takes no arguments")
        return cls(kind=kind)

    @property
    def is_numeric(self) -> bool:
        return self.kind in (ColumnKind.INT, ColumnKind.DECIMAL)

    @property
    def is_temporal(self) -> bool:
        return self.kind in (ColumnKind.DATE, ColumnKind.DATETIME)

    @property
    def arrow_type(self) -> pa.DataType:
        if self.precision is not None and self.scale is not None:
            return pa.decimal128(self.precision, self.scale)
        return _ARROW_TYPES[self.kind]

    def __str__(self) -> str:
        if self.kind is ColumnKind.DECIMAL:
            return f"decimal({self.precision},{self.scale})"
        return self.kind.value


def _split_type(text: str) -> tuple[str, list[str] | None]:
    """Splits `name(a, b)` into its lowercase name and stripped arguments."""
    match = _TYPE_PATTERN.match(re.sub(r"\s+", "", text).lower())
    if match is None:
        raise CatalogError(f"cannot parse type '{text}'")
    args = match.group("args")
    return match.group("name"), None if args is None else args.split(",")


def _decimal_bounds_error(precision: int, scale: int) -> str | None:
    if 1 <= precision <= MAX_DECIMAL_PRECISION and 0 <= scale <= precision:
        return None
    return f"needs 1 <= precision <= {MAX_DECIMAL_PRECISION} and 0 <= scale <= precision"


def _parse_decimal(text: str, args: list[str] | None) -> ColumnType:
    if args is None or len(args) != 2 or not all(arg.isdecimal() for arg in args):
        raise CatalogError(f"type '{text}' must have the form decimal(precision,scale)")
    precision, scale = int(args[0]), int(args[1])
    bounds_error = _decimal_bounds_error(precision, scale)
    if bounds_error is not None:
        raise CatalogError(f"invalid type '{text}': {bounds_error}")
    return ColumnType.decimal(precision, scale)


_SQL_SERVER_FIXED_TYPES: dict[str, ColumnType] = {
    "money": ColumnType.decimal(19, 4),
    "smallmoney": ColumnType.decimal(10, 4),
    "tinyint": ColumnType(kind=ColumnKind.INT),
    "smallint": ColumnType(kind=ColumnKind.INT),
    "int": ColumnType(kind=ColumnKind.INT),
    "bigint": ColumnType(kind=ColumnKind.INT),
    "char": ColumnType(kind=ColumnKind.STRING),
    "nchar": ColumnType(kind=ColumnKind.STRING),
    "varchar": ColumnType(kind=ColumnKind.STRING),
    "nvarchar": ColumnType(kind=ColumnKind.STRING),
    "bit": ColumnType(kind=ColumnKind.BOOL),
    "date": ColumnType(kind=ColumnKind.DATE),
    "datetime": ColumnType(kind=ColumnKind.DATETIME),
    "datetime2": ColumnType(kind=ColumnKind.DATETIME),
}

# numeric is a synonym of decimal in SQL Server (e.g. SalesOrderDetail.LineTotal is numeric(38,6)).
_SQL_SERVER_DECIMAL_TYPES = frozenset({"decimal", "numeric"})


def parse_sql_server_type(type_name: str) -> ColumnType:
    """Maps a SQL Server type such as `nvarchar(50)` or `money` to its catalog equivalent."""
    name, args = _split_type(type_name)
    if name in _SQL_SERVER_DECIMAL_TYPES:
        return _parse_decimal(type_name, args)
    column_type = _SQL_SERVER_FIXED_TYPES.get(name)
    if column_type is None:
        raise CatalogError(f"SQL Server type '{type_name}' has no catalog equivalent")
    return column_type


DUCKDB_INTEGER_TYPES = frozenset(
    {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "UHUGEINT",
    }
)

_DUCKDB_FIXED_TYPES: dict[str, ColumnType] = {
    "VARCHAR": ColumnType(kind=ColumnKind.STRING),
    "DATE": ColumnType(kind=ColumnKind.DATE),
    "TIMESTAMP": ColumnType(kind=ColumnKind.DATETIME),
    "BOOLEAN": ColumnType(kind=ColumnKind.BOOL),
} | {name: ColumnType(kind=ColumnKind.INT) for name in DUCKDB_INTEGER_TYPES}
_DUCKDB_DECIMAL_PATTERN = re.compile(r"DECIMAL\((\d+),(\d+)\)")


def parse_duckdb_type(type_name: str) -> ColumnType:
    """Maps a DuckDB result type such as `DECIMAL(38,4)` or `HUGEINT` to its catalog equivalent.

    Integer types other than BIGINT map to `int`; the query normalizer casts them to BIGINT.
    """
    column_type = _DUCKDB_FIXED_TYPES.get(type_name)
    if column_type is not None:
        return column_type
    decimal = _DUCKDB_DECIMAL_PATTERN.fullmatch(type_name)
    if decimal is not None:
        return ColumnType.decimal(int(decimal.group(1)), int(decimal.group(2)))
    raise QueryValidationError(f"DuckDB type '{type_name}' has no catalog equivalent")


class Column(BaseModel, frozen=True):
    name: str
    type: ColumnType
    role: ColumnRole


class TableSchema(BaseModel, frozen=True):
    """Ordered, immutable list of columns of a result table.

    Column names are unique ignoring case, because DuckDB resolves identifiers case-insensitively.
    """

    columns: tuple[Column, ...]

    @model_validator(mode="after")
    def _check_unique_names(self) -> Self:
        clashes = case_insensitive_duplicates(self.names)
        if clashes:
            raise ValueError(f"duplicate column names (case-insensitive): {', '.join(clashes)}")
        return self

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    @property
    def arrow_fields(self) -> list[tuple[str, pa.DataType]]:
        """Names and Arrow types an Arrow table must have to hold data of this schema."""
        return [(column.name, column.type.arrow_type) for column in self.columns]


def case_insensitive_duplicates(names: Sequence[str]) -> list[str]:
    """Names that clash with another name when case is ignored, in their original order."""
    counts = Counter(name.casefold() for name in names)
    return [name for name in names if counts[name.casefold()] > 1]


def arrow_fields(schema: pa.Schema) -> list[tuple[str, pa.DataType]]:
    """Names and types of an Arrow schema, comparable with TableSchema.arrow_fields."""
    return [(field.name, field.type) for field in schema]
