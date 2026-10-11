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


class SpecValidationError(DataConciergeError):
    """A transform specification does not fit the schema of its source."""


class UnknownHandleError(DataConciergeError):
    """A result handle does not exist in the session."""
