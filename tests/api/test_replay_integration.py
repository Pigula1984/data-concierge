"""Provenance reproduces the answer (R14): P3 and P5 run through `ask` on the real database
with a scripted model, then their provenance is replayed without a model in a new session."""

from collections.abc import Awaitable, Callable
from datetime import date

import pytest
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.models.function import FunctionModel

from data_concierge.api.schemas import ProcedureStep, Response
from data_concierge.bootstrap import CompositionRoot, DuckDBSessionFactory
from data_concierge.catalog.models import Catalog
from data_concierge.catalog.repository import SqlServerMetadataRepository
from data_concierge.core.context import RequestContext
from data_concierge.core.settings import Settings
from data_concierge.core.sqlserver import SqlServerClient
from data_concierge.procedures.repository import SqlServerProcedureRepository
from data_concierge.results.service import ResultService

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

USER_ID = 1
YEAR_2024 = {"date_from": "2024-01-01", "date_to": "2025-01-01"}
MONTHLY_REPORT_SQL = """
WITH months AS (
  SELECT CAST(range AS DATE) AS month
  FROM range(DATE '2024-01-01', DATE '2025-01-01', INTERVAL 1 MONTH)
), sales AS (
  SELECT CAST(date_trunc('month', "OrderDate") AS DATE) AS month,
         sum("SubTotal") AS net_sales,
         count(DISTINCT "SalesOrderID") AS order_count
  FROM r1
  GROUP BY 1
)
SELECT m.month, COALESCE(s.net_sales, 0) AS net_sales, COALESCE(s.order_count, 0) AS order_count
FROM months m LEFT JOIN sales s USING (month)
ORDER BY m.month
"""
BERLIN_SALES_SQL = """
SELECT CAST(date_trunc('month', o."OrderDate") AS DATE) AS month,
       sum(o."SubTotal") AS net_sales,
       count(DISTINCT o."SalesOrderID") AS order_count
FROM r2 AS o
JOIN (SELECT DISTINCT "CustomerID" FROM r1) AS c ON c."CustomerID" = o."CustomerID"
GROUP BY 1
ORDER BY month
"""


def present(handle: str) -> ToolCallPart:
    return ToolCallPart(
        "present_result",
        {"status": "ok", "message": "Gotowe.", "tables": [{"handle": handle, "title": "Raport"}]},
    )


@pytest.fixture
def ask(
    settings: Settings, agent_client: SqlServerClient, scripted: Callable[..., FunctionModel]
) -> Callable[..., Awaitable[Response]]:
    async def run(prompt: str, *steps: ToolCallPart) -> Response:
        root = CompositionRoot.assemble(
            settings,
            SqlServerMetadataRepository(agent_client),
            SqlServerProcedureRepository(agent_client),
            scripted(*steps),
            today=lambda: date(2025, 4, 15),
        )
        return await root.ask_service.ask(prompt, user_id=USER_ID)

    return run


def replay(
    response: Response, catalog: Catalog, client: SqlServerClient, settings: Settings
) -> ResultService:
    """Procedures with their parameters, then the executed SQL, in handle order, in a new
    session; every step gives the handle and the row count it recorded."""
    session = DuckDBSessionFactory(SqlServerProcedureRepository(client), settings).create()
    context = RequestContext(user_id=USER_ID, role="replay")
    for step in response.provenance:
        if isinstance(step, ProcedureStep):
            entry = catalog.entry(step.name)
            args = {p.arg: step.params[p.name] for p in entry.model_params if p.arg}
            handle = session.procedures.call(entry, args, context).handle
        else:
            handle = session.queries.run(step.sql).handle
        assert handle == step.handle
        assert session.results.describe(handle).row_count == step.rows
    return session.results


async def test_p3_monthly_report_is_reproduced_from_its_provenance(
    ask: Callable[..., Awaitable[Response]],
    catalog: Catalog,
    agent_client: SqlServerClient,
    settings: Settings,
) -> None:
    response = await ask(
        "Zrób raport sprzedaży po miesiącach za 2024",
        ToolCallPart("sales_order_list", YEAR_2024),
        ToolCallPart("query", {"sql": MONTHLY_REPORT_SQL}),
        present("r2"),
    )
    assert response.status == "ok"
    [table] = response.tables

    replayed = replay(response, catalog, agent_client, settings).export(
        table.handle, max_rows=10_000
    )

    assert table.row_count == 12
    assert replayed.rows == table.rows


async def test_p5_berlin_customers_sales_is_reproduced_from_its_provenance(
    ask: Callable[..., Awaitable[Response]],
    catalog: Catalog,
    agent_client: SqlServerClient,
    settings: Settings,
) -> None:
    response = await ask(
        "Sprzedaż po miesiącach w 2024 dla klientów z Berlina",
        ToolCallPart("customer_list", {"city": "Berlin"}),
        ToolCallPart("sales_order_list", YEAR_2024),
        ToolCallPart("query", {"sql": BERLIN_SALES_SQL}),
        present("r3"),
    )
    assert response.status == "ok"
    assert [step.step for step in response.provenance] == ["procedure", "procedure", "query"]
    [table] = response.tables

    replayed = replay(response, catalog, agent_client, settings).export(
        table.handle, max_rows=10_000
    )

    assert table.row_count > 0
    assert replayed.rows == table.rows
