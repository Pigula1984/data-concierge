"""DuckDB's own parser turns the model's SQL into a tree; the walker lists what the rules judge.

The tree format of `json_serialize_sql` is internal to DuckDB, so the walker accepts only the
node types and fields it knows (architecture, section 7.3, rule 2): a change of the format after
a DuckDB upgrade ends in a refusal, never in an unchecked part of the query.
"""

import json
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Protocol

import duckdb
from pydantic import JsonValue

from data_concierge.core.duckdb_connection import connect_locked
from data_concierge.core.errors import QueryValidationError
from data_concierge.core.sql import fold_identifier
from data_concierge.query.models import (
    LIMIT_MODIFIERS,
    ORDER_MODIFIER,
    JsonObject,
    NodeKind,
    ParsedQuery,
    QueryNode,
    QueryTree,
    RuleViolation,
)

type Scope = frozenset[str]

# query_location of elements the parser generated itself.
NO_LOCATION = 2**64 - 1

_COMMON_EXPRESSION_KEYS = frozenset({"class", "type", "alias", "query_location"})
_EXPRESSION_KEYS: dict[str, frozenset[str]] = {
    "COLUMN_REF": frozenset({"column_names"}),
    "CONSTANT": frozenset({"value"}),
    "PARAMETER": frozenset({"identifier"}),
    "FUNCTION": frozenset(
        {
            "function_name",
            "schema",
            "catalog",
            "children",
            "filter",
            "order_bys",
            "distinct",
            "is_operator",
            "export_state",
        }
    ),
    "WINDOW": frozenset(
        {
            "function_name",
            "schema",
            "catalog",
            "children",
            "partitions",
            "orders",
            "start",
            "end",
            "start_expr",
            "end_expr",
            "offset_expr",
            "default_expr",
            "ignore_nulls",
            "filter_expr",
            "exclude_clause",
            "distinct",
            "arg_orders",
        }
    ),
    "CAST": frozenset({"child", "cast_type", "try_cast"}),
    "COMPARISON": frozenset({"left", "right"}),
    "CONJUNCTION": frozenset({"children"}),
    "OPERATOR": frozenset({"children"}),
    "CASE": frozenset({"case_checks", "else_expr"}),
    "BETWEEN": frozenset({"input", "lower", "upper"}),
    "SUBQUERY": frozenset({"subquery", "subquery_type", "child", "comparison_type"}),
    "STAR": frozenset(
        {
            "relation_name",
            "exclude_list",
            "replace_list",
            "columns",
            "expr",
            "qualified_exclude_list",
            "rename_list",
        }
    ),
}
_OPERATOR_TYPES = frozenset(
    {
        "OPERATOR_COALESCE",
        "OPERATOR_IS_NULL",
        "OPERATOR_IS_NOT_NULL",
        "OPERATOR_NOT",
        "COMPARE_IN",
        "COMPARE_NOT_IN",
    }
)
_SUBQUERY_TYPES = frozenset({"SCALAR", "EXISTS", "NOT_EXISTS", "ANY"})

_COMMON_TABLE_KEYS = frozenset({"type", "alias", "sample", "query_location"})
_TABLE_KEYS: dict[str, frozenset[str]] = {
    "BASE_TABLE": frozenset(
        {"schema_name", "table_name", "column_name_alias", "catalog_name", "at_clause"}
    ),
    "JOIN": frozenset(
        {
            "left",
            "right",
            "condition",
            "join_type",
            "ref_type",
            "using_columns",
            "delim_flipped",
            "duplicate_eliminated_columns",
        }
    ),
    "SUBQUERY": frozenset({"subquery", "column_name_alias"}),
    "TABLE_FUNCTION": frozenset({"function", "column_name_alias", "with_ordinality"}),
    "PIVOT": frozenset(
        {
            "source",
            "aggregates",
            "unpivot_names",
            "pivots",
            "groups",
            "include_nulls",
            "column_name_alias",
        }
    ),
    "EMPTY": frozenset(),
}
# POSITIONAL pairs rows by their order and ASOF picks one of several equal matches.
_JOIN_REFERENCES = frozenset({"REGULAR", "NATURAL", "CROSS"})
_JOIN_TYPES = frozenset({"INNER", "LEFT", "RIGHT", "OUTER", "FULL", "SEMI", "ANTI"})

