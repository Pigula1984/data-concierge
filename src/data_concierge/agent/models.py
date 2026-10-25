"""What the model returns at the end of a turn, and the outcome of a turn for the caller."""

from dataclasses import dataclass, replace
from typing import TypeGuard

from pydantic import BaseModel, Field
from pydantic_ai.messages import ModelMessage, ModelResponse, ModelResponsePart, ToolCallPart

from data_concierge.api.schemas import AnswerStatus
from data_concierge.results.models import Handle

OUTPUT_TOOL = "present_result"


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

    def with_message(self, message: str) -> AgentTurn:
        """The turn with another `message`, in the answer and in the last `present_result` call
        of the history, so the next turn sees the template the user read (plan KTD8)."""
        messages = list(self.messages)
        for index in reversed(range(len(messages))):
            response = messages[index]
            if isinstance(response, ModelResponse) and any(
                self._is_answer(part) for part in response.parts
            ):
                parts = [self._with_message(part, message) for part in response.parts]
                messages[index] = replace(response, parts=parts)
                break
        answer = self.answer.model_copy(update={"message": message})
        return AgentTurn(answer=answer, messages=tuple(messages))

    @staticmethod
    def _is_answer(part: ModelResponsePart) -> TypeGuard[ToolCallPart]:
        return isinstance(part, ToolCallPart) and part.tool_name == OUTPUT_TOOL

    @classmethod
    def _with_message(cls, part: ModelResponsePart, message: str) -> ModelResponsePart:
        if cls._is_answer(part):
            return replace(part, args={**part.args_as_dict(), "message": message})
        return part
