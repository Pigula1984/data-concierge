"""Outcome of each evaluation case, the report by group printed after the run, and the
archive of the run (plan U18; measures of architecture 002, section 9)."""

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    UserPromptPart,
)

from data_concierge.agent.anchors import ANCHOR
from data_concierge.agent.models import OUTPUT_TOOL, AgentAnswer
from data_concierge.api.schemas import AnswerStatus
from data_concierge.core.settings import ModelDataAccess

# Polish numbers group thousands with a space (also no-break and narrow no-break) and use a
# decimal comma: "43 671 889,50". Plain numbers such as 2294.99 are read as well.
NUMBER_PATTERN = re.compile(r"(?<![\w.,])\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?")
# Dates ("2024-06-01", "30.05.2022") and account numbers ("AW00012698") are not amounts.
NOT_AMOUNTS = re.compile(r"\d{4}-\d{2}(?:-\d{2})?|\d{1,2}\.\d{1,2}\.\d{4}|[A-Z]{2}\d{6,}")
# Years and small counts ("12 miesięcy", "3 klientów") are not counted.
YEARS = range(1900, 2101)
SMALL_COUNT = 100


@dataclass
class TurnTrace:
    """What one model run of a case did, read from its messages."""

    query_retries: int = 0
    # The model's tool calls in order (with the SQL of every query) and each correction asked
    # of it, e.g. a rejected query and the check's message.
    steps: list[dict[str, object]] = field(default_factory=list)

    @classmethod
    def from_messages(cls, messages: Sequence[ModelMessage]) -> TurnTrace:
        """Only this turn: the messages after the last request that carries the user's prompt."""
        start = max(
            (
                index
                for index, message in enumerate(messages)
                if isinstance(message, ModelRequest)
                and any(isinstance(part, UserPromptPart) for part in message.parts)
            ),
            default=0,
        )
        trace = cls()
        for message in messages[start:]:
            if isinstance(message, ModelResponse):
                trace.steps += [
                    {"call": part.tool_name, "args": part.args_as_dict()}
                    for part in message.parts
                    if isinstance(part, ToolCallPart)
                ]
                continue
            for part in message.parts:
                if isinstance(part, RetryPromptPart) and part.tool_name is not None:
                    trace.steps.append({"retry": part.tool_name, "error": part.content})
                    trace.query_retries += part.tool_name == "query"
        return trace


class NumberAudit:
    """Numbers the model wrote into the texts for the user itself instead of by anchor
    (architecture 002, section 9). A measurement only: it never fails a case."""

    @classmethod
    def outside_anchors(cls, history: Sequence[ModelMessage]) -> tuple[str, ...]:
        """The numbers of the turn's template: the last `present_result` of the session
        history, whose message the comment phase has already replaced (plan KTD11)."""
        template = cls._template(history)
        if template is None:
            return ()
        return tuple(number for text in template.texts for number in cls._numbers(text))

    @staticmethod
    def _template(history: Sequence[ModelMessage]) -> AgentAnswer | None:
        for message in reversed(history):
            if not isinstance(message, ModelResponse):
                continue
            for part in message.parts:
                if isinstance(part, ToolCallPart) and part.tool_name == OUTPUT_TOOL:
                    return AgentAnswer.model_validate(part.args_as_dict())
        return None

    @staticmethod
    def _numbers(text: str) -> list[str]:
        found = []
        plain = NOT_AMOUNTS.sub(" ", ANCHOR.sub(" ", text))
        for match in NUMBER_PATTERN.finditer(plain):
            number = Decimal(re.sub(r"[^\d,.]", "", match.group()).replace(",", "."))
            if number == number.to_integral_value() and (
                int(number) in YEARS or number < SMALL_COUNT
            ):
                continue
            found.append(match.group())
        return found


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    case_id: str
    group: str
    status: AnswerStatus
    status_ok: bool
    procedure_ok: bool
    params_ok: bool
    result_ok: bool | None
    query_retries: int
    numbers_outside_anchors: tuple[str, ...]
    assumptions_ok: bool | None = None
    candidates_ok: bool | None = None
    anchor_retries: int = 0
    # Answers that kept their draft because the comment phase failed (mode minimal).
    drafts: int = 0
    turns: int = 1
    seconds: float = 0.0
    # In USD, both phases; None when the price list lacks the model.
    cost: Decimal | None = None

    @property
    def failed_checks(self) -> tuple[str, ...]:
        """The checks that fail the case; the measures never do."""
        checks = (
            (f"status {self.status}", self.status_ok),
            ("procedure", self.procedure_ok),
            ("params", self.params_ok),
            ("result", self.result_ok is not False),
            ("candidates", self.candidates_ok is not False),
            ("assumptions", self.assumptions_ok is not False),
        )
        return tuple(name for name, ok in checks if not ok)

    @property
    def passed(self) -> bool:
        return not self.failed_checks


