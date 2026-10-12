from typing import Any

import pytest

from data_concierge.core.errors import SpecValidationError
from data_concierge.core.types import Column, ColumnRole, ColumnType, TableSchema
from data_concierge.results.models import ColumnStats
from data_concierge.transform.spec import TransformSpec
from data_concierge.transform.validator import SpecValidator, ValidatedSpec

ORDERS = TableSchema(
    columns=(
        Column(name="SalesOrderID", type=ColumnType.parse("int"), role=ColumnRole.ID),
        Column(name="OrderDate", type=ColumnType.parse("date"), role=ColumnRole.TIME),
        Column(name="CustomerName", type=ColumnType.parse("string"), role=ColumnRole.DIMENSION),
        Column(name="Territory", type=ColumnType.parse("string"), role=ColumnRole.DIMENSION),
        Column(name="SubTotal", type=ColumnType.decimal(19, 4), role=ColumnRole.MEASURE),
        Column(name="TotalDue", type=ColumnType.decimal(19, 4), role=ColumnRole.MEASURE),
    )
)

MONTH = {
    "op": "derive",
    "name": "month",
    "fn": "date_trunc",
    "unit": "month",
    "column": "OrderDate",
}
MONTHLY_SALES = {
    "op": "aggregate",
    "group_by": ["month"],
    "measures": [
        {"name": "net_sales", "fn": "sum", "column": "SubTotal"},
        {"name": "order_count", "fn": "count_distinct", "column": "SalesOrderID"},
    ],
}


def _validate(
    *steps: dict[str, Any],
    facts: tuple[dict[str, Any], ...] = (),
    stats: dict[str, ColumnStats] | None = None,
) -> ValidatedSpec:
    spec = TransformSpec.model_validate({"source": "r1", "steps": list(steps), "facts": facts})
    return SpecValidator().validate(spec, ORDERS, stats)


def _types(schema: TableSchema) -> list[tuple[str, str]]:
    return [(column.name, str(column.type)) for column in schema.columns]


def test_monthly_report_from_the_architecture_validates() -> None:
    validated = _validate(
        MONTH,
        MONTHLY_SALES,
        {
            "op": "fill_periods",
            "column": "month",
            "unit": "month",
            "from": "2024-01-01",
            "to": "2025-01-01",
            "fill": {"net_sales": 0, "order_count": 0},
        },
        {"op": "sort", "by": [{"column": "month", "dir": "asc"}]},
        facts=(
            {"name": "annual_total", "fn": "sum", "column": "net_sales"},
            {"name": "best_month", "fn": "argmax", "column": "net_sales", "return": "month"},
        ),
    )

    assert _types(validated.output) == [
        ("month", "date"),
        ("net_sales", "decimal(38,4)"),
        ("order_count", "int"),
    ]
    assert {name: str(t) for name, t in validated.fact_types.items()} == {
        "annual_total": "decimal(38,4)",
        "best_month": "date",
    }


def test_sum_of_text_column_names_step_and_lists_measures() -> None:
    step = {
        "op": "aggregate",
        "measures": [{"name": "total", "fn": "sum", "column": "CustomerName"}],
    }

    with pytest.raises(SpecValidationError) as error:
        _validate(step)

    message = str(error.value)
    assert message.startswith("step 1 (aggregate):")
    assert "'CustomerName'" in message
    assert "SubTotal, TotalDue" in message


def test_date_trunc_on_measure_needs_a_time_column() -> None:
    step = {"op": "derive", "name": "m", "fn": "date_trunc", "unit": "month", "column": "SubTotal"}

    with pytest.raises(SpecValidationError, match="expected 'time'"):
        _validate(step)


def test_column_is_usable_only_after_the_step_that_creates_it() -> None:
    with pytest.raises(SpecValidationError, match=r"step 1 .*'month' does not exist"):
        _validate(MONTHLY_SALES, MONTH)

    assert _validate(MONTH, MONTHLY_SALES).output.names == ("month", "net_sales", "order_count")


def test_avg_is_a_decimal_with_scale_six() -> None:
    step = {
        "op": "aggregate",
        "measures": [{"name": "avg_order", "fn": "avg", "column": "SubTotal"}],
    }

    assert _types(_validate(step).output) == [("avg_order", "decimal(38,6)")]


def test_pivot_over_a_column_with_too_many_values_is_rejected_early() -> None:
    pivot = {"op": "pivot", "rows": "OrderDate", "columns": "CustomerName", "measure": "SubTotal"}

    with pytest.raises(SpecValidationError, match="300 values, more than the limit of 50"):
        _validate(pivot, stats={"CustomerName": ColumnStats(distinct_count=300)})


