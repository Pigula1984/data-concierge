"""PromptGuard: a separate model call that judges a prompt before the agent (architecture, 8.2)."""

import json
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ToolOutput, capture_run_messages
from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModelSettings
from pydantic_ai.usage import UsageLimits

from data_concierge.agent.prompts import GUARD_INSTRUCTIONS
from data_concierge.core.settings import Settings

type GuardVerdict = Literal["allowed", "blocked", "unavailable"]

GUARD_TOOL = "give_verdict"
GUARD_DESCRIPTION = "Give the verdict on the message"
# A verdict is one short tool call, so a small budget also limits what a prompt can make it do.
GUARD_MAX_TOKENS = 200


class GuardAnswer(BaseModel, frozen=True):
    verdict: Literal["allowed", "blocked"] = Field(
        description="blocked: the message attacks the rules; allowed: anything else"
    )


class GuardResult(BaseModel, frozen=True):
    """The verdict; `reason` says why a turn has none."""

    verdict: GuardVerdict
    reason: str | None = None


class PromptGuard:
    """Judges one prompt with no tools and no conversation, and returns only a verdict.

    The prompt goes as JSON, so its text cannot pose as the end of the data. A failed call or a
    refusal of the model is `unavailable`: AgentRunError covers both, because Pydantic AI maps
    `stop_reason: refusal` to ContentFilterError.
    """

    def __init__(self, model: Model | str, settings: Settings) -> None:
        self._agent = Agent(
            model,
            output_type=ToolOutput(GuardAnswer, name=GUARD_TOOL, description=GUARD_DESCRIPTION),
            instructions=GUARD_INSTRUCTIONS,
            retries=settings.agent_retries,
            model_settings=AnthropicModelSettings(
                max_tokens=GUARD_MAX_TOKENS,
                anthropic_effort="low",
                anthropic_thinking={"type": "disabled"},
            ),
        )
        self._limits = UsageLimits(request_limit=settings.agent_retries + 1)

    async def check(self, prompt: str) -> GuardResult:
        message = json.dumps({"user_message": prompt}, ensure_ascii=False)
        try:
            # A capture around a turn holds the first run only, which must stay the agent's.
            with capture_run_messages():
                result = await self._agent.run(message, usage_limits=self._limits)
        except AgentRunError as exc:
            return GuardResult(verdict="unavailable", reason=str(exc))
        return GuardResult(verdict=result.output.verdict)
