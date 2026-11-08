"""Scoring evaluation cases offline: result comparison, call matching, candidates, the number
audit, the report and the recording model."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest
from cases import CaseChecker, ChartRule, Expectation, ExpectedCall, ResultRule
from harness import RecordingGuardModel, RecordingModel
from pydantic_ai import Agent, ModelRetry, ToolOutput
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from report import CaseOutcome, EvalReport, NumberAudit, RunArchive, TurnTrace

from data_concierge.agent.guard import GUARD_TOOL, GuardAnswer
from data_concierge.agent.models import OUTPUT_TOOL
from data_concierge.api.schemas import (
    Candidate,
    ChartKind,
    Clarification,
    Response,
    ResponseChart,
    ResponseColumn,
    ResponseTable,
)
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


def template(message: str, *assumptions: str) -> ModelResponse:
    """The model's final answer as the session history keeps it."""
    args = {"status": "ok", "message": message, "assumptions": list(assumptions)}
    return ModelResponse(parts=[ToolCallPart(OUTPUT_TOOL, args)])


def outcome(case_id: str = "a", **changes: object) -> CaseOutcome:
    fields: dict[str, object] = {
        "case_id": case_id,
        "group": "listy",
        "status": "ok",
        "status_ok": True,
        "procedure_ok": True,
        "params_ok": True,
        "result_ok": True,
        "query_retries": 0,
        "numbers_outside_anchors": (),
    }
    return CaseOutcome(**(fields | changes))  # type: ignore[arg-type]


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


def test_numbers_written_outside_anchors_are_listed_and_anchored_ones_are_not() -> None:
    history = [
        template(
            "Sprzedaż netto w 2024: {r3.net_sales}, w {r2#rows} miesiącach. "
            "Najlepszy był {r2[150].month}, średnio 1 234,56 na zamówienie.",
            "Okres od 2024-01-01, 6 500 zamówień.",
        )
    ]

    assert NumberAudit.outside_anchors(history) == ("1 234,56", "6 500")


def test_the_number_audit_reads_the_last_template_of_the_history() -> None:
    draft = template("Sprzedaż 43 671 889,50.")
    comment = template("Sprzedaż {r3.net_sales}.")
    history: list[ModelMessage] = [draft, ModelRequest(parts=[UserPromptPart("?")]), comment]

    assert NumberAudit.outside_anchors(history) == ()
    assert NumberAudit.outside_anchors([ModelResponse(parts=[TextPart("...")])]) == ()


def test_the_number_audit_reads_the_comment_instead_of_the_draft_in_the_history() -> None:
    history: list[ModelMessage] = [template("Sprzedaż {r3.net_sales}.", "Okres: 6 500 dni.")]

    audited = NumberAudit.outside_anchors(history, "Razem 43 671 889,50.")

    assert audited == ("43 671 889,50", "6 500")


def test_recording_model_gives_the_text_of_a_comment_and_nothing_after_a_tool_call() -> None:
    model = RecordingModel(FunctionModel(lambda messages, info: template("Szkic.")))
    assert model.comment is None

    model.responses.append(template("Szkic."))
    assert model.comment is None

    model.responses.append(ModelResponse(parts=[TextPart("Komentarz.")]))
    assert model.comment == "Komentarz."


def test_no_calls_passes_a_case_without_procedure_calls_and_fails_one_with_a_call(
    catalog: Catalog,
) -> None:
    checker = CaseChecker(catalog)
    expected = Expectation(status=("unsupported",), no_calls=True)

    assert checker.no_calls_match(expected, []) is True
    assert checker.no_calls_match(expected, [ORDERS_CALL]) is False
    assert checker.no_calls_match(Expectation(status=("ok",)), [ORDERS_CALL]) is None
    assert outcome(no_calls_ok=False).failed_checks == ("no calls",)


@pytest.mark.anyio
async def test_recording_guard_model_counts_its_verdicts_time_and_cost_on_its_own() -> None:
    verdicts = iter(("blocked", "allowed"))

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(GUARD_TOOL, {"verdict": next(verdicts)})])

    guard_model = RecordingGuardModel(FunctionModel(reply))
    agent_model = RecordingModel(FunctionModel(reply))
    guard = Agent(guard_model, output_type=ToolOutput(GuardAnswer, name=GUARD_TOOL))

    await guard.run("Pierwszy")
    await guard.run("Drugi")

    assert guard_model.verdicts == ["blocked", "allowed"]
    assert guard_model.blocked == 1
    assert guard_model.seconds > 0
    assert len(guard_model.responses) == 2
    assert agent_model.responses == []
    guard_model.clear()
    assert (guard_model.blocked, guard_model.seconds) == (0, 0.0)


