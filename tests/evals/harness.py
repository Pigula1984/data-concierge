"""What the evaluation run injects into the application: its clock and a recording repository."""

from collections.abc import Mapping
from datetime import date

from cases import Call

from data_concierge.catalog.models import CatalogEntry
from data_concierge.procedures.models import ProcedureResult
from data_concierge.procedures.repository import ProcedureRepository


class EvalClock:
    """The date AskService adds to the prompt; each case sets its own."""

    def __init__(self, today: date) -> None:
        self.today = today

    def __call__(self) -> date:
        return self.today


class RecordingProcedureRepository:
    """Calls SQL Server and keeps each call's procedure and parameters for the case checks."""

    def __init__(self, inner: ProcedureRepository) -> None:
        self._inner = inner
        self.calls: list[Call] = []

    def execute(self, entry: CatalogEntry, params: Mapping[str, object]) -> ProcedureResult:
        self.calls.append((entry.procedure, dict(params)))
        return self._inner.execute(entry, params)
