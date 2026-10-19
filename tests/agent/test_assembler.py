"""The response of section 9 built from the model's answer and the session's result store."""

import json
from collections.abc import Callable, Mapping

import pytest

from data_concierge.agent.assembler import ResponseAssembler
from data_concierge.agent.deps import AgentDeps
from data_concierge.agent.models import AgentAnswer, TableRef
from data_concierge.api.schemas import Candidate, Clarification, ProcedureStep, QueryStep
from data_concierge.catalog.models import Catalog

YEAR_2024 = {"date_from": "2024-01-01", "date_to": "2025-01-01"}
MONTHLY_SQL = (
    "SELECT CAST(date_trunc('month', \"OrderDate\") AS DATE) AS month, "
    'sum("SubTotal") AS net_sales, count(DISTINCT "SalesOrderID") AS order_count '
    "FROM r1 GROUP BY 1 ORDER BY month"
)
BERLIN_SQL = (
    'SELECT sum(o."SubTotal") AS net_sales FROM r2 AS o '
    'JOIN (SELECT DISTINCT "CustomerID" FROM r1) AS c ON c."CustomerID" = o."CustomerID"'
)


@pytest.fixture
def assembler(deps: AgentDeps) -> ResponseAssembler:
    return ResponseAssembler(deps.results, max_rows=10_000)


@pytest.fixture
def call(deps: AgentDeps, catalog: Catalog) -> Callable[[str, Mapping[str, object]], str]:
    """Calls a catalog procedure by tool name and returns its handle."""

    def run(tool: str, args: Mapping[str, object]) -> str:
        entry = next(entry for entry in catalog.entries if entry.tool == tool)
        return deps.procedures.call(entry, args, deps.context).handle

    return run


def ok(*handles: str) -> AgentAnswer:
    return AgentAnswer(
        status="ok",
        message="Gotowe.",
        tables=tuple(TableRef(handle=handle, title=f"Tabela {handle}") for handle in handles),
        assumptions=("sprzedaż = SubTotal (netto)",),
    )


def test_query_result_has_the_procedure_and_the_query_in_provenance(
    assembler: ResponseAssembler, deps: AgentDeps, call: Callable[[str, Mapping[str, object]], str]
) -> None:
    call("sales_order_list", YEAR_2024)
    deps.queries.run(MONTHLY_SQL)

    response = assembler.assemble(ok("r2"), session_id="s1", correlation_id="c1")

    procedure, query = response.provenance
    assert procedure == ProcedureStep(
        handle="r1",
        name="api.SalesOrder_List",
        params={"DateFrom": "2024-01-01", "DateTo": "2025-01-01", "CustomerID": None},
        rows=3,
        truncated=False,
    )
    assert query == QueryStep(
        handle="r2", sources=("r1",), submitted_sql=MONTHLY_SQL, sql=MONTHLY_SQL, rows=2
    )


def test_join_of_two_procedures_has_both_in_provenance(
    assembler: ResponseAssembler, deps: AgentDeps, call: Callable[[str, Mapping[str, object]], str]
) -> None:
    call("customer_list", {"city": "Berlin"})
    call("sales_order_list", YEAR_2024)
    deps.queries.run(BERLIN_SQL)

    response = assembler.assemble(ok("r3"), session_id="s1", correlation_id="c1")

    assert [(step.step, step.handle) for step in response.provenance] == [
        ("procedure", "r1"),
        ("procedure", "r2"),
        ("query", "r3"),
    ]
    assert isinstance(response.provenance[2], QueryStep)
    assert response.provenance[2].sources == ("r1", "r2")
    assert response.tables[0].rows == (("149.7500",),)


def test_shared_ancestor_appears_once(
    assembler: ResponseAssembler, deps: AgentDeps, call: Callable[[str, Mapping[str, object]], str]
) -> None:
    call("sales_order_list", YEAR_2024)
    deps.queries.run(MONTHLY_SQL)
    deps.queries.run("SELECT count(*) AS order_count FROM r1")

    response = assembler.assemble(ok("r3", "r2"), session_id="s1", correlation_id="c1")

    assert [step.handle for step in response.provenance] == ["r1", "r2", "r3"]
    assert [table.handle for table in response.tables] == ["r3", "r2"]


def test_answer_fields_pass_through_to_the_response(
    assembler: ResponseAssembler, deps: AgentDeps, call: Callable[[str, Mapping[str, object]], str]
) -> None:
    call("sales_order_list", YEAR_2024)

    response = assembler.assemble(ok("r1"), session_id="s1", correlation_id="c1")

    assert (response.status, response.message) == ("ok", "Gotowe.")
    assert response.assumptions == ("sprzedaż = SubTotal (netto)",)


