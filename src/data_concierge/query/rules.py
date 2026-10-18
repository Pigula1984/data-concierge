"""Rules of architecture section 7.3, each a class judging the parsed query (Strategy).

Rule 1 (exactly one SELECT) is checked by the parser before there is a tree. Rule 9 and the
result types are checked on the columns that DuckDB binds, because `*` and unaliased columns
get their names only there. The function lists below are the only place that decides what
the model may call; the instructions for the model are built from them.
"""

import re
from collections.abc import Iterable, Sequence
from typing import Protocol, override

from data_concierge.core.errors import QueryValidationError
from data_concierge.core.sql import fold_identifier
from data_concierge.core.types import ColumnType, case_insensitive_duplicates, parse_duckdb_type
from data_concierge.query.models import (
    NodeKind,
    ParsedQuery,
    QueryContext,
    QueryNode,
    RuleViolation,
)
from data_concierge.results.models import HANDLE_PATTERN, DescribedColumn, Handle

COLUMN_NAME_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]*")

TABLE_FUNCTIONS = frozenset({"range", "generate_series"})
AGGREGATE_FUNCTIONS = frozenset(
    {"sum", "count", "count_star", "min", "max", "avg", "bool_and", "bool_or"}
)
WINDOW_FUNCTIONS = AGGREGATE_FUNCTIONS | {"rank", "dense_rank"}
SCALAR_FUNCTIONS = frozenset(
    {
        # Arithmetic.
        "+", "-", "*", "/", "%", "nullif", "greatest", "least", "abs", "round", "floor",
        "ceil", "ceiling",
        # Dates: extract(...) arrives as date_part, `INTERVAL n MONTH` as to_months(n).
        "date_trunc", "date_part", "year", "quarter", "month", "day", "strftime", "make_date",
        "to_years", "to_quarters", "to_months", "to_weeks", "to_days",
        # Text: LIKE, NOT LIKE, ILIKE and NOT ILIKE arrive as ~~, !~~, ~~* and !~~*.
        "lower", "upper", "trim", "concat", "||", "length", "~~", "!~~", "~~*", "!~~*",
        "starts_with", "contains",
    }
)  # fmt: skip
ALLOWED_FUNCTIONS = SCALAR_FUNCTIONS | AGGREGATE_FUNCTIONS
# REAL arrives as FLOAT.
FLOAT_TYPES = frozenset({"FLOAT", "DOUBLE"})
# Functions whose result is DOUBLE even over DECIMAL arguments, named for messages.
_DOUBLE_RESULTS = {"/": "division", "avg": "`avg`"}

_NON_DETERMINISTIC = "the result would change between runs; write the value as a literal"
_ORDER_DEPENDENT = "the result depends on the order of rows"
_HINTS = {
    "row_number": "use `rank` or `dense_rank` with QUALIFY",
    "ntile": "use `rank` or `dense_rank` with QUALIFY",
    "lag": "join the result with itself on the shifted period",
    "lead": "join the result with itself on the shifted period",
    "first_value": "use `min` or `max`, or `rank` with QUALIFY",
    "last_value": "use `min` or `max`, or `rank` with QUALIFY",
    "nth_value": "use `rank` with QUALIFY",
    "any_value": f"{_ORDER_DEPENDENT}; use `min` or `max`",
    "arbitrary": f"{_ORDER_DEPENDENT}; use `min` or `max`",
    "first": f"{_ORDER_DEPENDENT}; use `min` or `max`",
    "last": f"{_ORDER_DEPENDENT}; use `min` or `max`",
    "list": f"{_ORDER_DEPENDENT}; return the rows instead",
    "array_agg": f"{_ORDER_DEPENDENT}; return the rows instead",
    "string_agg": f"{_ORDER_DEPENDENT}; return the rows instead",
    "random": _NON_DETERMINISTIC,
    "uuid": _NON_DETERMINISTIC,
    "gen_random_uuid": _NON_DETERMINISTIC,
    "now": _NON_DETERMINISTIC,
    "current_date": _NON_DETERMINISTIC,
    "current_timestamp": _NON_DETERMINISTIC,
    "get_current_timestamp": _NON_DETERMINISTIC,
    "today": _NON_DETERMINISTIC,
    "nextval": _NON_DETERMINISTIC,
    "setseed": _NON_DETERMINISTIC,
}
_DATE_PART_HINT = "this part returns DOUBLE; use year, quarter, month, day or another integer part"


_ROW_PICKERS = {
    NodeKind.DISTINCT_ON: "DISTINCT ON keeps an arbitrary row; use `rank` with QUALIFY",
    NodeKind.SAMPLE: "SAMPLE returns random rows",
    NodeKind.RECURSIVE_CTE: "WITH RECURSIVE is not allowed",
}


class QueryRule(Protocol):
    def check(self, query: ParsedQuery, context: QueryContext) -> Iterable[RuleViolation]: ...


class AllowedNodesRule(QueryRule):
    """Rule 2: only node types the walker knows."""

    @override
    def check(self, query: ParsedQuery, context: QueryContext) -> Iterable[RuleViolation]:
        for node in _of_kind(query, NodeKind.UNSUPPORTED):
            yield RuleViolation(2, f"{node.name} is not supported{_fragment(query, node)}")


