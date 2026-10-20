"""POST /ask and GET /health through FastAPI's TestClient, with a stub AskService."""

import pytest
from fastapi.testclient import TestClient

from data_concierge.api.schemas import Response
from data_concierge.api.service import AskService
from data_concierge.bootstrap import CompositionRoot


class StubAskService(AskService):
    """Answers every prompt with the given response and records the calls."""

    def __init__(self, response: Response) -> None:
        self.response = response
        self.calls: list[tuple[str, int, str | None]] = []

    async def ask(self, prompt: str, user_id: int, session_id: str | None = None) -> Response:
        self.calls.append((prompt, user_id, session_id))
        return self.response


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


def test_error_while_answering_is_status_error_in_a_200_response(
    client: TestClient, stub: StubAskService
) -> None:
    stub.response = ERROR

    reply = client.post("/ask", json={"prompt": "Raport", "user_id": 7})

    assert reply.status_code == 200
    assert reply.json()["status"] == "error"


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
