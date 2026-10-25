"""AgentService: one turn of the Pydantic AI agent over the session's tools (section 11.2)."""

import logging
from collections.abc import Sequence

from pydantic_ai import Agent, ModelRetry, RunContext, Tool, ToolOutput
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModelSettings
from pydantic_ai.usage import UsageLimits

from data_concierge.agent.anchors import Anchors
from data_concierge.agent.deps import AgentDeps
from data_concierge.agent.models import OUTPUT_TOOL, AgentAnswer, AgentTurn, CandidatesFrom
from data_concierge.core.settings import Settings
from data_concierge.core.types import ColumnKind
from data_concierge.results.service import ResultService

logger = logging.getLogger(__name__)

# Pydantic AI appends the AgentAnswer docstring after ". ", so no closing period here.
OUTPUT_DESCRIPTION = "End the turn: the status, a short comment and the handles to return"


class AgentService:
    """Runs the agent with fixed instructions and tools, so every conversation starts with the
    same bytes (plan KTD11), and turns a run that cannot finish into an `error` answer."""

    def __init__(
        self,
        model: Model | str,
        tools: Sequence[Tool[AgentDeps]],
        instructions: str,
        settings: Settings,
    ) -> None:
        self._agent = Agent(
            model,
            deps_type=AgentDeps,
            output_type=ToolOutput(AgentAnswer, name=OUTPUT_TOOL, description=OUTPUT_DESCRIPTION),
            instructions=instructions,
            tools=tools,
            retries=settings.agent_retries,
            model_settings=self.model_settings(settings),
            # An answer ends the turn before tools called in the same response run, so it can
            # only name handles whose summaries the model has already seen (section 7.5).
            end_strategy="early",
        )
        self._max_candidates = settings.model_max_rows
        self._agent.output_validator(self._check_answer)
        self._limits = UsageLimits(request_limit=settings.max_agent_steps)

    async def run(self, prompt: str, history: Sequence[ModelMessage], deps: AgentDeps) -> AgentTurn:
        """On failure the history stays as it was, without the failed turn."""
        try:
            result = await self._agent.run(
                prompt, message_history=list(history), deps=deps, usage_limits=self._limits
            )
        except (UsageLimitExceeded, UnexpectedModelBehavior) as exc:
            logger.warning(
                "agent turn failed (correlation id %s): %s", deps.context.correlation_id, exc
            )
            return AgentTurn(
                answer=AgentAnswer(status="error", message=""), messages=tuple(history)
            )
        self._log_dropped_thinking(result.new_messages(), deps.context.correlation_id)
        if result.output.status == "error":
            # The response shows only a generic message, so the model's account goes to the log.
            logger.warning(
                "the model ended the turn with an error (correlation id %s): %s",
                deps.context.correlation_id,
                result.output.message,
            )
        return AgentTurn(answer=result.output, messages=tuple(result.all_messages()))

    @staticmethod
    def model_settings(settings: Settings) -> AnthropicModelSettings:
        """Claude settings; another provider ignores the `anthropic_` keys (section 11.2).

        Thinking blocks bound to a changed conversation start are dropped instead of failing
        the request (KTD11); `_log_dropped_thinking` reports when that happens.
        """
        return AnthropicModelSettings(
            max_tokens=settings.max_output_tokens,
            anthropic_effort=settings.effort,
            anthropic_thinking={
                "type": "adaptive",
                "block_binding": {"prefix_mismatch_behavior": "drop_block"},
            },
            anthropic_cache_instructions=True,
            anthropic_cache_tool_definitions=True,
        )

    def _check_answer(self, ctx: RunContext[AgentDeps], answer: AgentAnswer) -> AgentAnswer:
        """Returned handles exist, a clarification request has its question, its candidates
        come from a small handle and every anchor names a value. An error answer is not
        checked: the user gets a generic message."""
        if answer.status == "error":
            return answer
        available = ctx.deps.results.handles()
        unknown = [table.handle for table in answer.tables if table.handle not in available]
        if unknown:
            raise ModelRetry(
                f"unknown handles {', '.join(unknown)}; available handles: "
                f"{', '.join(available) or 'none'}"
            )
        if answer.status == "needs_clarification" and answer.clarification is None:
            raise ModelRetry("status needs_clarification requires `clarification` with a question")
        if answer.clarification is not None and answer.clarification.candidates_from is not None:
            self._check_candidates(ctx.deps.results, answer.clarification.candidates_from)
        anchors = Anchors(ctx.deps.results)
        problems = [problem for text in answer.texts for problem in anchors.problems(text)]
        if problems:
            raise ModelRetry(f"fix these anchors: {'; '.join(problems)}")
        return answer

    def _check_candidates(self, results: ResultService, source: CandidatesFrom) -> None:
        available = results.handles()
        if source.handle not in available:
            raise ModelRetry(
                f"candidates_from: unknown handle {source.handle}; available handles: "
                f"{', '.join(available) or 'none'}"
            )
        stored = results.describe(source.handle)
        column = stored.table_schema.column(source.id_column)
        if column is None:
            raise ModelRetry(
                f"candidates_from: {source.handle} has no column {source.id_column}; columns: "
                f"{', '.join(stored.table_schema.names)}"
            )
        if column.type.kind not in (ColumnKind.INT, ColumnKind.STRING):
            raise ModelRetry("candidates_from: id_column must be an int or a string column")
        # The user's choice comes back as the id, so it must be a key the model may see.
        if column.sensitive:
            raise ModelRetry(
                f"candidates_from: {column.name} is not shown to you; use a technical key such "
                "as CustomerID or ProductID"
            )
        if stored.row_count > self._max_candidates:
            raise ModelRetry(
                f"candidates_from: {source.handle} has {stored.row_count} rows; at most "
                f"{self._max_candidates} can be candidates"
            )

    @staticmethod
    def _log_dropped_thinking(messages: Sequence[ModelMessage], correlation_id: str) -> None:
        """Anthropic dropped a thinking block because the conversation start changed: a sign
        that instructions or tools are not stable, which also defeats the prompt cache."""
        for message in messages:
            details = message.provider_details if isinstance(message, ModelResponse) else None
            if details and details.get("input_transformations"):
                logger.warning(
                    "the model dropped thinking blocks (correlation id %s): %s",
                    correlation_id,
                    details["input_transformations"],
                )
