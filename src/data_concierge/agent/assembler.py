"""ResponseAssembler: the model's answer turned into the response of section 9."""

from collections.abc import Sequence
from typing import cast

from data_concierge.agent.anchors import Anchors
from data_concierge.agent.formatting import ValueFormatter
from data_concierge.agent.models import AgentAnswer, CandidatesFrom, ClarificationRequest, TableRef
from data_concierge.api.schemas import (
    Candidate,
    Clarification,
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
    """Adds the data and provenance of the returned handles from the session's result store
    and fills the anchors of the texts; the model only names handles and values (plan KTD17,
    architecture 002, section 6.1)."""

    def __init__(self, results: ResultService, max_rows: int) -> None:
        self._results = results
        self._formatter = ValueFormatter()
        self._anchors = Anchors(results, self._formatter)
        self._max_rows = max_rows

    def assemble(self, answer: AgentAnswer, session_id: str, correlation_id: str) -> Response:
        if answer.status == "error":
            return self.failure(session_id, correlation_id)
        anchored = {handle for text in answer.texts for handle in self._anchors.handles(text)}
        clarification = answer.clarification
        if clarification is not None and clarification.candidates_from is not None:
            anchored.add(clarification.candidates_from.handle)
        return Response(
            status=answer.status,
            session_id=session_id,
            message=self._anchors.fill(answer.message),
            tables=tuple(self._table(ref) for ref in answer.tables),
            assumptions=tuple(self._anchors.fill(text) for text in answer.assumptions),
            clarification=self._clarification(clarification),
            provenance=self._provenance([*(ref.handle for ref in answer.tables), *anchored]),
        )

    @staticmethod
    def failure(session_id: str, correlation_id: str) -> Response:
        """A failed turn: a generic message with the correlation id and no details."""
        return Response(
            status="error",
            session_id=session_id,
            message=ERROR_MESSAGE.format(correlation_id=correlation_id),
        )

    def _clarification(self, request: ClarificationRequest | None) -> Clarification | None:
        if request is None:
            return None
        source = request.candidates_from
        return Clarification(
            question=self._anchors.fill(request.question),
            candidates=() if source is None else self._candidates(source),
        )

    def _candidates(self, source: CandidatesFrom) -> tuple[Candidate, ...]:
        """One candidate per row in the handle's order: the id and the other columns' values
        (architecture 002, section 4.3). The validator allowed only a small handle."""
        schema = self._results.describe(source.handle).table_schema
        id_column = schema.column(source.id_column)
        assert id_column is not None, "the answer validator checked the column"
        columns, index = schema.columns, schema.columns.index(id_column)
        rows = self._results.export(source.handle, self._max_rows).rows
        return tuple(
            Candidate(
                # The validator allowed only an int or text id column.
                id=cast(int | str, row[index]),
                description=", ".join(
                    self._formatter.format(value, column.type, column.role)
                    for position, (column, value) in enumerate(zip(columns, row, strict=True))
                    if position != index
                ),
            )
            for row in rows
        )

    def _table(self, ref: TableRef) -> ResponseTable:
        exported = self._results.export(ref.handle, self._max_rows)
        return ResponseTable(
            handle=ref.handle,
            title=self._anchors.fill(ref.title),
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
