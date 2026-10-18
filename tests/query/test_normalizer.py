"""QueryNormalizer: full ORDER BY in the query tree and BIGINT for integer columns."""

import pytest

from data_concierge.query.normalizer import QueryNormalizer
from data_concierge.query.parser import DuckDBSqlParser
from data_concierge.results.models import DescribedColumn


def _normalize(sql: str, types: list[str]) -> str:
    parser = DuckDBSqlParser()
    columns = [DescribedColumn(f"c{i}", t) for i, t in enumerate(types, start=1)]
    return QueryNormalizer(parser).normalize(parser.parse(sql), columns)


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT a, b FROM r1", "SELECT a, b FROM r1 ORDER BY 1 ASC NULLS LAST, 2 ASC NULLS LAST"),
        (
            "SELECT a, b FROM r1 ORDER BY b DESC LIMIT 3",
            "SELECT a, b FROM r1 ORDER BY b DESC, 1 ASC NULLS LAST, 2 ASC NULLS LAST LIMIT 3",
        ),
        (
            "SELECT a, b FROM r1 UNION SELECT a, b FROM r2 LIMIT 3",
            "(SELECT a, b FROM r1) UNION (SELECT a, b FROM r2) "
            "ORDER BY 1 ASC NULLS LAST, 2 ASC NULLS LAST LIMIT 3",
        ),
    ],
)
def test_order_by_ends_with_every_column_position_before_limit(sql: str, expected: str) -> None:
    assert _normalize(sql, ["VARCHAR", "VARCHAR"]) == expected


def test_integer_columns_other_than_bigint_are_cast_in_an_outer_select() -> None:
    executed = _normalize("SELECT a, b, c FROM r1", ["HUGEINT", "BIGINT", "INTEGER"])

    assert executed == (
        'SELECT * REPLACE (CAST("c1" AS BIGINT) AS "c1", CAST("c3" AS BIGINT) AS "c3") '
        "FROM (SELECT a, b, c FROM r1 ORDER BY 1 ASC NULLS LAST, 2 ASC NULLS LAST, "
        "3 ASC NULLS LAST) AS result"
    )
