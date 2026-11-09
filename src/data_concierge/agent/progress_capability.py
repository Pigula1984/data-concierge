"""ProgressCapability: the agent's steps reach the turn's progress as they begin."""

from collections.abc import Mapping
from typing import override

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.capabilities.abstract import ValidatedToolArgs
from pydantic_ai.messages import ModelRequest, RetryPromptPart, ToolCallPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import ToolDefinition

from data_concierge.agent.deps import AgentDeps
from data_concierge.agent.models import OUTPUT_TOOL
from data_concierge.agent.progress import TurnProgress
from data_concierge.agent.tools import describe_result, query
from data_concierge.catalog.models import Catalog


class ProgressCapability(AbstractCapability[AgentDeps]):
    """Reports each model round and each tool call before it runs (architecture 003, section 8).

    It only observes, like AuditCapability: the request and the tool arguments pass through
    unchanged, and a step names a tool, never its arguments.
    """

    def __init__(self, procedure_texts: Mapping[str, str]) -> None:
        self._procedure_texts = dict(procedure_texts)

    @classmethod
    def for_catalog(cls, catalog: Catalog) -> ProgressCapability:
        """The step of every exposed procedure, from its catalog `progress` and name."""
        return cls(
            {
                entry.tool: TurnProgress.procedure_text(entry.progress, entry.procedure)
                for entry in catalog.exposed_entries
            }
        )

    @override
    async def before_model_request(
        self, ctx: RunContext[AgentDeps], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        """A round that answers a rejected `present_result` corrects the answer; the hook for
        output validation errors does not see the backend's checks, which run as output
        processing, so the retry prompt in the request is the reliable sign."""
        latest = request_context.messages[-1] if request_context.messages else None
        if isinstance(latest, ModelRequest) and any(
            isinstance(part, RetryPromptPart) and part.tool_name == OUTPUT_TOOL
            for part in latest.parts
        ):
            ctx.deps.progress.retry()
        else:
            ctx.deps.progress.model_round()
        return request_context

    @override
    async def before_tool_execute(
        self,
        ctx: RunContext[AgentDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
    ) -> ValidatedToolArgs:
        """Output tools never reach this hook, so `present_result` is not a step."""
        name = tool_def.name
        if name == query.__name__:
            ctx.deps.progress.query()
        elif name == describe_result.__name__:
            ctx.deps.progress.describe()
        else:
            text = self._procedure_texts.get(name) or TurnProgress.procedure_text(None, name)
            ctx.deps.progress.procedure(text)
        return args
