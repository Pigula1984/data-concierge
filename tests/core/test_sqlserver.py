from collections.abc import Sequence

import pyodbc
import pytest
from pydantic import SecretStr

from data_concierge.core.errors import ProcedureError, ProcedureTimeoutError
from data_concierge.core.sqlserver import SqlRows, SqlServerClient


class RecordingClient(SqlServerClient):
    """Records the statement and values instead of connecting to SQL Server."""

    def __init__(self) -> None:
        super().__init__(SecretStr("unused"), timeout_seconds=30)
        self.calls: list[tuple[str, list[object]]] = []

    def fetch(
        self, sql: str, params: Sequence[object] = (), max_rows: int | None = None
    ) -> SqlRows:
        self.calls.append((sql, list(params)))
        return SqlRows(columns=(), rows=(), has_more=False)


def test_procedure_call_binds_every_parameter_by_name() -> None:
    client = RecordingClient()

    client.fetch_procedure("api.SalesOrder_List", {"DateFrom": "2024-01-01", "UserId": 7})

    assert client.calls == [
        (
            "SET NOCOUNT ON; EXEC [api].[SalesOrder_List] @DateFrom = ?, @UserId = ?",
            ["2024-01-01", 7],
        )
    ]


def test_procedure_without_parameters_and_bracket_in_its_name() -> None:
    client = RecordingClient()

    client.fetch_procedure("api.Odd]Name")

    assert client.calls == [("SET NOCOUNT ON; EXEC [api].[Odd]]Name]", [])]


def _error(sqlstate: str, text: str) -> ProcedureError:
    return SqlServerClient(SecretStr("unused"), timeout_seconds=30)._domain_error(
        pyodbc.Error(sqlstate, text)
    )


def test_driver_prefixes_and_codes_are_removed_from_sql_errors() -> None:
    error = _error(
        "42000",
        "[42000] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]Could not find stored "
        "procedure 'api.X'. (2812) (SQLExecDirectW); [42000] [Microsoft][ODBC Driver 18 for "
        "SQL Server][SQL Server]Statement(s) could not be prepared. (8180)",
    )

    assert str(error) == "SQL Server error 42000: Could not find stored procedure 'api.X'."


@pytest.mark.parametrize("sqlstate", ["08001", "28000"])
def test_connection_and_login_failures_hide_server_and_account(sqlstate: str) -> None:
    error = _error(
        sqlstate,
        f"[{sqlstate}] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]Login failed for "
        "user 'agent_reader'. (18456) (SQLDriverConnect)",
    )

    assert "agent_reader" not in str(error)
    assert str(error).startswith(f"cannot connect to SQL Server (SQLSTATE {sqlstate})")


def test_query_timeout_maps_to_timeout_error() -> None:
    assert isinstance(_error("HYT00", "[HYT00] Query timeout expired"), ProcedureTimeoutError)
