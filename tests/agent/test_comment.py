"""The comment phase of mode minimal offline (architecture 002, section 5.2; plan KTD8): a
recording model writes the comment over a real session with sentinel rows."""

import pytest
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from data_concierge.agent.comment import CommentService
from data_concierge.agent.deps import AgentDeps
from data_concierge.agent.models import AgentAnswer, TableRef
from data_concierge.catalog.models import Catalog
from data_concierge.core.settings import ModelDataAccess, Settings
from data_concierge.results.service import ResultService

pytestmark = pytest.mark.anyio

YEAR_2024 = {"date_from": "2024-01-01", "date_to": "2025-01-01"}
MONTHLY_SQL = (
    "SELECT CAST(date_trunc('month', \"OrderDate\") AS DATE) AS month, "
    'sum("SubTotal") AS net_sales FROM r2 GROUP BY 1 ORDER BY month'
)
TOP_CUSTOMER_SQL = (
    'SELECT "CustomerID", "CustomerName", CAST(sum("SubTotal") AS DECIMAL(38,2)) AS net_sales '
    'FROM r2 GROUP BY 1, 2 ORDER BY net_sales DESC, "CustomerID"'
)


class CommentModel:
    """Replies with the given comments in order and keeps the text of every request."""

    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)
        self.requests: list[str] = []

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.requests.append(ModelMessagesTypeAdapter.dump_json(messages).decode())
        return ModelResponse(parts=[TextPart(self._replies.pop(0))])


@pytest.fixture
def mode() -> ModelDataAccess:
    return "minimal"


@pytest.fixture
def results(catalog: Catalog, sentinel_deps: AgentDeps) -> ResultService:
    """r1 customers, r2 orders of 2024, r3 sales by month, r4 customers by sales."""
    context = sentinel_deps.context
    sentinel_deps.procedures.call(catalog.entry("api.Customer_List"), {"city": "x"}, context)
    sentinel_deps.procedures.call(catalog.entry("api.SalesOrder_List"), YEAR_2024, context)
    sentinel_deps.queries.run(MONTHLY_SQL)
    sentinel_deps.queries.run(TOP_CUSTOMER_SQL)
    return sentinel_deps.results


def draft(message: str = "Sprzedaż po miesiącach.") -> AgentAnswer:
    return AgentAnswer(
        status="ok",
        message=message,
        tables=(TableRef(handle="r3", title="Sprzedaż {r4[1].CustomerName} po miesiącach"),),
        assumptions=("Najlepszy klient: {r4[1].CustomerName}",),
    )


async def test_comment_sees_returned_and_anchored_tables_without_sensitive_columns(
    agent_settings: Settings, results: ResultService
) -> None:
    model = CommentModel("Najwięcej kupił {r4[1].CustomerName}: {r4[1].net_sales}.")

    comment = await CommentService(FunctionModel(model), agent_settings).write(
        "Sprzedaż po miesiącach za 2024", draft(), results
    )

    assert comment == "Najwięcej kupił {r4[1].CustomerName}: {r4[1].net_sales}."
    (request,) = model.requests
    # The returned table r3 and the anchored r4, with their values; not r1 and r2.
    assert '\\"handle\\":\\"r3\\"' in request
    assert '\\"handle\\":\\"r4\\"' in request
    assert '\\"handle\\":\\"r1\\"' not in request
    assert '\\"handle\\":\\"r2\\"' not in request
    assert "111111.1110" in request
    assert "144444.34" in request
    # The draft goes as a template, and the sensitive name is anchored, never shown.
    assert "Najlepszy klient: {r4[1].CustomerName}" in request
    assert "SENTINEL-NAME" not in request
    assert "SENTINEL-ACCOUNT" not in request


async def test_bad_anchor_in_the_comment_is_sent_back_for_a_fix(
    agent_settings: Settings, results: ResultService
) -> None:
    model = CommentModel("Suma: {r3.net_sales}.", "Najlepszy miesiąc: {r3[1].month}.")

    comment = await CommentService(FunctionModel(model), agent_settings).write(
        "Sprzedaż po miesiącach za 2024", draft(), results
    )

    assert comment == "Najlepszy miesiąc: {r3[1].month}."
    assert "r3 has 2 rows" in model.requests[1]
