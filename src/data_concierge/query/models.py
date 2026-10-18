"""What the rules see of a parsed query, and what the model gets back after running one."""

from dataclasses import dataclass
from enum import StrEnum

from pydantic import JsonValue

from data_concierge.results.models import Handle, ResultSummary

# A node of the json_serialize_sql tree, and the whole tree.
type JsonObject = dict[str, JsonValue]
type QueryTree = JsonObject

# Modifier types of a query node that the normalizer edits.
ORDER_MODIFIER = "ORDER_MODIFIER"
LIMIT_MODIFIERS = frozenset({"LIMIT_MODIFIER", "LIMIT_PERCENT_MODIFIER"})


class NodeKind(StrEnum):
    TABLE = "table"
    CTE = "cte"
    TABLE_FUNCTION = "table_function"
    FUNCTION = "function"
    WINDOW = "window"
    CAST = "cast"
    CONSTANT = "constant"
    PARAMETER = "parameter"
    SUBQUERY = "subquery"
    GROUP_BY = "group_by"
    PIVOT = "pivot"
    LIMIT = "limit"
    DISTINCT_ON = "distinct_on"
    SAMPLE = "sample"
    RECURSIVE_CTE = "recursive_cte"
    # An element outside the allowlist of node types (rule 2).
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class QueryNode:
    """One element of the query tree that a rule may judge.

    `name` is a lowercase function or table name, a type id for casts and constants, or a
    description for unsupported elements. `enclosing` lists the functions whose arguments
    contain the node, outermost first.
    """

    kind: NodeKind
    name: str = ""
    location: int | None = None
    enclosing: tuple[str, ...] = ()
    # A table name qualified with a schema or catalog, or a table with AT (...).
    qualified: bool = False
    # A table reference that resolves to a CTE in scope.
    cte: bool = False
    # LIMIT of the outermost query.
    top_level: bool = False
    # A window whose frame counts rows (ROWS BETWEEN ...), not peers of equal sort keys.
    rows_frame: bool = False
    # Part of a select-list item at any query level, not of a condition or sort key.
    in_select_list: bool = False
    # Inside a CAST(... AS DECIMAL(p,s)) within that item.
    under_decimal_cast: bool = False


@dataclass(frozen=True, slots=True)
class ParsedQuery:
    submitted_sql: str
    tree: QueryTree
    nodes: tuple[QueryNode, ...]


@dataclass(frozen=True, slots=True)
class RuleViolation:
    """A broken rule of section 7.3, or a result type of section 7.4 when `rule` is None."""

    rule: int | None
    message: str

    def __str__(self) -> str:
        label = "Result type" if self.rule is None else f"Rule {self.rule}"
        return f"{label}: {self.message}"


@dataclass(frozen=True, slots=True)
class QueryContext:
    """The session as the rules see it."""

    handles: tuple[Handle, ...]
    truncated: frozenset[Handle]


class QuerySummary(ResultSummary, frozen=True):
    """The model's view of a query result, with the sources and the SQL the backend ran."""

    sources: tuple[Handle, ...]
    sql: str
