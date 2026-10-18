"""DuckDBSqlParser and QueryTreeWalker: what the rules see of a query."""

import copy

import pytest

from data_concierge.core.errors import QueryValidationError
from data_concierge.query.models import NodeKind, QueryNode
from data_concierge.query.parser import DuckDBSqlParser, QueryTreeWalker


def _nodes(sql: str, kind: NodeKind) -> list[QueryNode]:
    return [node for node in DuckDBSqlParser().parse(sql).nodes if node.kind is kind]


def test_interval_literal_is_one_function_without_its_double_cast() -> None:
    sql = "SELECT * FROM range(DATE '2024-01-01', DATE '2025-01-01', INTERVAL 1 MONTH)"

    functions = [node.name for node in _nodes(sql, NodeKind.FUNCTION)]
    casts = [node.name for node in _nodes(sql, NodeKind.CAST)]

    assert functions == ["to_months"]
    assert casts == ["DATE", "DATE"]


def test_cte_is_visible_only_after_its_definition() -> None:
    sql = (
        "WITH first AS (SELECT * FROM second), second AS (SELECT * FROM r1) "
        "SELECT * FROM first JOIN second USING (a)"
    )

    tables = [(node.name, node.cte) for node in _nodes(sql, NodeKind.TABLE)]

    assert tables == [("second", False), ("r1", False), ("first", True), ("second", True)]


def test_function_names_are_lowercase_and_nesting_is_recorded() -> None:
    nodes = _nodes('SELECT SUM("a" / 2) AS s FROM r1', NodeKind.FUNCTION)

    assert [(node.name, node.enclosing) for node in nodes] == [("sum", ()), ("/", ("sum",))]


def test_unknown_field_in_the_tree_is_an_unsupported_node() -> None:
    tree = copy.deepcopy(DuckDBSqlParser().parse('SELECT "a" AS b FROM r1').tree)
    statements = tree["statements"]
    assert isinstance(statements, list)
    assert isinstance(statements[0], dict)
    root = statements[0]["node"]
    assert isinstance(root, dict)
    root["new_field"] = None

    nodes = QueryTreeWalker().walk(tree)

    assert [n.name for n in nodes if n.kind is NodeKind.UNSUPPORTED] == [
        "SELECT_NODE with fields new_field"
    ]


def test_missing_field_in_the_tree_is_refused_as_rule_2(monkeypatch: pytest.MonkeyPatch) -> None:
    def walk_changed_format(self: QueryTreeWalker, tree: object) -> tuple[QueryNode, ...]:
        raise KeyError("select_list")

    monkeypatch.setattr(QueryTreeWalker, "walk", walk_changed_format)

    with pytest.raises(QueryValidationError, match="Rule 2: unsupported query structure"):
        DuckDBSqlParser().parse("SELECT 1 AS a")


def test_syntax_error_returns_the_duckdb_message() -> None:
    with pytest.raises(QueryValidationError, match='syntax error at or near "SELEC"'):
        DuckDBSqlParser().parse("SELEC 1")


def test_render_gives_back_equivalent_sql() -> None:
    parser = DuckDBSqlParser()
    parsed = parser.parse("SELECT \"Territory\" AS t FROM r1 WHERE \"City\" = 'Margie''s'")

    assert parser.render(parsed.tree) == "SELECT Territory AS t FROM r1 WHERE (City = 'Margie''s')"
