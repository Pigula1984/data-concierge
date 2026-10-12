"""Applies a transform specification to a stored result: validate, execute, describe."""

from pydantic import BaseModel

from data_concierge.core.errors import SpecValidationError
from data_concierge.results.models import ResultSummary
from data_concierge.results.service import ResultService
from data_concierge.transform.executor import FactValue, TransformExecutor
from data_concierge.transform.spec import (
    AggregateStep,
    FillPeriodsStep,
    PivotStep,
    TransformSpec,
)
from data_concierge.transform.validator import SpecValidator

# Steps whose result would look complete even though the input was cut at max_rows (KTD23).
AGGREGATING_STEPS = (AggregateStep, PivotStep, FillPeriodsStep)


class TransformSummary(BaseModel, frozen=True):
    """What the model gets back: the new handle's summary and the computed facts."""

    result: ResultSummary
    facts: dict[str, FactValue]


class TransformService:
    def __init__(
        self, results: ResultService, executor: TransformExecutor, validator: SpecValidator
    ) -> None:
        self._results = results
        self._executor = executor
        self._validator = validator

    def apply(self, spec: TransformSpec) -> TransformSummary:
        source = self._results.describe(spec.source)
        if self._results.is_truncated(spec.source):
            _reject_aggregation_of_truncated(spec)
        pivot = spec.pivot
        stats = (
            self._results.column_stats(spec.source, {pivot.columns}) if pivot is not None else None
        )
        validated = self._validator.validate(spec, source.table_schema, stats)
        execution = self._executor.run(validated)
        return TransformSummary(
            result=self._results.summarize(execution.handle), facts=execution.facts
        )


def _reject_aggregation_of_truncated(spec: TransformSpec) -> None:
    aggregating: list[str] = sorted(
        {step.op for step in spec.steps if isinstance(step, AGGREGATING_STEPS)}
    )
    if spec.facts:
        aggregating.append("facts")
    if aggregating:
        raise SpecValidationError(
            f"{spec.source} is incomplete: the procedure returned more rows than its max_rows "
            f"limit, so {', '.join(aggregating)} would give wrong totals. Call the procedure "
            "again with narrower parameters, for example a shorter period."
        )
