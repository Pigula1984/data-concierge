"""SQL text helper shared by every module that builds DuckDB queries."""


# module-level: the result store, ResultService and QueryService (two layers) all build
# DuckDB SQL; no one class owns identifier quoting.
def quote_identifier(identifier: str) -> str:
    """Quotes a column or table name, doubling embedded quotes (KTD22).

    Every identifier, including column names taken from data values, goes through this one
    helper; values themselves always travel as query parameters.
    """
    return '"' + identifier.replace('"', '""') + '"'