def test_step_after_pivot_is_rejected() -> None:
    pivot = {"op": "pivot", "rows": "month", "columns": "Territory", "measure": "SubTotal"}
    after = {"op": "aggregate", "measures": [{"name": "n", "fn": "count", "column": "month"}]}

    with pytest.raises(SpecValidationError, match=r"step 3 .*pivot must be the last step"):
        _validate(MONTH, pivot, after)


def test_sort_by_rows_column_and_limit_may_follow_pivot() -> None:
    pivot = {"op": "pivot", "rows": "month", "columns": "Territory", "measure": "SubTotal"}

    validated = _validate(
        MONTH, pivot, {"op": "sort", "by": [{"column": "month"}]}, {"op": "limit", "n": 5}
    )

    assert validated.output.names == ("month",)
    assert validated.pivot is not None


def test_fact_cannot_use_a_pivot_column() -> None:
    pivot = {"op": "pivot", "rows": "month", "columns": "Territory", "measure": "SubTotal"}

    with pytest.raises(SpecValidationError, match="facts cannot use pivot columns"):
        _validate(MONTH, pivot, facts=({"name": "total", "fn": "sum", "column": "Northwest"},))


def test_argmax_return_column_must_exist() -> None:
    fact = {"name": "best", "fn": "argmax", "column": "SubTotal", "return": "Region"}

    with pytest.raises(SpecValidationError, match="fact 'best': column 'Region' does not exist"):
        _validate(facts=(fact,))


def test_alias_differing_only_in_case_from_an_existing_column_is_rejected() -> None:
    step = {
        "op": "derive",
        "name": "subtotal",
        "fn": "arithmetic",
        "operator": "*",
        "left": "SubTotal",
        "right": "SubTotal",
    }

    with pytest.raises(SpecValidationError, match="clashes with existing column 'SubTotal'"):
        _validate(step)


def test_arithmetic_result_types() -> None:
    def derive(name: str, operator: str) -> dict[str, Any]:
        return {
            "op": "derive",
            "name": name,
            "fn": "arithmetic",
            "operator": operator,
            "left": "TotalDue",
            "right": "SubTotal",
        }

    validated = _validate(derive("extra", "-"), derive("product", "*"), derive("ratio", "/"))

    assert _types(validated.output)[-3:] == [
        ("extra", "decimal(38,4)"),
        ("product", "decimal(38,8)"),
        ("ratio", "decimal(38,6)"),
    ]


def test_fill_periods_needs_the_period_as_the_only_non_measure_column() -> None:
    fill = {
        "op": "fill_periods",
        "column": "month",
        "unit": "month",
        "from": "2024-01-01",
        "to": "2025-01-01",
    }
    by_month_and_territory = {
        "op": "aggregate",
        "group_by": ["month", "Territory"],
        "measures": [{"name": "net_sales", "fn": "sum", "column": "SubTotal"}],
    }

    with pytest.raises(SpecValidationError, match="also found: Territory"):
        _validate(MONTH, by_month_and_territory, fill)


def test_select_renames_and_keeps_types() -> None:
    step = {
        "op": "select",
        "columns": [{"column": "CustomerName", "alias": "customer"}, {"column": "SubTotal"}],
    }

    assert _types(_validate(step).output) == [("customer", "string"), ("SubTotal", "decimal(19,4)")]


PIVOT_MONTH_BY_TERRITORY = {
    "op": "pivot",
    "rows": "month",
    "columns": "Territory",
    "measure": "SubTotal",
}


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        (
            [{"op": "sort", "by": [{"column": "Region"}]}],
            r"step 1 \(sort\): column 'Region' does not exist",
        ),
        (
            [{"op": "select", "columns": [{"column": "Region"}]}],
            r"step 1 \(select\): column 'Region' does not exist",
        ),
        (
            [
                {
                    "op": "select",
                    "columns": [
                        {"column": "SubTotal", "alias": "territory"},
                        {"column": "Territory"},
                    ],
                }
            ],
            r"step 1 \(select\): .*clashes with existing column 'territory'",
        ),
        (
            [MONTH, PIVOT_MONTH_BY_TERRITORY, {"op": "sort", "by": [{"column": "Northwest"}]}],
            r"step 3 \(sort\): pivot must be the last step",
        ),
    ],
)
def test_sort_and_select_reject_incorrect_use(steps: list[dict[str, Any]], expected: str) -> None:
    with pytest.raises(SpecValidationError, match=expected):
        _validate(*steps)
