"""What the model gets back after running a query."""

from data_concierge.results.models import Handle, ResultSummary


class QuerySummary(ResultSummary, frozen=True):
    """The model's view of a query result, with the sources and the SQL the backend ran."""

    sources: tuple[Handle, ...]
    sql: str
