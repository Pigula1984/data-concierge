"""AgentAnswer texts and AgentTurn.with_message: the comment replaces the draft in the answer,
not in the history."""

from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, UserPromptPart

from data_concierge.agent.models import (
    OUTPUT_TOOL,
    AgentAnswer,
    AgentTurn,
    ChartRequest,
    TableRef,
)


def test_with_message_changes_the_answer_and_leaves_the_history_with_the_draft() -> None:
    history = (
        ModelRequest(parts=[UserPromptPart("Raport za 2024")]),
        ModelResponse(
            parts=[ToolCallPart(OUTPUT_TOOL, {"status": "ok", "message": "Szkic {r2.total}."})]
        ),
    )
    turn = AgentTurn(answer=AgentAnswer(status="ok", message="Szkic {r2.total}."), messages=history)

    commented = turn.with_message("Komentarz {r2.total}.")

    assert commented.answer.message == "Komentarz {r2.total}."
    assert commented.messages == history
    assert turn.answer.message == "Szkic {r2.total}."


def test_chart_title_is_one_of_the_texts_the_user_reads() -> None:
    answer = AgentAnswer(
        status="ok",
        message="Gotowe.",
        tables=(TableRef(handle="r2", title="Sprzedaż"),),
        chart=ChartRequest(
            handle="r2", kind="line", x="month", y=("net_sales",), title="Razem {r3.total}"
        ),
    )

    assert "Razem {r3.total}" in answer.texts
