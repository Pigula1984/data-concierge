"""HTTP entry point: `POST /ask` and `GET /health` (architecture, section 9).

A request FastAPI cannot validate and a prompt over the limit get 422; a failure while answering
is status `error` in a 200 response, because a turn never raises.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from data_concierge.api.schemas import Response
from data_concierge.api.service import AskService
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


# module-level: a FastAPI handler.
@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
