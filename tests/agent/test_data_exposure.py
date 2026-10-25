"""In mode `none` no value from the rows reaches the model (architecture 002, section 4; plan
U7, KTD10); in mode `minimal` rows reach only the comment phase, and sensitive values never
(section 5; plan U8).

The session's procedures return sentinel rows. A scripted model records every request it
gets: the instructions, the tool definitions and all messages, history included. The test
then looks for every value of the session's handles in that traffic, raw and formatted for the
user, except ids and dates, which the model may see (D1, D4).
"""

import json
from collections.abc import Callable, Sequence

import pytest
from pydantic_ai import Tool
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from data_concierge.agent.assembler import ResponseAssembler
from data_concierge.agent.comment import CommentService
from data_concierge.agent.deps import AgentDeps
from data_concierge.agent.formatting import ValueFormatter
from data_concierge.agent.models import AgentTurn
from data_concierge.agent.service import AgentService
from data_concierge.api.service import AskService
from data_concierge.api.sessions import InMemorySessionRepository
from data_concierge.api.sessions import Session as ApiSession
from data_concierge.core.settings import ModelExposure, Settings
from data_concierge.core.types import Column, ColumnRole
from data_concierge.results.service import ResultService

pytestmark = pytest.mark.anyio

YEAR_2024 = {"date_from": "2024-01-01", "date_to": "2025-01-01"}
MONTHLY_SQL = (
    "SELECT CAST(date_trunc('month', \"OrderDate\") AS DATE) AS month, "
    'sum("SubTotal") AS net_sales, max("Territory") AS territory '
    "FROM r2 GROUP BY 1 ORDER BY month"
)
TOTAL_SQL = 'SELECT CAST(sum("SubTotal") AS DECIMAL(38,2)) AS annual_total FROM r2'
TOP_CUSTOMER_SQL = (
    'SELECT "CustomerID", "CustomerName", CAST(sum("SubTotal") AS DECIMAL(38,2)) AS net_sales '
    'FROM r2 GROUP BY 1, 2 ORDER BY net_sales DESC, "CustomerID"'
)
# Small counts are left out: they would match row counts the model may see.
CANDIDATES_SQL = (
    'SELECT "CustomerID", "AccountNumber", "CustomerName", "Territory" FROM r1 '
    'ORDER BY "CustomerID"'
)
SENTINELS = (
    "SENTINEL-NAME-7Q",
    "SENTINEL-ACCOUNT-1A",
    "SENTINEL-TERRITORY-9M",
    "SENTINEL-CITY-2B",
    "98765.4321",
    "156790.0233",
)


def present(status: str = "ok", message: str = "Gotowe.", **fields: object) -> ToolCallPart:
    return ToolCallPart("present_result", {"status": status, "message": message, **fields})


class RecordingModel:
    """Replays one response per step and keeps the text of every request it receives."""

    def __init__(self, *steps: ToolCallPart | TextPart) -> None:
        self._steps = list(steps)
        self.traffic: list[str] = []

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tools = [
            {"name": t.name, "description": t.description, "schema": t.parameters_json_schema}
            for t in (*info.function_tools, *info.output_tools)
        ]
        self.traffic.append(ModelMessagesTypeAdapter.dump_json(messages).decode())
        self.traffic.append(json.dumps(tools, ensure_ascii=False))
        return ModelResponse(parts=[self._steps.pop(0)])

    @property
    def text(self) -> str:
        return "\n".join(self.traffic)


