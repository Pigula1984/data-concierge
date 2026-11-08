"""POST /ask, POST /ask/stream and GET /health through FastAPI's TestClient, with a stub
AskService."""

import json

import pytest
from fastapi.testclient import TestClient

from data_concierge.agent.progress import ProgressEvent, ProgressListener
from data_concierge.api.schemas import Response
from data_concierge.api.service import AskService
from data_concierge.bootstrap import CompositionRoot
from data_concierge.core.errors import PromptTooLongError


class StubAskService(AskService):
    """Answers every prompt with the given response and records the calls."""

    def __init__(self, response: Response) -> None:
        self.response = response
        self.refusal: PromptTooLongError | None = None
        self.calls: list[tuple[str, int, str | None]] = []

    async def ask(
        self,
        prompt: str,
        user_id: int,
        session_id: str | None = None,
        listener: ProgressListener | None = None,
    ) -> Response:
        self.calls.append((prompt, user_id, session_id))
        if self.refusal is not None:
            raise self.refusal
        if listener is not None:
            listener(ProgressEvent(step="guard", text="Sprawdzam prośbę…"))
        return self.response

    def check_prompt(self, prompt: str) -> None:
        if self.refusal is not None:
            raise self.refusal


ERROR = Response(status="error", session_id="s1", message="Nie udało się. Identyfikator: abc.")


@pytest.fixture
def stub() -> StubAskService:
    return StubAskService(Response(status="ok", session_id="s1", message="Gotowe."))


@pytest.fixture
def client(stub: StubAskService) -> TestClient:
    return TestClient(CompositionRoot(stub).http_app())


def test_ask_returns_the_response_contract(client: TestClient, stub: StubAskService) -> None:
    reply = client.post("/ask", json={"prompt": "Raport", "user_id": 7, "session_id": "s1"})

    assert reply.status_code == 200
    assert reply.json() == stub.response.model_dump(mode="json")
    assert set(reply.json()) == set(Response.model_fields)
    assert stub.calls == [("Raport", 7, "s1")]


def test_request_without_prompt_is_rejected_with_422(
    client: TestClient, stub: StubAskService
) -> None:
    reply = client.post("/ask", json={"user_id": 7})

    assert reply.status_code == 422
    assert stub.calls == []


def test_prompt_over_the_limit_is_rejected_with_422_and_the_reason(
    client: TestClient, stub: StubAskService
) -> None:
    stub.refusal = PromptTooLongError("Prośba jest za długa.")

    reply = client.post("/ask", json={"prompt": "x", "user_id": 7})

    assert reply.status_code == 422
    assert reply.json() == {"detail": "Prośba jest za długa."}


def test_error_while_answering_is_status_error_in_a_200_response(
    client: TestClient, stub: StubAskService
) -> None:
    stub.response = ERROR

    reply = client.post("/ask", json={"prompt": "Raport", "user_id": 7})

    assert reply.status_code == 200
    assert reply.json()["status"] == "error"


def stream_data(body: str) -> list[dict[str, object]]:
    """The JSON of every `data:` line of a server-sent event stream."""
    return [
        json.loads(line[len("data: ") :]) for line in body.splitlines() if line.startswith("data: ")
    ]


def test_stream_sends_each_step_then_the_response(client: TestClient, stub: StubAskService) -> None:
    reply = client.post("/ask/stream", json={"prompt": "Raport", "user_id": 7, "session_id": "s1"})

    assert reply.status_code == 200
    assert reply.headers["content-type"].startswith("text/event-stream")
    assert stream_data(reply.text) == [
        {"step": "guard", "text": "Sprawdzam prośbę…"},
        stub.response.model_dump(mode="json"),
    ]
    assert stub.calls == [("Raport", 7, "s1")]


def test_stream_rejects_a_prompt_over_the_limit_with_422_before_it_opens(
    client: TestClient, stub: StubAskService
) -> None:
    stub.refusal = PromptTooLongError("Prośba jest za długa.")

    reply = client.post("/ask/stream", json={"prompt": "x", "user_id": 7})

    assert reply.status_code == 422
    assert reply.json() == {"detail": "Prośba jest za długa."}
    assert stub.calls == []


def test_stream_request_without_user_is_rejected_with_422(
    client: TestClient, stub: StubAskService
) -> None:
    reply = client.post("/ask/stream", json={"prompt": "Raport"})

    assert reply.status_code == 422
    assert stub.calls == []


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
