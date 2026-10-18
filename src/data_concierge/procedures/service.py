"""ProcedureService: a catalog procedure called with the model's arguments, stored as a handle."""

from collections.abc import Mapping
from typing import Any, assert_never

from pydantic import ValidationError

from data_concierge.catalog.models import CatalogEntry, InjectSource
from data_concierge.core.context import RequestContext
from data_concierge.core.errors import ParameterValidationError
from data_concierge.procedures.models import ProcedureArguments
from data_concierge.procedures.repository import ProcedureRepository
from data_concierge.results.models import ProcedureLineage, ResultSummary, ResultTable
from data_concierge.results.service import ResultService


class ProcedureService:
    """Validates the arguments, adds the injected parameters, runs the procedure and saves the
    result with its lineage in the session's result store."""

    def __init__(self, repository: ProcedureRepository, results: ResultService) -> None:
        self._repository = repository
        self._results = results

    def call(
        self, entry: CatalogEntry, args: Mapping[str, object], context: RequestContext
    ) -> ResultSummary:
        arguments = self._validate(entry, args)
        injected = {
            param.name: self._injected(param.inject, context)
            for param in entry.params
            if param.inject is not None
        }
        result = self._repository.execute(
            entry, self._by_parameter_name(entry, arguments.model_dump()) | injected
        )
        # Injected values identify the caller, not the data, so the lineage leaves them out.
        lineage = ProcedureLineage(
            procedure=entry.procedure,
            params=self._by_parameter_name(entry, arguments.model_dump(mode="json")),
            truncated=result.truncated,
        )
        handle = self._results.save(
            ResultTable(data=result.data, schema=entry.table_schema, lineage=lineage)
        )
        return self._results.summarize(handle)

    @staticmethod
    def _validate(entry: CatalogEntry, args: Mapping[str, object]) -> ProcedureArguments:
        try:
            return ProcedureArguments.for_entry(entry).model_validate(args)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                for error in exc.errors()
            )
            raise ParameterValidationError(
                f"invalid arguments for {entry.tool}: {problems}"
            ) from exc

    @staticmethod
    def _by_parameter_name(entry: CatalogEntry, dumped: dict[str, Any]) -> dict[str, Any]:
        """Argument values keyed by the database parameter name instead of the argument name."""
        return {param.name: dumped[param.arg] for param in entry.model_params if param.arg}

    @staticmethod
    def _injected(source: InjectSource, context: RequestContext) -> object:
        match source:
            case InjectSource.USER_ID:
                return context.user_id
            case _:
                assert_never(source)
