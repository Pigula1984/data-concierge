"""Column types, roles and table schemas shared by the catalog, results and transforms."""

import re
from enum import StrEnum
from typing import Self

import pyarrow as pa
from pydantic import BaseModel, model_validator

from data_concierge.core.errors import CatalogError, SpecValidationError

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

    @model_validator(mode="after")
    def _check_decimal_arguments(self) -> Self:
        if self.kind is not ColumnKind.DECIMAL:
            if self.precision is not None or self.scale is not None:
                raise ValueError(f"type '{self.kind}' takes no precision or scale")
            return self
        if self.precision is None or self.scale is None:
            raise ValueError("type 'decimal' needs precision and scale")
        if (
            not 1 <= self.precision <= MAX_DECIMAL_PRECISION
            or not 0 <= self.scale <= self.precision
        ):
            raise ValueError(
                f"{self} needs 1 <= precision <= "
                f"{MAX_DECIMAL_PRECISION} and 0 <= scale <= precision"
            )
        return self

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


def _parse_decimal(text: str, args: list[str] | None) -> ColumnType:
    if args is None or len(args) != 2 or not all(arg.isdigit() for arg in args):
        raise CatalogError(f"type '{text}' must have the form decimal(precision,scale)")
    try:
        return ColumnType.decimal(int(args[0]), int(args[1]))
    except ValueError as exc:
        raise CatalogError(f"invalid type '{text}': {exc}") from exc


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


class Column(BaseModel, frozen=True):
    name: str
    type: ColumnType
    role: ColumnRole


class TableSchema(BaseModel, frozen=True):
    """Ordered, immutable list of columns of a result table."""

    columns: tuple[Column, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    def require(self, name: str, role: ColumnRole | None = None) -> Column:
        """Returns the named column, checking its role when one is given."""
        column = next((column for column in self.columns if column.name == name), None)
        if column is None:
            raise SpecValidationError(
                f"column '{name}' does not exist; available columns: {', '.join(self.names)}"
            )
        if role is not None and column.role is not role:
            raise SpecValidationError(
                f"column '{name}' has role '{column.role}', expected '{role}'; "
                f"available '{role}' columns: {', '.join(self._names_with_role(role)) or 'none'}"
            )
        return column

    def with_column(self, name: str, column_type: ColumnType, role: ColumnRole) -> Self:
        """Returns a new schema with the column appended."""
        if name in self.names:
            raise SpecValidationError(f"column '{name}' already exists")
        column = Column(name=name, type=column_type, role=role)
        return self.model_copy(update={"columns": (*self.columns, column)})

    def _names_with_role(self, role: ColumnRole) -> list[str]:
        return [column.name for column in self.columns if column.role is role]
