"""SQL text helpers shared by every module that builds DuckDB queries."""


def quote_identifier(identifier: str) -> str:
    """Quotes a column or table name, doubling embedded quotes (KTD22).

    Every identifier, including column names taken from data values, goes through this one
    helper; values themselves always travel as query parameters.
    """
    return '"' + identifier.replace('"', '""') + '"'
