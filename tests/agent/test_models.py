"""AgentTurn.with_message: the comment replaces the draft in the answer and in the history."""

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)

from data_concierge.agent.models import OUTPUT_TOOL, AgentAnswer, AgentTurn


def answer_call(message: str) -> ModelResponse:
    return ModelResponse(
        parts=[
            TextPart("Gotowe."),
            ToolCallPart(OUTPUT_TOOL, {"status": "ok", "message": message, "tables": []}),
        ]
    )


def messages_of(messages: tuple[ModelMessage, ...]) -> list[str]:
    """The `message` of every `present_result` call, oldest first."""
    return [
        str(part.args_as_dict()["message"])
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart) and part.tool_name == OUTPUT_TOOL
    ]


def test_with_message_replaces_only_the_last_answer_call_of_the_history() -> None:
    history = (
        ModelRequest(parts=[UserPromptPart("Raport za 2023")]),
        answer_call("Pierwsza tura."),
        ModelRequest(parts=[UserPromptPart("A za 2024?")]),
        # A rejected answer of this turn, retried after the validator's request for a fix.
        answer_call("Szkic {r9.total}."),
        answer_call("Szkic {r2.total}."),
    )
    turn = AgentTurn(answer=AgentAnswer(status="ok", message="Szkic {r2.total}."), messages=history)

    commented = turn.with_message("Komentarz {r2.total}.")

    assert commented.answer.message == "Komentarz {r2.total}."
    assert messages_of(commented.messages) == [
        "Pierwsza tura.",
        "Szkic {r9.total}.",
        "Komentarz {r2.total}.",
    ]
    # The other parts of the response stay as they were.
    assert commented.messages[-1].parts[0] == TextPart("Gotowe.")
    assert messages_of(turn.messages)[-1] == "Szkic {r2.total}."
