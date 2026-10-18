"""SQL text helpers shared by every module that builds DuckDB queries."""

import string

_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def quote_identifier(identifier: str) -> str:
    """Quotes a column or table name, doubling embedded quotes (KTD22).

    Every identifier, including column names taken from data values, goes through this one
    helper; values themselves always travel as query parameters.
    """
    return '"' + identifier.replace('"', '""') + '"'


def fold_identifier(identifier: str) -> str:
    """The form DuckDB compares identifiers in: ASCII letters lowercased, nothing else changed.

    Python's casefold also maps e.g. the long s and the Kelvin sign to ASCII letters, so a
    quoted CTE name could look like a system view name that DuckDB treats as different.
    """
    return identifier.translate(_ASCII_LOWER)
