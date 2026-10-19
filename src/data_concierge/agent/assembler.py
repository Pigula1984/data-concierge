"""ResponseAssembler: the model's answer turned into the response of section 9."""

from collections.abc import Sequence

from data_concierge.agent.models import AgentAnswer, TableRef
from data_concierge.api.schemas import (
    ProcedureStep,
    ProvenanceStep,
    QueryStep,
    Response,
    ResponseColumn,
    ResponseTable,
)
from data_concierge.results.models import Handle, ProcedureLineage, StoredResult
from data_concierge.results.service import ResultService

ERROR_MESSAGE = "Nie udało się przygotować odpowiedzi. Identyfikator zgłoszenia: {correlation_id}."


class ResponseAssembler:
    """Adds the data and provenance of the returned handles from the session's result store;
    the model only names the handles (plan KTD17)."""

    def __init__(self, results: ResultService, max_rows: int) -> None:
        self._results = results
        self._max_rows = max_rows

    def assemble(self, answer: AgentAnswer, session_id: str, correlation_id: str) -> Response:
        """An `error` answer gets a generic message with the correlation id and no details."""
        if answer.status == "error":
            return Response(
                status="error",
                session_id=session_id,
                message=ERROR_MESSAGE.format(correlation_id=correlation_id),
            )
        return Response(
            status=answer.status,
            session_id=session_id,
            message=answer.message,
            tables=tuple(self._table(ref) for ref in answer.tables),
            assumptions=answer.assumptions,
            clarification=answer.clarification,
            provenance=self._provenance([ref.handle for ref in answer.tables]),
        )

    def _table(self, ref: TableRef) -> ResponseTable:
        exported = self._results.export(ref.handle, self._max_rows)
        return ResponseTable(
            handle=ref.handle,
            title=ref.title,
            columns=tuple(ResponseColumn(name=c.name, type=c.type) for c in exported.columns),
            rows=exported.rows,
            row_count=exported.row_count,
            truncated=exported.truncated,
        )

    def _provenance(self, handles: Sequence[Handle]) -> tuple[ProvenanceStep, ...]:
        """Ancestors of all returned handles, each once, in handle order (section 7.6)."""
        ancestors = {
            stored.handle: stored for handle in handles for stored in self._results.lineage(handle)
        }
        return tuple(
            self._step(ancestors[handle])
            for handle in self._results.handles()
            if handle in ancestors
        )

    @staticmethod
    def _step(stored: StoredResult) -> ProvenanceStep:
        lineage = stored.lineage
        if isinstance(lineage, ProcedureLineage):
            return ProcedureStep(
                handle=stored.handle,
                name=lineage.procedure,
                params=lineage.params,
                rows=stored.row_count,
                truncated=lineage.truncated,
            )
        return QueryStep(
            handle=stored.handle,
            sources=lineage.sources,
            submitted_sql=lineage.submitted_sql,
            sql=lineage.sql,
            rows=stored.row_count,
        )
