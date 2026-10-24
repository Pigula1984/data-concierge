from datetime import date, timedelta
from decimal import Decimal

import pyarrow as pa

from data_concierge.core.settings import ModelExposure
from data_concierge.core.types import Column, ColumnRole, ColumnType, TableSchema
from data_concierge.results.models import (
    ColumnStats,
    ProcedureLineage,
    QueryLineage,
    ResultSummary,
    ResultTable,
)
from data_concierge.results.repository import DuckDBResultStore
from data_concierge.results.service import ResultService

ORDERS_SCHEMA = TableSchema(
    columns=(
        Column(name="OrderDate", type=ColumnType.parse("date"), role=ColumnRole.TIME),
        Column(name="Territory", type=ColumnType.parse("string"), role=ColumnRole.DIMENSION),
        Column(name="SubTotal", type=ColumnType.decimal(19, 4), role=ColumnRole.MEASURE),
    )
)


def _orders(count: int, territories: int = 5, truncated: bool = False) -> ResultTable:
    data = pa.table(
        {
            "OrderDate": pa.array(
                [date(2024, 1, 1) + timedelta(days=i % 366) for i in range(count)],
                type=pa.date32(),
            ),
            "Territory": pa.array([f"T{i % territories:02d}" for i in range(count)]),
            "SubTotal": pa.array(
                [Decimal(i) + Decimal("0.5000") for i in range(count)],
                type=pa.decimal128(19, 4),
            ),
        }
    )
    lineage = ProcedureLineage(
        procedure="api.SalesOrder_List", params={"DateFrom": "2024-01-01"}, truncated=truncated
    )
    return ResultTable(data=data, schema=ORDERS_SCHEMA, lineage=lineage)


NONE = ModelExposure.for_mode("none")
MINIMAL = ModelExposure.for_mode("minimal")


def _service(exposure: ModelExposure = NONE) -> tuple[ResultService, DuckDBResultStore]:
    store = DuckDBResultStore()
    return ResultService(store, exposure), store


CUSTOMER_ORDERS_SCHEMA = TableSchema(
    columns=(
        Column(name="CustomerID", type=ColumnType.parse("int"), role=ColumnRole.ID),
        Column(
            name="AccountNumber",
            type=ColumnType.parse("string"),
            role=ColumnRole.ID,
            sensitive=True,
        ),
        Column(name="OrderDate", type=ColumnType.parse("date"), role=ColumnRole.TIME),
        Column(
            name="CustomerName",
            type=ColumnType.parse("string"),
            role=ColumnRole.DIMENSION,
            sensitive=True,
        ),
        Column(name="CustomerType", type=ColumnType.parse("string"), role=ColumnRole.DIMENSION),
        Column(name="SubTotal", type=ColumnType.decimal(19, 4), role=ColumnRole.MEASURE),
    )
)


def _customer_orders(count: int, types: int = 2) -> ResultTable:
    data = pa.table(
        {
            "CustomerID": pa.array([11000 + 7 * i for i in range(count)], type=pa.int64()),
            "AccountNumber": pa.array([f"AW{11000 + 7 * i:08d}" for i in range(count)]),
            "OrderDate": pa.array(
                [date(2024, 3, 1) - timedelta(days=i) for i in range(count)], type=pa.date32()
            ),
            "CustomerName": pa.array([f"Customer {i}" for i in range(count)], type=pa.string()),
            "CustomerType": pa.array([f"type {i % types:02d}" for i in range(count)]),
            "SubTotal": pa.array(
                [Decimal(i) + Decimal("0.2500") for i in range(count)], type=pa.decimal128(19, 4)
            ),
        },
        schema=pa.schema(CUSTOMER_ORDERS_SCHEMA.arrow_fields),
    )
    lineage = ProcedureLineage(procedure="api.Test", params={}, truncated=False)
    return ResultTable(data=data, schema=CUSTOMER_ORDERS_SCHEMA, lineage=lineage)


def test_summary_in_mode_none_has_structure_dates_and_ids_only() -> None:
    service, _ = _service(NONE)

    summary = service.summarize(service.save(_customer_orders(3)))

    assert summary.row_count == 3
    assert [column.name for column in summary.columns] == list(CUSTOMER_ORDERS_SCHEMA.names)
    assert summary.stats == {"OrderDate": ColumnStats(min="2024-02-28", max="2024-03-01")}
    assert summary.ids == {"CustomerID": (11000, 11007, 11014)}
    assert "preview" not in ResultSummary.model_fields


def test_summary_in_mode_minimal_adds_values_of_dimensions_that_are_not_sensitive() -> None:
    service, _ = _service(MINIMAL)

    stats = service.summarize(service.save(_customer_orders(3))).stats

    assert stats == {
        "OrderDate": ColumnStats(min="2024-02-28", max="2024-03-01"),
        "CustomerType": ColumnStats(values=("type 00", "type 01")),
    }


def test_dimension_with_more_than_twenty_values_has_only_their_count() -> None:
    service, _ = _service(MINIMAL)

    stats = service.summarize(service.save(_customer_orders(30, types=25))).stats

    assert stats["CustomerType"] == ColumnStats(distinct_count=25)


def test_ids_are_listed_in_row_order_up_to_the_model_row_limit() -> None:
    service, _ = _service()

    small = service.summarize(service.save(_customer_orders(50)))
    large = service.summarize(service.save(_customer_orders(51)))

    assert small.ids == {"CustomerID": tuple(11000 + 7 * i for i in range(50))}
    assert large.ids == {}


def test_empty_result_has_no_ids_and_no_dates() -> None:
    service, _ = _service()

    summary = service.summarize(service.save(_customer_orders(0)))

    assert summary.row_count == 0
    assert summary.ids == {"CustomerID": ()}
    assert summary.stats == {"OrderDate": ColumnStats(min=None, max=None)}


def test_export_cuts_at_max_rows_and_flags_truncation() -> None:
    service, _ = _service()
    handle = service.save(_orders(10_001))

    exported = service.export(handle, max_rows=10_000)

    assert len(exported.rows) == 10_000
    assert exported.row_count == 10_001
    assert exported.truncated is True


def test_truncation_of_a_procedure_result_is_inherited_by_derived_handles() -> None:
    service, store = _service()
    source = service.save(_orders(5, truncated=True))
    other = service.save(_orders(3))
    sql = f'SELECT * FROM "{source}" UNION ALL SELECT * FROM "{other}"'
    derived = store.materialize(
        sql, ORDERS_SCHEMA, QueryLineage(sources=(source, other), submitted_sql=sql, sql=sql)
    )

    summary = service.summarize(derived)

    assert summary.truncated is True
    assert service.export(derived, max_rows=100).truncated is True
    assert service.is_truncated(other) is False
    assert [stored.handle for stored in service.lineage(derived)] == [source, other, derived]