def test_no_data_answer_has_its_message_and_no_tables(assembler: ResponseAssembler) -> None:
    answer = AgentAnswer(
        status="no_data",
        message="Brak zamówień w 2019. Dane obejmują 2022-05-30 do 2025-06-29.",
        assumptions=("okres: 2019-01-01 do 2019-12-31",),
    )

    response = assembler.assemble(answer, session_id="s1", correlation_id="c1")

    assert response.status == "no_data"
    assert response.message == answer.message
    assert response.assumptions == answer.assumptions
    assert (response.tables, response.provenance) == ((), ())


def test_query_step_keeps_the_model_sql_and_the_executed_sql(
    assembler: ResponseAssembler, deps: AgentDeps, call: Callable[[str, Mapping[str, object]], str]
) -> None:
    call("sales_order_list", YEAR_2024)
    submitted = 'SELECT sum("SalesOrderID") AS id_total FROM r1'
    deps.queries.run(submitted)

    step = assembler.assemble(ok("r2"), session_id="s1", correlation_id="c1").provenance[1]

    assert isinstance(step, QueryStep)
    assert step.submitted_sql == submitted
    assert step.sql != submitted
    assert "AS BIGINT" in step.sql


def test_table_over_the_response_limit_is_cut_and_marked(
    deps: AgentDeps, call: Callable[[str, Mapping[str, object]], str]
) -> None:
    call("sales_order_list", YEAR_2024)

    response = ResponseAssembler(deps.results, max_rows=2).assemble(
        ok("r1"), session_id="s1", correlation_id="c1"
    )

    (table,) = response.tables
    assert (len(table.rows), table.row_count, table.truncated) == (2, 3, True)


def test_monthly_report_matches_the_contract_example(
    assembler: ResponseAssembler, deps: AgentDeps, call: Callable[[str, Mapping[str, object]], str]
) -> None:
    call("sales_order_list", YEAR_2024)
    deps.queries.run(MONTHLY_SQL)

    payload = assembler.assemble(ok("r2"), session_id="s1", correlation_id="c1").model_dump(
        mode="json"
    )

    assert payload["status"] == "ok"
    assert payload["session_id"] == "s1"
    assert payload["clarification"] is None
    (table,) = payload["tables"]
    assert table == {
        "handle": "r2",
        "title": "Tabela r2",
        "columns": [
            {"name": "month", "type": "date"},
            {"name": "net_sales", "type": "decimal(38,4)"},
            {"name": "order_count", "type": "int"},
        ],
        "rows": [["2024-01-01", "149.7500", 2], ["2024-03-01", "10.0000", 1]],
        "row_count": 2,
        "truncated": False,
    }
    assert set(payload["provenance"][0]) == {
        "step",
        "handle",
        "name",
        "params",
        "rows",
        "truncated",
    }
    assert set(payload["provenance"][1]) == {
        "step",
        "handle",
        "sources",
        "submitted_sql",
        "sql",
        "rows",
    }


def test_json_has_no_floating_point_numbers(
    assembler: ResponseAssembler, deps: AgentDeps, call: Callable[[str, Mapping[str, object]], str]
) -> None:
    call("sales_order_list", YEAR_2024)
    deps.queries.run(MONTHLY_SQL)

    def no_float(text: str) -> float:
        raise AssertionError(f"float in the response: {text}")

    json.loads(
        assembler.assemble(ok("r1", "r2"), session_id="s1", correlation_id="c1").model_dump_json(),
        parse_float=no_float,
    )


def test_clarification_has_candidates_and_no_tables(assembler: ResponseAssembler) -> None:
    candidates = tuple(
        Candidate(id=customer_id, description=f"AW000{customer_id}")
        for customer_id in (12698, 20550, 28763)
    )
    answer = AgentAnswer(
        status="needs_clarification",
        message="Znalazłem trzech klientów o tej nazwie.",
        clarification=Clarification(question="Którego klienta wybrać?", candidates=candidates),
    )

    response = assembler.assemble(answer, session_id="s1", correlation_id="c1")

    assert response.tables == ()
    assert response.provenance == ()
    assert response.clarification is not None
    assert [c.id for c in response.clarification.candidates] == [12698, 20550, 28763]


def test_table_derived_from_a_truncated_procedure_is_marked_truncated(
    assembler: ResponseAssembler,
    call: Callable[[str, Mapping[str, object]], str],
    truncate_procedures: Callable[[bool], None],
) -> None:
    truncate_procedures(True)
    call("sales_order_list", YEAR_2024)

    response = assembler.assemble(ok("r1"), session_id="s1", correlation_id="c1")

    assert response.tables[0].truncated is True
    assert isinstance(response.provenance[0], ProcedureStep)
    assert response.provenance[0].truncated is True


def test_error_has_a_generic_message_with_the_correlation_id(
    assembler: ResponseAssembler, call: Callable[[str, Mapping[str, object]], str]
) -> None:
    call("sales_order_list", YEAR_2024)
    answer = AgentAnswer(status="error", message="", tables=(TableRef(handle="r1", title="x"),))

    response = assembler.assemble(answer, session_id="s1", correlation_id="abc123")

    assert response.status == "error"
    assert "abc123" in response.message
    assert response.tables == ()
    assert response.provenance == ()
