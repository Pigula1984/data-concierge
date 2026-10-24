"""Fixed agent tools: thin controllers for the model over the session's services.

Pydantic AI builds a tool's schema from a function's signature and the parameter descriptions
from its docstring, so the tools are functions and their `Args` are written for the model. The
tool descriptions depend on the mode, so the registry sets them from the texts below
(architecture, sections 6.2 and 7.2; architecture 002, section 6.4).
"""

import functools
import logging
from collections.abc import Callable
from typing import cast

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

logger = logging.getLogger(__name__)

# What a summary holds, by mode (architecture 002, sections 4.1 and 5.1).
SUMMARY_NONE = "schema, row count, date ranges and ids"
SUMMARY_MINIMAL = "schema, row count, date ranges, ids and dimension values"
QUERY_DESCRIPTION = (
    "Run one read-only SELECT in DuckDB SQL over result handles of this session (r1, r2, ...). "
    "The result is stored under a new handle and you get its summary ({summary}), never its "
    "rows. Use CTEs, JOIN, WHERE, GROUP BY, PIVOT. Keep money as DECIMAL: CAST(avg(...) AS "
    "DECIMAL(38,6)), divide with NULLIF(divisor, 0) and CAST the quotient to DECIMAL(38,6), or to "
    "DECIMAL(38,2) for a value you anchor in a text for the user. Give "
    "every computed column an English snake_case alias. Write dates as literals, not "
    "current_date or now(). Add ORDER BY when order matters, with a tie-breaking id column for "
    "a stable top N."
)
DESCRIBE_RESULT_DESCRIPTION = (
    "The summary ({summary}) of a result handle again, e.g. one from an earlier turn. It never "
    "shows rows; a value for the user goes into the text as an anchor."
)

# Errors the model can fix by changing its call or its SQL (plan KTD10).
INPUT_ERRORS = (ParameterValidationError, UnknownHandleError, QueryError)


# module-level: a decorator wraps a tool function before Pydantic AI reads its signature.
def retry_on_domain_error[**P, R](tool: Callable[P, R]) -> Callable[P, R]:
    """Maps domain errors to ModelRetry (fixable input) or ToolFailed (execution failure).

    ToolFailed returns the message without a request to correct the call and does not use the
    retry budget; the instructions tell the model what to do after it. The model gets only the
    message; the error's `detail` goes to the log with the correlation id. Other exceptions pass
    through unchanged.
    """

    @functools.wraps(tool)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return tool(*args, **kwargs)
        except (*INPUT_ERRORS, ProcedureError) as exc:
            if exc.detail is not None:
                # Every tool takes the run context first.
                context = cast(RunContext[AgentDeps], args[0]).deps.context
                logger.warning(
                    "tool error (correlation id %s): %s", context.correlation_id, exc.detail
                )
            if isinstance(exc, ProcedureError):
                raise ToolFailed(str(exc)) from exc
            raise ModelRetry(str(exc)) from exc

    return wrapper


# module-level: Pydantic AI builds the tool from this function's signature and docstring.
@retry_on_domain_error
def query(ctx: RunContext[AgentDeps], sql: str) -> QuerySummary:
    """Runs the model's SELECT and stores its result (description: QUERY_DESCRIPTION).

    Args:
        sql: A single SELECT statement (CTEs allowed) reading only result handles.
    """
    return ctx.deps.queries.run(sql)


# module-level: Pydantic AI builds the tool from this function's signature and docstring.
@retry_on_domain_error
def describe_result(ctx: RunContext[AgentDeps], handle: str) -> ResultSummary:
    """The summary of a handle again (description: DESCRIBE_RESULT_DESCRIPTION).

    Args:
        handle: A result handle of this session, e.g. r1.
    """
    return ctx.deps.results.summarize(handle)
