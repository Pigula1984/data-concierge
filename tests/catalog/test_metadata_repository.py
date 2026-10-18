from collections.abc import Mapping, Sequence
from datetime import date

import pytest

from data_concierge.catalog.models import DataRange, SqlField
from data_concierge.catalog.repository import SqlServerMetadataRepository
from data_concierge.core.sqlserver import SqlRows


@pytest.mark.integration
def test_new_procedure_has_parameters_and_result_columns(
    metadata: SqlServerMetadataRepository,
) -> None:
    described = metadata.describe("api.SalesOrder_List")

    assert described is not None
    assert described.params == (
        SqlField("DateFrom", "date"),
        SqlField("DateTo", "date"),
        SqlField("CustomerID", "int"),
        SqlField("UserId", "int"),
    )
    assert described.columns is not None
    assert SqlField("SubTotal", "money") in described.columns
    assert SqlField("OrderDate", "date") in described.columns


@pytest.mark.integration
def test_existing_procedure_describes_seven_columns(
    metadata: SqlServerMetadataRepository,
) -> None:
    described = metadata.describe("dbo.uspGetEmployeeManagers")

    assert described is not None
    assert described.columns is not None
    assert len(described.columns) == 7


@pytest.mark.integration
def test_decimal_types_carry_precision_and_scale(metadata: SqlServerMetadataRepository) -> None:
    described = metadata.describe("api.SalesOrderLine_List")

    assert described is not None
    assert described.columns is not None
    assert SqlField("LineTotal", "numeric(38,6)") in described.columns


@pytest.mark.integration
def test_procedure_the_agent_cannot_execute_is_missing(
    metadata: SqlServerMetadataRepository,
) -> None:
    assert metadata.describe("api.DoesNotExist") is None
    assert metadata.describe("HumanResources.uspUpdateEmployeeLogin") is None


@pytest.mark.integration
def test_data_range_procedure_returns_first_and_last_order_date(
    metadata: SqlServerMetadataRepository,
) -> None:
    assert metadata.read_data_range("api.SalesOrder_DataRange") == DataRange(
        first=date(2022, 5, 30), last=date(2025, 6, 29)
    )


class FakeExecutor:
    """Returns prepared result sets in call order, like SQL Server would for describe()."""

    def __init__(self, *results: SqlRows) -> None:
        self._results = list(results)

    def fetch(
        self, sql: str, params: Sequence[object] = (), max_rows: int | None = None
    ) -> SqlRows:
        return self._results.pop(0)

    def fetch_procedure(
        self,
        procedure: str,
        params: Mapping[str, object] | None = None,
        max_rows: int | None = None,
    ) -> SqlRows:
        return self._results.pop(0)


def _rows(*rows: tuple[object, ...]) -> SqlRows:
    return SqlRows(columns=(), rows=rows, has_more=False)


@pytest.mark.parametrize(
    "column_rows",
    [
        pytest.param((), id="no description rows"),
        pytest.param(((None, None, "uses a temporary table"),), id="error row"),
    ],
)
def test_procedure_without_a_result_description_has_no_columns(
    column_rows: tuple[tuple[object, ...], ...],
) -> None:
    executor = FakeExecutor(_rows((42,)), _rows(("@Id", "int")), _rows(*column_rows))

    described = SqlServerMetadataRepository(executor).describe("dbo.uspUndescribable")

    assert described is not None
    assert described.params == (SqlField("Id", "int"),)
    assert described.columns is None