_QUERY_KEYS: dict[str, frozenset[str]] = {
    "SELECT_NODE": frozenset(
        {
            "type",
            "modifiers",
            "cte_map",
            "select_list",
            "from_table",
            "where_clause",
            "group_expressions",
            "group_sets",
            "aggregate_handling",
            "having",
            "sample",
            "qualify",
        }
    ),
    "SET_OPERATION_NODE": frozenset(
        {"type", "modifiers", "cte_map", "left", "right", "setop_type", "setop_all"}
    ),
}
_STATEMENT_KEYS = frozenset({"node", "named_param_map"})
_CTE_KEYS = frozenset({"aliases", "query", "materialized", "key_targets"})
_ORDER_KEYS = frozenset({"type", "orders"})
_ORDER_ITEM_KEYS = frozenset({"type", "null_order", "expression"})
_LIMIT_KEYS = frozenset({"type", "limit", "offset"})
_DISTINCT_KEYS = frozenset({"type", "distinct_on_targets"})
_CASE_CHECK_KEYS = frozenset({"when_expr", "then_expr"})
_PIVOT_KEYS = frozenset({"pivot_expressions", "unpivot_names", "entries", "pivot_enum"})
_PIVOT_ENTRY_KEYS = frozenset({"values", "star_expr", "alias"})
_SINGLE_SELECT = "the query must be exactly one SELECT statement"

# Parts for which date_part returns an integer; epoch, julian and others return DOUBLE.
INTEGER_DATE_PARTS = frozenset(
    {
        "year", "years", "quarter", "quarters", "month", "months", "week", "weeks", "day",
        "days", "dayofweek", "dow", "isodow", "dayofyear", "doy", "yearweek", "isoyear",
        "hour", "hours", "minute", "minutes", "second", "seconds", "decade", "century",
        "millennium",
    }
)  # fmt: skip

# `INTERVAL 1 MONTH` arrives as to_months(CAST(trunc(CAST(1 AS DOUBLE)) AS INTEGER)).
INTERVAL_FUNCTIONS = frozenset(
    {
        "to_years",
        "to_quarters",
        "to_months",
        "to_weeks",
        "to_days",
        "to_hours",
        "to_minutes",
        "to_seconds",
        "to_milliseconds",
        "to_microseconds",
        "to_centuries",
        "to_decades",
        "to_millennia",
    }
)


class SqlParser(Protocol):
    def parse(self, sql: str) -> ParsedQuery: ...

    def render(self, tree: QueryTree) -> str: ...


class DuckDBSqlParser:
    """Parses and renders SQL on its own locked-down DuckDB connection that holds no data."""

    def __init__(self) -> None:
        # Parsing needs no parallelism; one thread avoids a pool of idle worker threads.
        self._connection = connect_locked("SET threads = 1")
        self._lock = threading.Lock()

    def parse(self, sql: str) -> ParsedQuery:
        with self._lock:
            cursor = self._connection.cursor()
            self._require_single_select(cursor, sql)
            serialized = json.loads(_scalar(cursor, "SELECT json_serialize_sql(?)", sql))
        if serialized.get("error"):
            message = str(serialized.get("error_message", "invalid query"))
            # A statement that DuckDB rewrites, e.g. PRAGMA, passes as SELECT up to here.
            if serialized.get("error_type") != "parser":
                message = str(RuleViolation(1, f"{_SINGLE_SELECT} ({message})"))
            raise QueryValidationError(message)
        try:
            nodes = QueryTreeWalker().walk(serialized)
        except (KeyError, TypeError) as exc:
            # A field the walker relies on is missing: the tree format changed.
            violation = RuleViolation(2, f"unsupported query structure ({exc})")
            raise QueryValidationError(str(violation)) from exc
        return ParsedQuery(submitted_sql=sql, tree=serialized, nodes=nodes)

    def render(self, tree: QueryTree) -> str:
        with self._lock:
            cursor = self._connection.cursor()
            return _scalar(cursor, "SELECT json_deserialize_sql(?)", json.dumps(tree))

    @staticmethod
    def _require_single_select(cursor: duckdb.DuckDBPyConnection, sql: str) -> None:
        try:
            statements = cursor.extract_statements(sql)
        except duckdb.ParserException as exc:
            raise QueryValidationError(str(exc)) from exc
        except duckdb.Error as exc:
            raise QueryValidationError(str(RuleViolation(1, f"{_SINGLE_SELECT} ({exc})"))) from exc
        types = [statement.type.name for statement in statements]
        if types != ["SELECT"]:
            got = ", ".join(types) or "nothing"
            raise QueryValidationError(str(RuleViolation(1, f"{_SINGLE_SELECT}; got: {got}")))


