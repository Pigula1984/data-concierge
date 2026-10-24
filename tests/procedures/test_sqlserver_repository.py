import time
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal

import pyarrow as pa
import pyarrow.compute as pc
import pytest
from pydantic import SecretStr

from data_concierge.catalog.models import Catalog
from data_concierge.core.errors import ProcedureError, ProcedureTimeoutError
from data_concierge.core.settings import Settings
from data_concierge.core.sqlserver import SqlRows, SqlServerClient
from data_concierge.procedures.repository import SqlServerProcedureRepository

JANUARY_2024 = {"DateFrom": date(2024, 1, 1), "DateTo": date(2024, 2, 1), "CustomerID": None}


@pytest.fixture
def repository(agent_client: SqlServerClient) -> SqlServerProcedureRepository:
    return SqlServerProcedureRepository(agent_client)


@pytest.mark.integration
def test_money_arrives_as_exact_decimal_with_catalog_types(
    repository: SqlServerProcedureRepository, admin_client: SqlServerClient, catalog: Catalog
) -> None:
    result = repository.execute(catalog.entry("api.SalesOrder_List"), {**JANUARY_2024, "UserId": 1})

    assert result.data.schema.field("SubTotal").type == pa.decimal128(19, 4)
    assert result.data.schema.field("OrderDate").type == pa.date32()
    reference = admin_client.fetch(
        "SELECT COUNT(*), SUM(SubTotal) FROM Sales.SalesOrderHeader "
        "WHERE OrderDate >= '2024-01-01' AND OrderDate < '2024-02-01'"
    ).rows[0]
    assert (result.data.num_rows, pc.sum(result.data["SubTotal"]).as_py()) == reference
    assert reference == (400, Decimal("2087872.4623"))
    assert not result.truncated


@pytest.mark.integration
def test_result_above_max_rows_is_cut_and_marked_truncated(
    repository: SqlServerProcedureRepository, catalog: Catalog
) -> None:
    entry = catalog.entry("api.SalesOrder_List").model_copy(update={"max_rows": 100})

    result = repository.execute(
        entry, {"DateFrom": date(2024, 1, 1), "DateTo": date(2025, 1, 1), "UserId": 1}
    )

    assert result.data.num_rows == 100
    assert result.truncated


@pytest.mark.integration
def test_existing_dbo_procedure_returns_catalog_columns(
    repository: SqlServerProcedureRepository, catalog: Catalog
) -> None:
    entry = catalog.entry("dbo.uspGetEmployeeManagers")

    result = repository.execute(entry, {"BusinessEntityID": 100})

    assert result.data.column_names == list(entry.table_schema.names)
    assert result.data.num_rows > 0


@pytest.mark.integration
def test_query_over_time_limit_raises_timeout_error(settings: Settings) -> None:
    client = SqlServerClient.from_settings(
        settings.model_copy(update={"procedure_timeout_seconds": 1})
    )
    started = time.monotonic()

    with pytest.raises(ProcedureTimeoutError):
        client.fetch("WAITFOR DELAY '00:00:05'; SELECT 1")

    assert time.monotonic() - started < 2


@pytest.mark.integration
def test_missing_procedure_raises_error_without_password(
    repository: SqlServerProcedureRepository, catalog: Catalog, settings: Settings
) -> None:
    entry = catalog.entry("api.Customer_Search").model_copy(
        update={"procedure": "api.DoesNotExist"}
    )

    with pytest.raises(ProcedureError) as raised:
        repository.execute(entry, {"Name": "x", "UserId": 1})

    assert str(raised.value) == "SQL Server error 2812 (SQLSTATE 42000)"
    assert raised.value.detail is not None
    assert "Could not find stored procedure 'api.DoesNotExist'" in raised.value.detail


@pytest.mark.integration
def test_login_failure_names_neither_account_nor_password(settings: Settings) -> None:
    # A password with ";" and "}" also proves the connection string escapes them: the driver
    # attempts the login (SQLSTATE 28000) instead of rejecting a malformed string.
    wrong = "Wrong;pass}word1"
    client = SqlServerClient.from_settings(
        settings.model_copy(update={"sql_password": SecretStr(wrong)})
    )

    with pytest.raises(ProcedureError) as raised:
        client.fetch("SELECT 1")

    message = str(raised.value)
    assert message.startswith("cannot connect to SQL Server (SQLSTATE 28000)")
    assert wrong not in message
    assert settings.sql_user not in message


@pytest.mark.integration
@pytest.mark.parametrize(
    "statement",
    [
        "SELECT TOP 1 SalesOrderID FROM Sales.SalesOrderHeader",
        "EXEC HumanResources.uspUpdateEmployeeLogin 1, NULL, N'x', '2020-01-01', 0",
    ],
)
def test_agent_account_cannot_read_tables_or_run_write_procedures(
    agent_client: SqlServerClient, statement: str
) -> None:
    with pytest.raises(ProcedureError, match=r"^SQL Server error 229 ") as raised:
        agent_client.fetch(statement)

    assert raised.value.detail is not None
    assert "permission was denied" in raised.value.detail


