"""The command line with a stub AskService: JSON output, exit codes and the chat session."""

import io
import json

import pytest

from data_concierge.api.cli import CommandLine
from data_concierge.api.schemas import Response
from data_concierge.api.service import AskService

pytestmark = pytest.mark.anyio


class StubAskService(AskService):
    """Replays the given responses in order and records the calls."""

    def __init__(self, *responses: Response, drop_on_error: bool = False) -> None:
        self.responses = list(responses)
        self.drop_on_error = drop_on_error
        self.dropped: set[str] = set()
        self.calls: list[tuple[str, int, str | None]] = []

    async def ask(self, prompt: str, user_id: int, session_id: str | None = None) -> Response:
        """With `drop_on_error`, an error response drops its session, as after an engine failure."""
        self.calls.append((prompt, user_id, session_id))
        response = self.responses.pop(0)
        if self.drop_on_error and response.status == "error":
            self.dropped.add(response.session_id)
        return response

    def has_session(self, session_id: str) -> bool:
        return session_id not in self.dropped


def ok(session_id: str = "s1") -> Response:
    return Response(status="ok", session_id=session_id, message="Gotowe.")


def documents(output: str) -> list[dict[str, object]]:
    """The JSON documents written one after another, one per turn."""
    decoder, found, rest = json.JSONDecoder(), [], output.lstrip()
    while rest:
        document, end = decoder.raw_decode(rest)
        found.append(document)
        rest = rest[end:].lstrip()
    return found


async def run(service: AskService, *argv: str, stdin: str = "") -> tuple[int, str]:
    stdout = io.StringIO()
    command_line = CommandLine(service, io.StringIO(stdin), stdout)
    code = await command_line.run(CommandLine.parser().parse_args(argv))
    return code, stdout.getvalue()


async def test_ask_prints_the_response_json_and_exits_with_0() -> None:
    service = StubAskService(ok())

    code, output = await run(service, "ask", "Raport sprzedaży", "--user", "7")

    assert code == 0
    assert json.loads(output) == ok().model_dump(mode="json")
    assert service.calls == [("Raport sprzedaży", 7, None)]


async def test_ask_with_status_error_exits_with_a_nonzero_code() -> None:
    error = Response(status="error", session_id="s1", message="Nie udało się.")

    code, output = await run(StubAskService(error), "ask", "Raport", "--user", "7")

    assert code != 0
    assert json.loads(output)["status"] == "error"


async def test_chat_passes_the_session_id_of_the_first_turn_to_the_next_turns() -> None:
    service = StubAskService(ok("s1"), ok("s1"))

    code, output = await run(
        service, "chat", "--user", "7", stdin="Sprzedaż klienta Mohamed Pal\n\nTen pierwszy\n"
    )

    assert code == 0
    assert service.calls == [
        ("Sprzedaż klienta Mohamed Pal", 7, None),
        ("Ten pierwszy", 7, "s1"),
    ]
    assert [response["session_id"] for response in documents(output)] == ["s1", "s1"]


async def test_chat_starts_a_new_session_after_its_session_was_dropped() -> None:
    dropped = Response(status="error", session_id="s1", message="Nie udało się.")
    service = StubAskService(ok("s1"), dropped, ok("s2"), drop_on_error=True)

    code, _ = await run(service, "chat", "--user", "7", stdin="Pierwsza\nDruga\nTrzecia\n")

    assert code == 0
    assert [session_id for _, _, session_id in service.calls] == [None, "s1", None]
