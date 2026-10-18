import logging
from datetime import date

import pytest

from data_concierge.catalog.models import (
    Catalog,
    CatalogEntry,
    DataRange,
    ProcedureMetadata,
    SqlField,
)
from data_concierge.catalog.service import CatalogService
from data_concierge.core.errors import ConfigurationError
from data_concierge.core.types import ColumnKind, ColumnType

_SQL_TYPES = {
    ColumnKind.INT: "int",
    ColumnKind.STRING: "nvarchar(50)",
    ColumnKind.DATE: "date",
    ColumnKind.DATETIME: "datetime",
    ColumnKind.BOOL: "bit",
}


def _sql_type(column_type: ColumnType) -> str:
    # The catalog text form of a decimal, decimal(p,s), is also its SQL Server name.
    return _SQL_TYPES.get(column_type.kind, str(column_type))


def _matching(entry: CatalogEntry) -> ProcedureMetadata:
    """Metadata a database would report for a procedure that matches its catalog entry."""
    return ProcedureMetadata(
        params=tuple(SqlField(param.name, _sql_type(param.type)) for param in entry.params),
        columns=tuple(SqlField(column.name, _sql_type(column.type)) for column in entry.columns),
    )


class FakeMetadata:
    """Metadata in memory: every catalog procedure matches unless a test overrides it."""

    def __init__(self, catalog: Catalog) -> None:
        self.procedures: dict[str, ProcedureMetadata | None] = {
            entry.procedure: _matching(entry) for entry in catalog.entries
        }
        self.range_calls: list[str] = []

    def describe(self, procedure: str) -> ProcedureMetadata | None:
        return self.procedures.get(procedure)

    def read_data_range(self, procedure: str) -> DataRange:
        self.range_calls.append(procedure)
        return DataRange(first=date(2022, 5, 30), last=date(2025, 6, 29))

    def get(self, procedure: str) -> ProcedureMetadata:
        described = self.procedures[procedure]
        assert described is not None
        return described

    def columns(self, procedure: str) -> tuple[SqlField, ...]:
        columns = self.get(procedure).columns
        assert columns is not None
        return columns

    def update(self, procedure: str, **fields: object) -> None:
        self.procedures[procedure] = self.get(procedure).model_copy(update=fields)

    def replace_param(self, procedure: str, name: str, sql_type: str) -> None:
        params = tuple(
            SqlField(name, sql_type) if param.name == name else param
            for param in self.get(procedure).params
        )
        self.update(procedure, params=params)

    def drop_column(self, procedure: str, name: str) -> None:
        columns = tuple(column for column in self.columns(procedure) if column.name != name)
        self.update(procedure, columns=columns)


@pytest.fixture
def fake_metadata(catalog: Catalog) -> FakeMetadata:
    return FakeMetadata(catalog)


def test_matching_database_passes(catalog: Catalog, fake_metadata: FakeMetadata) -> None:
    CatalogService(catalog, fake_metadata).validate()


def test_parameter_type_mismatch_names_procedure_parameter_and_both_types(
    catalog: Catalog, fake_metadata: FakeMetadata
) -> None:
    fake_metadata.replace_param("api.SalesOrder_List", "DateFrom", "datetime")

    with pytest.raises(ConfigurationError) as raised:
        CatalogService(catalog, fake_metadata).validate()

    message = str(raised.value)
    assert "api.SalesOrder_List" in message
    assert "DateFrom" in message
    assert "date in the catalog but datetime in the database" in message


def test_all_mismatches_are_reported_in_one_error(
    catalog: Catalog, fake_metadata: FakeMetadata
) -> None:
    fake_metadata.procedures["api.Product_Search"] = None
    fake_metadata.drop_column("api.SalesOrder_List", "TotalDue")

    with pytest.raises(ConfigurationError) as raised:
        CatalogService(catalog, fake_metadata).validate()

    message = str(raised.value)
    assert "api.Product_Search: the procedure does not exist" in message
    assert "api.SalesOrder_List: column TotalDue is in the catalog but not in the database" in (
        message
    )


def test_parameter_only_in_the_database_is_a_mismatch(
    catalog: Catalog, fake_metadata: FakeMetadata
) -> None:
    params = fake_metadata.get("api.Customer_Search").params
    fake_metadata.update("api.Customer_Search", params=(*params, SqlField("Limit", "int")))

    with pytest.raises(ConfigurationError, match="parameter Limit is in the database but not"):
        CatalogService(catalog, fake_metadata).validate()


def test_columns_in_a_different_order_are_a_mismatch(
    catalog: Catalog, fake_metadata: FakeMetadata
) -> None:
    columns = fake_metadata.columns("api.SalesOrder_DataRange")
    fake_metadata.update("api.SalesOrder_DataRange", columns=tuple(reversed(columns)))

    with pytest.raises(ConfigurationError, match="columns are in a different order"):
        CatalogService(catalog, fake_metadata).validate()


def test_procedure_without_described_columns_passes_with_a_warning(
    catalog: Catalog, fake_metadata: FakeMetadata, caplog: pytest.LogCaptureFixture
) -> None:
    fake_metadata.update("dbo.uspGetBillOfMaterials", columns=None)

    with caplog.at_level(logging.WARNING):
        CatalogService(catalog, fake_metadata).validate()

    assert "dbo.uspGetBillOfMaterials" in caplog.text


def test_money_and_smallint_match_catalog_decimal_and_int(
    catalog: Catalog, fake_metadata: FakeMetadata
) -> None:
    database_types = {"StandardCost": "money", "ListPrice": "money", "BOMLevel": "smallint"}
    columns = tuple(
        SqlField(column.name, database_types.get(column.name, column.sql_type))
        for column in fake_metadata.columns("dbo.uspGetBillOfMaterials")
    )
    fake_metadata.update("dbo.uspGetBillOfMaterials", columns=columns)

    CatalogService(catalog, fake_metadata).validate()


def test_type_without_catalog_equivalent_is_a_mismatch(
    catalog: Catalog, fake_metadata: FakeMetadata
) -> None:
    fake_metadata.replace_param("api.Customer_Search", "Name", "xml")

    with pytest.raises(ConfigurationError, match="string in the catalog but xml"):
        CatalogService(catalog, fake_metadata).validate()


def test_data_ranges_are_read_once_per_range_procedure(
    catalog: Catalog, fake_metadata: FakeMetadata
) -> None:
    ranges = CatalogService(catalog, fake_metadata).load_data_ranges()

    expected = DataRange(first=date(2022, 5, 30), last=date(2025, 6, 29))
    assert ranges == {"sales_order_list": expected, "sales_order_line_list": expected}
    assert fake_metadata.range_calls == ["api.SalesOrder_DataRange"]
