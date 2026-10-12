"""Compiles a validated specification into parameterized DuckDB SQL (architecture, 7.4).

Identifiers come only from the validated schema and pass through one quoting helper; every
value travels as a query parameter. The final ORDER BY always covers every output column, so
ties cannot reorder rows between runs (KTD21).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from data_concierge.core.errors import SpecValidationError
from data_concierge.core.sql import quote_identifier
from data_concierge.core.types import Column, ColumnKind, ColumnRole, ColumnType, TableSchema
from data_concierge.results.models import Handle
from data_concierge.transform.spec import (
    INT,
    AggregateStep,
    DeriveStep,
    Fact,
    FillPeriodsStep,
    LimitStep,
    Measure,
    PeriodUnit,
    PivotStep,
    SelectStep,
    SortDirection,
    SortStep,
    TransformStep,
)
from data_concierge.transform.validator import ValidatedSpec

type SortOrder = list[tuple[str, SortDirection]]

_INTERVALS: dict[PeriodUnit, str] = {
    "day": "INTERVAL 1 DAY",
    "week": "INTERVAL 7 DAY",
    "month": "INTERVAL 1 MONTH",
    "quarter": "INTERVAL 3 MONTH",
    "year": "INTERVAL 1 YEAR",
}


@dataclass(frozen=True, slots=True)
class CompiledQuery:
    sql: str
    params: tuple[object, ...]
    schema: TableSchema


class SqlQueryBuilder:
    """Collects CTEs s1, s2, ... and their parameters in textual order."""

    def __init__(self, source: str) -> None:
        self._ctes: list[str] = []
        self._params: list[object] = []
        self.current = quote_identifier(source)

    def add(self, select_sql: str, params: Sequence[object] = ()) -> None:
        name = f"s{len(self._ctes) + 1}"
        self._ctes.append(f"{name} AS (\n  {select_sql}\n)")
        self._params.extend(params)
        self.current = name

    def build(
        self, final_sql: str, params: Sequence[object] = ()
    ) -> tuple[str, tuple[object, ...]]:
        prefix = f"WITH {',\n'.join(self._ctes)}\n" if self._ctes else ""
        return prefix + final_sql, (*self._params, *params)


@dataclass(frozen=True, slots=True)
class _StepPlan:
    """A step and the schema the validator computed after it (only the rows column after a
    pivot, whose columns come from the data)."""

    step: TransformStep
    output: TableSchema


@dataclass(frozen=True, slots=True)
class _CompileState:
    """Row order and schema while the steps are compiled one after another."""

    order: SortOrder
    schema: TableSchema


class TransformCompiler:
    """Turns each step into one CTE; sort steps only change the tracked row order."""

    def compile(
        self, validated: ValidatedSpec, pivot_values: Sequence[object] = ()
    ) -> CompiledQuery:
        builder, state = self._compile_steps(validated, pivot_values)
        sql, params = builder.build(
            f"SELECT * FROM {builder.current}\nORDER BY {_order_by(state.order, state.schema)}"
        )
        return CompiledQuery(sql=sql, params=params, schema=state.schema)

    def compile_pivot_values(self, validated: ValidatedSpec, limit: int) -> CompiledQuery:
        """Distinct values of the pivot column, read before the final query (KTD22)."""
        pivot = validated.pivot
        assert pivot is not None
        builder, state = self._compile_steps(validated, (), stop=pivot)
        column = state.schema.require(pivot.columns)
        name = quote_identifier(column.name)
        sql, params = builder.build(
            f"SELECT DISTINCT {name} FROM {builder.current} "
            f"WHERE {name} IS NOT NULL ORDER BY {name} LIMIT ?",
            [limit],
        )
        return CompiledQuery(sql=sql, params=params, schema=TableSchema(columns=(column,)))

    def compile_fill_checks(
        self, validated: ValidatedSpec
    ) -> list[tuple[FillPeriodsStep, CompiledQuery]]:
        """Per fill_periods step, a query counting period values that do not start a period.

        Such rows never match a generated period, so filling would silently replace them.
        """
        checks = []
        for step in validated.spec.steps:
            if not isinstance(step, FillPeriodsStep):
                continue
            builder, _ = self._compile_steps(validated, (), stop=step)
            period = quote_identifier(step.column)
            sql, params = builder.build(
                f"SELECT count(*) FROM {builder.current} WHERE {period} IS NOT NULL "
                f"AND {period} <> CAST(date_trunc('{step.unit}', {period}) AS DATE)"
            )
            count = Column(name="count", type=INT, role=ColumnRole.MEASURE)
            checks.append(
                (step, CompiledQuery(sql=sql, params=params, schema=TableSchema(columns=(count,))))
            )
        return checks

    def compile_facts(self, validated: ValidatedSpec, handle: Handle) -> CompiledQuery:
        """One row with a column per fact, computed from the stored result."""
        facts = validated.spec.facts
        parts = [
            f"({_fact_sql(fact, quote_identifier(handle), validated.fact_types[fact.name])}) "
            f"AS {quote_identifier(fact.name)}"
            for fact in facts
        ]
        columns = tuple(
            Column(name=fact.name, type=validated.fact_types[fact.name], role=ColumnRole.MEASURE)
            for fact in facts
        )
        return CompiledQuery(
            sql="SELECT " + ",\n       ".join(parts), params=(), schema=TableSchema(columns=columns)
        )

    def _compile_steps(
        self,
        validated: ValidatedSpec,
        pivot_values: Sequence[object],
        stop: TransformStep | None = None,
    ) -> tuple[SqlQueryBuilder, _CompileState]:
        """Adds one CTE per step, stopping before `stop` when it is given."""
        builder = SqlQueryBuilder(validated.spec.source)
        state = _CompileState(order=[], schema=validated.source)
        for plan in _plans(validated):
            if plan.step is stop:
                break
            state = self._add_step(builder, plan, state, pivot_values)
        return builder, state

    def _add_step(
        self,
        builder: SqlQueryBuilder,
        plan: _StepPlan,
        state: _CompileState,
        pivot_values: Sequence[object],
    ) -> _CompileState:
        """Adds the step's CTE; returns the row order and the schema after the step."""
        prev, schema, output = builder.current, state.schema, plan.output
        match plan.step:
            case DeriveStep() as step:
                derived = _derive_sql(step, output.require(step.name).type)
                builder.add(f"SELECT *, {derived} AS {quote_identifier(step.name)} FROM {prev}")
                return _CompileState(state.order, output)
            case AggregateStep() as step:
                builder.add(_aggregate_sql(step, schema, prev))
                return _CompileState([(name, "asc") for name in step.group_by], output)
            case FillPeriodsStep() as step:
                sql, params = _fill_periods_sql(step, schema, prev)
                builder.add(sql, params)
                return _CompileState(state.order, output)
            case PivotStep() as step:
                sql, params, result = _pivot_sql(step, schema, prev, pivot_values)
                builder.add(sql, params)
                return _CompileState([(step.rows, "asc")], result)
            case SortStep() as step:
                # Keeps the running schema: after a pivot it holds the pivot columns.
                return _CompileState([(key.column, key.dir) for key in step.by], schema)
            case LimitStep() as step:
                builder.add(
                    f"SELECT * FROM {prev} ORDER BY {_order_by(state.order, schema)} LIMIT ?",
                    [step.n],
                )
                return state
            case SelectStep() as step:
                return _CompileState(_add_select(builder, step, state.order), output)