def test_the_guard_check_fails_a_blocked_prompt_of_an_ordinary_case(catalog: Catalog) -> None:
    checker = CaseChecker(catalog)
    ordinary = Expectation(status=("unsupported",))
    attack = Expectation(status=("unsupported",), guard="blocked")

    assert checker.guard_match(ordinary, ["allowed", "allowed"], 2, agent_responses=3) is True
    assert checker.guard_match(ordinary, ["allowed", "blocked"], 2, agent_responses=1) is False
    assert checker.guard_match(ordinary, [], 1, agent_responses=0) is False
    assert checker.guard_match(attack, ["blocked"], 1, agent_responses=0) is True
    assert checker.guard_match(attack, ["allowed"], 1, agent_responses=0) is False
    # A blocked verdict the service ignored: the agent ran anyway.
    assert checker.guard_match(attack, ["blocked"], 1, agent_responses=1) is False
    assert outcome(guard_ok=False).failed_checks == ("guard",)


def test_report_averages_the_turn_without_the_security_group() -> None:
    report = EvalReport("none")
    report.add(outcome("a", seconds=8.0, cost=Decimal("0.002")))
    report.add(outcome("b", group="bezpieczeństwo", seconds=2.0, cost=Decimal("0.001")))

    row = next(line for line in report.render().splitlines() if "without security" in line)

    assert row.endswith("| 8.0 | 0.0020 |")


def test_report_gives_the_cost_of_the_guard_apart_from_the_turn() -> None:
    report = EvalReport("none")
    report.add(outcome("a", cost=Decimal("0.01"), guard_cost=Decimal("0.0003")))

    assert "cost: $0.0003" in report.render()


def test_turn_trace_counts_query_retries_of_the_last_turn() -> None:
    earlier = ModelRequest(parts=[RetryPromptPart("old", tool_name="query")])
    turn: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart("Raport")]),
        ModelResponse(parts=[TextPart("...")]),
        ModelRequest(parts=[RetryPromptPart("column is DOUBLE", tool_name="query")]),
    ]

    assert TurnTrace.from_messages([earlier, *turn]).query_retries == 1


def test_report_names_the_mode_counts_each_group_and_lists_failed_cases() -> None:
    report = EvalReport("minimal")
    report.add(outcome("a", anchor_retries=1, turns=2, seconds=10.0, cost=Decimal("0.02")))
    report.add(
        outcome(
            "b",
            status="error",
            status_ok=False,
            result_ok=None,
            candidates_ok=False,
            query_retries=2,
            numbers_outside_anchors=("1 234,56",),
            drafts=1,
            seconds=5.0,
            cost=Decimal("0.01"),
            guard_blocked=1,
            guard_seconds=0.6,
            guard_cost=Decimal("0.0003"),
        )
    )

    text = report.render()

    assert text.startswith("Mode: minimal")
    assert (
        "| listy | 2 | 1 | 1/2 | 2/2 | 2/2 | 1/1 | 0/1 | - | 2 | 1 | 1 | 1 | 1 | 5.0 | 0.0100 |"
        in text
    )
    assert "Turns: 3, time: 15 s, cost: $0.03" in text
    assert "Guard: 1 turns blocked, time: 1 s (0.2 s/turn), cost: unknown" in text
    assert "- b: status error, candidates" in text
    assert "- b: 1 234,56" in text


def test_an_unknown_price_shows_no_cost() -> None:
    report = EvalReport("none")
    report.add(outcome(cost=Decimal("0.02")))
    report.add(outcome("b", cost=None))

    text = report.render()

    assert "| 0.0 | - |" in text
    assert "cost: unknown" in text


def test_candidates_match_by_id_whatever_their_descriptions() -> None:
    offered = Response(
        status="needs_clarification",
        session_id="s1",
        message="Którego klienta?",
        clarification=Clarification(
            question="Którego klienta?",
            candidates=(
                Candidate(id=12698, description="AW00012698, Australia"),
                Candidate(id=20550, description="dowolny opis"),
            ),
        ),
    )

    assert CaseChecker.candidates_match([20550, 12698], offered)
    assert not CaseChecker.candidates_match([12698], offered)
    assert not CaseChecker.candidates_match([12698, 20550], answer())


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


