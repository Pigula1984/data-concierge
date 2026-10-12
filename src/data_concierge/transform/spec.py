"""Transform specification: a closed list of steps the model can ask for (architecture, 7.2).

Each step knows how to validate itself against the schema it receives and which schema it
produces. Compilation to SQL lives in TransformCompiler, not in the steps.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StringConstraints,
    model_validator,
)

from data_concierge.core.errors import SpecValidationError
from data_concierge.core.types import (
    MAX_DECIMAL_PRECISION,
    Column,
    ColumnKind,
    ColumnRole,
    ColumnType,
    TableSchema,
)
from data_concierge.results.models import Handle

NAME_PATTERN = r"^[a-z][a-z0-9_]*$"

# Names the model gives to new columns and facts (KTD22).
type ModelName = Annotated[str, StringConstraints(pattern=NAME_PATTERN)]
type PeriodUnit = Literal["day", "week", "month", "quarter", "year"]
type SortDirection = Literal["asc", "desc"]

INT = ColumnType(kind=ColumnKind.INT)
DATE = ColumnType(kind=ColumnKind.DATE)
# avg and division produce DOUBLE in DuckDB; the compiler casts them to this exact type (KTD7).
DERIVED_DECIMAL = ColumnType.decimal(MAX_DECIMAL_PRECISION, 6)
# Pivot columns allowed in one result; the validator and the executor share this limit.
MAX_PIVOT_COLUMNS = 50


class _SpecModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class DeriveStep(_SpecModel):
    """New column from a whitelisted function of one time column or two numeric columns."""

    op: Literal["derive"]
    name: ModelName
    fn: Literal["date_trunc", "year", "quarter", "month", "arithmetic"]
    column: str | None = Field(default=None, description="Time column for date functions.")
    unit: PeriodUnit | None = Field(default=None, description="Period for date_trunc.")
    operator: Literal["+", "-", "*", "/"] | None = None
    left: str | None = Field(default=None, description="Left column for arithmetic.")
    right: str | None = Field(default=None, description="Right column for arithmetic.")

    @model_validator(mode="after")
    def _check_arguments(self) -> Self:
        if self.fn == "arithmetic":
            if self.operator is None or self.left is None or self.right is None:
                raise ValueError("arithmetic needs 'operator', 'left' and 'right'")
            if self.column is not None or self.unit is not None:
                raise ValueError("arithmetic takes no 'column' or 'unit'")
            return self
        if self.column is None:
            raise ValueError(f"{self.fn} needs 'column'")
        if (self.fn == "date_trunc") != (self.unit is not None):
            raise ValueError("'unit' is required for date_trunc and only allowed there")
        if self.operator is not None or self.left is not None or self.right is not None:
            raise ValueError(f"{self.fn} takes no 'operator', 'left' or 'right'")
        return self

    def output_schema(self, schema: TableSchema) -> TableSchema:
        if self.fn == "arithmetic":
            return schema.with_column(self.name, self._arithmetic_type(schema), ColumnRole.MEASURE)
        assert self.column is not None
        source = schema.require(self.column, role=ColumnRole.TIME)
        if not source.type.is_temporal:
            raise SpecValidationError(
                f"{self.fn} needs a date column; '{source.name}' is {source.type}"
            )
        if self.fn == "date_trunc":
            return schema.with_column(self.name, DATE, ColumnRole.TIME)
        return schema.with_column(self.name, INT, ColumnRole.DIMENSION)

    def _arithmetic_type(self, schema: TableSchema) -> ColumnType:
        assert self.left is not None
        assert self.right is not None
        left, right = (_numeric(schema, name, "arithmetic") for name in (self.left, self.right))
        if self.operator == "/":
            return DERIVED_DECIMAL
        if left.type.kind is ColumnKind.INT and right.type.kind is ColumnKind.INT:
            return INT
        left_scale, right_scale = _scale(left.type), _scale(right.type)
        scale = left_scale + right_scale if self.operator == "*" else max(left_scale, right_scale)
        if scale > MAX_DECIMAL_PRECISION:
            raise SpecValidationError(f"the result of '{self.operator}' would need scale {scale}")
        return ColumnType.decimal(MAX_DECIMAL_PRECISION, scale)


class Measure(_SpecModel):
    name: ModelName
    fn: Literal["sum", "count", "count_distinct", "avg", "min", "max"]
    column: str

    def output_type(self, schema: TableSchema) -> ColumnType:
        if self.fn in ("count", "count_distinct"):
            schema.require(self.column)
            return INT
        if self.fn in ("min", "max"):
            return _ordered(schema, self.column, self.fn).type
        source = _numeric(schema, self.column, self.fn, role=ColumnRole.MEASURE)
        return DERIVED_DECIMAL if self.fn == "avg" else sum_type(source.type)


class AggregateStep(_SpecModel):
    """Groups rows and computes measures; without group_by it returns one total row."""

    op: Literal["aggregate"]
    group_by: tuple[str, ...] = ()
    measures: tuple[Measure, ...] = Field(min_length=1)

    def output_schema(self, schema: TableSchema) -> TableSchema:
        result = TableSchema(columns=())
        for name in self.group_by:
            column = schema.require(name)
            if column.role is ColumnRole.MEASURE:
                raise SpecValidationError(
                    f"cannot group by measure '{name}'; group by dimension, time or id columns"
                )
            result = result.with_column(column.name, column.type, column.role)
        for measure in self.measures:
            result = result.with_column(
                measure.name, measure.output_type(schema), ColumnRole.MEASURE
            )
        return result


class FillPeriodsStep(_SpecModel):
    """Adds the missing periods of a requested range, filling chosen measures with a value."""

    op: Literal["fill_periods"]
    column: str
    unit: PeriodUnit
    from_: date = Field(alias="from", description="First period start, inclusive.")
    to: date = Field(description="End of the range, exclusive.")
    fill: dict[str, Decimal] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        if self.from_ >= self.to:
            raise ValueError(f"'from' ({self.from_}) must be before 'to' ({self.to})")
        if _period_start(self.from_, self.unit) != self.from_:
            raise ValueError(f"'from' ({self.from_}) must be the first day of a {self.unit}")
        return self

    def output_schema(self, schema: TableSchema) -> TableSchema:
        period = schema.require(self.column, role=ColumnRole.TIME)
        if period.type != DATE:
            raise SpecValidationError(
                f"fill_periods needs a date column such as the result of date_trunc; "
                f"'{period.name}' is {period.type}"
            )
        others = [column for column in schema.columns if column.name != period.name]
        not_measures = [column.name for column in others if column.role is not ColumnRole.MEASURE]
        if not_measures:
            raise SpecValidationError(
                f"fill_periods needs '{period.name}' as the only non-measure column; "
                f"also found: {', '.join(not_measures)}"
            )
        for name in self.fill:
            _numeric(schema, name, "fill", role=ColumnRole.MEASURE)
        return schema


class PivotStep(_SpecModel):
    """Rows x columns x summed measure; must be the last step (KTD22)."""

    op: Literal["pivot"]
    rows: str
    columns: str
    measure: str

    def output_schema(self, schema: TableSchema) -> TableSchema:
        rows = schema.require(self.rows)
        pivot = schema.require(self.columns)
        for column in (rows, pivot):
            if column.role is ColumnRole.MEASURE:
                raise SpecValidationError(
                    f"pivot cannot use measure '{column.name}' as rows or columns"
                )
        if rows.name == pivot.name:
            raise SpecValidationError("pivot needs different columns for rows and columns")
        _numeric(schema, self.measure, "pivot", role=ColumnRole.MEASURE)
        # Pivot column names come from the data; only the rows column is known before execution.
        return TableSchema(columns=(rows,))

    def value_type(self, schema: TableSchema) -> ColumnType:
        return sum_type(schema.require(self.measure).type)


class SortKey(_SpecModel):
    column: str
    dir: SortDirection = "asc"


class SortStep(_SpecModel):
    op: Literal["sort"]
    by: tuple[SortKey, ...] = Field(min_length=1)

    def output_schema(self, schema: TableSchema) -> TableSchema:
        for key in self.by:
            schema.require(key.column)
        return schema


class LimitStep(_SpecModel):
    op: Literal["limit"]
    n: PositiveInt

    def output_schema(self, schema: TableSchema) -> TableSchema:
        return schema


class SelectColumn(_SpecModel):
    column: str
    alias: ModelName | None = None

    @property
    def output_name(self) -> str:
        return self.alias or self.column


class SelectStep(_SpecModel):
    """Chooses, orders and optionally renames columns."""

    op: Literal["select"]
    columns: tuple[SelectColumn, ...] = Field(min_length=1)

    def output_schema(self, schema: TableSchema) -> TableSchema:
        result = TableSchema(columns=())
        for selected in self.columns:
            column = schema.require(selected.column)
            result = result.with_column(selected.output_name, column.type, column.role)
        return result


type TransformStep = Annotated[
    DeriveStep | AggregateStep | FillPeriodsStep | PivotStep | SortStep | LimitStep | SelectStep,
    Field(discriminator="op"),
]


class Fact(_SpecModel):
    """A named scalar computed from the final result; the model quotes only these in comments."""

    name: ModelName
    fn: Literal["sum", "avg", "min", "max", "argmax", "count"]
    column: str | None = Field(default=None, description="Not used by count.")
    return_: str | None = Field(
        default=None, alias="return", description="argmax: column whose value is returned."
    )

    @model_validator(mode="after")
    def _check_arguments(self) -> Self:
        if self.fn == "count":
            if self.column is not None or self.return_ is not None:
                raise ValueError("count counts rows and takes no 'column' or 'return'")
            return self
        if self.column is None:
            raise ValueError(f"{self.fn} needs 'column'")
        if (self.fn == "argmax") != (self.return_ is not None):
            raise ValueError("'return' is required for argmax and only allowed there")
        return self

    def output_type(self, schema: TableSchema) -> ColumnType:
        if self.fn == "count":
            return INT
        assert self.column is not None
        if self.fn in ("min", "max"):
            return _ordered(schema, self.column, self.fn).type
        source = _numeric(schema, self.column, self.fn, role=ColumnRole.MEASURE)
        if self.fn == "argmax":
            assert self.return_ is not None
            return schema.require(self.return_).type
        return DERIVED_DECIMAL if self.fn == "avg" else sum_type(source.type)


class TransformSpec(_SpecModel):
    """Steps applied in order to one source handle, plus facts computed from the result."""

    source: Handle = Field(description="Handle of the source result, e.g. r1.")
    steps: tuple[TransformStep, ...] = ()
    facts: tuple[Fact, ...] = ()

    @property
    def pivot(self) -> PivotStep | None:
        return next((step for step in self.steps if isinstance(step, PivotStep)), None)


def sum_type(column_type: ColumnType) -> ColumnType:
    """sum widens decimals to precision 38 and keeps integers as integers."""
    if column_type.kind is ColumnKind.DECIMAL:
        return ColumnType.decimal(MAX_DECIMAL_PRECISION, _scale(column_type))
    return INT


def _scale(column_type: ColumnType) -> int:
    return column_type.scale or 0


def _numeric(
    schema: TableSchema, name: str, operation: str, role: ColumnRole | None = None
) -> Column:
    column = schema.require(name, role=role)
    if not column.type.is_numeric:
        measures = [c.name for c in schema.columns if c.role is ColumnRole.MEASURE]
        raise SpecValidationError(
            f"{operation} needs a numeric column; '{name}' is {column.type}. "
            f"Available measures: {', '.join(measures) or 'none'}"
        )
    return column


def _ordered(schema: TableSchema, name: str, operation: str) -> Column:
    column = schema.require(name)
    if column.role not in (ColumnRole.MEASURE, ColumnRole.TIME):
        raise SpecValidationError(
            f"{operation} needs a measure or time column; '{name}' has role '{column.role}'"
        )
    return column


def _period_start(day: date, unit: PeriodUnit) -> date:
    match unit:
        case "day":
            return day
        case "week":
            return date.fromordinal(day.toordinal() - day.weekday())
        case "month":
            return day.replace(day=1)
        case "quarter":
            return day.replace(month=(day.month - 1) // 3 * 3 + 1, day=1)
        case "year":
            return day.replace(month=1, day=1)