def _plans(validated: ValidatedSpec) -> list[_StepPlan]:
    outputs = (*validated.step_inputs[1:], validated.output)
    return [
        _StepPlan(step=step, output=output)
        for step, output in zip(validated.spec.steps, outputs, strict=False)
    ]


def _add_select(builder: SqlQueryBuilder, step: SelectStep, order: SortOrder) -> SortOrder:
    pairs = [(selected.column, selected.output_name) for selected in step.columns]
    columns = ", ".join(
        quote_identifier(source)
        if source == target
        else f"{quote_identifier(source)} AS {quote_identifier(target)}"
        for source, target in pairs
    )
    builder.add(f"SELECT {columns} FROM {builder.current}")
    # A source column may be selected twice; the sort order follows its first output name.
    first_name: dict[str, str] = {}
    for source, target in pairs:
        first_name.setdefault(source, target)
    return [(first_name[name], direction) for name, direction in order if name in first_name]


def sql_type(column_type: ColumnType) -> str:
    match column_type.kind:
        case ColumnKind.INT:
            return "BIGINT"
        case ColumnKind.STRING:
            return "VARCHAR"
        case ColumnKind.DATE:
            return "DATE"
        case ColumnKind.DATETIME:
            return "TIMESTAMP"
        case ColumnKind.BOOL:
            return "BOOLEAN"
        case ColumnKind.DECIMAL:
            return f"DECIMAL({column_type.precision},{column_type.scale})"


def _order_by(order: SortOrder, schema: TableSchema) -> str:
    """Sort keys first, then every remaining column, so the row order is total (KTD21)."""
    keys = [(name, direction) for name, direction in order if name in schema.names]
    used = {name for name, _ in keys}
    keys += [(name, "asc") for name in schema.names if name not in used]
    return ", ".join(f"{quote_identifier(name)} {direction.upper()}" for name, direction in keys)


