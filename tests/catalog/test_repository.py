import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from data_concierge.catalog.models import Catalog
from data_concierge.catalog.repository import YamlCatalogRepository
from data_concierge.core.errors import CatalogError

FIXTURE = Path(__file__).parent / "fixtures" / "minimal-catalog.yaml"

type Entries = list[dict[str, Any]]


def _load_variant(tmp_path: Path, change: Callable[[Entries], None]) -> Catalog:
    entries: Entries = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    change(entries)
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml.safe_dump(entries, allow_unicode=True), encoding="utf-8")
    return YamlCatalogRepository(path).load()


def test_minimal_catalog_loads() -> None:
    catalog = YamlCatalogRepository(FIXTURE).load()

    assert [entry.tool for entry in catalog.exposed_entries] == ["order_list"]
    assert catalog.entry("api.Order_List").table_schema.names == (
        "OrderID",
        "OrderDate",
        "Amount",
    )


def test_sensitive_column_reaches_the_table_schema_and_defaults_to_false(
    tmp_path: Path,
) -> None:
    def change(entries: Entries) -> None:
        entries[0]["columns"][0]["sensitive"] = True

    schema = _load_variant(tmp_path, change).entry("api.Order_List").table_schema

    assert [(column.name, column.sensitive) for column in schema.columns] == [
        ("OrderID", True),
        ("OrderDate", False),
        ("Amount", False),
    ]


# Architecture 002, section 5.3: names of customers, products and people, and business ids
# the model does not need for joining results.
SENSITIVE_COLUMNS = {
    ("customer_search", "AccountNumber"),
    ("customer_search", "CustomerName"),
    ("customer_list", "AccountNumber"),
    ("customer_list", "CustomerName"),
    ("sales_order_list", "CustomerName"),
    ("sales_order_line_list", "CustomerName"),
    ("sales_order_line_list", "ProductName"),
    ("product_search", "ProductName"),
    ("product_search", "ProductNumber"),
    ("get_bill_of_materials", "ComponentDesc"),
    ("get_where_used_product", "ComponentDesc"),
    *(
        (tool, column)
        for tool in ("get_employee_managers", "get_manager_employees")
        for column in ("FirstName", "LastName", "ManagerFirstName", "ManagerLastName")
    ),
}


def test_repository_catalog_marks_the_sensitive_columns_of_the_design(catalog: Catalog) -> None:
    marked = {
        (entry.tool, column.name)
        for entry in catalog.entries
        for column in entry.table_schema.columns
        if column.sensitive
    }

    assert marked == SENSITIVE_COLUMNS


@pytest.mark.parametrize("procedure", ["api.SalesOrderLine_List", "api.Product_Search"])
def test_repository_catalog_lists_the_product_categories(catalog: Catalog, procedure: str) -> None:
    (category,) = [c for c in catalog.entry(procedure).columns if c.name == "Category"]

    assert category.values == ("Accessories", "Bikes", "Clothing", "Components")


def test_param_both_injected_and_required_is_rejected(tmp_path: Path) -> None:
    def change(entries: Entries) -> None:
        entries[0]["params"][1]["required"] = True

    with pytest.raises(CatalogError, match="UserId' cannot be both 'inject' and 'required'"):
        _load_variant(tmp_path, change)


def test_default_for_on_non_measure_column_is_rejected(tmp_path: Path) -> None:
    def change(entries: Entries) -> None:
        entries[0]["columns"][1]["default_for"] = ["date"]

    with pytest.raises(CatalogError, match="OrderDate' has role 'time'"):
        _load_variant(tmp_path, change)


def test_duplicate_tool_name_names_both_procedures(tmp_path: Path) -> None:
    def change(entries: Entries) -> None:
        entries[1]["tool"] = "order_list"

    with pytest.raises(CatalogError, match=re.escape("api.Order_List and api.Order_DataRange")):
        _load_variant(tmp_path, change)


def test_example_with_undeclared_argument_is_rejected(tmp_path: Path) -> None:
    def change(entries: Entries) -> None:
        entries[0]["examples"][0]["call"]["user_id"] = 7

    with pytest.raises(CatalogError, match="undeclared arguments: user_id"):
        _load_variant(tmp_path, change)


def test_column_names_differing_only_in_case_are_rejected(tmp_path: Path) -> None:
    def change(entries: Entries) -> None:
        entries[0]["columns"].append({"name": "amount", "type": "int", "role": "measure"})

    with pytest.raises(CatalogError, match="duplicate column names: amount"):
        _load_variant(tmp_path, change)


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    def change(entries: Entries) -> None:
        entries[0]["params"][0]["requried"] = True

    with pytest.raises(CatalogError, match=r"api.Order_List.params.0.requried"):
        _load_variant(tmp_path, change)


def test_invalid_decimal_reports_location_and_plain_message(tmp_path: Path) -> None:
    def change(entries: Entries) -> None:
        entries[0]["columns"][2]["type"] = "decimal(4,6)"

    with pytest.raises(CatalogError) as error:
        _load_variant(tmp_path, change)

    message = str(error.value)
    assert "api.Order_List.columns.2.type: invalid type 'decimal(4,6)'" in message
    assert "pydantic" not in message


def test_missing_data_range_procedure_is_rejected(tmp_path: Path) -> None:
    def change(entries: Entries) -> None:
        del entries[1]

    with pytest.raises(
        CatalogError, match=re.escape("'api.Order_DataRange' is not in the catalog")
    ):
        _load_variant(tmp_path, change)


def test_missing_file_is_a_catalog_error(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="cannot read catalog file"):
        YamlCatalogRepository(tmp_path / "missing.yaml").load()