class Session:
    """One conversation over the sentinel rows, with every model request recorded."""

    def __init__(
        self,
        tools: Sequence[Tool[AgentDeps]],
        instructions: str,
        settings: Settings,
        deps: AgentDeps,
        *steps: ToolCallPart,
    ) -> None:
        self.model = RecordingModel(*steps)
        self.deps = deps
        self._agent = AgentService(FunctionModel(self.model), tools, instructions, settings)
        self._history: tuple[ModelMessage, ...] = ()

    async def ask(self, prompt: str) -> AgentTurn:
        turn = await self._agent.run(prompt, self._history, self.deps)
        self._history = turn.messages
        return turn

    def leaked(self) -> list[str]:
        """Sentinels and values of the session's handles found in the traffic."""
        return sorted(v for v in (*SENTINELS, *self._hidden_values()) if v in self.model.text)

    def _hidden_values(self) -> set[str]:
        """Every value of every handle except ids and dates."""
        return values(self.deps.results, lambda c: c.role not in (ColumnRole.ID, ColumnRole.TIME))


def values(results: ResultService, kept: Callable[[Column], bool]) -> set[str]:
    """The values of the kept columns of every handle, raw and as the user reads them."""
    formatter = ValueFormatter()
    found: set[str] = set()
    for handle in results.handles():
        columns = results.describe(handle).table_schema.columns
        rows = results.export(handle, max_rows=10_000).rows
        for index, column in enumerate(columns):
            for row in rows if kept(column) else ():
                if row[index] is not None:
                    found.add(str(row[index]))
                    found.add(formatter.format(row[index], column.type, column.role))
    return found


@pytest.fixture
def session(
    agent_tools: tuple[Tool[AgentDeps], ...],
    instructions: str,
    agent_settings: Settings,
    sentinel_deps: AgentDeps,
) -> Callable[..., Session]:
    """Opens a session with the given steps: `session(*steps)`."""

    class Bound(Session):
        def __init__(self, *steps: ToolCallPart) -> None:
            super().__init__(agent_tools, instructions, agent_settings, sentinel_deps, *steps)

    return Bound


async def test_procedures_query_describe_and_an_anchor_show_the_model_no_values(
    session: Callable[..., Session],
) -> None:
    conversation = session(
        ToolCallPart("customer_list", {"city": "Berlin"}),
        ToolCallPart("sales_order_list", YEAR_2024),
        ToolCallPart("query", {"sql": MONTHLY_SQL}),
        ToolCallPart("query", {"sql": TOTAL_SQL}),
        ToolCallPart("describe_result", {"handle": "r1"}),
        present(
            message="Sprzedaż netto w 2024: {r4.annual_total}.",
            tables=[{"handle": "r3", "title": "Sprzedaż netto po miesiącach, 2024"}],
        ),
    )

    turn = await conversation.ask("Sprzedaż klientów po miesiącach za 2024")
    response = ResponseAssembler(conversation.deps.results, 10_000).assemble(turn.answer, "s", "c")

    assert turn.answer.status == "ok"
    assert conversation.leaked() == []
    assert response.message == "Sprzedaż netto w 2024: 156\N{NO-BREAK SPACE}790,02."
    # The traffic does carry the tool results: ids and dates are allowed (D1, D4).
    assert "77001" in conversation.model.text
    assert "2024-02-03" in conversation.model.text


async def test_conversion_error_on_a_sentinel_does_not_quote_it(
    session: Callable[..., Session],
) -> None:
    conversation = session(
        ToolCallPart("customer_list", {"city": "Berlin"}),
        ToolCallPart("query", {"sql": 'SELECT CAST("CustomerName" AS INTEGER) AS n FROM r1'}),
        present("no_data"),
    )

    await conversation.ask("Klienci z Berlina")

    assert "ConversionException" in conversation.model.text
    assert conversation.leaked() == []


