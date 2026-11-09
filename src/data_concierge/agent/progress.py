"""Turn progress: the steps of a turn the user sees while waiting (architecture 003, section 8).

The texts are fixed here or come from the catalog, so a step never carries the model's text,
the arguments of a tool call or a value from the data.
"""

import logging
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)

type ProgressStep = Literal["guard", "model", "procedure", "query", "describe", "retry", "comment"]


class ProgressEvent(BaseModel, frozen=True):
    """One step of a turn, sent as it begins."""

    step: ProgressStep
    text: str


# Receives the steps of a turn, e.g. the stream of `POST /ask/stream` or the terminal.
type ProgressListener = Callable[[ProgressEvent], None]


class TurnProgress:
    """Created by AskService for each turn, next to its TurnAudit. A listener that cannot write
    (a closed terminal) must not fail the turn it describes, so that error is only logged."""

    GUARD = "Sprawdzam prośbę…"
    FIRST_ROUND = "Analizuję prośbę…"
    NEXT_ROUND = "Analizuję wyniki…"
    PROCEDURE = "Pobieram dane"
    QUERY = "Liczę zestawienie…"
    DESCRIBE = "Sprawdzam wynik…"
    RETRY = "Poprawiam odpowiedź…"
    COMMENT = "Piszę komentarz…"

    def __init__(self, listener: ProgressListener | None, correlation_id: str) -> None:
        self._listener = listener
        self._correlation_id = correlation_id
        self._rounds = 0

    @classmethod
    def silent(cls) -> TurnProgress:
        """Progress nobody listens to: `POST /ask`, one-shot `ask` and the evaluation."""
        return cls(None, "")

    @staticmethod
    def procedure_text(progress: str | None, procedure: str) -> str:
        """`Pobieram zamówienia sprzedaży (api.SalesOrder_List)…`; a procedure without a
        catalog `progress` still names what runs."""
        return f"{progress or TurnProgress.PROCEDURE} ({procedure})…"

    def guard(self) -> None:
        self._report("guard", self.GUARD)

    def model_round(self) -> None:
        self._report("model", self.NEXT_ROUND if self._rounds else self.FIRST_ROUND)
        self._rounds += 1

    def retry(self) -> None:
        """A round in which the model corrects an answer the backend sent back."""
        self._report("retry", self.RETRY)
        self._rounds += 1

    def procedure(self, text: str) -> None:
        self._report("procedure", text)

    def query(self) -> None:
        self._report("query", self.QUERY)

    def describe(self) -> None:
        self._report("describe", self.DESCRIBE)

    def comment(self) -> None:
        self._report("comment", self.COMMENT)

    def _report(self, step: ProgressStep, text: str) -> None:
        if self._listener is None:
            return
        try:
            self._listener(ProgressEvent(step=step, text=text))
        # A closed terminal or a broken pipe, like the audit log's own failures.
        except (OSError, ValueError) as exc:
            logger.warning(
                "progress step %s not reported (correlation id %s): %s",
                step,
                self._correlation_id,
                exc,
            )
