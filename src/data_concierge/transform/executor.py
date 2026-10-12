"""Runs compiled specifications in the session's DuckDB result store."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from data_concierge.core.errors import SpecValidationError
from data_concierge.results.models import Handle, QueryRecord, TransformLineage, to_json_scalar
from data_concierge.results.service import ResultService
from data_concierge.transform.compiler import CompiledQuery, TransformCompiler
from data_concierge.transform.spec import MAX_PIVOT_COLUMNS
from data_concierge.transform.validator import ValidatedSpec

type FactValue = Decimal | int | str | bool | date | datetime | None


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    handle: Handle
    facts: dict[str, FactValue]


class TransformExecutor(Protocol):
    def run(self, validated: ValidatedSpec) -> ExecutionResult: ...


class DuckDBExecutor:
    """Compiles the specification to SQL and stores its result under a new handle.

    Queries and the new handle go through the session's ResultService, which owns the locked
    DuckDB connection, its lock and its time limit.
    """

    def __init__(
        self,
        results: ResultService,
        compiler: TransformCompiler | None = None,
        max_pivot_columns: int = MAX_PIVOT_COLUMNS,
    ) -> None:
        self._results = results
        self._compiler = compiler or TransformCompiler()
        self._max_pivot_columns = max_pivot_columns

    def run(self, validated: ValidatedSpec) -> ExecutionResult:
        queries: list[QueryRecord] = []
        self._check_fill_periods(validated)
        pivot_values: tuple[object, ...] = ()
        if validated.pivot is not None:
            pivot_values = self._pivot_values(validated, queries)
        compiled = self._compiler.compile(validated, pivot_values)
        queries.append(_record(compiled))
        lineage = TransformLineage(
            source=validated.spec.source,
            spec=validated.spec.model_dump(mode="json", by_alias=True),
            queries=tuple(queries),
        )
        handle = self._results.materialize(compiled.sql, compiled.params, compiled.schema, lineage)
        return ExecutionResult(handle=handle, facts=self._facts(validated, handle))

    def _check_fill_periods(self, validated: ValidatedSpec) -> None:
        for step, check in self._compiler.compile_fill_checks(validated):
            unaligned = self._results.query(check.sql, check.params).column(0)[0].as_py()
            if unaligned:
                raise SpecValidationError(
                    f"fill_periods: {unaligned} values of '{step.column}' are not the first day "
                    f"of a {step.unit}; derive the column with date_trunc (unit '{step.unit}') "
                    "and aggregate by it before fill_periods"
                )

    def _pivot_values(
        self, validated: ValidatedSpec, queries: list[QueryRecord]
    ) -> tuple[object, ...]:
        pivot = validated.pivot
        assert pivot is not None
        compiled = self._compiler.compile_pivot_values(validated, limit=self._max_pivot_columns + 1)
        queries.append(_record(compiled))
        values = tuple(self._results.query(compiled.sql, compiled.params).column(0).to_pylist())
        if len(values) > self._max_pivot_columns:
            raise SpecValidationError(
                f"pivot: '{pivot.columns}' has more than {self._max_pivot_columns} values; "
                "aggregate first or pivot by a column with fewer values"
            )
        return values

    def _facts(self, validated: ValidatedSpec, handle: Handle) -> dict[str, FactValue]:
        if not validated.spec.facts:
            return {}
        compiled = self._compiler.compile_facts(validated, handle)
        row: dict[str, FactValue] = self._results.query(compiled.sql, compiled.params).to_pylist()[
            0
        ]
        return row


def _record(compiled: CompiledQuery) -> QueryRecord:
    return QueryRecord(sql=compiled.sql, params=tuple(to_json_scalar(p) for p in compiled.params))