def _derive_sql(step: DeriveStep, result_type: ColumnType) -> str:
    if step.fn == "arithmetic":
        assert step.left is not None
        assert step.right is not None
        left, right = quote_identifier(step.left), quote_identifier(step.right)
        if step.operator == "/":
            # Decimal division runs in DOUBLE; a zero divisor gives NULL instead of NaN.
            return f"CAST({left} / NULLIF({right}, 0) AS {sql_type(result_type)})"
        expression = f"({left} {step.operator} {right})"
        return (
            expression if result_type == INT else f"CAST({expression} AS {sql_type(result_type)})"
        )
    assert step.column is not None
    column = quote_identifier(step.column)
    if step.fn == "date_trunc":
        return f"CAST(date_trunc('{step.unit}', {column}) AS DATE)"
    return f"{step.fn}({column})"


def _measure_sql(measure: Measure, schema: TableSchema) -> str:
    column = quote_identifier(measure.column)
    match measure.fn:
        case "count":
            return f"count({column})"
        case "count_distinct":
            return f"count(DISTINCT {column})"
        case "sum" | "avg":
            # avg is DOUBLE and sum of integers is HUGEINT in DuckDB; cast to the validated type.
            return f"CAST({measure.fn}({column}) AS {sql_type(measure.output_type(schema))})"
        case "min" | "max":
            return f"{measure.fn}({column})"


def _aggregate_sql(step: AggregateStep, schema: TableSchema, prev: str) -> str:
    groups = [quote_identifier(name) for name in step.group_by]
    measures = [f"{_measure_sql(m, schema)} AS {quote_identifier(m.name)}" for m in step.measures]
    sql = f"SELECT {', '.join([*groups, *measures])} FROM {prev}"
    return sql + (f" GROUP BY {', '.join(groups)}" if groups else "")


def _fill_periods_sql(
    step: FillPeriodsStep, schema: TableSchema, prev: str
) -> tuple[str, list[object]]:
    period = quote_identifier(step.column)
    params: list[object] = []
    selected = []
    for column in schema.columns:
        name = quote_identifier(column.name)
        if column.name == step.column:
            selected.append(f"p.{name}")
        elif column.name in step.fill:
            selected.append(f"COALESCE(s.{name}, CAST(? AS {sql_type(column.type)})) AS {name}")
            params.append(step.fill[column.name])
        else:
            selected.append(f"s.{name}")
    periods = (
        f"SELECT CAST(range AS DATE) AS {period} "
        f"FROM range(CAST(? AS DATE), CAST(? AS DATE), {_INTERVALS[step.unit]})"
    )
    sql = (
        f"SELECT {', '.join(selected)} FROM ({periods}) p "
        f"LEFT JOIN {prev} s ON s.{period} = p.{period}"
    )
    return sql, [*params, step.from_, step.to]


def _pivot_sql(
    step: PivotStep, schema: TableSchema, prev: str, values: Sequence[object]
) -> tuple[str, list[object], TableSchema]:
    rows = schema.require(step.rows)
    value_type = step.value_type(schema)
    pivot_column = quote_identifier(step.columns)
    measure = quote_identifier(step.measure)
    rows_column = quote_identifier(rows.name)
    result = TableSchema(columns=(rows,))
    cells = []
    for value in values:
        name = _pivot_column_name(value)
        if not name:
            raise SpecValidationError(
                f"pivot: '{step.columns}' has an empty value, which cannot become a column name; "
                "pivot by another column"
            )
        try:
            result = result.with_column(name, value_type, ColumnRole.MEASURE)
        except SpecValidationError as exc:
            raise SpecValidationError(f"pivot column names clash: {exc}") from exc
        cells.append(
            f"CAST(sum(CASE WHEN {pivot_column} = ? THEN {measure} ELSE 0 END) "
            f"AS {sql_type(value_type)}) AS {quote_identifier(name)}"
        )
    sql = f"SELECT {', '.join([rows_column, *cells])} FROM {prev} GROUP BY {rows_column}"
    return sql, list(values), result


def _pivot_column_name(value: object) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)


def _fact_sql(fact: Fact, table: str, fact_type: ColumnType) -> str:
    if fact.fn == "count":
        return f"SELECT count(*) FROM {table}"
    assert fact.column is not None
    column = quote_identifier(fact.column)
    if fact.fn == "argmax":
        assert fact.return_ is not None
        # Ties return the smallest value of the return column (KTD21).
        returned = quote_identifier(fact.return_)
        return (
            f"SELECT {returned} FROM {table} WHERE {column} IS NOT NULL "
            f"ORDER BY {column} DESC, {returned} ASC LIMIT 1"
        )
    if fact.fn in ("sum", "avg"):
        return f"SELECT CAST({fact.fn}({column}) AS {sql_type(fact_type)}) FROM {table}"
    return f"SELECT {fact.fn}({column}) FROM {table}"
