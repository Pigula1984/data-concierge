"""What the agent's tools receive through Pydantic AI's `ctx.deps`."""

from dataclasses import dataclass

from data_concierge.core.context import RequestContext
from data_concierge.procedures.service import ProcedureService
from data_concierge.query.service import QueryService
from data_concierge.results.service import ResultService


@dataclass(frozen=True, slots=True)
class AgentDeps:
    """The caller and the services of one session; built per request, read by the tools."""

    context: RequestContext
    procedures: ProcedureService
    queries: QueryService
    results: ResultService
