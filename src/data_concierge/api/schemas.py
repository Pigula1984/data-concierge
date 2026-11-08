"""The backend's response contract (architecture, section 9).

This module imports only `core` and `results`: the agent module builds these models, and the
api module calls the agent, so a dependency back on either would make an import cycle.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, JsonValue

from data_concierge.core.types import ColumnType
from data_concierge.results.models import Handle, Rows

type AnswerStatus = Literal["ok", "no_data", "needs_clarification", "unsupported", "error"]
type ChartKind = Literal["line", "bar", "stacked_bar", "pie", "scatter"]


class Candidate(BaseModel, frozen=True):
    """One of several matching entities the user has to choose from."""

    id: int | str = Field(description="Id of the entity from the search result, e.g. CustomerID")
    description: str = Field(
        description="What tells the candidates apart, in Polish, e.g. account number, "
        "territory and order count"
    )


class Clarification(BaseModel, frozen=True):
    question: str = Field(description="The question to the user, in Polish")
    candidates: tuple[Candidate, ...] = Field(
        default=(), description="Entities to choose from, when the question is which one"
    )


class ResponseColumn(BaseModel, frozen=True):
    name: str
    type: ColumnType


class ResponseTable(BaseModel, frozen=True):
    """Full data of one returned handle; decimals are text, dates ISO strings."""

    handle: Handle
    title: str
    columns: tuple[ResponseColumn, ...]
    rows: Rows
    row_count: int
    # Rows were cut: by the response row limit, or by max_rows of an ancestor procedure.
    truncated: bool


class ResponseChart(BaseModel, frozen=True):
    """A chart of one returned table: the page draws it from the rows of that table, so the
    data is not sent twice (architecture 003, section 7.2)."""

    handle: Handle
    kind: ChartKind
    x: str
    y: tuple[str, ...]
    series: str | None = None
    title: str


class ProcedureStep(BaseModel, frozen=True):
    """A procedure call; injected parameters such as `@UserId` are not listed."""

    step: Literal["procedure"] = "procedure"
    handle: Handle
    name: str
    params: dict[str, JsonValue]
    rows: int
    truncated: bool


class QueryStep(BaseModel, frozen=True):
    """A query over earlier handles, as the model wrote it and as the backend ran it."""

    step: Literal["query"] = "query"
    handle: Handle
    sources: tuple[Handle, ...]
    submitted_sql: str
    sql: str
    rows: int


type ProvenanceStep = Annotated[ProcedureStep | QueryStep, Field(discriminator="step")]


class Response(BaseModel, frozen=True):
    """What `ask` returns (architecture, section 9)."""

    status: AnswerStatus
    session_id: str
    message: str
    tables: tuple[ResponseTable, ...] = ()
    assumptions: tuple[str, ...] = ()
    clarification: Clarification | None = None
    chart: ResponseChart | None = None
    # Every ancestor of the returned tables, each once, in handle order (section 7.6).
    provenance: tuple[ProvenanceStep, ...] = ()
