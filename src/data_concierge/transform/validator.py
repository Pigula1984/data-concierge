"""Semantic validation of a transform specification against the source schema (7.3)."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from data_concierge.core.errors import SpecValidationError
from data_concierge.core.types import ColumnType, TableSchema
from data_concierge.results.models import ColumnStats
from data_concierge.transform.spec import (
    MAX_PIVOT_COLUMNS,
    LimitStep,
    PivotStep,
    SortStep,
    TransformSpec,
    TransformStep,
)


@dataclass(frozen=True, slots=True)
class ValidatedSpec:
    """A specification that passed validation, with the schema each step receives."""

    spec: TransformSpec
    source: TableSchema
    step_inputs: tuple[TableSchema, ...]
    # After a pivot only the rows column is known; the compiler adds the pivot columns.
    output: TableSchema
    fact_types: dict[str, ColumnType] = field(default_factory=dict)

    @property
    def pivot(self) -> PivotStep | None:
        return self.spec.pivot


class SpecValidator:
    """Tracks the schema step by step; errors name the step, the column and the alternatives."""

    def __init__(self, max_pivot_columns: int = MAX_PIVOT_COLUMNS) -> None:
        self._max_pivot_columns = max_pivot_columns

    def validate(
        self,
        spec: TransformSpec,
        source: TableSchema,
        source_stats: Mapping[str, ColumnStats] | None = None,
    ) -> ValidatedSpec:
        schema = source
        inputs: list[TableSchema] = []
        pivot: PivotStep | None = None
        limited = False
        for number, step in enumerate(spec.steps, start=1):
            if pivot is not None:
                _check_follows_pivot(number, step, pivot)
            inputs.append(schema)
            try:
                schema = step.output_schema(schema)
            except SpecValidationError as exc:
                raise SpecValidationError(f"{_step_label(number, step)}: {exc}") from exc
            if isinstance(step, PivotStep):
                # After a limit fewer values may reach the pivot than the source statistics show.
                if not limited:
                    self._check_pivot_width(number, step, source, source_stats or {})
                pivot = step
            limited = limited or isinstance(step, LimitStep)
        return ValidatedSpec(
            spec=spec,
            source=source,
            step_inputs=tuple(inputs),
            output=schema,
            fact_types=_fact_types(spec, schema, pivot),
        )

    def _check_pivot_width(
        self,
        number: int,
        step: PivotStep,
        source: TableSchema,
        source_stats: Mapping[str, ColumnStats],
    ) -> None:
        """Rejects early when the source statistics already show too many values; the
        compiler checks the exact count, since derived columns have no statistics."""
        stats = source_stats.get(step.columns) if step.columns in source.names else None
        count = stats.distinct_count if stats else None
        if count is not None and count > self._max_pivot_columns:
            raise SpecValidationError(
                f"{_step_label(number, step)}: '{step.columns}' has {count} values, more than the "
                f"limit of {self._max_pivot_columns} pivot columns"
            )


def _check_follows_pivot(number: int, step: TransformStep, pivot: PivotStep) -> None:
    allowed = isinstance(step, LimitStep) or (
        isinstance(step, SortStep) and all(key.column == pivot.rows for key in step.by)
    )
    if not allowed:
        raise SpecValidationError(
            f"{_step_label(number, step)}: pivot must be the last step; only sort by "
            f"'{pivot.rows}' and limit may follow it"
        )


def _step_label(number: int, step: TransformStep) -> str:
    return f"step {number} ({step.op})"


def _fact_types(
    spec: TransformSpec, schema: TableSchema, pivot: PivotStep | None
) -> dict[str, ColumnType]:
    types: dict[str, ColumnType] = {}
    for fact in spec.facts:
        if fact.name in types:
            raise SpecValidationError(f"fact '{fact.name}' is defined twice")
        used = [name for name in (fact.column, fact.return_) if name is not None]
        if pivot is not None and any(name not in schema.names for name in used):
            raise SpecValidationError(
                f"fact '{fact.name}': facts cannot use pivot columns; only '{pivot.rows}' "
                "and row count are available after a pivot"
            )
        try:
            types[fact.name] = fact.output_type(schema)
        except SpecValidationError as exc:
            raise SpecValidationError(f"fact '{fact.name}': {exc}") from exc
    return types
