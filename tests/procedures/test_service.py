from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal

import pyarrow as pa
import pytest

from data_concierge.catalog.models import Catalog, CatalogEntry
from data_concierge.core.context import RequestContext
from data_concierge.core.errors import ParameterValidationError
from data_concierge.procedures.models import ProcedureResult
from data_concierge.procedures.service import ProcedureService
from data_concierge.results.models import ProcedureLineage
from data_concierge.results.repository import DuckDBResultStore
from data_concierge.results.service import ResultService

CONTEXT = RequestContext(user_id=7, role="analyst")
ORDER_ROW = (
    43659,
    date(2024, 1, 5),
    29825,
    "Store A",
    "store",
    "Southeast",
    Decimal("20565.6206"),
    Decimal("23153.2339"),
)


class FakeProcedures:
    """Records calls and returns the given rows typed by the catalog entry."""

    def __init__(self, rows: list[tuple[object, ...]], truncated: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._rows = rows
        self._truncated = truncated

    def execute(self, entry: CatalogEntry, params: Mapping[str, object]) -> ProcedureResult:
        self.calls.append((entry.procedure, dict(params)))
        schema = pa.schema(entry.table_schema.arrow_fields)
        data = pa.Table.from_pylist(
            [dict(zip(schema.names, row, strict=True)) for row in self._rows], schema=schema
        )
        return ProcedureResult(data=data, truncated=self._truncated)


@pytest.fixture
def results() -> ResultService:
    return ResultService(DuckDBResultStore())


@pytest.fixture
def orders(catalog: Catalog) -> CatalogEntry:
    return catalog.entry("api.SalesOrder_List")


def _order_rows() -> list[tuple[object, ...]]:
    return [ORDER_ROW]


def test_call_passes_model_arguments_and_injected_user_id(
    orders: CatalogEntry, results: ResultService
) -> None:
    procedures = FakeProcedures(_order_rows())

    summary = ProcedureService(procedures, results).call(
        orders, {"date_from": "2024-01-01", "date_to": "2025-01-01", "customer_id": None}, CONTEXT
    )

    assert procedures.calls == [
        (
            "api.SalesOrder_List",
            {
                "DateFrom": date(2024, 1, 1),
                "DateTo": date(2025, 1, 1),
                "CustomerID": None,
                "UserId": 7,
            },
        )
    ]
    assert summary.handle == "r1"
    assert summary.row_count == 1
    assert not summary.truncated


def test_optional_argument_may_be_left_out(orders: CatalogEntry, results: ResultService) -> None:
    procedures = FakeProcedures(_order_rows())

    ProcedureService(procedures, results).call(
        orders, {"date_from": "2024-01-01", "date_to": "2025-01-01"}, CONTEXT
    )

    assert procedures.calls[0][1]["CustomerID"] is None


def test_lineage_has_procedure_and_parameters_without_injected_values(
    orders: CatalogEntry, results: ResultService
) -> None:
    summary = ProcedureService(FakeProcedures(_order_rows()), results).call(
        orders, {"date_from": "2024-01-01", "date_to": "2025-01-01", "customer_id": 29825}, CONTEXT
    )

    assert results.describe(summary.handle).lineage == ProcedureLineage(
        procedure="api.SalesOrder_List",
        params={"DateFrom": "2024-01-01", "DateTo": "2025-01-01", "CustomerID": 29825},
        truncated=False,
    )


def test_truncated_procedure_result_is_reported_in_the_summary(
    orders: CatalogEntry, results: ResultService
) -> None:
    procedures = FakeProcedures(_order_rows(), truncated=True)

    summary = ProcedureService(procedures, results).call(
        orders, {"date_from": "2024-01-01", "date_to": "2025-01-01"}, CONTEXT
    )

    assert summary.truncated


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (
            {"date_from": "2024-01-01", "date_to": "2025-01-01", "user_id": 1},
            "user_id: Extra inputs are not permitted",
        ),
        ({"date_from": "2024-13-01", "date_to": "2025-01-01"}, "date_from: Input should be"),
        ({"date_from": "2024-01-01"}, "date_to: Field required"),
    ],
)
def test_invalid_arguments_raise_parameter_validation_error_naming_the_argument(
    orders: CatalogEntry, results: ResultService, args: dict[str, object], message: str
) -> None:
    procedures = FakeProcedures(_order_rows())

    with pytest.raises(ParameterValidationError, match=message):
        ProcedureService(procedures, results).call(orders, args, CONTEXT)

    assert procedures.calls == []


def test_date_only_text_is_accepted_for_a_datetime_parameter(
    catalog: Catalog, results: ResultService
) -> None:
    procedures = FakeProcedures([])

    ProcedureService(procedures, results).call(
        catalog.entry("dbo.uspGetBillOfMaterials"),
        {"start_product_id": 749, "check_date": "2024-01-01"},
        CONTEXT,
    )

    assert procedures.calls[0][1] == {"StartProductID": 749, "CheckDate": datetime(2024, 1, 1)}