class EvalReport:
    """Collects case outcomes and renders the accuracy and measures table by group."""

    def __init__(self, mode: ModelDataAccess) -> None:
        self._mode = mode
        self._outcomes: list[CaseOutcome] = []

    def add(self, outcome: CaseOutcome) -> None:
        self._outcomes.append(outcome)

    def render(self) -> str:
        if not self._outcomes:
            return f"Mode {self._mode}: no evaluation cases ran."
        groups = sorted({outcome.group for outcome in self._outcomes})
        lines = [
            f"Mode: {self._mode}",
            "",
            "| group | cases | passed | status | procedure | params | result | candidates "
            "| query retries | anchor retries | numbers outside anchors | drafts | s/turn "
            "| $/turn |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for group in groups:
            lines.append(self._row(group, [o for o in self._outcomes if o.group == group]))
        lines.append(self._row("**all**", self._outcomes))
        lines += ["", self._totals(self._outcomes)]
        failed = [outcome for outcome in self._outcomes if not outcome.passed]
        if failed:
            lines += ["", "Failed cases:"]
            lines += [
                f"- {outcome.case_id}: {', '.join(outcome.failed_checks)}" for outcome in failed
            ]
        numbers = [outcome for outcome in self._outcomes if outcome.numbers_outside_anchors]
        if numbers:
            lines += ["", "Numbers written outside anchors (measured, not failed):"]
            lines += [
                f"- {outcome.case_id}: {', '.join(outcome.numbers_outside_anchors)}"
                for outcome in numbers
            ]
        return "\n".join(lines)

    @classmethod
    def _row(cls, group: str, outcomes: Sequence[CaseOutcome]) -> str:
        turns = sum(o.turns for o in outcomes)
        cost = cls._cost(outcomes)
        return (
            f"| {group} | {len(outcomes)} | {sum(o.passed for o in outcomes)} "
            f"| {cls._share(o.status_ok for o in outcomes)} "
            f"| {cls._share(o.procedure_ok for o in outcomes)} "
            f"| {cls._share(o.params_ok for o in outcomes)} "
            f"| {cls._checked(o.result_ok for o in outcomes)} "
            f"| {cls._checked(o.candidates_ok for o in outcomes)} "
            f"| {sum(o.query_retries for o in outcomes)} "
            f"| {sum(o.anchor_retries for o in outcomes)} "
            f"| {sum(len(o.numbers_outside_anchors) for o in outcomes)} "
            f"| {sum(o.drafts for o in outcomes)} "
            f"| {sum(o.seconds for o in outcomes) / turns:.1f} "
            f"| {'-' if cost is None else f'{cost / turns:.4f}'} |"
        )

    @classmethod
    def _totals(cls, outcomes: Sequence[CaseOutcome]) -> str:
        cost = cls._cost(outcomes)
        return (
            f"Turns: {sum(o.turns for o in outcomes)}, time: "
            f"{sum(o.seconds for o in outcomes):.0f} s, cost: "
            f"{'unknown' if cost is None else f'${cost:.2f}'}"
        )

    @staticmethod
    def _cost(outcomes: Sequence[CaseOutcome]) -> Decimal | None:
        costs = [o.cost for o in outcomes]
        return None if None in costs else sum((c for c in costs if c is not None), Decimal(0))

    @classmethod
    def _checked(cls, checks: Iterable[bool | None]) -> str:
        """The share of the cases where the check applied."""
        applied = [check for check in checks if check is not None]
        return cls._share(applied) if applied else "-"

    @staticmethod
    def _share(checks: Iterable[bool]) -> str:
        values = list(checks)
        return f"{sum(values)}/{len(values)}"


class RunArchive:
    """Keeps every case of a run as JSON in its own directory named by its start and mode,
    e.g. evals/runs/20261020-210500-none, so the model's calls, its SQL and the full answers
    can be read after the run."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @classmethod
    def started_now(cls, root: Path, mode: ModelDataAccess) -> RunArchive:
        return cls(root / f"{datetime.now():%Y%m%d-%H%M%S}-{mode}")

    def save_case(self, case_id: str, record: Mapping[str, object]) -> Path:
        path = self.directory / f"{case_id}.json"
        self._write(path, json.dumps(record, ensure_ascii=False, indent=2, default=str))
        return path

    def save_report(self, report: str) -> Path:
        path = self.directory / "report.md"
        self._write(path, report + "\n")
        return path

    def _write(self, path: Path, text: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
