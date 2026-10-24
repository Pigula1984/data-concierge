"""Synthetic orders and customers in a real locked-down DuckDB session.

Tests run in importlib mode and cannot import this module, so data and helpers reach them
through fixtures typed with the application's own classes.
"""

from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal

import pyarrow as pa
import pytest

from data_concierge.core.types import Column, ColumnRole, ColumnType, TableSchema
from data_concierge.query.policy import QueryPolicy
from data_concierge.query.service import QueryService
from data_concierge.results.models import Handle, ProcedureLineage, ResultTable
from data_concierge.results.repository import DuckDBResultStore
from data_concierge.results.service import ResultService

type Order = tuple[int, date, int, str, Decimal]
type Customer = tuple[int, str, str]
type SaveRows = Callable[..., Handle]

ORDERS_SCHEMA = TableSchema(
    columns=(
        Column(name="SalesOrderID", type=ColumnType.parse("int"), role=ColumnRole.ID),
        Column(name="OrderDate", type=ColumnType.parse("date"), role=ColumnRole.TIME),
        Column(name="CustomerID", type=ColumnType.parse("int"), role=ColumnRole.ID),
        Column(name="Territory", type=ColumnType.parse("string"), role=ColumnRole.DIMENSION),
        Column(name="SubTotal", type=ColumnType.decimal(19, 4), role=ColumnRole.MEASURE),
    )
)
CUSTOMERS_SCHEMA = TableSchema(
    columns=(
        Column(name="CustomerID", type=ColumnType.parse("int"), role=ColumnRole.ID),
        Column(
            name="CustomerName",
            type=ColumnType.parse("string"),
            role=ColumnRole.DIMENSION,
            sensitive=True,
        ),
        Column(name="City", type=ColumnType.parse("string"), role=ColumnRole.DIMENSION),
    )
)
TERRITORIES = ("Northwest", "Canada", "France")


def _orders_2024() -> list[Order]:
    """Orders in every month of 2024 except February and December."""
    orders: list[Order] = []
    for month in (1, 3, 4, 5, 6, 7, 8, 9, 10, 11):
        for day in (3, 17, 28):
            number = len(orders) + 1
            amount = Decimal(number * 1013 % 9973) + Decimal("0.0125") * number
            customer = number % 7 + 1
            orders.append(
                (number, date(2024, month, day), customer, TERRITORIES[number % 3], amount)
            )
    return orders


def _customers() -> list[Customer]:
    """Seven customers; Bothell has customers 2, 4 and 6."""
    return [(i, f"Customer {i}", "Bothell" if i % 2 == 0 else "Seattle") for i in range(1, 8)]


def _table(schema: TableSchema, rows: Sequence[tuple[object, ...]], truncated: bool) -> ResultTable:
    columns = list(zip(*rows, strict=True))
    data = pa.table(
        {
            column.name: pa.array(values, type=column.type.arrow_type)
            for column, values in zip(schema.columns, columns, strict=True)
        }
    )
    lineage = ProcedureLineage(procedure="api.Test", params={}, truncated=truncated)
    return ResultTable(data=data, schema=schema, lineage=lineage)


def _services(
    max_query_rows: int = 100_000, timeout: float = 30.0
) -> tuple[ResultService, QueryService]:
    store = DuckDBResultStore(query_timeout_seconds=timeout, max_query_rows=max_query_rows)
    results = ResultService(store)
    return results, QueryService(results, QueryPolicy())


@pytest.fixture
def new_services() -> Callable[..., tuple[ResultService, QueryService]]:
    """Builds another session, e.g. with lower limits or to reproduce a result."""
    return _services


@pytest.fixture
def services(request: pytest.FixtureRequest) -> tuple[ResultService, QueryService]:
    """The session under test; indirect parametrization passes other limits as keywords."""
    return _services(**getattr(request, "param", {}))


@pytest.fixture
def results(services: tuple[ResultService, QueryService]) -> ResultService:
    return services[0]


@pytest.fixture
def queries(services: tuple[ResultService, QueryService]) -> QueryService:
    return services[1]


def _orders_table(rows: Sequence[Order] | None = None, truncated: bool = False) -> ResultTable:
    return _table(ORDERS_SCHEMA, rows or _orders_2024(), truncated)


def _customers_table(
    rows: Sequence[Customer] | None = None, truncated: bool = False
) -> ResultTable:
    return _table(CUSTOMERS_SCHEMA, rows or _customers(), truncated)


@pytest.fixture
def orders_table() -> Callable[..., ResultTable]:
    """Orders (by default all of 2024) as a procedure result."""
    return _orders_table


@pytest.fixture
def customers_table() -> Callable[..., ResultTable]:
    """Customers (by default all seven) as a procedure result."""
    return _customers_table


@pytest.fixture
def save_orders(results: ResultService) -> SaveRows:
    """Saves orders (by default all of 2024) and returns their handle."""
    return lambda rows=None, truncated=False: results.save(_orders_table(rows, truncated))


@pytest.fixture
def save_customers(results: ResultService) -> SaveRows:
    """Saves customers (by default all seven) and returns their handle."""
    return lambda rows=None, truncated=False: results.save(_customers_table(rows, truncated))


@pytest.fixture
def sales(save_orders: SaveRows, save_customers: SaveRows) -> None:
    """The orders of 2024 in r1 and the customers in r2."""
    save_orders()
    save_customers()


@pytest.fixture
def orders() -> list[Order]:
    return _orders_2024()


@pytest.fixture
def customer_rows() -> list[Customer]:
    return _customers()
