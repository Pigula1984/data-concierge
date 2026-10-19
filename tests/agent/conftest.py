"""A real session (DuckDB results, queries, procedure service) over a fake SQL Server.

Agent tests drive the tools through Pydantic AI with a scripted FunctionModel, so only the
procedure repository is replaced: it returns fixed rows typed by the catalog entry.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pyarrow as pa
import pytest
from pydantic import SecretStr
from pydantic_ai import Agent, Tool
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.tools import ToolDefinition

from data_concierge.agent.deps import AgentDeps
from data_concierge.agent.prompts import AgentInstructions
from data_concierge.agent.registry import ProcedureToolFactory, ToolRegistry
from data_concierge.catalog.models import Catalog, CatalogEntry, DataRange
from data_concierge.core.context import RequestContext
from data_concierge.core.settings import Settings
from data_concierge.procedures.models import ProcedureResult
from data_concierge.procedures.service import ProcedureService
from data_concierge.query.policy import QueryPolicy
from data_concierge.query.service import QueryService
from data_concierge.results.repository import DuckDBResultStore
from data_concierge.results.service import ResultService

type Row = tuple[object, ...]
type ToolDefinitions = Callable[[Sequence[Tool[AgentDeps]]], Awaitable[list[ToolDefinition]]]

ORDERS: tuple[Row, ...] = (
    (
        43659,
        date(2024, 1, 5),
        12698,
        "Mohamed Pal",
        "individual",
        "Germany",
        Decimal("100.5000"),
        Decimal("120.0000"),
    ),
    (
        43660,
        date(2024, 1, 20),
        20550,
        "Mohamed Pal",
        "individual",
        "Germany",
        Decimal("49.2500"),
        Decimal("55.0000"),
    ),
    (
        43661,
        date(2024, 3, 2),
        11000,
        "Jon Yang",
        "individual",
        "Australia",
        Decimal("10.0000"),
        Decimal("12.0000"),
    ),
)
CUSTOMERS: tuple[Row, ...] = (
    (
        12698,
        "AW00012698",
        "Mohamed Pal",
        "individual",
        "Home",
        "Berlin",
        "Brandenburg",
        "Germany",
        "Germany",
    ),
    (
        20550,
        "AW00020550",
        "Mohamed Pal",
        "individual",
        "Home",
        "Berlin",
        "Hessen",
        "Germany",
        "Germany",
    ),
)
ROWS_BY_PROCEDURE: dict[str, tuple[Row, ...]] = {
    "api.SalesOrder_List": ORDERS,
    "api.Customer_List": CUSTOMERS,
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class FakeProcedureRepository:
    """Returns fixed rows per procedure and records the parameters of every call.

    `error` is raised instead of returning rows, e.g. to simulate a procedure timeout;
    `truncated` reports every result as cut at the catalog's max_rows.
    """

    rows: Mapping[str, Sequence[Row]] = field(default_factory=lambda: ROWS_BY_PROCEDURE)
    error: Exception | None = None
    truncated: bool = False
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def execute(self, entry: CatalogEntry, params: Mapping[str, object]) -> ProcedureResult:
        self.calls.append((entry.procedure, dict(params)))
        if self.error is not None:
            raise self.error
        rows = self.rows.get(entry.procedure, ())
        schema = entry.table_schema
        columns = [
            pa.array([row[index] for row in rows], type=column.type.arrow_type)
            for index, column in enumerate(schema.columns)
        ]
        table = pa.Table.from_arrays(columns, schema=pa.schema(schema.arrow_fields))
        return ProcedureResult(data=table, truncated=self.truncated)


@pytest.fixture
def data_ranges() -> dict[str, DataRange]:
    """Data ranges by tool name, as CatalogService.load_data_ranges reads them at startup."""
    sales = DataRange(first=date(2022, 5, 30), last=date(2025, 6, 29))
    return {"sales_order_list": sales, "sales_order_line_list": sales}


@pytest.fixture
def agent_tools(catalog: Catalog, data_ranges: dict[str, DataRange]) -> tuple[Tool[AgentDeps], ...]:
    """The tools of the repository catalog with strict schemas."""
    factory = ProcedureToolFactory(data_ranges, strict=True)
    return ToolRegistry(catalog, factory, strict=True).tools()


@pytest.fixture
def instructions(catalog: Catalog, data_ranges: dict[str, DataRange]) -> str:
    return AgentInstructions(catalog, data_ranges).text()


@pytest.fixture
def agent_settings() -> Settings:
    """Default model settings; the SQL Server values are required but unused offline."""
    return Settings(sql_server="localhost", sql_password=SecretStr("unused"), _env_file=None)


@pytest.fixture
def repository() -> FakeProcedureRepository:
    return FakeProcedureRepository()


@pytest.fixture
def procedure_calls(repository: FakeProcedureRepository) -> list[tuple[str, dict[str, object]]]:
    """Procedure and database parameters of every call, injected ones included."""
    return repository.calls


@pytest.fixture
def fail_procedures(repository: FakeProcedureRepository) -> Callable[[Exception], None]:
    """Makes every later procedure call raise the given error."""

    def fail(error: Exception) -> None:
        repository.error = error

    return fail


@pytest.fixture
def truncate_procedures(repository: FakeProcedureRepository) -> Callable[[bool], None]:
    """Makes every later procedure result cut at max_rows, or complete again."""

    def truncate(truncated: bool) -> None:
        repository.truncated = truncated

    return truncate


@pytest.fixture
def results() -> ResultService:
    return ResultService(DuckDBResultStore(query_timeout_seconds=30.0))


@pytest.fixture
def deps(repository: FakeProcedureRepository, results: ResultService) -> AgentDeps:
    return AgentDeps(
        context=RequestContext(user_id=7, role="analyst"),
        procedures=ProcedureService(repository, results),
        queries=QueryService(results, QueryPolicy()),
        results=results,
    )


class ScriptedModel:
    """A FunctionModel callback that replays one response per step.

    A step is a tool call or a full response; after the last step the model answers `done`.
    """

    def __init__(self, *steps: ToolCallPart | ModelResponse) -> None:
        self._steps = list(steps)

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not self._steps:
            return ModelResponse(parts=[TextPart("done")])
        step = self._steps.pop(0)
        return step if isinstance(step, ModelResponse) else ModelResponse(parts=[step])


@pytest.fixture
def scripted() -> Callable[..., FunctionModel]:
    """A model that replays the given steps; tests cannot import conftest in importlib mode."""
    return lambda *steps: FunctionModel(ScriptedModel(*steps))


@pytest.fixture
def tool_definitions() -> ToolDefinitions:
    """The tool definitions the model receives for the given tools, in the order it sees them."""

    async def definitions(tools: Sequence[Tool[AgentDeps]]) -> list[ToolDefinition]:
        seen: list[ToolDefinition] = []

        def capture(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            seen.extend(info.function_tools)
            return ModelResponse(parts=[TextPart("done")])

        await Agent(FunctionModel(capture), deps_type=AgentDeps, tools=tools).run("schema")
        return seen

    return definitions
