"""Values of results written into texts the user reads (architecture 002, section 6.1, D3)."""

from data_concierge.core.types import ColumnKind, ColumnRole, ColumnType
from data_concierge.results.models import JsonScalar

# The Polish digit group separator; a no-break space keeps the number on one line.
GROUP_SEPARATOR = "\N{NO-BREAK SPACE}"
NULL_TEXT = "brak danych"


class ValueFormatter:
    """Formats a value by its column's type: Polish decimal comma, digit groups from five
    integer digits, ids without groups, the scale of the column type, dates in ISO."""

    MIN_GROUPED_DIGITS = 5

    def format(self, value: JsonScalar, column_type: ColumnType, role: ColumnRole) -> str:
        if value is None:
            return NULL_TEXT
        if isinstance(value, bool):
            return "tak" if value else "nie"
        if role is ColumnRole.ID or column_type.kind not in (ColumnKind.INT, ColumnKind.DECIMAL):
            return str(value)
        # Decimals arrive as text with the column's scale (ResultService.to_json_scalar).
        return self._number(str(value))

    def _number(self, text: str) -> str:
        sign, digits = ("-", text[1:]) if text.startswith("-") else ("", text)
        whole, _, fraction = digits.partition(".")
        if len(whole) >= self.MIN_GROUPED_DIGITS:
            head = len(whole) % 3 or 3
            groups = [whole[:head]] + [whole[i : i + 3] for i in range(head, len(whole), 3)]
            whole = GROUP_SEPARATOR.join(groups)
        return sign + whole + (f",{fraction}" if fraction else "")
