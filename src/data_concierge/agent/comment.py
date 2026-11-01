"""CommentService: the comment phase of mode minimal (architecture 002, section 5.2; plan KTD8)."""

from dataclasses import dataclass

from pydantic_ai import Agent, ModelRetry, RunContext, Tool
from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model

from data_concierge.agent.anchors import Anchors
from data_concierge.agent.models import AgentAnswer
from data_concierge.agent.prompts import COMMENT_INSTRUCTIONS
from data_concierge.agent.service import AgentService
from data_concierge.core.errors import CommentError
from data_concierge.core.settings import Settings
from data_concierge.results.models import Handle
from data_concierge.results.service import ResultService

COMMENT_REQUEST = """\
# User request
{prompt}

# Draft answer
{draft}"""
READ_TABLES_TOOL = "read_tables"


@dataclass(frozen=True, slots=True)
class CommentDeps:
    """The session's results and the tables of the comment, as `read_tables` gives them."""

    results: ResultService
    tables: str


class CommentService:
    """Writes the final comment of a turn with status ok, after seeing the tables, without
    tools for the data and on the model of the tools phase.

    It gets the draft as a template with anchors, never the filled text, and the tables
    without their sensitive columns; its comment's anchors are checked like the draft's.

    The tables arrive as a tool result from a seeded exchange, marked for the model as data
    and not as the user's words; the seed adds no model round. That model turn has no thinking
    block, which the API requires when thinking is on, so this agent runs without thinking.
    """

    def __init__(self, model: Model | str, settings: Settings) -> None:
        model_settings = AgentService.model_settings(settings)
        model_settings["anthropic_thinking"] = {"type": "disabled"}
        self._agent = Agent(
            model,
            deps_type=CommentDeps,
            output_type=str,
            instructions=COMMENT_INSTRUCTIONS,
            tools=[Tool(self._read_tables, name=READ_TABLES_TOOL)],
            retries=settings.agent_retries,
            model_settings=model_settings,
        )
        self._agent.output_validator(self._check_comment)

    async def write(self, prompt: str, draft: AgentAnswer, results: ResultService) -> str:
        """The comment with anchors; raises CommentError when the model fails or its anchors
        are still wrong after the retries."""
        tables = self._tables(draft, results)
        try:
            result = await self._agent.run(
                message_history=self._exchange(prompt, draft, tables),
                deps=CommentDeps(results, tables),
            )
        except AgentRunError as exc:
            raise CommentError("the comment phase failed", detail=str(exc)) from exc
        return result.output

    @staticmethod
    def _read_tables(ctx: RunContext[CommentDeps]) -> str:
        """The tables of the answer, one JSON document per line."""
        return ctx.deps.tables

    @staticmethod
    def _check_comment(ctx: RunContext[CommentDeps], comment: str) -> str:
        problems = Anchors(ctx.deps.results).problems(comment)
        if problems:
            raise ModelRetry(f"fix these anchors: {'; '.join(problems)}")
        return comment

    @staticmethod
    def _exchange(prompt: str, draft: AgentAnswer, tables: str) -> list[ModelMessage]:
        """The request, a call of `read_tables` and its result; the agent answers the result."""
        template = draft.model_dump_json(include={"status", "message", "tables", "assumptions"})
        request = COMMENT_REQUEST.format(prompt=prompt, draft=template)
        call = ToolCallPart(READ_TABLES_TOOL, {})
        result = ToolReturnPart(READ_TABLES_TOOL, tables, tool_call_id=call.tool_call_id)
        return [
            ModelRequest(parts=[UserPromptPart(request)]),
            ModelResponse(parts=[call]),
            ModelRequest(parts=[result]),
        ]

    def _tables(self, draft: AgentAnswer, results: ResultService) -> str:
        return "\n".join(
            results.excerpt(handle).model_dump_json() for handle in self._handles(draft, results)
        )

    @staticmethod
    def _handles(draft: AgentAnswer, results: ResultService) -> list[Handle]:
        """The returned handles and those the draft's anchors name, in handle order."""
        named = {table.handle for table in draft.tables}
        named.update(handle for text in draft.texts for handle in Anchors.handles(text))
        return [handle for handle in results.handles() if handle in named]