class QueryTreeWalker:
    """Visits the whole tree once and lists the nodes the rules judge (Visitor)."""

    def __init__(self) -> None:
        self._nodes: list[QueryNode] = []
        # Where the walk is: inside a select-list item, and inside a CAST to DECIMAL there.
        self._in_select_list = False
        self._under_decimal_cast = False

    def walk(self, tree: QueryTree) -> tuple[QueryNode, ...]:
        self._nodes = []
        statements = tree.get("statements")
        if not isinstance(statements, list) or len(statements) != 1:
            self._unsupported("a tree without exactly one statement")
        else:
            self._statement(_as_json(statements[0]), frozenset(), (), top_level=True)
        return tuple(self._nodes)

    # Queries

    def _statement(
        self,
        statement: JsonObject,
        scope: Scope,
        enclosing: tuple[str, ...],
        top_level: bool = False,
    ) -> None:
        if self._known(statement, _STATEMENT_KEYS, "statement"):
            self._query(_as_json(statement["node"]), scope, enclosing, top_level)

    def _query(
        self, node: JsonObject, scope: Scope, enclosing: tuple[str, ...], top_level: bool
    ) -> None:
        # A subquery starts outside any column, also when it sits inside a select-list item.
        with self._column_context(in_select_list=False, under_decimal_cast=False):
            self._query_node(node, scope, enclosing, top_level)

    def _query_node(
        self, node: JsonObject, scope: Scope, enclosing: tuple[str, ...], top_level: bool
    ) -> None:
        node_type = str(node.get("type"))
        if node_type == "RECURSIVE_CTE_NODE":
            self._add(NodeKind.RECURSIVE_CTE, "WITH RECURSIVE")
            return
        keys = _QUERY_KEYS.get(node_type)
        if keys is None:
            self._unsupported(node_type)
            return
        if not self._known(node, keys, node_type):
            return
        scope = self._ctes(_as_json(node["cte_map"]), scope, enclosing)
        self._modifiers(_as_list(node["modifiers"]), scope, enclosing, top_level)
        if node_type == "SET_OPERATION_NODE":
            for side in ("left", "right"):
                self._query(_as_json(node[side]), scope, enclosing, top_level=False)
            return
        self._select(node, scope, enclosing)

    def _select(self, node: JsonObject, scope: Scope, enclosing: tuple[str, ...]) -> None:
        self._table(_as_json(node["from_table"]), scope, enclosing)
        with self._column_context(in_select_list=True, under_decimal_cast=False):
            self._expressions(node["select_list"], scope, enclosing)
        for clause in ("where_clause", "having", "qualify"):
            self._expression(node[clause], scope, enclosing)
        self._expressions(node["group_expressions"], scope, enclosing)
        if node["group_expressions"] or node["aggregate_handling"] == "FORCE_AGGREGATES":
            self._add(NodeKind.GROUP_BY, "GROUP BY")
        if node["sample"] is not None:
            self._add(NodeKind.SAMPLE, "SAMPLE")

    def _ctes(self, cte_map: JsonObject, scope: Scope, enclosing: tuple[str, ...]) -> Scope:
        """Each CTE sees the CTEs defined before it; the query body sees all of them."""
        for entry in _as_list(cte_map.get("map")):
            entry = _as_json(entry)
            name = str(entry["key"])
            definition = _as_json(entry["value"])
            if self._known(definition, _CTE_KEYS, f"CTE {name}"):
                if definition["key_targets"]:
                    self._unsupported(f"CTE {name} with USING KEY")
                self._statement(_as_json(definition["query"]), scope, enclosing)
            self._add(NodeKind.CTE, name)
            scope = scope | {fold_identifier(name)}
        return scope

    def _modifiers(
        self, modifiers: list[JsonValue], scope: Scope, enclosing: tuple[str, ...], top_level: bool
    ) -> None:
        for modifier in modifiers:
            modifier = _as_json(modifier)
            modifier_type = modifier.get("type")
            if modifier_type == ORDER_MODIFIER:
                if self._known(modifier, _ORDER_KEYS, "ORDER BY"):
                    self._orders(modifier["orders"], scope, enclosing)
            elif modifier_type in LIMIT_MODIFIERS:
                if self._known(modifier, _LIMIT_KEYS, "LIMIT"):
                    self._add(NodeKind.LIMIT, "LIMIT", top_level=top_level)
                    self._expressions([modifier["limit"], modifier["offset"]], scope, enclosing)
            elif modifier_type == "DISTINCT_MODIFIER":
                if (
                    self._known(modifier, _DISTINCT_KEYS, "DISTINCT")
                    and (modifier["distinct_on_targets"])
                ):
                    self._add(NodeKind.DISTINCT_ON, "DISTINCT ON")
            else:
                self._unsupported(f"modifier {modifier_type}")

    def _orders(self, orders: JsonValue, scope: Scope, enclosing: tuple[str, ...]) -> None:
        for order in _as_list(orders):
            order = _as_json(order)
            if self._known(order, _ORDER_ITEM_KEYS, "ORDER BY item"):
                self._expression(order["expression"], scope, enclosing)

    # Table references

    def _table(self, ref: JsonObject, scope: Scope, enclosing: tuple[str, ...]) -> None:
        ref_type = str(ref.get("type"))
        keys = _TABLE_KEYS.get(ref_type)
        if keys is None:
            self._unsupported(ref_type, ref)
            return
        if not self._known(ref, _COMMON_TABLE_KEYS | keys, ref_type):
            return
        if ref["sample"] is not None:
            self._add(NodeKind.SAMPLE, "SAMPLE", ref)
        match ref_type:
            case "BASE_TABLE":
                name = str(ref["table_name"])
                qualified = bool(ref["schema_name"] or ref["catalog_name"]) or (
                    ref["at_clause"] is not None
                )
                cte = not qualified and fold_identifier(name) in scope
                self._add(NodeKind.TABLE, name, ref, qualified=qualified, cte=cte)
            case "JOIN":
                self._join(ref, scope, enclosing)
            case "SUBQUERY":
                self._statement(_as_json(ref["subquery"]), scope, enclosing)
            case "TABLE_FUNCTION":
                self._table_function(_as_json(ref["function"]), scope, enclosing)
            case "PIVOT":
                self._pivot(ref, scope, enclosing)

    def _join(self, ref: JsonObject, scope: Scope, enclosing: tuple[str, ...]) -> None:
        if ref["ref_type"] not in _JOIN_REFERENCES:
            self._unsupported(f"{ref['ref_type']} JOIN", ref)
            return
        if ref["join_type"] not in _JOIN_TYPES or ref["duplicate_eliminated_columns"]:
            self._unsupported(f"{ref['join_type']} JOIN", ref)
            return
        self._table(_as_json(ref["left"]), scope, enclosing)
        self._table(_as_json(ref["right"]), scope, enclosing)
        self._expression(ref["condition"], scope, enclosing)

    def _table_function(
        self, function: JsonObject, scope: Scope, enclosing: tuple[str, ...]
    ) -> None:
        if function.get("class") != "FUNCTION":
            self._unsupported("table function", function)
            return
        keys = _COMMON_EXPRESSION_KEYS | _EXPRESSION_KEYS["FUNCTION"]
        if not self._known(function, keys, "table function"):
            return
        name = fold_identifier(str(function["function_name"]))
        self._add(NodeKind.TABLE_FUNCTION, name, function)
        self._expressions(function["children"], scope, enclosing)

    def _pivot(self, ref: JsonObject, scope: Scope, enclosing: tuple[str, ...]) -> None:
        if ref["unpivot_names"]:
            self._unsupported("UNPIVOT", ref)
            return
        self._add(NodeKind.PIVOT, "PIVOT", ref)
        self._table(_as_json(ref["source"]), scope, enclosing)
        # Each pivot aggregate becomes result columns, like a select-list item.
        with self._column_context(in_select_list=True, under_decimal_cast=False):
            self._expressions(ref["aggregates"], scope, enclosing)
        for pivot in _as_list(ref["pivots"]):
            pivot = _as_json(pivot)
            if not self._known(pivot, _PIVOT_KEYS, "PIVOT ... FOR"):
                continue
            # Without a literal IN list DuckDB runs extra queries to find the values.
            if not pivot["entries"] or pivot["pivot_enum"] or pivot["unpivot_names"]:
                self._unsupported("PIVOT without an explicit IN list of values", ref)
            for entry in _as_list(pivot["entries"]):
                entry = _as_json(entry)
                if self._known(entry, _PIVOT_ENTRY_KEYS, "PIVOT value"):
                    self._expression(entry["star_expr"], scope, enclosing)
            self._expressions(pivot["pivot_expressions"], scope, enclosing)

    # Expressions

    def _expressions(self, values: JsonValue, scope: Scope, enclosing: tuple[str, ...]) -> None:
        for value in _as_list(values):
            self._expression(value, scope, enclosing)

    def _expression(self, value: JsonValue, scope: Scope, enclosing: tuple[str, ...]) -> None:
        if value is None:
            return
        node = _as_json(value)
        node_class = str(node.get("class"))
        keys = _EXPRESSION_KEYS.get(node_class)
        if keys is None:
            self._unsupported(f"{node_class} expression", node)
            return
        if not self._known(node, _COMMON_EXPRESSION_KEYS | keys, node_class):
            return
        match node_class:
            case "COLUMN_REF":
                pass
            case "CONSTANT":
                value_type = _as_json(_as_json(node["value"])["type"])
                self._add(NodeKind.CONSTANT, str(value_type["id"]), node, enclosing=enclosing)
            case "PARAMETER":
                self._add(NodeKind.PARAMETER, str(node["identifier"]), node)
            case "FUNCTION":
                self._function(node, scope, enclosing)
            case "WINDOW":
                self._window(node, scope, enclosing)
            case "CAST":
                cast_type = str(_as_json(node["cast_type"])["id"])
                self._add(NodeKind.CAST, cast_type, node, enclosing=enclosing)
                decimal = self._under_decimal_cast or cast_type == "DECIMAL"
                with self._column_context(self._in_select_list, under_decimal_cast=decimal):
                    self._expression(node["child"], scope, enclosing)
            case "COMPARISON":
                self._expressions([node["left"], node["right"]], scope, enclosing)
            case "CONJUNCTION":
                self._expressions(node["children"], scope, enclosing)
            case "OPERATOR":
                if node["type"] not in _OPERATOR_TYPES:
                    self._unsupported(f"operator {node['type']}", node)
                    return
                self._expressions(node["children"], scope, enclosing)
            case "CASE":
                for check in _as_list(node["case_checks"]):
                    check = _as_json(check)
                    if self._known(check, _CASE_CHECK_KEYS, "CASE WHEN"):
                        self._expressions(
                            [check["when_expr"], check["then_expr"]], scope, enclosing
                        )
                self._expression(node["else_expr"], scope, enclosing)
            case "BETWEEN":
                self._expressions([node["input"], node["lower"], node["upper"]], scope, enclosing)
            case "SUBQUERY":
                self._subquery(node, scope, enclosing)
            case "STAR":
                self._star(node, scope, enclosing)

    def _function(self, node: JsonObject, scope: Scope, enclosing: tuple[str, ...]) -> None:
        # The parser itself fills schema `main` in for e.g. extract(...) and [...], so a
        # qualified name proves nothing; the rules judge the function name alone.
        name = fold_identifier(str(node["function_name"]))
        self._add(NodeKind.FUNCTION, _judged_name(name, node), node, enclosing=enclosing)
        if node["export_state"]:
            self._unsupported(f"{name} with EXPORT_STATE", node)
        interval_operand = _interval_operand(name, node)
        if interval_operand is not None:
            self._expression(interval_operand, scope, enclosing)
            return
        inner = (*enclosing, name)
        self._expressions(node["children"], scope, inner)
        order_bys = _as_json(node["order_bys"])
        # FILTER is a condition and ORDER BY a sort key: neither yields a column value.
        with self._column_context(in_select_list=False, under_decimal_cast=False):
            self._expression(node["filter"], scope, enclosing)
            if self._known(order_bys, _ORDER_KEYS, "ORDER BY in a function"):
                self._orders(order_bys["orders"], scope, inner)

    def _window(self, node: JsonObject, scope: Scope, enclosing: tuple[str, ...]) -> None:
        name = fold_identifier(str(node["function_name"]))
        # Frame bounds such as EXPR_PRECEDING_ROWS or CURRENT_ROW_ROWS count rows.
        rows_frame = any(str(node[bound]).endswith("_ROWS") for bound in ("start", "end"))
        self._add(NodeKind.WINDOW, name, node, enclosing=enclosing, rows_frame=rows_frame)
        inner = (*enclosing, name)
        self._expressions(node["children"], scope, inner)
        self._expression(node["default_expr"], scope, enclosing)
        # Partitions, sort keys, frame bounds and FILTER do not yield the column value.
        with self._column_context(in_select_list=False, under_decimal_cast=False):
            self._expressions(node["partitions"], scope, enclosing)
            self._orders(node["orders"], scope, enclosing)
            self._orders(node["arg_orders"], scope, inner)
            for key in ("start_expr", "end_expr", "offset_expr", "filter_expr"):
                self._expression(node[key], scope, enclosing)

    def _subquery(self, node: JsonObject, scope: Scope, enclosing: tuple[str, ...]) -> None:
        if node["subquery_type"] not in _SUBQUERY_TYPES:
            self._unsupported(f"{node['subquery_type']} subquery", node)
            return
        self._add(NodeKind.SUBQUERY, str(node["subquery_type"]), node, enclosing=enclosing)
        self._statement(_as_json(node["subquery"]), scope, enclosing)
        self._expression(node["child"], scope, enclosing)

    def _star(self, node: JsonObject, scope: Scope, enclosing: tuple[str, ...]) -> None:
        self._expression(node["expr"], scope, enclosing)
        for replacement in _as_list(node["replace_list"]):
            self._expression(_as_json(replacement).get("value"), scope, enclosing)

    @contextmanager
    def _column_context(self, in_select_list: bool, under_decimal_cast: bool) -> Iterator[None]:
        saved = (self._in_select_list, self._under_decimal_cast)
        self._in_select_list, self._under_decimal_cast = in_select_list, under_decimal_cast
        try:
            yield
        finally:
            self._in_select_list, self._under_decimal_cast = saved

    # Recording

    def _known(self, node: JsonObject, keys: Iterable[str], what: str) -> bool:
        unexpected = sorted(set(node) - set(keys))
        if unexpected:
            self._unsupported(f"{what} with fields {', '.join(unexpected)}", node)
        return not unexpected

    def _unsupported(self, what: str, node: JsonObject | None = None) -> None:
        self._add(NodeKind.UNSUPPORTED, what, node)

    def _add(
        self,
        kind: NodeKind,
        name: str,
        node: JsonObject | None = None,
        *,
        enclosing: tuple[str, ...] = (),
        qualified: bool = False,
        cte: bool = False,
        top_level: bool = False,
        rows_frame: bool = False,
    ) -> None:
        self._nodes.append(
            QueryNode(
                kind=kind,
                name=name,
                location=_location(node),
                enclosing=enclosing,
                qualified=qualified,
                cte=cte,
                top_level=top_level,
                rows_frame=rows_frame,
                in_select_list=self._in_select_list,
                under_decimal_cast=self._under_decimal_cast,
            )
        )


