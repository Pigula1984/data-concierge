"""Evaluation cases from evals/cases.yaml and the checks that score one answered case.

A case holds a prompt (and the user's answers to clarification questions), the expected status,
the procedure calls it expects with their model arguments, a reference query run in SQL Server
and the ids of the clarification candidates. Results are compared by values, not column names:
the model picks its own aliases and column order (plan U18). A case may change its expectation
in one mode, only for the changes listed in architecture 002, section 9 (plan KTD11).
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from data_concierge.api.schemas import AnswerStatus, Response, ResponseTable
from data_concierge.catalog.models import Catalog
from data_concierge.core.settings import ModelDataAccess
from data_concierge.core.sqlserver import SqlRows
from data_concierge.core.types import ColumnKind

CASES_PATH = Path(__file__).parents[2] / "evals" / "cases.yaml"
# A fixed "today", so cases that resolve relative periods or default dates are repeatable.
DEFAULT_TODAY = date(2025, 7, 1)

type Value = object
type Call = tuple[str, Mapping[str, object]]
type CandidateId = int | str


class _CaseModel(BaseModel, frozen=True, extra="forbid"):
    pass


class ExpectedCall(_CaseModel):
    """A procedure call by tool name; `args` are model arguments, matched as a subset."""

    tool: str
    args: dict[str, object] = Field(default_factory=dict)


class ResultRule(_CaseModel):
    """The reference query and how the answer's tables are compared with its rows.

    `rows`: some table has exactly as many rows, and each reference row's values appear in its
    own table row (in order when `ordered`). `contains`: the table may have more rows.
    `columns` limits the reference values to these columns, e.g. when the model may drop others.
    """

    sql: str
    rule: Literal["rows", "contains"] = "rows"
    ordered: bool = False
    columns: tuple[str, ...] = ()


class Expectation(_CaseModel):
    status: tuple[AnswerStatus, ...]
    calls: tuple[ExpectedCall, ...] = ()
    # Checked only when the final status is ok. A list holds alternatives: a total asked for
    # may come as one row or as a breakdown with the total in the comment.
    result: ResultRule | tuple[ResultRule, ...] | None = None
    # Each group lists ways of writing one fact (e.g. a date as ISO text or in Polish); at least
    # one of them must appear in the answer's assumptions, ignoring case.
    assumptions: tuple[tuple[str, ...], ...] = ()
    # Checked only when the final status is needs_clarification: the ids the user chooses from.
    candidates: tuple[CandidateId, ...] = ()

    @property
    def results(self) -> tuple[ResultRule, ...]:
        if self.result is None:
            return ()
        return self.result if isinstance(self.result, tuple) else (self.result,)


class ModeExpectation(_CaseModel):
    """What one mode expects instead of the case's own expectation; fields left out keep it."""

    status: tuple[AnswerStatus, ...] = ()
    result: ResultRule | tuple[ResultRule, ...] | None = None


class EvalCase(_CaseModel):
    id: str
    group: str
    prompt: str
    followups: tuple[str, ...] = ()
    today: date = DEFAULT_TODAY
    expect: Expectation
    modes: dict[ModelDataAccess, ModeExpectation] = Field(default_factory=dict)

    def expectation(self, mode: ModelDataAccess) -> Expectation:
        override = self.modes.get(mode)
        if override is None:
            return self.expect
        return self.expect.model_copy(
            update={name: getattr(override, name) for name in override.model_fields_set}
        )

    @property
    def reference_rules(self) -> tuple[ResultRule, ...]:
        """Every reference query of the case, its own and those of its modes, each once."""
        rules = [*self.expect.results]
        for mode in self.modes:
            rules += [rule for rule in self.expectation(mode).results if rule not in rules]
        return tuple(rules)

    @classmethod
    def load_all(cls, path: Path = CASES_PATH) -> tuple[EvalCase, ...]:
        entries = yaml.safe_load(path.read_text(encoding="utf-8"))
        return tuple(cls.model_validate(entry) for entry in entries)


