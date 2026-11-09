"""What the agent's tools receive through Pydantic AI's `ctx.deps`."""

from dataclasses import dataclass, field

from data_concierge.agent.progress import TurnProgress
from data_concierge.audit.service import TurnAudit
from data_concierge.core.context import RequestContext
from data_concierge.procedures.service import ProcedureService
from data_concierge.query.service import QueryService
from data_concierge.results.service import ResultService


@dataclass(frozen=True, slots=True)
class AgentDeps:
    """The caller, the services of one session, the audit and the progress of the turn; built
    per request, read by the tools."""

    context: RequestContext
    procedures: ProcedureService
    queries: QueryService
    results: ResultService
    audit: TurnAudit
    progress: TurnProgress = field(default_factory=TurnProgress.silent)
