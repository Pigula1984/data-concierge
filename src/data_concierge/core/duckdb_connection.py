"""Locked-down DuckDB connections (architecture, section 7.4a)."""

import duckdb


def connect_locked(*settings: str) -> duckdb.DuckDBPyConnection:
    """An in-memory connection without file, network or extension access.

    The caller's settings run before `lock_configuration`, after which no setting can change.
    """
    connection = duckdb.connect(":memory:")
    for statement in (
        "SET enable_external_access = false",
        "SET autoinstall_known_extensions = false",
        "SET autoload_known_extensions = false",
        *settings,
        "SET lock_configuration = true",
    ):
        connection.execute(statement)
    return connection
