from typing import Any

import pydantic
import pytest

from data_concierge.transform.spec import TransformSpec


def _spec(*steps: dict[str, Any], facts: tuple[dict[str, Any], ...] = ()) -> TransformSpec:
    return TransformSpec.model_validate({"source": "r1", "steps": list(steps), "facts": facts})


def test_unknown_operation_lists_the_allowed_ones() -> None:
    with pytest.raises(pydantic.ValidationError) as error:
        _spec({"op": "filter", "column": "CustomerType", "in": ["store"]})

    message = str(error.value)
    assert "'derive', 'aggregate', 'fill_periods', 'pivot', 'sort', 'limit', 'select'" in message


def test_model_given_name_must_match_the_name_pattern() -> None:
    with pytest.raises(pydantic.ValidationError, match=r"\^\[a-z\]\[a-z0-9_\]\*\$"):
        _spec(
            {
                "op": "aggregate",
                "group_by": [],
                "measures": [{"name": "Net Sales", "fn": "sum", "column": "SubTotal"}],
            }
        )


def test_fill_periods_needs_from_before_to() -> None:
    with pytest.raises(pydantic.ValidationError, match="must be before 'to'"):
        _spec(
            {
                "op": "fill_periods",
                "column": "month",
                "unit": "month",
                "from": "2025-01-01",
                "to": "2024-01-01",
            }
        )


def test_fill_periods_needs_from_at_the_start_of_a_period() -> None:
    with pytest.raises(pydantic.ValidationError, match="first day of a month"):
        _spec(
            {
                "op": "fill_periods",
                "column": "month",
                "unit": "month",
                "from": "2024-01-15",
                "to": "2025-01-01",
            }
        )


def test_date_trunc_needs_a_unit() -> None:
    with pytest.raises(pydantic.ValidationError, match="'unit' is required for date_trunc"):
        _spec({"op": "derive", "name": "month", "fn": "date_trunc", "column": "OrderDate"})


def test_arithmetic_needs_both_operands() -> None:
    with pytest.raises(pydantic.ValidationError, match="needs 'operator', 'left' and 'right'"):
        _spec({"op": "derive", "name": "tax", "fn": "arithmetic", "operator": "-", "left": "A"})


def test_argmax_needs_a_return_column() -> None:
    with pytest.raises(pydantic.ValidationError, match="'return' is required for argmax"):
        _spec(facts=({"name": "best", "fn": "argmax", "column": "net_sales"},))


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(pydantic.ValidationError, match="Extra inputs are not permitted"):
        _spec({"op": "limit", "n": 10, "offset": 5})
