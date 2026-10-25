"""The case file is checked before any paid run: offline against the catalog, and its reference
queries against the local SQL Server."""

from pathlib import Path

import pytest
from cases import CASES_PATH, EvalCase
from pydantic import ValidationError

from data_concierge.catalog.models import Catalog
from data_concierge.core.sqlserver import SqlServerClient

CASES = EvalCase.load_all(CASES_PATH)
# A pivot that mode none may also return in the long shape (architecture 002, section 9).
MODE_CASE = """
- id: pivot
  group: raporty i pivoty
  prompt: kategorie w kolumnach
  expect:
    status: [ok]
    calls: [{tool: sales_order_line_list}]
    result: {columns: [bikes], sql: SELECT 1 AS bikes}
  modes:
    none:
      result:
        - {columns: [bikes], sql: SELECT 1 AS bikes}
        - {columns: [category, net_sales], sql: "SELECT 'Bikes' AS category, 1 AS net_sales"}
"""
GROUPS = {
    "listy",
    "sprzedaż za okres",
    "sprzedaż klienta",
    "raporty i pivoty",
    "łączenie zbiorów",
    "inne dziedziny",
    "trudne",
}


def test_case_ids_are_unique_and_the_set_has_30_to_50_cases() -> None:
    ids = [case.id for case in CASES]

    assert len(ids) == len(set(ids))
    assert 30 <= len(CASES) <= 50


def test_every_case_belongs_to_a_group_of_section_13() -> None:
    assert {case.group for case in CASES} == GROUPS


def test_expected_calls_name_exposed_tools_and_their_arguments(catalog: Catalog) -> None:
    arguments = {
        entry.tool: {param.arg for param in entry.params if param.arg}
        for entry in catalog.exposed_entries
    }
    for case in CASES:
        for call in case.expect.calls:
            assert call.tool in arguments, case.id
            assert set(call.args) <= arguments[call.tool], case.id


def test_a_mode_replaces_only_the_fields_it_names(tmp_path: Path) -> None:
    path = tmp_path / "cases.yaml"
    path.write_text(MODE_CASE, encoding="utf-8")

    (case,) = EvalCase.load_all(path)

    assert case.expectation("minimal") == case.expect
    long = case.expectation("none")
    assert [rule.columns for rule in long.results] == [("bikes",), ("category", "net_sales")]
    assert long.status == ("ok",)
    assert long.calls == case.expect.calls
    assert [rule.columns for rule in case.reference_rules] == [
        ("bikes",),
        ("category", "net_sales"),
    ]


def test_an_unknown_mode_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "cases.yaml"
    path.write_text(MODE_CASE.replace("  none:", "  full:"), encoding="utf-8")

    with pytest.raises(ValidationError, match="full"):
        EvalCase.load_all(path)


def test_every_catalog_example_is_a_case(catalog: Catalog) -> None:
    prompts = {case.prompt for case in CASES}
    examples = [example.prompt for entry in catalog.entries for example in entry.examples]

    assert [prompt for prompt in examples if prompt not in prompts] == []


@pytest.mark.integration
@pytest.mark.parametrize(
    "case", [case for case in CASES if case.reference_rules], ids=lambda case: case.id
)
def test_reference_queries_return_the_compared_columns(
    case: EvalCase, admin_client: SqlServerClient
) -> None:
    for rule in case.reference_rules:
        reference = admin_client.fetch(rule.sql)

        assert reference.rows, case.id
        columns = {name.casefold() for name in reference.columns}
        assert {name.casefold() for name in rule.columns} <= columns, case.id


@pytest.mark.integration
def test_all_order_lines_exceed_max_rows_so_the_truncation_case_still_truncates(
    admin_client: SqlServerClient, catalog: Catalog
) -> None:
    """The premise of `obciety-wynik-produkty`: one call over the whole range is cut."""
    entry = next(e for e in catalog.entries if e.tool == "sales_order_line_list")
    count = admin_client.fetch("SELECT COUNT(*) FROM Sales.SalesOrderDetail").rows[0][0]

    assert isinstance(count, int)
    assert count > entry.max_rows
