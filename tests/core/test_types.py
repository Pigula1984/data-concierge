import re

import pyarrow as pa
import pytest

from data_concierge.core.errors import CatalogError, SpecValidationError
from data_concierge.core.types import (
    Column,
    ColumnKind,
    ColumnRole,
    ColumnType,
    TableSchema,
    parse_sql_server_type,
)


def test_parse_decimal_keeps_precision_and_scale() -> None:
    column_type = ColumnType.parse("decimal(19,4)")

    assert column_type.kind is ColumnKind.DECIMAL
    assert column_type.precision == 19
    assert column_type.scale == 4
    assert column_type.arrow_type == pa.decimal128(19, 4)


@pytest.mark.parametrize(
    ("text", "arrow_type"),
    [
        ("int", pa.int64()),
        ("string", pa.string()),
        ("date", pa.date32()),
        ("datetime", pa.timestamp("us")),
        ("bool", pa.bool_()),
    ],
)
def test_parse_simple_types_map_to_arrow(text: str, arrow_type: pa.DataType) -> None:
    assert ColumnType.parse(text).arrow_type == arrow_type


@pytest.mark.parametrize("text", ["int", "string", "date", "datetime", "bool", "decimal(19,4)"])
def test_column_type_string_round_trips(text: str) -> None:
    assert str(ColumnType.parse(text)) == text


def test_parse_tolerates_spaces_and_case() -> None:
    assert ColumnType.parse(" Decimal( 38 , 6 ) ") == ColumnType.parse("decimal(38,6)")


@pytest.mark.parametrize(
    "text", ["decimal(19)", "money", "decimal", "decimal(4,6)", "decimal(39,2)"]
)
def test_parse_rejects_invalid_catalog_type(text: str) -> None:
    with pytest.raises(CatalogError, match=re.escape(text)):
        ColumnType.parse(text)


@pytest.mark.parametrize(
    ("sql_type", "expected"),
    [
        ("money", "decimal(19,4)"),
        ("smallmoney", "decimal(10,4)"),
        ("tinyint", "int"),
        ("smallint", "int"),
        ("int", "int"),
        ("bigint", "int"),
        ("char(3)", "string"),
        ("nchar(3)", "string"),
        ("varchar(50)", "string"),
        ("nvarchar(50)", "string"),
        ("nvarchar(max)", "string"),
        ("bit", "bool"),
        ("date", "date"),
        ("datetime", "datetime"),
        ("datetime2(7)", "datetime"),
        ("decimal(19,4)", "decimal(19,4)"),
        ("numeric(38,6)", "decimal(38,6)"),
        ("MONEY", "decimal(19,4)"),
    ],
)
def test_sql_server_type_equivalence(sql_type: str, expected: str) -> None:
    assert parse_sql_server_type(sql_type) == ColumnType.parse(expected)


@pytest.mark.parametrize("sql_type", ["xml", "uniqueidentifier", "decimal"])
def test_sql_server_type_without_equivalent_is_rejected(sql_type: str) -> None:
    with pytest.raises(CatalogError, match=sql_type):
        parse_sql_server_type(sql_type)


def _orders_schema() -> TableSchema:
    return TableSchema(
        columns=(
            Column(name="OrderDate", type=ColumnType.parse("date"), role=ColumnRole.TIME),
            Column(name="Territory", type=ColumnType.parse("string"), role=ColumnRole.DIMENSION),
            Column(
                name="SubTotal", type=ColumnType.parse("decimal(19,4)"), role=ColumnRole.MEASURE
            ),
            Column(
                name="TotalDue", type=ColumnType.parse("decimal(19,4)"), role=ColumnRole.MEASURE
            ),
        )
    )


def test_require_returns_column_with_matching_role() -> None:
    column = _orders_schema().require("SubTotal", role=ColumnRole.MEASURE)

    assert column.name == "SubTotal"
    assert column.role is ColumnRole.MEASURE


def test_require_with_wrong_role_lists_available_columns_of_that_role() -> None:
    with pytest.raises(SpecValidationError, match="Territory") as error:
        _orders_schema().require("Territory", role=ColumnRole.MEASURE)

    assert "SubTotal, TotalDue" in str(error.value)


def test_require_unknown_column_lists_available_columns() -> None:
    with pytest.raises(SpecValidationError, match="Discount") as error:
        _orders_schema().require("Discount")

    assert "OrderDate, Territory, SubTotal, TotalDue" in str(error.value)


def test_with_column_returns_new_schema_and_keeps_original() -> None:
    original = _orders_schema()

    extended = original.with_column("month", ColumnType.parse("date"), ColumnRole.TIME)

    assert extended.names == ("OrderDate", "Territory", "SubTotal", "TotalDue", "month")
    assert original.names == ("OrderDate", "Territory", "SubTotal", "TotalDue")


def test_with_column_rejects_existing_name() -> None:
    with pytest.raises(SpecValidationError, match="SubTotal"):
        _orders_schema().with_column("SubTotal", ColumnType.parse("int"), ColumnRole.MEASURE)
