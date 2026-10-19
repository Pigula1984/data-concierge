"""Fixed agent tools: thin controllers for the model over the session's services.

Pydantic AI builds a tool's schema and description from a function's signature and docstring,
so the tools are functions and their docstrings are written for the model (architecture,
sections 6.2 and 7.2).
"""

import functools
from collections.abc import Callable

from pydantic_ai import ModelRetry, RunContext, ToolFailed

from data_concierge.agent.deps import AgentDeps
from data_concierge.core.errors import (
    ParameterValidationError,
    ProcedureError,
    QueryError,
    UnknownHandleError,
)
from data_concierge.query.models import QuerySummary
from data_concierge.results.models import ResultSummary

# Errors the model can fix by changing its call or its SQL (plan KTD10).
INPUT_ERRORS = (ParameterValidationError, UnknownHandleError, QueryError)


# module-level: a decorator wraps a tool function before Pydantic AI reads its signature.
def retry_on_domain_error[**P, R](tool: Callable[P, R]) -> Callable[P, R]:
    """Maps domain errors to ModelRetry (fixable input) or ToolFailed (execution failure).

    ToolFailed returns the message without a request to correct the call and does not use the
    retry budget; the instructions tell the model what to do after it. Other exceptions pass
    through unchanged.
    """

    @functools.wraps(tool)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return tool(*args, **kwargs)
        except INPUT_ERRORS as exc:
            raise ModelRetry(str(exc)) from exc
        except ProcedureError as exc:
            raise ToolFailed(str(exc)) from exc

    return wrapper


# module-level: Pydantic AI builds the tool from this function's signature and docstring.
@retry_on_domain_error
def query(ctx: RunContext[AgentDeps], sql: str) -> QuerySummary:
    """Run one read-only SELECT in DuckDB SQL over result handles of this session (r1, r2, ...).

    Use CTEs, JOIN, WHERE, GROUP BY, PIVOT. Keep money as DECIMAL: CAST(avg(...) AS
    DECIMAL(38,6)), divide with NULLIF(divisor, 0) and CAST the quotient to DECIMAL(38,6). Give
    every computed column an English snake_case alias. Write dates as literals, not current_date
    or now(). Add ORDER BY when order matters, with a tie-breaking id column for a stable top N.
    The result is stored under a new handle.

    Args:
        sql: A single SELECT statement (CTEs allowed) reading only result handles.
    """
    return ctx.deps.queries.run(sql)


# module-level: Pydantic AI builds the tool from this function's signature and docstring.
@retry_on_domain_error
def describe_result(ctx: RunContext[AgentDeps], handle: str) -> ResultSummary:
    """Summary (schema, statistics, preview) of a result handle again, e.g. one from an earlier
    turn. It shows no more rows than the summary you already got; to see specific rows, run
    `query`.

    Args:
        handle: A result handle of this session, e.g. r1.
    """
    return ctx.deps.results.summarize(handle)
