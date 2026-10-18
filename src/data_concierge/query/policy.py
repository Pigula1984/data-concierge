"""Checks the backend makes on a query from the model (architecture, section 7.3).

Security comes from the locked-down DuckDB connection (section 7.4a), so the query text is
not analysed. These checks cover what the engine does not decide: exactly one SELECT, result
columns the response contract accepts, and no computation over truncated procedure results.
"""

import re
from collections.abc import Sequence

from data_concierge.core.errors import QueryValidationError
from data_concierge.core.types import ColumnType, TableSchema
from data_concierge.results.models import DescribedColumn, Handle

COLUMN_NAME_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
# REAL arrives as FLOAT.
FLOAT_TYPES = frozenset({"FLOAT", "DOUBLE"})


class QueryPolicy:
    def check_statements(self, statement_types: Sequence[str]) -> None:
        """The text holds exactly one SELECT: no DDL, DML, settings or a second statement."""
        if list(statement_types) != ["SELECT"]:
            got = ", ".join(statement_types) or "nothing"
            raise QueryValidationError(
                f"the query must be exactly one SELECT statement; got: {got}"
            )

    def check_columns(self, columns: Sequence[DescribedColumn]) -> tuple[ColumnType, ...]:
        """Returns the catalog types of the result columns; reports every problem at once."""
        problems: list[str] = []
        types: list[ColumnType] = []
        for name, duckdb_type in columns:
            if not COLUMN_NAME_PATTERN.fullmatch(name):
                problems.append(
                    f"result column {name} needs an alias of English letters, digits and "
                    "underscores, e.g. AS net_sales"
                )
            try:
                types.append(ColumnType.from_duckdb(duckdb_type))
            except QueryValidationError:
                problems.append(self._type_problem(name, duckdb_type))
        duplicates = sorted(
            set(TableSchema.case_insensitive_duplicates([name for name, _ in columns]))
        )
        if duplicates:
            problems.append(
                f"result column names must differ ignoring case: {', '.join(duplicates)}"
            )
        if problems:
            raise QueryValidationError("\n".join(problems))
        return tuple(types)

    def check_sources(self, truncated: Sequence[Handle]) -> None:
        """Totals over a result cut at max_rows would look complete, so no query reads one."""
        if truncated:
            raise QueryValidationError(
                f"{', '.join(truncated)} come from a procedure result cut at max_rows, so the "
                "query would miss rows; call the procedure again with narrower parameters (e.g. "
                "a shorter period), or return the procedure result itself without a query"
            )

    @staticmethod
    def _type_problem(name: str, duckdb_type: str) -> str:
        if duckdb_type in FLOAT_TYPES:
            return f"column {name} is {duckdb_type}; CAST it to DECIMAL(38,6)"
        return f"column {name} has the unsupported type {duckdb_type}"
