"""The built browser app served under `/` next to the API, with its content security policy."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from data_concierge.api.schemas import Response
from data_concierge.api.service import AskService
from data_concierge.bootstrap import CompositionRoot
from data_concierge.web.controller import CONTENT_SECURITY_POLICY, WebFrontend

INDEX = "<!doctype html><title>Data Concierge</title>"
SCRIPT = "console.log('app');"


class StubAskService(AskService):
    """Answers every prompt with a fixed response."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def ask(self, prompt: str, user_id: int, session_id: str | None = None) -> Response:
        self.prompts.append(prompt)
        return Response(status="ok", session_id="s1", message="Gotowe.")


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text(INDEX, encoding="utf-8")
    (tmp_path / "assets" / "app.js").write_text(SCRIPT, encoding="utf-8")
    return tmp_path


def client_for(dist: Path, stub: StubAskService | None = None) -> TestClient:
    root = CompositionRoot(stub or StubAskService())
    return TestClient(root.http_app(WebFrontend(dist)))


def test_root_serves_the_built_page(dist: Path) -> None:
    reply = client_for(dist).get("/")

    assert reply.status_code == 200
    assert reply.headers["content-type"].startswith("text/html")
    assert reply.text == INDEX


def test_assets_of_the_build_are_served(dist: Path) -> None:
    reply = client_for(dist).get("/assets/app.js")

    assert reply.status_code == 200
    assert reply.text == SCRIPT


def test_api_routes_take_precedence_over_the_page(dist: Path) -> None:
    stub = StubAskService()
    client = client_for(dist, stub)

    ask = client.post("/ask", json={"prompt": "Raport", "user_id": 7})
    health = client.get("/health")

    assert ask.status_code == 200
    assert ask.json()["message"] == "Gotowe."
    assert stub.prompts == ["Raport"]
    assert health.json() == {"status": "ok"}


def test_unknown_path_is_not_found_rather_than_the_page(dist: Path) -> None:
    assert client_for(dist).get("/no-such-file").status_code == 404


def test_without_a_build_the_root_says_how_to_build_it(tmp_path: Path) -> None:
    client = client_for(tmp_path / "missing")

    page = client.get("/")
    ask = client.post("/ask", json={"prompt": "Raport", "user_id": 7})

    assert page.status_code == 200
    assert "npm run build" in page.text
    assert ask.status_code == 200


def test_the_build_hint_is_not_part_of_the_api_schema(tmp_path: Path) -> None:
    schema = client_for(tmp_path / "missing").get("/openapi.json").json()

    assert set(schema["paths"]) == {"/ask", "/health"}


@pytest.mark.parametrize("path", ["/", "/assets/app.js"])
def test_frontend_responses_carry_the_content_security_policy(dist: Path, path: str) -> None:
    reply = client_for(dist).get(path)

    assert reply.headers["content-security-policy"] == CONTENT_SECURITY_POLICY


def test_the_policy_allows_only_own_scripts_and_inline_styles() -> None:
    directives = dict(
        part.strip().split(" ", 1) for part in CONTENT_SECURITY_POLICY.split(";") if part.strip()
    )

    assert directives["script-src"] == "'self'"
    assert directives["object-src"] == "'none'"
    assert directives["frame-ancestors"] == "'none'"
    assert directives["base-uri"] == "'none'"
    unsafe = {name for name, sources in directives.items() if "'unsafe-" in sources}
    assert unsafe == {"style-src"}


def test_the_build_hint_carries_the_content_security_policy(tmp_path: Path) -> None:
    reply = client_for(tmp_path / "missing").get("/")

    assert reply.headers["content-security-policy"] == CONTENT_SECURITY_POLICY


def test_api_and_its_docs_keep_no_content_security_policy(dist: Path) -> None:
    # FastAPI's /docs page loads its scripts from a CDN, which the policy would block.
    client = client_for(dist)

    docs = client.get("/docs")
    ask = client.post("/ask", json={"prompt": "Raport", "user_id": 7})

    assert docs.status_code == 200
    assert "content-security-policy" not in docs.headers
    assert "content-security-policy" not in ask.headers