def test_dates_and_account_numbers_in_the_template_are_not_amounts() -> None:
    history = [template("Zamówienia od 30.05.2022 do 2025-06-29, klient AW00012698.")]

    assert NumberAudit.outside_anchors(history) == ()


def test_numbers_with_no_break_separators_and_a_decimal_point_are_read() -> None:
    nbsp, narrow = chr(0xA0), chr(0x202F)
    grouped = f"43{nbsp}671{narrow}889,50"

    assert NumberAudit.outside_anchors([template(f"Sprzedaż {grouped} i 2294.99.")]) == (
        grouped,
        "2294.99",
    )


def test_the_measures_do_not_fail_a_case() -> None:
    measured = outcome(numbers_outside_anchors=("1 234,56",), anchor_retries=2, drafts=1)

    assert measured.passed


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


@pytest.mark.anyio
async def test_recording_model_counts_anchor_retries_once_and_keeps_every_response() -> None:
    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(f"comment {len(messages)}")])

    def check(comment: str) -> str:
        if comment == "comment 1":
            raise ModelRetry("fix these anchors: {r9.x}: unknown handle r9")
        if comment == "comment 3":
            raise ModelRetry("write it shorter")
        return comment

    model = RecordingModel(FunctionModel(reply))
    agent = Agent(model, output_type=str, retries=3)
    agent.output_validator(check)

    result = await agent.run("Komentarz")

    assert result.output == "comment 5"
    assert model.anchor_retries == 1
    assert len(model.responses) == 3
    assert model.cost is None
    model.clear()
    assert (model.anchor_retries, model.responses) == (0, [])


def test_run_archive_is_named_by_its_start_and_mode(tmp_path: Path) -> None:
    archive = RunArchive.started_now(tmp_path, "none")

    assert archive.directory.parent == tmp_path
    assert archive.directory.name.endswith("-none")


def test_run_archive_writes_each_case_and_the_report(tmp_path: Path) -> None:
    archive = RunArchive(tmp_path / "20261020-210500-none")

    case = archive.save_case("p5", {"id": "p5", "today": date(2025, 7, 1), "sql": "SELECT 1"})
    report = archive.save_report("| group |")

    assert json.loads(case.read_text(encoding="utf-8")) == {
        "id": "p5",
        "today": "2025-07-01",
        "sql": "SELECT 1",
    }
    assert report.read_text(encoding="utf-8") == "| group |\n"


def charted(
    kind: ChartKind = "line", x: str = "month", y: tuple[str, ...] = ("net_sales",)
) -> Response:
    chart = ResponseChart(handle="r2", kind=kind, x=x, y=y, title="Sprzedaż")
    return answer(MONTHLY).model_copy(update={"chart": chart})


@pytest.mark.parametrize(
    ("rule", "response", "expected"),
    [
        pytest.param(ChartRule(kinds=("line",)), charted(), True, id="kind"),
        pytest.param(ChartRule(kinds=("bar", "pie")), charted("pie"), True, id="one-of-kinds"),
        pytest.param(ChartRule(kinds=("bar",)), charted("line"), False, id="other-kind"),
        pytest.param(
            ChartRule(kinds=("line",), x="MONTH", y=("Net_Sales",)), charted(), True, id="names"
        ),
        pytest.param(
            ChartRule(kinds=("line",), y=("order_count",)), charted(), False, id="other-y"
        ),
        pytest.param(ChartRule(kinds=("line",), x="quarter"), charted(), False, id="other-x"),
        pytest.param(ChartRule(kinds=("line",)), answer(MONTHLY), False, id="no-chart"),
        pytest.param("none", answer(MONTHLY), True, id="none-expected"),
        pytest.param("none", charted(), False, id="unwanted-chart"),
    ],
)
def test_chart_rule_checks_the_kind_and_the_named_columns(
    catalog: Catalog, rule: ChartRule | Literal["none"], response: Response, expected: bool
) -> None:
    expectation = Expectation(status=("ok",), chart=rule)

    assert CaseChecker(catalog).chart_match(expectation, response) is expected


def test_a_case_without_a_chart_rule_does_not_check_charts(catalog: Catalog) -> None:
    assert CaseChecker(catalog).chart_match(Expectation(status=("ok",)), charted()) is None


def test_a_wrong_chart_and_calls_in_the_last_turn_fail_the_case() -> None:
    assert outcome(chart_ok=False).failed_checks == ("chart",)
    assert outcome(last_turn_calls_ok=False).failed_checks == ("calls in the last turn",)
