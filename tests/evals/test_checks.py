"""Scoring evaluation cases offline: result comparison, call matching and the number audit."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from cases import CaseChecker, ExpectedCall, ResultRule
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from report import CaseOutcome, EvalReport, NumberAudit, RunArchive, TurnTrace

from data_concierge.api.schemas import Response, ResponseColumn, ResponseTable
from data_concierge.catalog.models import Catalog
from data_concierge.core.sqlserver import SqlRows
from data_concierge.core.types import ColumnType

MONTHLY = ResponseTable(
    handle="r2",
    title="Sprzedaż po miesiącach",
    columns=(
        ResponseColumn(name="month", type=ColumnType.parse("date")),
        ResponseColumn(name="net_sales", type=ColumnType.parse("decimal(38,4)")),
        ResponseColumn(name="order_count", type=ColumnType.parse("int")),
    ),
    rows=(("2024-01-01", "100.5000", 2), ("2024-02-01", "0.0000", 0), ("2024-03-01", "10.0000", 1)),
    row_count=3,
    truncated=False,
)
REFERENCE = SqlRows(
    columns=("month", "net_sales"),
    rows=((date(2024, 3, 1), Decimal("10.0000")), (date(2024, 1, 1), Decimal("100.5"))),
    has_more=False,
)
ORDERS_CALL = (
    "api.SalesOrder_List",
    {"DateFrom": date(2024, 1, 1), "DateTo": date(2025, 1, 1), "CustomerID": None, "UserId": 1},
)


def answer(*tables: ResponseTable, message: str = "Gotowe.") -> Response:
    return Response(status="ok", session_id="s1", message=message, tables=tables)


def test_contains_matches_values_ignoring_column_names_order_and_scale(catalog: Catalog) -> None:
    rule = ResultRule(sql="-", rule="contains")

    assert CaseChecker(catalog).result_matches(rule, answer(MONTHLY), REFERENCE)


def test_rows_rule_rejects_extra_rows_and_a_wrong_value(catalog: Catalog) -> None:
    checker = CaseChecker(catalog)
    wrong = SqlRows(columns=("net_sales",), rows=((Decimal("10.01"),),), has_more=False)

    assert not checker.result_matches(ResultRule(sql="-"), answer(MONTHLY), REFERENCE)
    assert not checker.result_matches(ResultRule(sql="-", rule="contains"), answer(MONTHLY), wrong)


def test_ordered_rule_needs_the_reference_order(catalog: Catalog) -> None:
    rule = ResultRule(sql="-", rule="contains", ordered=True)

    assert not CaseChecker(catalog).result_matches(rule, answer(MONTHLY), REFERENCE)


def test_columns_limit_the_compared_reference_values(catalog: Catalog) -> None:
    reference = SqlRows(columns=("label", "order_count"), rows=(("styczeń", 2),), has_more=False)
    rule = ResultRule(sql="-", rule="contains", columns=("order_count",))

    assert CaseChecker(catalog).result_matches(rule, answer(MONTHLY), reference)


def test_expected_arguments_match_as_a_subset_by_parameter_name(catalog: Catalog) -> None:
    checker = CaseChecker(catalog)
    year = ExpectedCall(
        tool="sales_order_list",
        args={"date_from": date(2024, 1, 1), "date_to": date(2025, 1, 1), "customer_id": None},
    )
    other_year = ExpectedCall(tool="sales_order_list", args={"date_from": date(2023, 1, 1)})

    assert checker.procedures_called([year], [ORDERS_CALL])
    assert checker.params_match([year], [ORDERS_CALL])
    assert not checker.params_match([other_year], [ORDERS_CALL])
    assert not checker.procedures_called([ExpectedCall(tool="customer_search")], [ORDERS_CALL])


def test_numbers_in_the_comment_are_found_at_the_shown_precision() -> None:
    table = MONTHLY.model_copy(update={"rows": (("2024-06-01", "43671889.5012", 400),)})
    message = (
        "Sprzedaż netto w 2024: 43 671 889,50, czyli 400 zamówień w 12 miesiącach. "
        "Średnio 1 234,56 na zamówienie."
    )

    assert NumberAudit.unmatched(answer(table, message=message), []) == ("1 234,56",)


def test_turn_trace_counts_query_retries_and_reads_query_results_of_the_last_turn() -> None:
    earlier = ModelRequest(parts=[RetryPromptPart("old", tool_name="query")])
    turn: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart("Raport")]),
        ModelResponse(parts=[TextPart("...")]),
        ModelRequest(parts=[RetryPromptPart("column is DOUBLE", tool_name="query")]),
        ModelRequest(parts=[ToolReturnPart("query", {"preview": [["2024-01-01", "1234.5000"]]})]),
    ]

    trace = TurnTrace.from_messages([earlier, *turn])

    assert trace.query_retries == 1
    assert Decimal("1234.5") in trace.seen_values


def test_report_counts_each_group_and_lists_failed_cases() -> None:
    report = EvalReport()
    passed = CaseOutcome("a", "listy", "ok", True, True, True, True, 0, ())
    failed = CaseOutcome("b", "listy", "error", False, True, True, None, 2, ("1 234,56",))
    report.add(passed)
    report.add(failed)

    text = report.render()

    assert "| listy | 2 | 1 | 1/2 | 2/2 | 2/2 | 1/1 | 2 | 1 |" in text
    assert "- b: status error" in text
    assert "- b: 1 234,56" in text


def test_a_decimal_rounded_by_the_model_matches_at_its_own_scale(catalog: Catalog) -> None:
    rounded = MONTHLY.model_copy(
        update={
            "columns": (ResponseColumn(name="net_sales", type=ColumnType.parse("decimal(38,2)")),),
            "rows": (("31604921.95",),),
        }
    )
    exact = SqlRows(columns=("net_sales",), rows=((Decimal("31604921.9512"),),), has_more=False)
    off = SqlRows(columns=("net_sales",), rows=((Decimal("31604921.9612"),),), has_more=False)
    rule = ResultRule(sql="-")

    assert CaseChecker(catalog).result_matches(rule, answer(rounded), exact)
    assert not CaseChecker(catalog).result_matches(rule, answer(rounded), off)


def test_dates_and_account_numbers_in_the_comment_are_not_amounts() -> None:
    message = "Zamówienia od 30.05.2022 do 2025-06-29, klient AW00012698."

    assert NumberAudit.unmatched(answer(message=message), []) == ()


def test_numbers_with_no_break_separators_and_a_decimal_point_are_read() -> None:
    table = MONTHLY.model_copy(update={"rows": (("2024-06-01", "2294.9900", 1),)})
    message = "Sprzedaż 43\u00a0671\u202f889,50 i 2294.99."

    assert NumberAudit.unmatched(answer(table, message=message), []) == ("43\u00a0671\u202f889,50",)


def test_a_number_outside_results_does_not_fail_a_case() -> None:
    outcome = CaseOutcome("a", "listy", "ok", True, True, True, True, 0, ("1 234,56",))

    assert outcome.passed


def test_rows_match_in_any_order_when_a_name_is_also_in_another_row(catalog: Catalog) -> None:
    names = ColumnType.parse("string")
    team = ResponseTable(
        handle="r1",
        title="Podwładni",
        columns=tuple(
            ResponseColumn(name=name, type=names)
            for name in ("ManagerFirstName", "ManagerLastName", "FirstName", "LastName")
        ),
        rows=(
            ("Roberto", "Tamburello", "Rob", "Walters"),
            ("Terri", "Duffy", "Roberto", "Tamburello"),
        ),
        row_count=2,
        truncated=False,
    )
    reference = SqlRows(
        columns=("FirstName", "LastName"),
        rows=(("Roberto", "Tamburello"), ("Rob", "Walters")),
        has_more=False,
    )

    assert CaseChecker(catalog).result_matches(ResultRule(sql="-"), answer(team), reference)


def test_assumptions_need_one_form_of_each_expected_fact() -> None:
    response = Response(
        status="ok",
        session_id="s1",
        message="Gotowe.",
        assumptions=("Okres: od 1 stycznia 2025 do 31 marca 2025.",),
    )
    period = (("2025-01-01", "1 stycznia 2025"), ("2025-03-31", "31 marca 2025"))
    other = (("2024-10-01", "1 października 2024"),)

    assert CaseChecker.assumptions_match(period, response)
    assert not CaseChecker.assumptions_match(other, response)


def test_turn_trace_keeps_the_calls_with_their_sql_and_the_corrections() -> None:
    turn: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart("Raport")]),
        ModelResponse(parts=[ToolCallPart("query", {"sql": "SELECT avg(x) FROM r1"})]),
        ModelRequest(parts=[RetryPromptPart("column is DOUBLE", tool_name="query")]),
        ModelResponse(parts=[ToolCallPart("query", {"sql": "SELECT 1 AS x FROM r1"})]),
    ]

    assert TurnTrace.from_messages(turn).steps == [
        {"call": "query", "args": {"sql": "SELECT avg(x) FROM r1"}},
        {"retry": "query", "error": "column is DOUBLE"},
        {"call": "query", "args": {"sql": "SELECT 1 AS x FROM r1"}},
    ]


def test_run_archive_writes_each_case_and_the_report(tmp_path: Path) -> None:
    archive = RunArchive(tmp_path / "20261020-210500")

    case = archive.save_case("p5", {"id": "p5", "today": date(2025, 7, 1), "sql": "SELECT 1"})
    report = archive.save_report("| group |")

    assert json.loads(case.read_text(encoding="utf-8")) == {
        "id": "p5",
        "today": "2025-07-01",
        "sql": "SELECT 1",
    }
    assert report.read_text(encoding="utf-8") == "| group |\n"