class SessionHandlesRule(QueryRule):
    """Rule 3: tables are result handles of this session or CTEs in scope."""

    @override
    def check(self, query: ParsedQuery, context: QueryContext) -> Iterable[RuleViolation]:
        available = f"available handles: {', '.join(context.handles) or 'none'}"
        for node in _of_kind(query, NodeKind.CTE):
            if HANDLE_PATTERN.fullmatch(node.name):
                yield RuleViolation(
                    3, f"CTE '{node.name}' is named like a result handle; give it another name"
                )
        for node in _of_kind(query, NodeKind.TABLE):
            if node.cte or _session_handle(node, context) is not None:
                continue
            if node.qualified:
                message = f"table '{node.name}' must be a result handle without schema or catalog"
            elif HANDLE_PATTERN.fullmatch(node.name):
                message = f"unknown handle '{node.name}'; {available}"
            else:
                message = f"table '{node.name}' is not a result handle of this session; {available}"
            yield RuleViolation(3, message)


class TableFunctionsRule(QueryRule):
    """Rule 4: only range and generate_series, e.g. for a grid of periods."""

    @override
    def check(self, query: ParsedQuery, context: QueryContext) -> Iterable[RuleViolation]:
        for node in _of_kind(query, NodeKind.TABLE_FUNCTION):
            if node.name not in TABLE_FUNCTIONS:
                yield RuleViolation(
                    4,
                    f"table function `{node.name}` is not allowed; "
                    f"only {' and '.join(sorted(TABLE_FUNCTIONS))}",
                )


class AllowedFunctionsRule(QueryRule):
    """Rule 5: scalar, aggregate and window functions from the allowlists."""

    @override
    def check(self, query: ParsedQuery, context: QueryContext) -> Iterable[RuleViolation]:
        for node in query.nodes:
            if node.kind is NodeKind.FUNCTION:
                yield from self._function(node, ALLOWED_FUNCTIONS)
            elif node.kind is NodeKind.WINDOW:
                yield from self._function(node, WINDOW_FUNCTIONS)
                if node.rows_frame:
                    yield RuleViolation(
                        5,
                        f"window `{node.name}` uses a ROWS frame, whose rows change with the order "
                        "of ties; use the default RANGE frame or aggregate by period",
                    )

    @staticmethod
    def _function(node: QueryNode, allowed: frozenset[str]) -> Iterable[RuleViolation]:
        if node.name not in allowed:
            default = f"allowed: {', '.join(sorted(allowed))}"
            hint = _DATE_PART_HINT if node.name.startswith("date_part(") else default
            hint = _HINTS.get(node.name, hint)
            yield RuleViolation(5, f"function `{node.name}` is not allowed; {hint}")


class DeterministicRowsRule(QueryRule):
    """Rule 6: no clause that picks arbitrary or random rows."""

    @override
    def check(self, query: ParsedQuery, context: QueryContext) -> Iterable[RuleViolation]:
        for node in query.nodes:
            if node.kind is NodeKind.LIMIT and not node.top_level:
                yield RuleViolation(
                    6, "LIMIT and OFFSET are allowed only in the outermost query (top N)"
                )
            elif node.kind in _ROW_PICKERS:
                yield RuleViolation(6, _ROW_PICKERS[node.kind])


class ExactArithmeticRule(QueryRule):
    """Rule 7: no floating point, whose sums depend on the order of addition."""

    @override
    def check(self, query: ParsedQuery, context: QueryContext) -> Iterable[RuleViolation]:
        for node in query.nodes:
            if node.kind is NodeKind.CAST and node.name in FLOAT_TYPES:
                yield RuleViolation(
                    7,
                    f"CAST to {node.name} is not allowed; keep amounts DECIMAL, e.g. "
                    "CAST(... AS DECIMAL(38,6))",
                )
            elif node.kind is NodeKind.CONSTANT and node.name in FLOAT_TYPES:
                yield RuleViolation(
                    7, "number literals in exponent notation are DOUBLE; write them as decimals"
                )
            else:
                yield from self._uncast_double(node)
                yield from self._inside_aggregate(node)

    @staticmethod
    def _uncast_double(node: QueryNode) -> Iterable[RuleViolation]:
        """A DOUBLE column, even in a CTE or subquery, could be summed one level up."""
        element = _double_result(node)
        if element is not None and node.in_select_list and not node.under_decimal_cast:
            yield RuleViolation(
                7,
                f"{element} gives DOUBLE; wrap it in CAST(... AS DECIMAL(38,6)) in the same column "
                "expression, also inside a CTE or subquery",
            )

    @staticmethod
    def _inside_aggregate(node: QueryNode) -> Iterable[RuleViolation]:
        aggregate = next((name for name in node.enclosing if name in WINDOW_FUNCTIONS), None)
        if aggregate is None:
            return
        element = "a subquery" if node.kind is NodeKind.SUBQUERY else _double_result(node)
        if element is None:
            return
        yield RuleViolation(
            7,
            f"{element} inside `{aggregate}` is not allowed; aggregate exact values first and "
            "divide the results, e.g. CAST(sum(a) / NULLIF(sum(b), 0) AS DECIMAL(38,6))",
        )


