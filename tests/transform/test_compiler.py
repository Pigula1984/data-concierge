"""SQL shape produced by the compiler, checked without running it."""

from datetime import date
from decimal import Decimal
from typing import Any

from data_concierge.core.types import Column, ColumnRole, ColumnType, TableSchema
from data_concierge.transform.compiler import TransformCompiler
from data_concierge.transform.spec import TransformSpec
from data_concierge.transform.validator import SpecValidator

ORDERS = TableSchema(
    columns=(
        Column(name="OrderDate", type=ColumnType.parse("date"), role=ColumnRole.TIME),
        Column(name='Odd"Name', type=ColumnType.parse("string"), role=ColumnRole.DIMENSION),
        Column(name="SubTotal", type=ColumnType.decimal(19, 4), role=ColumnRole.MEASURE),
    )
)


def _compile(*steps: dict[str, Any]) -> tuple[str, tuple[object, ...]]:
    spec = TransformSpec.model_validate({"source": "r1", "steps": list(steps)})
    compiled = TransformCompiler().compile(SpecValidator().validate(spec, ORDERS))
    return compiled.sql, compiled.params


def test_without_steps_every_column_orders_the_rows() -> None:
    sql, params = _compile()

    assert sql == 'SELECT * FROM "r1"\nORDER BY "OrderDate" ASC, "Odd""Name" ASC, "SubTotal" ASC'
    assert params == ()


def test_parameters_follow_their_placeholders_in_text_order() -> None:
    sql, params = _compile(
        {
            "op": "derive",
            "name": "month",
            "fn": "date_trunc",
            "unit": "month",
            "column": "OrderDate",
        },
        {
            "op": "aggregate",
            "group_by": ["month"],
            "measures": [{"name": "net_sales", "fn": "sum", "column": "SubTotal"}],
        },
        {
            "op": "fill_periods",
            "column": "month",
            "unit": "month",
            "from": "2024-01-01",
            "to": "2024-04-01",
            "fill": {"net_sales": 0},
        },
        {"op": "sort", "by": [{"column": "net_sales", "dir": "desc"}]},
        {"op": "limit", "n": 2},
    )

    assert sql.count("?") == len(params) == 4
    assert params == (Decimal(0), date(2024, 1, 1), date(2024, 4, 1), 2)
    assert 'ORDER BY "net_sales" DESC, "month" ASC LIMIT ?' in sql
    assert sql.endswith('ORDER BY "net_sales" DESC, "month" ASC')


def test_sort_order_survives_a_rename_in_select() -> None:
    sql, _ = _compile(
        {"op": "sort", "by": [{"column": "SubTotal", "dir": "desc"}]},
        {
            "op": "select",
            "columns": [{"column": "SubTotal", "alias": "amount"}, {"column": "OrderDate"}],
        },
    )

    assert sql.endswith('ORDER BY "amount" DESC, "OrderDate" ASC')