async def test_candidates_and_the_next_turn_show_the_model_no_values(
    session: Callable[..., Session],
) -> None:
    clarification = {
        "question": "Znaleziono {r2#rows} klientów. Którego wybierasz?",
        "candidates_from": {"handle": "r2", "id_column": "CustomerID"},
    }
    conversation = session(
        ToolCallPart("customer_list", {"city": "Berlin"}),
        ToolCallPart("query", {"sql": CANDIDATES_SQL}),
        present("needs_clarification", "Kilku klientów pasuje.", clarification=clarification),
        ToolCallPart("sales_order_list", {**YEAR_2024, "customer_id": 77001}),
        present(tables=[{"handle": "r3", "title": "Zamówienia klienta"}]),
    )

    first = await conversation.ask("Sprzedaż klienta z Berlina w 2024")
    response = ResponseAssembler(conversation.deps.results, 10_000).assemble(first.answer, "s", "c")
    await conversation.ask("Pierwszy")

    assert response.clarification is not None
    assert response.clarification.candidates[0].description == (
        "SENTINEL-ACCOUNT-1A, SENTINEL-NAME-7Q, SENTINEL-TERRITORY-9M"
    )
    assert conversation.leaked() == []


class SentinelSessions:
    """Hands out the session over the sentinel rows, so the test can read its handles."""

    def __init__(self, deps: AgentDeps) -> None:
        self._deps = deps

    def create(self) -> ApiSession:
        deps = self._deps
        return ApiSession(
            id="s1",
            procedures=deps.procedures,
            queries=deps.queries,
            results=deps.results,
            assembler=ResponseAssembler(deps.results, 10_000),
        )


@pytest.mark.parametrize("mode", ["minimal"])
async def test_in_mode_minimal_rows_reach_only_the_comment_and_sensitive_values_never(
    agent_tools: tuple[Tool[AgentDeps], ...],
    instructions: str,
    agent_settings: Settings,
    exposure: ModelExposure,
    sentinel_deps: AgentDeps,
) -> None:
    tools_model = RecordingModel(
        ToolCallPart("customer_list", {"city": "Berlin"}),
        ToolCallPart("sales_order_list", YEAR_2024),
        ToolCallPart("query", {"sql": TOTAL_SQL}),
        ToolCallPart("query", {"sql": TOP_CUSTOMER_SQL}),
        present(
            message="Sprzedaż netto w 2024: {r3.annual_total}.",
            # The procedure handle itself goes to the user.
            tables=[{"handle": "r2", "title": "Zamówienia, najlepszy {r4[1].CustomerName}"}],
            assumptions=["Najlepszy klient: {r4[1].CustomerName}"],
        ),
        present("no_data"),
    )
    comment_model = RecordingModel(
        TextPart("Razem {r3.annual_total}; najwięcej {r4[1].CustomerName}: {r4[1].net_sales}.")
    )
    service = AskService(
        AgentService(FunctionModel(tools_model), agent_tools, instructions, agent_settings),
        CommentService(FunctionModel(comment_model), agent_settings),
        InMemorySessionRepository(),
        SentinelSessions(sentinel_deps),
        exposure,
    )

    response = await service.ask("Zamówienia klientów z Berlina w 2024", user_id=7)
    await service.ask("A w 2019?", user_id=7, session_id=response.session_id)

    results = sentinel_deps.results
    sensitive = values(results, lambda column: column.sensitive)
    measures = values(results, lambda column: column.role is ColumnRole.MEASURE)
    assert response.message == (
        "Razem 156\N{NO-BREAK SPACE}790,02; najwięcej SENTINEL-NAME-7Q: "
        "144\N{NO-BREAK SPACE}444,34."
    )
    assert sorted(v for v in sensitive | measures if v in tools_model.text) == []
    assert sorted(v for v in sensitive if v in comment_model.text) == []
    # Literal sentinels too, in case a lost `sensitive` flag shrank the set above.
    for sentinel in ("SENTINEL-NAME-7Q", "SENTINEL-ACCOUNT-1A", "98765.4321"):
        assert sentinel not in tools_model.text
    for sentinel in ("SENTINEL-NAME-7Q", "SENTINEL-ACCOUNT-1A"):
        assert sentinel not in comment_model.text
    # The rows of the procedure handle returned as a table reach the comment phase only.
    assert "98765.4321" in comment_model.text
