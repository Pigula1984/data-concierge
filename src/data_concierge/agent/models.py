"""What the model returns at the end of a turn, and the outcome of a turn for the caller."""

from dataclasses import dataclass

from pydantic import BaseModel, Field
from pydantic_ai.messages import ModelMessage

from data_concierge.api.schemas import AnswerStatus
from data_concierge.results.models import Handle


class TableRef(BaseModel, frozen=True):
    handle: Handle = Field(description="Result handle to return, e.g. r2")
    title: str = Field(description="Table title for the user, in Polish; data values by anchor")


# The backend lists the rows of this handle as the candidates (architecture 002, section 4.3).
# Docstrings of these models reach the model, so this note is a comment.
class CandidatesFrom(BaseModel, frozen=True):
    handle: Handle = Field(
        description="Handle with one row per candidate, within the row limit of the "
        "instructions; its other columns describe the candidates to the user, e.g. account "
        "number and territory"
    )
    id_column: str = Field(description="Column with the id of the candidate, e.g. CustomerID")


class ClarificationRequest(BaseModel, frozen=True):
    question: str = Field(
        description="The question to the user, in Polish; data values by anchor, e.g. {r2#rows}"
    )
    candidates_from: CandidatesFrom | None = Field(
        default=None, description="When the user must choose one of several entities"
    )


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
        description="Short comment for the user, in Polish. Write every value from the data as "
        "an anchor the backend fills in: {r3.net_sales} (one-row handle), {r2[1].month} (row "
        "1), {r2#rows} (row count)"
    )
    tables: tuple[TableRef, ...] = Field(default=(), description="Result handles to return")
    assumptions: tuple[str, ...] = Field(
        default=(),
        description="Choices made for the user, in Polish, e.g. the measure column and the "
        "period; data values by anchor",
    )
    clarification: ClarificationRequest | None = Field(
        default=None, description="Required when the status is needs_clarification"
    )

    @property
    def texts(self) -> tuple[str, ...]:
        """Every text the user reads, where anchors may stand (architecture 002, 6.1)."""
        question = (self.clarification.question,) if self.clarification is not None else ()
        titles = tuple(table.title for table in self.tables)
        return (self.message, *titles, *self.assumptions, *question)


@dataclass(frozen=True, slots=True)
class AgentTurn:
    """The answer of one turn and the conversation history to continue from."""

    answer: AgentAnswer
    messages: tuple[ModelMessage, ...]
