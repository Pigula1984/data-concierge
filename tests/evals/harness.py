"""What the evaluation run injects into the application: its clock, a recording repository and
a recording model."""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from time import perf_counter

from cases import Call
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
)
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from data_concierge.agent.guard import GUARD_TOOL
from data_concierge.catalog.models import CatalogEntry
from data_concierge.procedures.models import ProcedureResult
from data_concierge.procedures.repository import ProcedureRepository

# The start of the retry both anchor validators send: the answer's and the comment phase's.
ANCHOR_RETRY = "fix these anchors"


class EvalClock:
    """The date AskService adds to the prompt; each case sets its own."""

    def __init__(self, today: date) -> None:
        self.today = today

    def __call__(self) -> date:
        return self.today


class RecordingProcedureRepository:
    """Calls SQL Server and keeps each call's procedure and parameters for the case checks."""

    def __init__(self, inner: ProcedureRepository) -> None:
        self._inner = inner
        self.calls: list[Call] = []

    def execute(self, entry: CatalogEntry, params: Mapping[str, object]) -> ProcedureResult:
        self.calls.append((entry.procedure, dict(params)))
        return self._inner.execute(entry, params)


class RecordingModel(WrapperModel):
    """The configured model; keeps every response of both phases of a turn, so the comment
    phase counts too, which `capture_run_messages` does not see (plan KTD11)."""

    def __init__(self, wrapped: Model) -> None:
        super().__init__(wrapped)
        self.responses: list[ModelResponse] = []
        self.anchor_retries = 0
        self.seconds = 0.0

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        # Only the newest request: each request also resends the history before it.
        self.anchor_retries += self._anchor_retries(messages[-1])
        started = perf_counter()
        response = await super().request(messages, model_settings, model_request_parameters)
        self.seconds += perf_counter() - started
        self.responses.append(response)
        return response

    def clear(self) -> None:
        self.responses.clear()
        self.anchor_retries = 0
        self.seconds = 0.0

    @property
    def comment(self) -> str | None:
        """The text of the comment phase, when the turn ended with one: the tools phase ends
        with a call of `present_result`, and the history keeps only that draft."""
        last = self.responses[-1] if self.responses else None
        return last.text if last is not None and not last.tool_calls else None

    @property
    def cost(self) -> Decimal | None:
        """The price of the responses in USD; None when the price list lacks the model."""
        try:
            return sum((response.cost().total_price for response in self.responses), Decimal(0))
        except LookupError:
            return None

    @staticmethod
    def _anchor_retries(message: ModelMessage) -> int:
        if not isinstance(message, ModelRequest):
            return 0
        return sum(
            isinstance(part, RetryPromptPart)
            and isinstance(part.content, str)
            and part.content.startswith(ANCHOR_RETRY)
            for part in message.parts
        )


class RecordingGuardModel(RecordingModel):
    """The guard's model, recorded apart, because the audit log is off in the evaluation and a
    comparison with earlier runs could not separate its cost."""

    @property
    def blocked(self) -> int:
        """The turns the guard stopped."""
        return sum(
            isinstance(part, ToolCallPart)
            and part.tool_name == GUARD_TOOL
            and part.args_as_dict().get("verdict") == "blocked"
            for response in self.responses
            for part in response.parts
        )
