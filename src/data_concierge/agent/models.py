"""What the model returns at the end of a turn, and the outcome of a turn for the caller."""

from dataclasses import dataclass

from pydantic import BaseModel, Field
from pydantic_ai.messages import ModelMessage

from data_concierge.api.schemas import AnswerStatus, Clarification
from data_concierge.results.models import Handle


class TableRef(BaseModel, frozen=True):
    handle: Handle = Field(description="Result handle to return, e.g. r2")
    title: str = Field(description="Table title for the user, in Polish")


# The docstring is part of the `present_result` description the model reads; the model
# names tables by handle and the backend adds their data and provenance (plan KTD17).
class AgentAnswer(BaseModel, frozen=True):
    """Name tables by handle only: the backend adds their data and provenance."""

    status: AnswerStatus = Field(
        description="ok: the tables answer the request; no_data: nothing matches; "
        "needs_clarification: the user must choose or add something; unsupported: no "
        "procedure provides this data; error: a procedure failed and the data could not be "
        "fetched"
    )
    message: str = Field(
        description="Short comment for the user, in Polish; numbers only from query results "
        "you saw in full"
    )
    tables: tuple[TableRef, ...] = Field(default=(), description="Result handles to return")
    assumptions: tuple[str, ...] = Field(
        default=(),
        description="Choices made for the user, in Polish, e.g. the measure column and the period",
    )
    clarification: Clarification | None = Field(
        default=None, description="Required when the status is needs_clarification"
    )


@dataclass(frozen=True, slots=True)
class AgentTurn:
    """The answer of one turn and the conversation history to continue from."""

    answer: AgentAnswer
    messages: tuple[ModelMessage, ...]