class NoParametersRule(QueryRule):
    """Rule 8: literals in the text, which then travel into the lineage with the SQL."""

    @override
    def check(self, query: ParsedQuery, context: QueryContext) -> Iterable[RuleViolation]:
        for node in _of_kind(query, NodeKind.PARAMETER):
            yield RuleViolation(
                8, f"query parameter {node.name} is not allowed; write literal values"
            )


class TruncatedSourcesRule(QueryRule):
    """Rule 10: totals over a procedure result cut at max_rows would look complete."""

    @override
    def check(self, query: ParsedQuery, context: QueryContext) -> Iterable[RuleViolation]:
        truncated = [h for h in referenced_handles(query, context) if h in context.truncated]
        if truncated and _aggregates(query):
            yield RuleViolation(
                10,
                f"{', '.join(truncated)} come from a procedure result cut at max_rows, so totals "
                "would miss rows; call the procedure again with narrower parameters (e.g. a "
                "shorter period) or return the rows without aggregation",
            )


DEFAULT_RULES: tuple[QueryRule, ...] = (
    AllowedNodesRule(),
    SessionHandlesRule(),
    TableFunctionsRule(),
    AllowedFunctionsRule(),
    DeterministicRowsRule(),
    ExactArithmeticRule(),
    NoParametersRule(),
    TruncatedSourcesRule(),
)


class QueryPolicy:
    """Runs every rule and reports all violations in one error, so the model fixes them at once."""

    def __init__(self, rules: Sequence[QueryRule] = DEFAULT_RULES) -> None:
        self._rules = rules

    def check(self, query: ParsedQuery, context: QueryContext) -> tuple[Handle, ...]:
        """Returns the handles the query reads; raises QueryValidationError on violations."""
        _raise_if_any([v for rule in self._rules for v in rule.check(query, context)])
        return referenced_handles(query, context)

    @staticmethod
    def result_types(columns: Sequence[DescribedColumn]) -> tuple[ColumnType, ...]:
        """Rule 9 and the result types of section 7.4, on the columns DuckDB binds."""
        violations: list[RuleViolation] = []
        types: list[ColumnType] = []
        for name, duckdb_type in columns:
            if not COLUMN_NAME_PATTERN.fullmatch(name):
                violations.append(
                    RuleViolation(
                        9,
                        f"result column {name} needs an alias of English letters, digits and "
                        "underscores, e.g. AS net_sales",
                    )
                )
            try:
                types.append(parse_duckdb_type(duckdb_type))
            except QueryValidationError:
                violations.append(_type_problem(name, duckdb_type))
        duplicates = sorted(set(case_insensitive_duplicates([name for name, _ in columns])))
        if duplicates:
            violations.append(
                RuleViolation(
                    9, f"result column names must differ ignoring case: {', '.join(duplicates)}"
                )
            )
        _raise_if_any(violations)
        return tuple(types)


def referenced_handles(query: ParsedQuery, context: QueryContext) -> tuple[Handle, ...]:
    """Session handles the query reads, each once, in creation order."""
    found = {_session_handle(node, context) for node in _of_kind(query, NodeKind.TABLE)}
    return tuple(handle for handle in context.handles if handle in found)


def _session_handle(node: QueryNode, context: QueryContext) -> Handle | None:
    """The session handle a table reference reads; DuckDB resolves `R1` as `r1`."""
    if node.cte or node.qualified:
        return None
    name = fold_identifier(node.name)
    return next((h for h in context.handles if fold_identifier(h) == name), None)


def _double_result(node: QueryNode) -> str | None:
    if node.kind is NodeKind.FUNCTION or (node.kind is NodeKind.WINDOW and node.name == "avg"):
        return _DOUBLE_RESULTS.get(node.name)
    return None


def _aggregates(query: ParsedQuery) -> bool:
    return any(
        node.kind in (NodeKind.WINDOW, NodeKind.GROUP_BY, NodeKind.PIVOT)
        or (node.kind is NodeKind.FUNCTION and node.name in AGGREGATE_FUNCTIONS)
        for node in query.nodes
    )


def _type_problem(name: str, duckdb_type: str) -> RuleViolation:
    if duckdb_type in FLOAT_TYPES:
        return RuleViolation(None, f"column {name} is {duckdb_type}; CAST it to DECIMAL(38,6)")
    return RuleViolation(None, f"column {name} has the unsupported type {duckdb_type}")


def _raise_if_any(violations: Sequence[RuleViolation]) -> None:
    if violations:
        raise QueryValidationError("\n".join(str(violation) for violation in violations))


def _of_kind(query: ParsedQuery, kind: NodeKind) -> Iterable[QueryNode]:
    return (node for node in query.nodes if node.kind is kind)


def _fragment(query: ParsedQuery, node: QueryNode) -> str:
    if node.location is None:
        return ""
    text = " ".join(query.submitted_sql[node.location : node.location + 40].split())
    return f' (near "{text}")'
