"""SQL Server access through pyodbc; the only module that imports the driver.

The procedure adapter and the metadata repository both run their statements through
SqlServerClient, so connecting, the time limit and error messages without login data live in
one place (architecture, sections 5.5 and 8).
"""

import re
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from typing import Protocol

import pyodbc
from pydantic import SecretStr

from data_concierge.core.errors import ProcedureError, ProcedureTimeoutError
from data_concierge.core.settings import Settings

_LOGIN_TIMEOUT_SECONDS = 15
# SQLSTATE of an ODBC statement that exceeded the query timeout.
_TIMEOUT_SQLSTATE = "HYT00"
# SQLSTATE classes of connection and authorization failures (08xxx, 28000): their text names
# the server and the login, so the caller gets a fixed message instead.
_CONNECTION_SQLSTATE_CLASSES = ("08", "28")
# A pyodbc message is "[SQLSTATE] [Microsoft][ODBC Driver 18 ...][SQL Server]text (native)
# (SQLFunction)", with further diagnostic records after "; [".
_NATIVE_ERROR = re.compile(r"\((-?\d+)\)(?:\s*\(SQL\w+\))?\s*$")


class SqlExecutor(Protocol):
    """Runs SQL Server statements; the repositories depend on this, not on pyodbc."""

    def fetch(
        self, sql: str, params: Sequence[object] = (), max_rows: int | None = None
    ) -> SqlRows: ...

    def fetch_procedure(
        self,
        procedure: str,
        params: Mapping[str, object] | None = None,
        max_rows: int | None = None,
    ) -> SqlRows: ...


@dataclass(frozen=True, slots=True)
class SqlRows:
    """The first result set of a statement; `has_more` when rows past `max_rows` were left."""

    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    has_more: bool


class SqlServerClient:
    """Runs one statement per connection and returns its first result set.

    pyodbc connections are not shared between threads (threadsafety 1) and agent tools run in
    worker threads, so every call opens its own connection (plan KTD12). The query timeout is
    set on the connection before the cursor exists, which is when the driver applies it (KTD13).
    """

    def __init__(self, connection_string: SecretStr, timeout_seconds: int) -> None:
        self._connection_string = connection_string
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls, settings: Settings) -> SqlServerClient:
        """The client of the agent account, with the procedure time limit."""
        return cls(cls._agent_connection_string(settings), settings.procedure_timeout_seconds)

    def fetch_procedure(
        self,
        procedure: str,
        params: Mapping[str, object] | None = None,
        max_rows: int | None = None,
    ) -> SqlRows:
        """Runs `SET NOCOUNT ON; EXEC <proc> @Name = ?, ...` with every value bound by name
        (plan KTD14); keys are parameter names without `@`."""
        names = list(params or {})
        arguments = ", ".join(f"@{name} = ?" for name in names)
        statement = f"SET NOCOUNT ON; EXEC {self._quote_name(procedure)} {arguments}".rstrip()
        return self.fetch(statement, [(params or {})[name] for name in names], max_rows)

    def fetch(
        self, sql: str, params: Sequence[object] = (), max_rows: int | None = None
    ) -> SqlRows:
        """Reads at most `max_rows` rows; later rows and result sets are discarded unread."""
        try:
            with closing(self._connect()) as connection:
                connection.timeout = self._timeout_seconds
                cursor = connection.cursor()
                cursor.execute(sql, *params)
                if cursor.description is None:
                    raise ProcedureError("the statement returned no result set")
                columns = tuple(column[0] for column in cursor.description)
                rows = cursor.fetchall() if max_rows is None else cursor.fetchmany(max_rows + 1)
        except pyodbc.Error as exc:
            raise self._domain_error(exc) from exc
        has_more = max_rows is not None and len(rows) > max_rows
        return SqlRows(
            columns=columns,
            rows=tuple(tuple(row) for row in rows[:max_rows]),
            has_more=has_more,
        )

    def _connect(self) -> pyodbc.Connection:
        return pyodbc.connect(
            self._connection_string.get_secret_value(),
            autocommit=True,
            timeout=_LOGIN_TIMEOUT_SECONDS,
        )

    def _domain_error(self, exc: pyodbc.Error) -> ProcedureError:
        """The error number and SQLSTATE without the driver's text, which may quote values from
        the data (architecture 002, section 6.2) and names the login on connection failures
        (section 5.5); the text of other errors stays in `detail`."""
        sqlstate = str(exc.args[0]) if exc.args else ""
        if sqlstate == _TIMEOUT_SQLSTATE:
            return ProcedureTimeoutError(
                f"the call exceeded the {self._timeout_seconds} s time limit and was stopped"
            )
        if sqlstate.startswith(_CONNECTION_SQLSTATE_CLASSES):
            return ProcedureError(
                f"cannot connect to SQL Server (SQLSTATE {sqlstate}); check the server and the "
                "agent account in the configuration"
            )
        message = str(exc.args[1]) if len(exc.args) > 1 else str(exc)
        native = _NATIVE_ERROR.search(message.split("; [", 1)[0])
        number = f" {native.group(1)}" if native else ""
        return ProcedureError(f"SQL Server error{number} (SQLSTATE {sqlstate})", detail=message)

    @classmethod
    def _agent_connection_string(cls, settings: Settings) -> SecretStr:
        """ODBC connection string of the agent account; secret because it holds the password."""
        attributes = {
            "DRIVER": settings.sql_driver,
            "SERVER": settings.sql_server,
            "DATABASE": settings.sql_database,
            "UID": settings.sql_user,
            "PWD": settings.sql_password.get_secret_value(),
            "Encrypt": "yes",
            "TrustServerCertificate": "yes" if settings.sql_trust_server_certificate else "no",
            "APP": "data-concierge",
        }
        return SecretStr(
            ";".join(f"{key}={cls._braced(value)}" for key, value in attributes.items())
        )

    @staticmethod
    def _braced(value: str) -> str:
        """An ODBC attribute value in braces, so `;` or `=` in a password cannot end it."""
        return "{" + value.replace("}", "}}") + "}"

    @staticmethod
    def _quote_name(name: str) -> str:
        """Quotes a possibly schema-qualified name such as `api.SalesOrder_List`."""
        return ".".join("[" + part.replace("]", "]]") + "]" for part in name.split("."))
