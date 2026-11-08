"""HTTP entry point: `POST /ask`, `POST /ask/stream` and `GET /health` (architecture, section 9).

A request FastAPI cannot validate and a prompt over the limit get 422; a failure while answering
is status `error` in a 200 response, because a turn never raises.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.sse import EventSourceResponse
from pydantic import BaseModel

from data_concierge.api.schemas import Response
from data_concierge.api.service import AskService
from data_concierge.api.stream import StreamItem, TurnStream
from data_concierge.core.errors import PromptTooLongError


class AskRequest(BaseModel, frozen=True):
    prompt: str
    user_id: int
    session_id: str | None = None


router = APIRouter()


# module-level: a FastAPI dependency; the composition root puts the service in the app state.
def ask_service(request: Request) -> AskService:
    service: AskService = request.app.state.ask_service
    return service


# module-level: a FastAPI handler.
@router.post("/ask")
async def ask(body: AskRequest, service: Annotated[AskService, Depends(ask_service)]) -> Response:
    try:
        return await service.ask(body.prompt, body.user_id, body.session_id)
    except PromptTooLongError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


# module-level: a FastAPI dependency; the composition root puts the stream in the app state.
def turn_stream(request: Request) -> TurnStream:
    stream: TurnStream = request.app.state.turn_stream
    return stream


# module-level: a FastAPI dependency, so a prompt over the limit gets 422 before the stream opens.
def checked_request(
    body: AskRequest, service: Annotated[AskService, Depends(ask_service)]
) -> AskRequest:
    try:
        service.check_prompt(body.prompt)
    except PromptTooLongError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return body


# module-level: a FastAPI handler.
@router.post("/ask/stream", response_class=EventSourceResponse)
async def ask_stream(
    body: Annotated[AskRequest, Depends(checked_request)],
    stream: Annotated[TurnStream, Depends(turn_stream)],
) -> AsyncIterator[StreamItem]:
    """Server-sent events: each step of the turn as it begins (`step`, `text`), then the same
    response as `POST /ask` (architecture 003, section 8). When the turn fails unexpectedly,
    where `POST /ask` answers 500, the stream ends without a response; a client treats that as
    a failed turn."""
    async for item in stream.events(body.prompt, body.user_id, body.session_id):
        yield item


# module-level: a FastAPI handler.
@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
