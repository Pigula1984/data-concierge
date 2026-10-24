"""Anchors: values of results in texts for the user, filled in by the backend (architecture
002, section 6.1; plan KTD4).

The model writes `{r3.net_sales}` (the column of a one-row handle), `{r2[6].month}` (row 6,
counted from 1 in the handle's row order) or `{r2#rows}` (the handle's row count). Braces
that start with `r` and a digit are an attempted anchor and must have this shape; other text in
braces stays as it is.
"""

import re

from data_concierge.agent.formatting import ValueFormatter
from data_concierge.core.errors import AnchorError
from data_concierge.core.types import ColumnRole, ColumnType
from data_concierge.results.models import Handle
from data_concierge.results.service import ResultService

ROW_COUNT_TYPE = ColumnType.parse("int")
ANCHOR = re.compile(r"\{(?P<handle>r\d+)(?:#rows|(?:\[(?P<row>\d+)\])?\.(?P<column>[^{}]+))\}")
# Anything the model meant as an anchor, so a typo never reaches the user unfilled (R12).
ATTEMPTED_ANCHOR = re.compile(r"\{\s*[rR]\d[^{}]*\}")


class Anchors:
    """Checks and fills anchors with one set of rules, so a text that passed the check is
    always filled the same way."""

    def __init__(self, results: ResultService, formatter: ValueFormatter | None = None) -> None:
        self._results = results
        self._formatter = formatter or ValueFormatter()

    def problems(self, text: str) -> list[str]:
        """What is wrong with each anchor of the text, in the order they appear."""
        found = []
        for attempt in ATTEMPTED_ANCHOR.finditer(text):
            match = ANCHOR.fullmatch(attempt[0])
            try:
                if match is None:
                    raise AnchorError(
                        f"{attempt[0]}: not an anchor; write {{rN.column}}, {{rN[1].column}} "
                        "or {rN#rows}"
                    )
                self._value(match)
            except AnchorError as exc:
                found.append(str(exc))
        return found

    def fill(self, text: str) -> str:
        """The text with every anchor replaced by its formatted value."""
        return ANCHOR.sub(self._value, text)

    @staticmethod
    def handles(text: str) -> set[Handle]:
        return {match["handle"] for match in ANCHOR.finditer(text)}

    def _value(self, match: re.Match[str]) -> str:
        handle, row, name = match["handle"], match["row"], match["column"]
        available = self._results.handles()
        if handle not in available:
            raise AnchorError(
                f"{match[0]}: unknown handle {handle}; available handles: {', '.join(available)}"
            )
        stored = self._results.describe(handle)
        if name is None:
            return self._formatter.format(stored.row_count, ROW_COUNT_TYPE, ColumnRole.MEASURE)
        column = stored.table_schema.column(name)
        if column is None:
            raise AnchorError(
                f"{match[0]}: {handle} has no column {name}; columns: "
                f"{', '.join(stored.table_schema.names)}"
            )
        if stored.row_count == 0:
            raise AnchorError(f"{match[0]}: {handle} has no rows")
        if row is None and stored.row_count != 1:
            raise AnchorError(
                f"{match[0]}: {handle} has {stored.row_count} rows; for one of them name the "
                f"row, e.g. {{{handle}[1].{name}}}; for a total or a best value run a query "
                "that returns it in one row and anchor that handle"
            )
        index = 1 if row is None else int(row)
        if not 1 <= index <= stored.row_count:
            raise AnchorError(f"{match[0]}: {handle} has {stored.row_count} rows")
        value = self._results.cell(handle, index, column.name)
        return self._formatter.format(value, column.type, column.role)