def _judged_name(name: str, node: JsonObject) -> str:
    """date_part (also from extract) returns DOUBLE for parts such as epoch and julian, so the
    rules judge it with its part: plain `date_part` only for integer parts."""
    if name != "date_part":
        return name
    children = _as_list(node["children"])
    part = _as_json(children[0]) if children else {}
    value = _as_json(part.get("value"))
    if part.get("class") == "CONSTANT" and _as_json(value.get("type")).get("id") == "VARCHAR":
        unit = fold_identifier(str(value.get("value")))
        if unit in INTEGER_DATE_PARTS:
            return name
        return f"date_part('{unit}')"
    return "date_part(<non-literal part>)"


def _scalar(cursor: duckdb.DuckDBPyConnection, sql: str, argument: str) -> str:
    row = cursor.execute(sql, [argument]).fetchone()
    assert row is not None
    return str(row[0])


def _interval_operand(name: str, node: JsonObject) -> JsonValue | None:
    """The number inside an interval literal, or None when the call is not one."""
    children = _as_list(node["children"])
    if name not in INTERVAL_FUNCTIONS or len(children) != 1:
        return None
    to_integer = _as_json(children[0])
    if not _is_cast(to_integer, "INTEGER"):
        return None
    trunc = _as_json(to_integer["child"])
    if trunc.get("class") != "FUNCTION" or trunc.get("function_name") != "trunc":
        return None
    trunc_args = _as_list(trunc["children"])
    if trunc["filter"] is not None or len(trunc_args) != 1:
        return None
    to_double = _as_json(trunc_args[0])
    return to_double["child"] if _is_cast(to_double, "DOUBLE") else None


def _is_cast(node: JsonObject, type_id: str) -> bool:
    return (
        node.get("class") == "CAST"
        and not node.get("try_cast")
        and _as_json(node.get("cast_type")).get("id") == type_id
    )


def _location(node: JsonObject | None) -> int | None:
    location = None if node is None else node.get("query_location")
    return location if isinstance(location, int) and location != NO_LOCATION else None


def _as_json(value: JsonValue) -> JsonObject:
    return value if isinstance(value, dict) else {}


def _as_list(value: JsonValue) -> list[JsonValue]:
    return value if isinstance(value, list) else []
