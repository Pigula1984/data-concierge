"""QueryService: one SELECT from the model over result handles, run in the session's DuckDB."""

from collections.abc import Sequence

from data_concierge.core.sql import quote_identifier
from data_concierge.core.types import (
    DUCKDB_INTEGER_TYPES,
    Column,
    ColumnKind,
    ColumnRole,
    ColumnType,
    TableSchema,
)
from data_concierge.query.models import QuerySummary
from data_concierge.query.policy import QueryPolicy
from data_concierge.results.models import DescribedColumn, Handle, QueryLineage
from data_concierge.results.service import ResultService

_ROLES_BY_KIND = {
    ColumnKind.DATE: ColumnRole.TIME,
    ColumnKind.DATETIME: ColumnRole.TIME,
    ColumnKind.DECIMAL: ColumnRole.MEASURE,
    ColumnKind.INT: ColumnRole.MEASURE,
}


class QueryService:
    """Checks the query, runs it and stores its result under a new handle.

    Every failure is a QueryError with a message the model can act on (architecture, 7.3).
    """

    def __init__(self, results: ResultService, policy: QueryPolicy) -> None:
        self._results = results
        self._policy = policy

    def run(self, sql: str) -> QuerySummary:
        self._policy.check_statements(self._results.statement_types(sql))
        statement = self._without_trailing_semicolons(sql)
        columns = self._results.describe_query(statement)
        types = self._policy.check_columns(columns)
        sources = self._results.sources(statement)
        self._policy.check_sources([h for h in sources if self._results.is_truncated(h)])
        executed = self._with_bigint_integers(statement, columns)
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
        """A column copied from a source keeps that column's role and sensitivity; others get a
        role from the type and are sensitive when they hold text and a source has a sensitive
        column (architecture 002, section 5.3).

        A copied column is sensitive when any source has a sensitive column of its name and type,
        because the backend does not analyse which source the query copied it from.
        """
        source_columns = [
            column
            for handle in sources
            for column in self._results.describe(handle).table_schema.columns
        ]
        copied: dict[tuple[str, ColumnType], Column] = {}
        for column in source_columns:
            key = (column.name.casefold(), column.type)
            first = copied.setdefault(key, column)
            if column.sensitive and not first.sensitive:
                copied[key] = first.model_copy(update={"sensitive": True})
        reads_sensitive = any(column.sensitive for column in source_columns)
        return tuple(
            self._result_column(name, column_type, copied, reads_sensitive)
            for (name, _), column_type in zip(columns, types, strict=True)
        )

    @staticmethod
    def _result_column(
        name: str,
        column_type: ColumnType,
        copied: dict[tuple[str, ColumnType], Column],
        reads_sensitive: bool,
    ) -> Column:
        source = copied.get((name.casefold(), column_type))
        if source is not None:
            return Column(name=name, type=column_type, role=source.role, sensitive=source.sensitive)
        return Column(
            name=name,
            type=column_type,
            role=_ROLES_BY_KIND.get(column_type.kind, ColumnRole.DIMENSION),
            sensitive=reads_sensitive and column_type.kind is ColumnKind.STRING,
        )

    @staticmethod
    def _without_trailing_semicolons(sql: str) -> str:
        """The statement without the `;` that would break the subquery the backend wraps it in.

        A comment after the `;` stays and ends in a syntax error the model can fix; removing it
        would need a SQL lexer for `--` inside string literals.
        """
        statement = sql.rstrip()
        while statement.endswith(";"):
            statement = statement[:-1].rstrip()
        return statement

    @staticmethod
    def _with_bigint_integers(sql: str, columns: Sequence[DescribedColumn]) -> str:
        """Integer columns other than BIGINT (HUGEINT from sum, INTEGER from literals) are cast in
        an outer SELECT, which keeps the order of its subquery."""
        casts = [
            f"CAST({quote_identifier(name)} AS BIGINT) AS {quote_identifier(name)}"
            for name, duckdb_type in columns
            if duckdb_type in DUCKDB_INTEGER_TYPES and duckdb_type != "BIGINT"
        ]
        if not casts:
            return sql
        # The newline ends a trailing -- comment before the closing parenthesis.
        return f"SELECT * REPLACE ({', '.join(casts)}) FROM ({sql}\n) AS result"
