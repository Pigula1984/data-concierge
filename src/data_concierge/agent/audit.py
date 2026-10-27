"""AuditCapability: the model's steps of a turn go to the audit log as they happen."""

from typing import TypeGuard, override

from pydantic import JsonValue
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import (
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolReturnPart,
)
from pydantic_ai.models import ModelRequestContext
from pydantic_core import to_jsonable_python

from data_concierge.agent.deps import AgentDeps


class AuditCapability(AbstractCapability[AgentDeps]):
    """Records what the model decided and what the backend sent back to it (architecture,
    section 8.1). It only observes: the request and the response pass through unchanged, so
    the start of every conversation stays the same (plan KTD11)."""

    @override
    async def before_model_request(
        self, ctx: RunContext[AgentDeps], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        """The tool results and retry prompts of the request about to be sent; the first
        request of a turn carries only the prompt, which `request` already holds."""
        latest = request_context.messages[-1] if request_context.messages else None
        if isinstance(latest, ModelRequest):
            parts: list[JsonValue] = [
                self._input(part) for part in latest.parts if self._is_input(part)
            ]
            if parts:
                ctx.deps.audit.record("model_input", {"parts": parts})
        return request_context

    @override
    async def after_model_request(
        self,
        ctx: RunContext[AgentDeps],
        *,
        request_context: ModelRequestContext,
        response: ModelResponse,
    ) -> ModelResponse:
        ctx.deps.audit.record("model_response", self._response(response))
        return response

    @staticmethod
    def _is_input(part: ModelRequestPart) -> TypeGuard[ToolReturnPart | RetryPromptPart]:
        return isinstance(part, ToolReturnPart | RetryPromptPart)

    @staticmethod
    def _input(part: ToolReturnPart | RetryPromptPart) -> dict[str, JsonValue]:
        if isinstance(part, RetryPromptPart):
            return {
                "kind": "retry",
                "tool": part.tool_name,
                "tool_call_id": part.tool_call_id,
                "content": part.model_response(),
            }
        return {
            "kind": "tool_return",
            "tool": part.tool_name,
            "tool_call_id": part.tool_call_id,
            "content": to_jsonable_python(part.content),
        }

    @staticmethod
    def _response(response: ModelResponse) -> dict[str, JsonValue]:
        """Thinking without its signature, text, tool calls with their arguments, and tokens."""
        usage = response.usage
        return {
            "thinking": [
                part.content
                for part in response.parts
                if isinstance(part, ThinkingPart) and part.content
            ],
            "text": [part.content for part in response.parts if isinstance(part, TextPart)],
            "tool_calls": [
                {
                    "tool": part.tool_name,
                    "tool_call_id": part.tool_call_id,
                    "args": to_jsonable_python(part.args_as_dict()),
                }
                for part in response.tool_calls
            ],
            "usage": {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_read_tokens": usage.cache_read_tokens,
                "cache_write_tokens": usage.cache_write_tokens,
            },
        }
