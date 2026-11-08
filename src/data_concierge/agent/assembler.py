"""ResponseAssembler: the model's answer turned into the response of section 9."""

import re
from collections.abc import Iterable, Sequence
from typing import cast

from data_concierge.agent.anchors import Anchors
from data_concierge.agent.formatting import ValueFormatter
from data_concierge.agent.models import (
    AgentAnswer,
    CandidatesFrom,
    ChartRequest,
    ClarificationRequest,
    TableRef,
)
from data_concierge.api.schemas import (
    Candidate,
    Clarification,
    ProcedureStep,
    ProvenanceStep,
    QueryStep,
    Response,
    ResponseChart,
    ResponseColumn,
    ResponseTable,
)
from data_concierge.core.types import Column, ColumnKind, TableSchema
from data_concierge.results.models import Handle, JsonScalar, ProcedureLineage, StoredResult
from data_concierge.results.service import ResultService

ERROR_MESSAGE = "Nie udało się przygotować odpowiedzi. Identyfikator zgłoszenia: {correlation_id}."
REFUSAL_MESSAGE = "Tej prośby nie wykonam. Odpowiadam tylko na pytania o dane firmy z katalogu."
# Segments of a candidate description value: between commas, or around a spaced dash.
SEGMENT_BREAK = re.compile(r",|\s[-\u2013]\s")


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
            chart=self._chart(answer.chart),
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

    @staticmethod
    def refusal(session_id: str) -> Response:
        """A refused turn: the fixed message, without any text of the model."""
        return Response(status="unsupported", session_id=session_id, message=REFUSAL_MESSAGE)

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
                description=self._description(
                    (column, value)
                    for position, (column, value) in enumerate(zip(columns, row, strict=True))
                    if position != index
                ),
            )
            for row in rows
        )

    def _description(self, cells: Iterable[tuple[Column, JsonScalar]]) -> str:
        """The values joined by commas. A text value that an earlier value already holds as a
        whole segment is left out, e.g. the color after the product name "HL Mountain Frame -
        Black, 38"; a value inside a code such as "FR-M94S-42" stays."""
        parts: list[str] = []
        seen: set[str] = set()
        for column, value in cells:
            text = self._formatter.format(value, column.type, column.role)
            if column.type.kind is ColumnKind.STRING and text.casefold() in seen:
                continue
            parts.append(text)
            seen |= {segment.strip().casefold() for segment in SEGMENT_BREAK.split(text)}
        return ", ".join(parts)

    def _chart(self, request: ChartRequest | None) -> ResponseChart | None:
        """The chart with its title filled in and its columns spelled as in the table, so the
        page finds them among the table's columns (the checks matched them in any case)."""
        if request is None:
            return None
        schema = self._results.describe(request.handle).table_schema
        return ResponseChart(
            handle=request.handle,
            kind=request.kind,
            x=self._spelled(schema, request.x),
            y=tuple(self._spelled(schema, name) for name in request.y),
            series=None if request.series is None else self._spelled(schema, request.series),
            title=self._anchors.fill(request.title),
        )

    @staticmethod
    def _spelled(schema: TableSchema, name: str) -> str:
        column = schema.column(name)
        assert column is not None, "the answer validator checked the columns"
        return column.name

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
