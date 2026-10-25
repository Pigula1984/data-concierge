"""AskService: one call answers a prompt within a conversation (Facade, architecture section 4)."""

import logging
from collections.abc import Callable
from datetime import date

from data_concierge.agent.assembler import ResponseAssembler
from data_concierge.agent.comment import CommentService
from data_concierge.agent.models import AgentTurn
from data_concierge.agent.service import AgentService
from data_concierge.api.schemas import Response
from data_concierge.api.sessions import Session, SessionFactory, SessionRepository
from data_concierge.core.context import RequestContext
from data_concierge.core.errors import CommentError, ResultStoreError
from data_concierge.core.settings import DEFAULT_EXPOSURE, ModelExposure

logger = logging.getLogger(__name__)

# v1 does not authenticate the caller (plan, Scope Boundaries), so every caller has this role.
CALLER_ROLE = "user"
SESSION_EXPIRED_MESSAGE = (
    "Sesja {session_id} wygasła albo nie istnieje. Zacznij nową rozmowę bez identyfikatora sesji."
)


class AskService:
    """Runs one turn of the agent in a session and returns the response of section 9.

    It never raises: any failure of the turn becomes status `error` with the correlation id,
    and the session keeps the history from before the turn, unless its DuckDB broke. In mode
    minimal a turn with status ok gets its comment from the comment phase (architecture 002,
    section 5.2).
    """

    def __init__(
        self,
        agent: AgentService,
        comments: CommentService,
        sessions: SessionRepository,
        factory: SessionFactory,
        exposure: ModelExposure = DEFAULT_EXPOSURE,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._agent = agent
        self._comments = comments
        self._sessions = sessions
        self._factory = factory
        self._exposure = exposure
        self._today = today

    async def ask(self, prompt: str, user_id: int, session_id: str | None = None) -> Response:
        """Without `session_id` a new conversation starts; an unknown one is not continued,
        because the model would see the rest of a conversation without its beginning."""
        context = RequestContext(user_id=user_id, role=CALLER_ROLE)
        if session_id is None:
            session = self._factory.create()
            # Saved now, so the session_id of an error response can still be continued.
            self._sessions.save(session)
        else:
            found = self._sessions.get(session_id)
            if found is None:
                return Response(
                    status="error",
                    session_id=session_id,
                    message=SESSION_EXPIRED_MESSAGE.format(session_id=session_id),
                )
            session = found
        try:
            return await self._turn(session, prompt, context)
        except ResultStoreError:
            logger.exception("result store failed (correlation id %s)", context.correlation_id)
            self._sessions.delete(session.id)
        except Exception:
            logger.exception("ask failed (correlation id %s)", context.correlation_id)
        return ResponseAssembler.failure(session.id, context.correlation_id)

    def has_session(self, session_id: str) -> bool:
        """False once a session is gone, e.g. dropped after its DuckDB failed."""
        return self._sessions.get(session_id) is not None

    async def _turn(self, session: Session, prompt: str, context: RequestContext) -> Response:
        dated = self._with_date(prompt)
        turn = await self._agent.run(dated, session.history, session.deps(context))
        if self._exposure.comment_phase and turn.answer.status == "ok":
            turn = await self._commented(turn, dated, session, context)
        response = session.assembler.assemble(turn.answer, session.id, context.correlation_id)
        self._sessions.save(session.with_history(turn.messages))
        return response

    async def _commented(
        self, turn: AgentTurn, prompt: str, session: Session, context: RequestContext
    ) -> AgentTurn:
        """The turn with the comment of the comment phase, in the answer and in the history;
        when the phase fails, the turn keeps its draft."""
        try:
            comment = await self._comments.write(prompt, turn.answer, session.results)
        except CommentError as exc:
            logger.warning(
                "the comment phase failed (correlation id %s), the draft is used: %s",
                context.correlation_id,
                exc.detail,
            )
            return turn
        return turn.with_message(comment)

    def _with_date(self, prompt: str) -> str:
        """The current date goes in the user's message, so the instructions stay the same in
        every conversation and stay cached (plan KTD11)."""
        return f"{prompt}\n\nCurrent date: {self._today().isoformat()}"
