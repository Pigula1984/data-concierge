import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from data_concierge.catalog.repository import YamlCatalogRepository
from data_concierge.core.errors import CatalogError

FIXTURE = Path(__file__).parent / "fixtures" / "minimal-catalog.yaml"

type Entries = list[dict[str, Any]]


def _load_variant(tmp_path: Path, change: Callable[[Entries], None]) -> None:
    entries: Entries = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    change(entries)
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml.safe_dump(entries, allow_unicode=True), encoding="utf-8")
    YamlCatalogRepository(path).load()


def test_minimal_catalog_loads() -> None:
    catalog = YamlCatalogRepository(FIXTURE).load()

    assert [entry.tool for entry in catalog.exposed_entries] == ["order_list"]
    assert catalog.entry("api.Order_List").table_schema.names == (
        "OrderID",
        "OrderDate",
        "Amount",
    )


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
