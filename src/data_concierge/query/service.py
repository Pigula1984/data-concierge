"""QueryService: one SELECT from the model over result handles, checked and run reproducibly."""

from collections.abc import Sequence

from data_concierge.core.types import Column, ColumnKind, ColumnRole, ColumnType, TableSchema
from data_concierge.query.models import QueryContext, QuerySummary
from data_concierge.query.normalizer import QueryNormalizer
from data_concierge.query.parser import SqlParser
from data_concierge.query.rules import QueryPolicy
from data_concierge.results.models import DescribedColumn, Handle, QueryLineage
from data_concierge.results.service import ResultService

_ROLES_BY_KIND = {
    ColumnKind.DATE: ColumnRole.TIME,
    ColumnKind.DATETIME: ColumnRole.TIME,
    ColumnKind.DECIMAL: ColumnRole.MEASURE,
    ColumnKind.INT: ColumnRole.MEASURE,
}


class QueryService:
    """Parses, checks, binds and normalizes the query, then stores its result under a new handle.

    Every failure is a QueryError with a message the model can act on (architecture, 7.3).
    """

    def __init__(
        self,
        results: ResultService,
        parser: SqlParser,
        policy: QueryPolicy,
        normalizer: QueryNormalizer,
    ) -> None:
        self._results = results
        self._parser = parser
        self._policy = policy
        self._normalizer = normalizer

    def run(self, sql: str) -> QuerySummary:
        query = self._parser.parse(sql)
        handles = self._results.handles()
        context = QueryContext(
            handles=handles,
            truncated=frozenset(h for h in handles if self._results.is_truncated(h)),
        )
        sources = self._policy.check(query, context)
        columns = self._results.describe_query(self._parser.render(query.tree))
        types = self._policy.result_types(columns)
        executed = self._normalizer.normalize(query, columns)
        handle = self._results.materialize(
            executed,
            TableSchema(columns=self._result_columns(columns, types, sources)),
            QueryLineage(sources=sources, submitted_sql=sql, sql=executed),
        )
        summary = self._results.summarize(handle)
        return QuerySummary(**dict(summary), sources=sources, sql=executed)

    def _result_columns(
        self,
        columns: Sequence[DescribedColumn],
        types: Sequence[ColumnType],
        sources: Sequence[Handle],
    ) -> tuple[Column, ...]:
        """A column copied from a source keeps that column's role; others get one from the type."""
        inherited = {
            (column.name.casefold(), column.type): column.role
            for handle in reversed(sources)
            for column in self._results.describe(handle).table_schema.columns
        }
        return tuple(
            Column(
                name=name,
                type=column_type,
                role=inherited.get(
                    (name.casefold(), column_type),
                    _ROLES_BY_KIND.get(column_type.kind, ColumnRole.DIMENSION),
                ),
            )
            for (name, _), column_type in zip(columns, types, strict=True)
        )
