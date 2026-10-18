"""Domain exception hierarchy shared by all modules."""


class DataConciergeError(Exception):
    """Base class for all domain errors raised by Data Concierge."""


class ConfigurationError(DataConciergeError):
    """Startup configuration is invalid or inconsistent with the database."""


class CatalogError(DataConciergeError):
    """The agent catalog contains an invalid or unsupported definition."""


class ParameterValidationError(DataConciergeError):
    """Procedure arguments supplied by the model are invalid."""


class ProcedureError(DataConciergeError):
    """A stored procedure call failed during execution."""


class ProcedureTimeoutError(ProcedureError):
    """A stored procedure call exceeded its time limit."""


class QueryError(DataConciergeError):
    """A query written by the model over result handles cannot be run; the model may fix it."""


class QueryValidationError(QueryError):
    """The query breaks a rule of architecture section 7.3 or has a result type it cannot keep."""


class QueryExecutionError(QueryError):
    """DuckDB could not bind or run the query, or its result exceeds the row limit."""


class QueryTimeoutError(QueryError):
    """A DuckDB query exceeded its time limit."""


class UnknownHandleError(DataConciergeError):
    """A result handle does not exist in the session."""
