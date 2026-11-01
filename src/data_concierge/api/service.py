"""AskService: one call answers a prompt within a conversation (Facade, architecture section 4)."""

import logging
from collections.abc import Callable
from datetime import date
from time import perf_counter

from data_concierge.agent.assembler import ResponseAssembler
from data_concierge.agent.comment import CommentService
from data_concierge.agent.models import AgentTurn
from data_concierge.agent.service import AgentService
from data_concierge.api.schemas import Response
from data_concierge.api.sessions import Session, SessionFactory, SessionRepository
from data_concierge.audit.service import AuditService, TurnAudit
from data_concierge.core.context import RequestContext, RoleDirectory
from data_concierge.core.errors import CommentError, PromptTooLongError, ResultStoreError
from data_concierge.core.settings import DEFAULT_EXPOSURE, ModelExposure

logger = logging.getLogger(__name__)

SESSION_EXPIRED_MESSAGE = (
    "Sesja {session_id} wygasła albo nie istnieje. Zacznij nową rozmowę bez identyfikatora sesji."
)
PROMPT_TOO_LONG_MESSAGE = (
    "Prośba jest za długa: limit to {limit} znaków. Skróć ją i wyślij ponownie."
)


class AskService:
    """Runs one turn of the agent in a session and returns the response of section 9.

    A prompt over the limit raises PromptTooLongError: that is input validation, before a turn
    begins. Otherwise it never raises: any failure of the turn becomes status `error` with the
    correlation id, and the session keeps the history from before the turn, unless its DuckDB
    broke. In mode
    minimal a turn with status ok gets its comment from the comment phase (architecture 002,
    section 5.2). Every turn, a failed one too, leaves its events in the audit log
    (architecture, section 8.1).
    """

    def __init__(
        self,
        agent: AgentService,
        comments: CommentService,
        sessions: SessionRepository,
        factory: SessionFactory,
        roles: RoleDirectory,
        max_prompt_chars: int,
        exposure: ModelExposure = DEFAULT_EXPOSURE,
        today: Callable[[], date] = date.today,
        audit: AuditService | None = None,
    ) -> None:
        self._agent = agent
        self._comments = comments
        self._sessions = sessions
        self._factory = factory
        self._roles = roles
        self._max_prompt_chars = max_prompt_chars
        self._exposure = exposure
        self._today = today
        self._audit = audit if audit is not None else AuditService.disabled()

    async def ask(self, prompt: str, user_id: int, session_id: str | None = None) -> Response:
        """Without `session_id` a new conversation starts; an unknown one is not continued,
        because the model would see the rest of a conversation without its beginning, and the
        session of another user looks the same as an unknown one."""
        if len(prompt) > self._max_prompt_chars:
            raise PromptTooLongError(PROMPT_TOO_LONG_MESSAGE.format(limit=self._max_prompt_chars))
        started = perf_counter()
        context = RequestContext(user_id=user_id, role=self._roles.role_of(user_id))
        continued = session_id is not None
        session: Session | None
        if session_id is None:
            session = self._factory.create(user_id)
            # Saved now, so the session_id of an error response can still be continued.
            self._sessions.save(session)
            session_id = session.id
        else:
            session = self._sessions.get(session_id)
        audit = self._audit.turn(context, session_id)
        today = self._today()
        audit.record(
            "request",
            {
                "prompt": prompt,
                "current_date": today.isoformat(),
                "continued_session": continued,
                "model_data_access": self._exposure.mode,
                "model": self._agent.model_name,
            },
        )
        if session is not None and session.owner_id != user_id:
            audit.record("session_rejected", {"owner_id": session.owner_id})
            session = None
        dated_prompt = self._with_date(prompt, today)
        response = await self._respond(dated_prompt, session, session_id, context, audit)
        audit.record(
            "response",
            {
                "duration_ms": round((perf_counter() - started) * 1000),
                "response": response.model_dump(mode="json"),
            },
        )
        return response

    def has_session(self, session_id: str) -> bool:
        """False once a session is gone, e.g. dropped after its DuckDB failed."""
        return self._sessions.get(session_id) is not None

    async def _respond(
        self,
        dated_prompt: str,
        session: Session | None,
        session_id: str,
        context: RequestContext,
        audit: TurnAudit,
    ) -> Response:
        if session is None:
            return Response(
                status="error",
                session_id=session_id,
                message=SESSION_EXPIRED_MESSAGE.format(session_id=session_id),
            )
        try:
            return await self._turn(session, dated_prompt, context, audit)
        except ResultStoreError as exc:
            logger.exception("result store failed (correlation id %s)", context.correlation_id)
            audit.record("turn_failed", TurnAudit.failure(exc))
            self._sessions.delete(session.id)
        except Exception as exc:
            logger.exception("ask failed (correlation id %s)", context.correlation_id)
            audit.record("turn_failed", TurnAudit.failure(exc))
        return ResponseAssembler.failure(session.id, context.correlation_id)

    async def _turn(
        self, session: Session, prompt: str, context: RequestContext, audit: TurnAudit
    ) -> Response:
        turn = await self._agent.run(prompt, session.history, session.deps(context, audit))
        if self._exposure.comment_phase and turn.answer.status == "ok":
            turn = await self._commented(turn, prompt, session, context, audit)
        response = session.assembler.assemble(turn.answer, session.id, context.correlation_id)
        self._sessions.save(session.with_history(turn.messages))
        return response

    async def _commented(
        self,
        turn: AgentTurn,
        prompt: str,
        session: Session,
        context: RequestContext,
        audit: TurnAudit,
    ) -> AgentTurn:
        """The turn with the comment of the comment phase in the answer; when the phase
        fails, the turn keeps its draft."""
        try:
            comment = await self._comments.write(prompt, turn.answer, session.results)
        except CommentError as exc:
            logger.warning(
                "the comment phase failed (correlation id %s), the draft is used: %s",
                context.correlation_id,
                exc.detail,
            )
            audit.record("comment", {"failed": exc.detail})
            return turn
        audit.record("comment", {"comment": comment})
        return turn.with_message(comment)

    @staticmethod
    def _with_date(prompt: str, today: date) -> str:
        """The current date goes in the user's message, so the instructions stay the same in
        every conversation and stay cached (plan KTD11)."""
        return f"{prompt}\n\nCurrent date: {today.isoformat()}"
