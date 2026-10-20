"""Outcome of each evaluation case, the report by group printed after the run, and the
archive of the run (plan U18)."""

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pydantic import BaseModel
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from data_concierge.api.schemas import AnswerStatus, Response

# Polish numbers group thousands with a space (also no-break and narrow no-break) and use a
# decimal comma: "43 671 889,50". Plain numbers such as 2294.99 are read as well.
NUMBER_PATTERN = re.compile(r"(?<![\w.,])\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?")
# Dates ("2024-06-01", "30.05.2022") and account numbers ("AW00012698") are not amounts.
NOT_AMOUNTS = re.compile(r"\d{4}-\d{2}(?:-\d{2})?|\d{1,2}\.\d{1,2}\.\d{4}|[A-Z]{2}\d{6,}")
# Years and small counts ("12 miesięcy", "3 klientów") are not checked against results.
YEARS = range(1900, 2101)
SMALL_COUNT = 100


@dataclass
class TurnTrace:
    """What one model run of a case did, read from its messages."""

    query_retries: int = 0
    # Numbers in every tool result the model saw: query results, procedure summaries.
    seen_values: list[Decimal] = field(default_factory=list)
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
                elif isinstance(part, ToolReturnPart):
                    trace.seen_values.extend(NumberAudit.decimals(part.content))
        return trace


class NumberAudit:
    """Numbers in the comment that no table and no query result the model saw contains
    (architecture, section 7.5). A measurement only: it never fails a case."""

    @classmethod
    def unmatched(cls, response: Response, seen: Iterable[Decimal]) -> tuple[str, ...]:
        values = [*seen, *cls._table_values(response)]
        return tuple(
            text
            for text, number in cls._numbers(response.message)
            if not cls._found(text, number, values)
        )

    @classmethod
    def decimals(cls, content: object) -> list[Decimal]:
        """Every number inside a tool result, e.g. a QuerySummary preview and statistics."""
        if isinstance(content, BaseModel):
            content = content.model_dump(mode="json")
        if isinstance(content, dict):
            return [value for item in content.values() for value in cls.decimals(item)]
        if isinstance(content, list | tuple):
            return [value for item in content for value in cls.decimals(item)]
        if isinstance(content, bool):
            return []
        if isinstance(content, int | str):
            try:
                number = Decimal(str(content))
            except InvalidOperation:
                return []
            return [number] if number.is_finite() else []
        return []

    @classmethod
    def _table_values(cls, response: Response) -> list[Decimal]:
        return [
            Decimal(str(value))
            for table in response.tables
            for row in table.rows
            for column, value in zip(table.columns, row, strict=True)
            if value is not None and column.type.is_numeric
        ]

    @staticmethod
    def _numbers(message: str) -> list[tuple[str, Decimal]]:
        found = []
        for match in NUMBER_PATTERN.finditer(NOT_AMOUNTS.sub(" ", message)):
            text = match.group()
            number = Decimal(re.sub(r"[ \u00a0\u202f]", "", text).replace(",", "."))
            if number == number.to_integral_value() and (
                int(number) in YEARS or number < SMALL_COUNT
            ):
                continue
            found.append((text, number))
        return found

    @staticmethod
    def _found(text: str, number: Decimal, values: Iterable[Decimal]) -> bool:
        """The number is a value rounded to the precision the comment shows."""
        fraction = re.search(r"[.,](\d+)$", text)
        places = len(fraction.group(1)) if fraction else 0
        quantum = Decimal(1).scaleb(-places)
        for value in values:
            try:
                if value.quantize(quantum) == number:
                    return True
            except InvalidOperation:
                continue
        return False


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
    unmatched_numbers: tuple[str, ...]
    assumptions_ok: bool | None = None

    @property
    def failed_checks(self) -> tuple[str, ...]:
        """The checks that fail the case; numbers outside results never do."""
        checks = (
            (f"status {self.status}", self.status_ok),
            ("procedure", self.procedure_ok),
            ("params", self.params_ok),
            ("result", self.result_ok is not False),
            ("assumptions", self.assumptions_ok is not False),
        )
        return tuple(name for name, ok in checks if not ok)

    @property
    def passed(self) -> bool:
        return not self.failed_checks


class EvalReport:
    """Collects case outcomes and renders the accuracy table by group."""

    def __init__(self) -> None:
        self._outcomes: list[CaseOutcome] = []

    def add(self, outcome: CaseOutcome) -> None:
        self._outcomes.append(outcome)

    def render(self) -> str:
        if not self._outcomes:
            return "No evaluation cases ran."
        groups = sorted({outcome.group for outcome in self._outcomes})
        lines = [
            "| group | cases | passed | status | procedure | params | result | query retries "
            "| numbers outside results |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for group in groups:
            lines.append(self._row(group, [o for o in self._outcomes if o.group == group]))
        lines.append(self._row("**all**", self._outcomes))
        failed = [outcome for outcome in self._outcomes if not outcome.passed]
        if failed:
            lines += ["", "Failed cases:"]
            lines += [
                f"- {outcome.case_id}: {', '.join(outcome.failed_checks)}" for outcome in failed
            ]
        numbers = [outcome for outcome in self._outcomes if outcome.unmatched_numbers]
        if numbers:
            lines += ["", "Numbers in the comment not found in results (measured, not failed):"]
            lines += [
                f"- {outcome.case_id}: {', '.join(outcome.unmatched_numbers)}"
                for outcome in numbers
            ]
        return "\n".join(lines)

    @classmethod
    def _row(cls, group: str, outcomes: Sequence[CaseOutcome]) -> str:
        checked = [o for o in outcomes if o.result_ok is not None]
        return (
            f"| {group} | {len(outcomes)} | {sum(o.passed for o in outcomes)} "
            f"| {cls._share(o.status_ok for o in outcomes)} "
            f"| {cls._share(o.procedure_ok for o in outcomes)} "
            f"| {cls._share(o.params_ok for o in outcomes)} "
            f"| {cls._share(o.result_ok is True for o in checked) if checked else '-'} "
            f"| {sum(o.query_retries for o in outcomes)} "
            f"| {sum(len(o.unmatched_numbers) for o in outcomes)} |"
        )

    @staticmethod
    def _share(checks: Iterable[bool]) -> str:
        values = list(checks)
        return f"{sum(values)}/{len(values)}"


class RunArchive:
    """Keeps every case of a run as JSON in its own directory, e.g. evals/runs/20261020-210500,
    so the model's calls, its SQL and the full answers can be read after the run."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @classmethod
    def started_now(cls, root: Path) -> RunArchive:
        return cls(root / datetime.now().strftime("%Y%m%d-%H%M%S"))

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
