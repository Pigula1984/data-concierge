"""ChartPolicy: the chart the model asks for can be drawn from a returned table (architecture
003, section 7.2; plan 3B, KTD3 and KTD4).

The checks read only the schema, row counts and counts of distinct values, so the messages back
to the model carry column names and numbers, never values from the data (architecture 002,
section 6.2).
"""

from dataclasses import dataclass

from data_concierge.agent.models import AgentAnswer, ChartRequest
from data_concierge.api.schemas import ChartKind
from data_concierge.core.types import Column, ColumnKind, TableSchema
from data_concierge.results.models import StoredResult
from data_concierge.results.service import ResultService

NUMERIC = frozenset({ColumnKind.INT, ColumnKind.DECIMAL})
CATEGORY = frozenset(
    {
        ColumnKind.STRING,
        ColumnKind.DATE,
        ColumnKind.DATETIME,
        ColumnKind.INT,
        ColumnKind.DECIMAL,
        ColumnKind.BOOL,
    }
)
MAX_SERIES = 12


@dataclass(frozen=True, slots=True)
class KindRule:
    """What one chart kind accepts: x column kinds, whether y is a single column, and the
    largest number of rows or distinct x values it stays readable with."""

    x_kinds: frozenset[ColumnKind]
    x_label: str
    single_y: bool = False
    allows_series: bool = True
    max_rows: int | None = None
    max_categories: int | None = None


RULES: dict[ChartKind, KindRule] = {
    "line": KindRule(
        x_kinds=CATEGORY - {ColumnKind.BOOL}, x_label="date, number or text", max_rows=1000
    ),
    "bar": KindRule(x_kinds=CATEGORY, x_label="category", max_categories=50),
    "stacked_bar": KindRule(x_kinds=CATEGORY, x_label="category", max_categories=50),
    "pie": KindRule(
        x_kinds=frozenset({ColumnKind.STRING}),
        x_label="text",
        single_y=True,
        allows_series=False,
        max_rows=8,
    ),
    "scatter": KindRule(x_kinds=NUMERIC, x_label="numeric", single_y=True, max_rows=1000),
}


class ChartPolicy:
    """Checks one answer's chart against its table, in the style of `Anchors.problems`: every
    problem is a sentence the model can act on, and an answer without a chart has none."""

    def __init__(self, results: ResultService, max_rows: int) -> None:
        self._results = results
        self._max_rows = max_rows

    def problems(self, answer: AgentAnswer) -> list[str]:
        chart = answer.chart
        if chart is None:
            return []
        if answer.status != "ok":
            return ["chart: only with status ok"]
        returned = [table.handle for table in answer.tables]
        if chart.handle not in returned:
            listed = ", ".join(returned) or "none"
            return [f"chart: {chart.handle} is not one of the returned tables ({listed})"]
        stored = self._results.describe(chart.handle)
        # `query` cannot read a procedure result cut at its row limit, so only the procedure
        # can be called again; a table over the response limit can be aggregated.
        if self._results.is_truncated(chart.handle):
            return [
                f"chart: {chart.handle} comes from a procedure result cut at its row limit, so a "
                "chart would miss data; call the procedure with narrower parameters or answer "
                "without a chart"
            ]
        if stored.row_count > self._max_rows:
            return [
                f"chart: {chart.handle} has {stored.row_count} rows, more than a response shows "
                f"({self._max_rows}); aggregate it in query first (e.g. by month) and chart that "
                "handle"
            ]
        missing = self._missing_columns(chart, stored.table_schema)
        if missing:
            return missing
        return [*self._shape_problems(chart, stored), *self._size_problems(chart, stored)]

    @staticmethod
    def _missing_columns(chart: ChartRequest, schema: TableSchema) -> list[str]:
        named = [chart.x, *chart.y, *([chart.series] if chart.series is not None else [])]
        return [
            f"chart: {chart.handle} has no column {name}; columns: {', '.join(schema.names)}"
            for name in dict.fromkeys(named)
            if schema.column(name) is None
        ]

    @staticmethod
    def _shape_problems(chart: ChartRequest, stored: StoredResult) -> list[str]:
        rule = RULES[chart.kind]
        found: list[str] = []
        x = ChartPolicy._column(stored, chart.x)
        if x.type.kind not in rule.x_kinds:
            found.append(
                f"chart: {chart.kind} needs a {rule.x_label} x column, {x.name} is {x.type.kind}"
            )
        for name in chart.y:
            if ChartPolicy._column(stored, name).type.kind not in NUMERIC:
                found.append(f"chart: y column {name} must be numeric (int or decimal)")
        if not chart.y:
            found.append("chart: give at least one y column")
        if len({name.casefold() for name in chart.y}) != len(chart.y):
            found.append("chart: each y column once")
        if rule.single_y and len(chart.y) != 1:
            found.append(f"chart: {chart.kind} needs exactly one y column")
        if chart.series is not None and not rule.allows_series:
            found.append(f"chart: {chart.kind} takes no series")
        elif chart.series is not None and len(chart.y) != 1:
            found.append("chart: with series, give exactly one y column")
        if chart.kind == "stacked_bar" and chart.series is None and len(chart.y) < 2:
            found.append("chart: stacked_bar needs series or at least two y columns")
        if chart.series is None and len(chart.y) > MAX_SERIES:
            found.append(f"chart: at most {MAX_SERIES} series, got {len(chart.y)} y columns")
        return found

    def _size_problems(self, chart: ChartRequest, stored: StoredResult) -> list[str]:
        rule = RULES[chart.kind]
        found: list[str] = []
        handle = chart.handle
        if rule.max_rows is not None and stored.row_count > rule.max_rows:
            advice = (
                "aggregate the rest in query or choose bar"
                if chart.kind == "pie"
                else "aggregate in query"
            )
            found.append(
                f"chart: {chart.kind} needs at most {rule.max_rows} rows, {handle} has "
                f"{stored.row_count}; {advice}"
            )
        if rule.max_categories is not None:
            count = self._results.distinct_count(handle, self._column(stored, chart.x).name)
            if count > rule.max_categories:
                found.append(
                    f"chart: {chart.kind} needs at most {rule.max_categories} categories in "
                    f"{chart.x}, {handle} has {count}; aggregate in query"
                )
        found += self._repeat_problems(chart, stored)
        if chart.series is not None and rule.allows_series:
            series = self._column(stored, chart.series).name
            count = self._results.distinct_count(handle, series)
            if count > MAX_SERIES:
                found.append(
                    f"chart: at most {MAX_SERIES} series, {chart.series} has {count} values"
                )
        return found

    def _repeat_problems(self, chart: ChartRequest, stored: StoredResult) -> list[str]:
        """Every kind but scatter draws one point per x (per x and series): a repeated key
        would hide rows, so the table must hold each key once."""
        if chart.kind == "scatter":
            return []
        keys = [self._column(stored, chart.x).name]
        if chart.series is not None:
            keys.append(self._column(stored, chart.series).name)
        count = self._results.distinct_count(chart.handle, *keys)
        if count == stored.row_count:
            return []
        named = " and ".join([chart.x, *([chart.series] if chart.series is not None else [])])
        verb = "repeats" if chart.series is None else "repeat"
        advice = (
            "aggregate in query or set series" if chart.series is None else "aggregate in query"
        )
        return [
            f"chart: {named} {verb} in {chart.handle} ({count} distinct of {stored.row_count} "
            f"rows); {advice}"
        ]

    @staticmethod
    def _column(stored: StoredResult, name: str) -> Column:
        column = stored.table_schema.column(name)
        assert column is not None, "missing columns were reported first"
        return column
