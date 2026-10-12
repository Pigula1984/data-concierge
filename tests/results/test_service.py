from datetime import date, timedelta
from decimal import Decimal

import pyarrow as pa

from data_concierge.core.types import Column, ColumnRole, ColumnType, TableSchema
from data_concierge.results.models import (
    ColumnStats,
    ProcedureLineage,
    QueryRecord,
    ResultTable,
    TransformLineage,
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


def _service() -> tuple[ResultService, DuckDBResultStore]:
    store = DuckDBResultStore()
    return ResultService(store), store


def test_small_result_is_previewed_in_full() -> None:
    service, _ = _service()

    summary = service.summarize(service.save(_orders(14)))

    assert summary.row_count == 14
    assert len(summary.preview) == 14


def test_large_result_preview_is_limited_to_ten_rows() -> None:
    service, _ = _service()

    summary = service.summarize(service.save(_orders(200)))

    assert summary.row_count == 200
    assert len(summary.preview) == 10
    assert summary.preview[0] == ("2024-01-01", "T00", "0.5000")


def test_time_and_measure_columns_get_min_and_max() -> None:
    service, _ = _service()

    stats = service.summarize(service.save(_orders(3))).stats

    assert stats["OrderDate"] == ColumnStats(min="2024-01-01", max="2024-01-03")
    assert stats["SubTotal"] == ColumnStats(min="0.5000", max="2.5000")


def test_dimension_lists_values_up_to_twenty_and_counts_beyond() -> None:
    service, _ = _service()

    few = service.summarize(service.save(_orders(40, territories=5))).stats["Territory"]
    many = service.summarize(service.save(_orders(40, territories=30))).stats["Territory"]

    assert few == ColumnStats(values=("T00", "T01", "T02", "T03", "T04"))
    assert many == ColumnStats(distinct_count=30)


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
    derived = store.materialize(
        f'SELECT * FROM "{source}" ORDER BY "OrderDate"',
        [],
        ORDERS_SCHEMA,
        TransformLineage(
            source=source, spec={"steps": []}, queries=(QueryRecord(sql="SELECT ..."),)
        ),
    )

    summary = service.summarize(derived)
    chain = service.lineage_chain(derived)

    assert summary.truncated is True
    assert service.export(derived, max_rows=100).truncated is True
    assert [handle for handle, _ in chain] == [source, derived]