@pytest.mark.integration
def test_customer_search_finds_every_namesake(
    repository: SqlServerProcedureRepository, catalog: Catalog
) -> None:
    result = repository.execute(
        catalog.entry("api.Customer_Search"), {"Name": "Mohamed Pal", "UserId": 1}
    )

    assert sorted(result.data["CustomerID"].to_pylist()) == [12698, 20550, 28763]


@pytest.mark.integration
def test_product_search_returns_every_variant(
    repository: SqlServerProcedureRepository, catalog: Catalog
) -> None:
    result = repository.execute(
        catalog.entry("api.Product_Search"), {"Name": "HL Mountain Frame", "UserId": 1}
    )

    assert result.data.num_rows == 10
    assert set(result.data["Subcategory"].to_pylist()) == {"Mountain Frames"}


@pytest.mark.integration
@pytest.mark.parametrize(
    ("params", "column", "expected"),
    [
        pytest.param({"City": "Berlin"}, "City", {"Berlin"}, id="city"),
        pytest.param({"CountryRegionCode": "DE"}, "CountryRegion", {"Germany"}, id="country"),
        pytest.param({"CustomerType": "store"}, "CustomerType", {"store"}, id="customer type"),
    ],
)
def test_customer_list_filters(
    repository: SqlServerProcedureRepository,
    catalog: Catalog,
    params: dict[str, object],
    column: str,
    expected: set[str],
) -> None:
    result = repository.execute(catalog.entry("api.Customer_List"), {**params, "UserId": 1})

    assert result.data.num_rows > 0
    assert set(result.data[column].to_pylist()) == expected


@pytest.mark.integration
def test_berlin_customers_live_in_five_regions_as_people_and_stores(
    repository: SqlServerProcedureRepository, catalog: Catalog
) -> None:
    result = repository.execute(catalog.entry("api.Customer_List"), {"City": "Berlin", "UserId": 1})

    assert len(set(result.data["StateProvince"].to_pylist())) == 5
    assert set(result.data["CustomerType"].to_pylist()) == {"individual", "store"}


@pytest.mark.integration
def test_order_lines_filter_by_product_category(
    repository: SqlServerProcedureRepository, catalog: Catalog
) -> None:
    result = repository.execute(
        catalog.entry("api.SalesOrderLine_List"),
        {**JANUARY_2024, "ProductCategoryID": 1, "UserId": 1},
    )

    assert result.data.num_rows > 0
    assert set(result.data["Category"].to_pylist()) == {"Bikes"}


class FakeExecutor:
    """Returns one prepared result set for any procedure call."""

    def __init__(self, columns: tuple[str, ...], *rows: tuple[object, ...]) -> None:
        self._result = SqlRows(columns=columns, rows=rows, has_more=False)

    def fetch(
        self, sql: str, params: Sequence[object] = (), max_rows: int | None = None
    ) -> SqlRows:
        return self._result

    def fetch_procedure(
        self,
        procedure: str,
        params: Mapping[str, object] | None = None,
        max_rows: int | None = None,
    ) -> SqlRows:
        return self._result


ORDER_COLUMNS = (
    "SalesOrderID",
    "OrderDate",
    "CustomerID",
    "CustomerName",
    "CustomerType",
    "Territory",
    "SubTotal",
    "TotalDue",
)


def _order(sub_total: Decimal) -> tuple[object, ...]:
    return (1, date(2024, 1, 5), 2, "Store A", "store", "Southeast", sub_total, Decimal("1.0000"))


def test_result_columns_other_than_the_catalog_are_an_error(catalog: Catalog) -> None:
    columns = (*ORDER_COLUMNS[:-2], "TotalDue", "SubTotal")
    repository = SqlServerProcedureRepository(FakeExecutor(columns, _order(Decimal("1.0000"))))

    with pytest.raises(ProcedureError, match="the catalog expects"):
        repository.execute(catalog.entry("api.SalesOrder_List"), {})


def test_value_outside_the_catalog_type_is_an_error(catalog: Catalog) -> None:
    too_large = Decimal("12345678901234567890.0000")  # decimal(19,4) holds 15 integer digits
    repository = SqlServerProcedureRepository(FakeExecutor(ORDER_COLUMNS, _order(too_large)))

    with pytest.raises(ProcedureError, match="column SubTotal does not fit the catalog type"):
        repository.execute(catalog.entry("api.SalesOrder_List"), {})


def test_value_that_does_not_fit_the_catalog_type_stays_out_of_the_message(
    catalog: Catalog,
) -> None:
    order = (1, date(2024, 1, 5), "SENTINEL-NAME-7Q", "Store A", "store", "Southeast")
    rows = (*order, Decimal("1.0000"), Decimal("1.0000"))
    repository = SqlServerProcedureRepository(FakeExecutor(ORDER_COLUMNS, rows))

    with pytest.raises(ProcedureError) as raised:
        repository.execute(catalog.entry("api.SalesOrder_List"), {})

    assert str(raised.value) == (
        "api.SalesOrder_List: column CustomerID does not fit the catalog type int"
    )
    assert raised.value.detail is not None
    assert "SENTINEL-NAME-7Q" in raised.value.detail
