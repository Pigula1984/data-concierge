"""The built browser app under `/`, next to `POST /ask` and `GET /health` (architecture 003).

Only the app's own responses carry the content security policy: FastAPI's `/docs` and `/redoc`
load their scripts from a CDN and would break under `script-src 'self'`.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from starlette.datastructures import MutableHeaders
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Styles stay inline-capable because the grid injects its theme styles at runtime; scripts come
# only from the app's own files.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'"
)

BUILD_HINT = (
    "Interfejs w przeglądarce nie jest zbudowany.\n"
    "Zbuduj go w katalogu frontend: npm ci, potem npm run build, i uruchom serwer ponownie.\n"
    "API działa bez interfejsu: POST /ask, GET /health.\n"
)


class ContentSecurityPolicy:
    """Wraps an ASGI app so each of its responses carries the content security policy."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_with_policy(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
            await send(message)

        await self._app(scope, receive, send_with_policy)


class WebFrontend:
    """Serves the built frontend from `dist_path`, or a page saying how to build it."""

    def __init__(self, dist_path: Path) -> None:
        self._dist_path = dist_path

    def mount(self, app: FastAPI) -> None:
        """Call after the API routes are added, so they take precedence over the files."""
        if (self._dist_path / "index.html").is_file():
            files = StaticFiles(directory=self._dist_path, html=True)
            app.mount("/", ContentSecurityPolicy(files), name="frontend")
            return
        app.add_api_route("/", self._build_hint, methods=["GET"], include_in_schema=False)

    @staticmethod
    async def _build_hint() -> PlainTextResponse:
        return PlainTextResponse(
            BUILD_HINT, headers={"Content-Security-Policy": CONTENT_SECURITY_POLICY}
        )
