import re

import pyarrow as pa
import pydantic
import pytest

from data_concierge.core.errors import CatalogError, QueryValidationError
from data_concierge.core.types import (
    Column,
    ColumnKind,
    ColumnRole,
    ColumnType,
    TableSchema,
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
    assert ColumnType.from_sql_server(sql_type) == ColumnType.parse(expected)


@pytest.mark.parametrize("sql_type", ["xml", "uniqueidentifier", "decimal"])
def test_sql_server_type_without_equivalent_is_rejected(sql_type: str) -> None:
    with pytest.raises(CatalogError, match=sql_type):
        ColumnType.from_sql_server(sql_type)


def test_table_schema_rejects_duplicate_names_ignoring_case() -> None:
    column_type = ColumnType.parse("int")
    with pytest.raises(pydantic.ValidationError, match="SubTotal, subtotal"):
        TableSchema(
            columns=(
                Column(name="SubTotal", type=column_type, role=ColumnRole.MEASURE),
                Column(name="subtotal", type=column_type, role=ColumnRole.MEASURE),
            )
        )


def test_column_type_reads_and_writes_its_text_form() -> None:
    column = Column.model_validate({"name": "SubTotal", "type": "decimal(19,4)", "role": "measure"})

    assert column.type == ColumnType.decimal(19, 4)
    assert column.model_dump(mode="json")["type"] == "decimal(19,4)"
    assert Column.model_validate(column.model_dump()) == column


def test_column_type_text_error_names_the_type() -> None:
    with pytest.raises(pydantic.ValidationError, match="unknown column type 'money'"):
        Column.model_validate({"name": "Amount", "type": "money", "role": "measure"})


def test_parse_reports_decimal_bounds_without_pydantic_details() -> None:
    with pytest.raises(CatalogError) as error:
        ColumnType.parse("decimal(4,6)")

    assert str(error.value) == (
        "invalid type 'decimal(4,6)': needs 1 <= precision <= 38 and 0 <= scale <= precision"
    )


@pytest.mark.parametrize(
    ("duckdb_type", "expected"),
    [
        ("DECIMAL(38,4)", "decimal(38,4)"),
        ("HUGEINT", "int"),
        ("INTEGER", "int"),
        ("BIGINT", "int"),
        ("VARCHAR", "string"),
        ("DATE", "date"),
        ("TIMESTAMP", "datetime"),
        ("BOOLEAN", "bool"),
    ],
)
def test_from_duckdb_maps_result_types(duckdb_type: str, expected: str) -> None:
    assert str(ColumnType.from_duckdb(duckdb_type)) == expected


@pytest.mark.parametrize(
    "duckdb_type",
    ["DOUBLE", "FLOAT", "INTERVAL", "VARCHAR[]", "STRUCT(a INTEGER)", "TIMESTAMP WITH TIME ZONE"],
)
def test_from_duckdb_rejects_types_without_catalog_equivalent(duckdb_type: str) -> None:
    with pytest.raises(QueryValidationError, match="no catalog equivalent"):
        ColumnType.from_duckdb(duckdb_type)
