"""CommentService: the comment phase of mode minimal (architecture 002, section 5.2; plan KTD8)."""

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.exceptions import AgentRunError
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
{draft}

# Tables
{tables}"""


class CommentService:
    """Writes the final comment of a turn with status ok, after seeing the tables, without
    tools and on the model of the tools phase.

    It gets the draft as a template with anchors, never the filled text, and the tables
    without their sensitive columns; its comment's anchors are checked like the draft's.
    """

    def __init__(self, model: Model | str, settings: Settings) -> None:
        self._agent = Agent(
            model,
            deps_type=ResultService,
            output_type=str,
            instructions=COMMENT_INSTRUCTIONS,
            retries=settings.agent_retries,
            model_settings=AgentService.model_settings(settings),
        )
        self._agent.output_validator(self._check_comment)

    async def write(self, prompt: str, draft: AgentAnswer, results: ResultService) -> str:
        """The comment with anchors; raises CommentError when the model fails or its anchors
        are still wrong after the retries."""
        try:
            result = await self._agent.run(self._request(prompt, draft, results), deps=results)
        except AgentRunError as exc:
            raise CommentError("the comment phase failed", detail=str(exc)) from exc
        return result.output

    @staticmethod
    def _check_comment(ctx: RunContext[ResultService], comment: str) -> str:
        problems = Anchors(ctx.deps).problems(comment)
        if problems:
            raise ModelRetry(f"fix these anchors: {'; '.join(problems)}")
        return comment

    def _request(self, prompt: str, draft: AgentAnswer, results: ResultService) -> str:
        template = draft.model_dump_json(include={"status", "message", "tables", "assumptions"})
        tables = "\n".join(
            results.excerpt(handle).model_dump_json() for handle in self._handles(draft, results)
        )
        return COMMENT_REQUEST.format(prompt=prompt, draft=template, tables=tables)

    @staticmethod
    def _handles(draft: AgentAnswer, results: ResultService) -> list[Handle]:
        """The returned handles and those the draft's anchors name, in handle order."""
        named = {table.handle for table in draft.tables}
        named.update(handle for text in draft.texts for handle in Anchors.handles(text))
        return [handle for handle in results.handles() if handle in named]
