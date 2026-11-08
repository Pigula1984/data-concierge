"""TurnStream: the steps and the response of one turn for `POST /ask/stream`."""

import asyncio
import logging
from collections.abc import AsyncGenerator

from data_concierge.agent.progress import ProgressEvent
from data_concierge.api.schemas import Response
from data_concierge.api.service import AskService

logger = logging.getLogger(__name__)

type StreamItem = ProgressEvent | Response


class TurnStream:
    """Runs each turn in its own task, so a client that disconnects ends only its stream: the
    turn still saves its session and its audit, like a turn of `POST /ask` (architecture 003,
    section 8). The stream reads the turn's steps from a queue and ends with its response."""

    def __init__(self, service: AskService) -> None:
        self._service = service
        # The event loop keeps only weak references to tasks, so running turns are kept here.
        self._running: set[asyncio.Task[None]] = set()

    @property
    def running(self) -> int:
        """Turns still running, e.g. after their client disconnected."""
        return len(self._running)

    async def events(
        self, prompt: str, user_id: int, session_id: str | None
    ) -> AsyncGenerator[StreamItem]:
        """The steps of the turn as they begin, then its response. When the turn fails
        unexpectedly, the stream ends without a response."""
        queue: asyncio.Queue[StreamItem | None] = asyncio.Queue()
        task = asyncio.create_task(self._turn(queue, prompt, user_id, session_id))
        self._running.add(task)
        task.add_done_callback(self._running.discard)
        while (item := await queue.get()) is not None:
            yield item
            if isinstance(item, Response):
                return

    async def _turn(
        self,
        queue: asyncio.Queue[StreamItem | None],
        prompt: str,
        user_id: int,
        session_id: str | None,
    ) -> None:
        try:
            response = await self._service.ask(prompt, user_id, session_id, queue.put_nowait)
        except Exception:
            logger.exception("streamed turn failed")
            queue.put_nowait(None)
        else:
            queue.put_nowait(response)
