"""The SQL the backend runs: the model's query with a full ORDER BY and BIGINT integers."""

import copy
from collections.abc import Sequence

from pydantic import JsonValue

from data_concierge.core.sql import quote_identifier
from data_concierge.core.types import DUCKDB_INTEGER_TYPES
from data_concierge.query.models import (
    LIMIT_MODIFIERS,
    ORDER_MODIFIER,
    JsonObject,
    ParsedQuery,
    QueryTree,
)
from data_concierge.query.parser import SqlParser
from data_concierge.results.models import DescribedColumn


class QueryNormalizer:
    def __init__(self, parser: SqlParser) -> None:
        self._parser = parser

    def normalize(self, query: ParsedQuery, columns: Sequence[DescribedColumn]) -> str:
        """Appends every column position to the outermost ORDER BY (KTD29), so ties always
        break the same way and a LIMIT keeps the same rows, then casts integer columns other
        than BIGINT in an outer SELECT, which keeps the order of its subquery."""
        sql = self._parser.render(complete_order_by(query.tree, len(columns)))
        casts = [
            f"CAST({quote_identifier(name)} AS BIGINT) AS {quote_identifier(name)}"
            for name, duckdb_type in columns
            if duckdb_type in DUCKDB_INTEGER_TYPES and duckdb_type != "BIGINT"
        ]
        if not casts:
            return sql
        return f"SELECT * REPLACE ({', '.join(casts)}) FROM ({sql}) AS result"


def complete_order_by(tree: QueryTree, column_count: int) -> QueryTree:
    """A copy of the tree whose outermost ORDER BY ends with positions 1..n, ascending, NULLS
    LAST; a missing ORDER BY is inserted before LIMIT."""
    completed = copy.deepcopy(tree)
    root = _object(_object(_list(completed["statements"])[0])["node"])
    modifiers = _list(root["modifiers"])
    positions: list[JsonValue] = [_position(i) for i in range(1, column_count + 1)]
    types = [_object(modifier).get("type") for modifier in modifiers]
    if ORDER_MODIFIER in types:
        _list(_object(modifiers[types.index(ORDER_MODIFIER)])["orders"]).extend(positions)
    else:
        limit_at = next((i for i, t in enumerate(types) if t in LIMIT_MODIFIERS), len(types))
        modifiers.insert(limit_at, {"type": ORDER_MODIFIER, "orders": positions})
    return completed


def _object(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        raise TypeError(f"expected a JSON object in the query tree, got {type(value).__name__}")
    return value


def _list(value: JsonValue) -> list[JsonValue]:
    if not isinstance(value, list):
        raise TypeError(f"expected a JSON list in the query tree, got {type(value).__name__}")
    return value


def _position(position: int) -> JsonValue:
    return {
        "type": "ASCENDING",
        "null_order": "NULLS_LAST",
        "expression": {
            "class": "CONSTANT",
            "type": "VALUE_CONSTANT",
            "alias": "",
            "query_location": 0,
            "value": {
                "type": {"id": "INTEGER", "type_info": None},
                "is_null": False,
                "value": position,
            },
        },
    }