class CaseChecker:
    """Scores the procedure calls and the final response of one case."""

    def __init__(self, catalog: Catalog) -> None:
        self._entries = {entry.tool: entry for entry in catalog.exposed_entries}

    def procedures_called(self, expected: Sequence[ExpectedCall], calls: Sequence[Call]) -> bool:
        called = {procedure for procedure, _ in calls}
        return all(self._entries[call.tool].procedure in called for call in expected)

    def params_match(self, expected: Sequence[ExpectedCall], calls: Sequence[Call]) -> bool:
        """Every expected call has a recorded call of its procedure with the expected values."""
        return all(any(self._matches(call, made) for made in calls) for call in expected)

    @staticmethod
    def assumptions_match(expected: Sequence[Sequence[str]], response: Response) -> bool:
        text = " ".join(response.assumptions).casefold()
        return all(any(form.casefold() in text for form in group) for group in expected)

    @staticmethod
    def candidates_match(expected: Sequence[CandidateId], response: Response) -> bool:
        """The clarification offers exactly the expected ids, whatever their descriptions."""
        if response.clarification is None:
            return False
        offered = {candidate.id for candidate in response.clarification.candidates}
        return offered == set(expected)

    def result_matches(self, rule: ResultRule, response: Response, reference: SqlRows) -> bool:
        wanted = [self._reference_row(rule, reference, row) for row in reference.rows]
        return any(self._table_matches(rule, table, wanted) for table in response.tables)

    def _matches(self, expected: ExpectedCall, made: Call) -> bool:
        entry = self._entries[expected.tool]
        procedure, params = made
        if procedure != entry.procedure:
            return False
        by_arg = {param.arg: param.name for param in entry.model_params if param.arg}
        return all(params.get(by_arg[arg]) == value for arg, value in expected.args.items())

    def _table_matches(
        self, rule: ResultRule, table: ResponseTable, wanted: Sequence[list[Value]]
    ) -> bool:
        rows = [self._response_row(table, row) for row in table.rows]
        if rule.rule == "rows" and len(rows) != len(wanted):
            return False
        if rule.ordered:
            return len(rows) >= len(wanted) and all(
                self._row_contains(want, row) for want, row in zip(wanted, rows, strict=False)
            )
        return self._all_rows_matched(wanted, rows)

    @classmethod
    def _all_rows_matched(cls, wanted: Sequence[list[Value]], rows: Sequence[list[Value]]) -> bool:
        """Each reference row gets its own response row (maximum bipartite matching).

        First fit is not enough: a name can appear in a row as the employee and in another
        as the manager, so a row taken early may be the only one a later reference row fits.
        """
        candidates = [
            [index for index, row in enumerate(rows) if cls._row_contains(want, row)]
            for want in wanted
        ]
        owner: dict[int, int] = {}

        def assign(want: int, seen: set[int]) -> bool:
            for row in candidates[want]:
                if row in seen:
                    continue
                seen.add(row)
                if row not in owner or assign(owner[row], seen):
                    owner[row] = want
                    return True
            return False

        return all(assign(want, set()) for want in range(len(wanted)))

    @classmethod
    def _row_contains(cls, want: Sequence[Value], row: Sequence[Value]) -> bool:
        """Each wanted value has its own equal value in the row, in any column."""
        unused = list(row)
        for value in want:
            index = next((i for i, got in enumerate(unused) if cls._same(value, got)), None)
            if index is None:
                return False
            del unused[index]
        return True

    @staticmethod
    def _same(expected: Value, got: Value) -> bool:
        """A response decimal also matches the reference rounded to the response's own scale,
        e.g. a sum the model cast to DECIMAL(38,2)."""
        if isinstance(got, Decimal) and isinstance(expected, Decimal | int):
            if isinstance(expected, bool):
                return False
            reference = Decimal(expected)
            return got == reference or got == reference.quantize(got, rounding=ROUND_HALF_UP)
        return expected == got

    @classmethod
    def _reference_row(
        cls, rule: ResultRule, reference: SqlRows, row: Sequence[object]
    ) -> list[Value]:
        names = [name.casefold() for name in reference.columns]
        keep = {name.casefold() for name in rule.columns} or set(names)
        return [
            cls._normalized(value) for name, value in zip(names, row, strict=True) if name in keep
        ]

    @classmethod
    def _response_row(cls, table: ResponseTable, row: Sequence[object]) -> list[Value]:
        values = (
            Decimal(str(value))
            if column.type.kind is ColumnKind.DECIMAL and value is not None
            else value
            for column, value in zip(table.columns, row, strict=True)
        )
        return [cls._normalized(value) for value in values]

    @staticmethod
    def _normalized(value: object) -> Value:
        """Dates as ISO text, the form the response carries them in."""
        if isinstance(value, datetime | date):
            return value.isoformat()
        if isinstance(value, float):
            raise TypeError("a float in a result would lose precision")
        return value
