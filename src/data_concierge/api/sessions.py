"""Conversations kept in process memory (plan KTD19): history and the session's result store."""

from dataclasses import dataclass, replace
from typing import Protocol

from pydantic_ai.messages import ModelMessage

from data_concierge.agent.assembler import ResponseAssembler
from data_concierge.agent.deps import AgentDeps
from data_concierge.core.context import RequestContext
from data_concierge.procedures.service import ProcedureService
from data_concierge.query.service import QueryService
from data_concierge.results.service import ResultService


@dataclass(frozen=True, slots=True)
class Session:
    """One conversation: its services over one DuckDB result store, and the message history."""

    id: str
    procedures: ProcedureService
    queries: QueryService
    results: ResultService
    assembler: ResponseAssembler
    history: tuple[ModelMessage, ...] = ()

    def deps(self, context: RequestContext) -> AgentDeps:
        return AgentDeps(
            context=context, procedures=self.procedures, queries=self.queries, results=self.results
        )

    def with_history(self, history: tuple[ModelMessage, ...]) -> Session:
        return replace(self, history=history)


class SessionFactory(Protocol):
    """Creates a session with a new result store; implemented by the composition root."""

    def create(self) -> Session: ...


class SessionRepository(Protocol):
    def get(self, session_id: str) -> Session | None: ...

    def save(self, session: Session) -> None: ...

    def delete(self, session_id: str) -> None: ...


class InMemorySessionRepository:
    """Sessions live until the process ends or their DuckDB fails; v1 has one owner, so they
    do not expire."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def save(self, session: Session) -> None:
        self._sessions[session.id] = session

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
