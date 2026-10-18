"""Reference queries over procedure results agree with SQL Server (architecture, 7.4).

Each test loads data through the catalog procedures as agent_reader, runs a `query` in the
session's DuckDB and compares the exported rows (decimals as text, so the scale is compared
too) with the same report computed on the tables with Windows authentication.
"""

from collections.abc import Mapping

import pytest

from data_concierge.catalog.models import Catalog
from data_concierge.core.context import RequestContext
from data_concierge.core.sqlserver import SqlServerClient
from data_concierge.procedures.repository import SqlServerProcedureRepository
from data_concierge.procedures.service import ProcedureService
from data_concierge.query.service import QueryService
from data_concierge.results.models import (
    Handle,
    ProcedureLineage,
    QueryLineage,
    Rows,
)
from data_concierge.results.service import ResultService

pytestmark = pytest.mark.integration

CONTEXT = RequestContext(user_id=1, role="analyst")
YEAR_2024 = {"date_from": "2024-01-01", "date_to": "2025-01-01"}
MAX_ROWS = 10_000


class Session:
    """Procedures and queries of one session, compared against reference SQL."""

    def __init__(
        self,
        procedures: ProcedureService,
        results: ResultService,
        queries: QueryService,
        catalog: Catalog,
        admin: SqlServerClient,
    ) -> None:
        self._procedures = procedures
        self._results = results
        self._queries = queries
        self._catalog = catalog
        self._admin = admin

    def call(self, procedure: str, args: Mapping[str, object]) -> Handle:
        summary = self._procedures.call(self._catalog.entry(procedure), args, CONTEXT)
        assert not summary.truncated
        return summary.handle

    def query(self, sql: str) -> Rows:
        handle = self._queries.run(sql).handle
        return self._results.export(handle, MAX_ROWS).rows

    def reference(self, sql: str) -> Rows:
        rows = self._admin.fetch(sql).rows
        return tuple(tuple(ResultService.to_json_scalar(value) for value in row) for row in rows)


@pytest.fixture
def session(
    agent_client: SqlServerClient,
    admin_client: SqlServerClient,
    catalog: Catalog,
    results: ResultService,
    queries: QueryService,
) -> Session:
    procedures = ProcedureService(SqlServerProcedureRepository(agent_client), results)
    return Session(procedures, results, queries, catalog, admin_client)


def test_monthly_sales_with_distinct_order_count(session: Session) -> None:
    session.call("api.SalesOrder_List", YEAR_2024)

    rows = session.query("""
        WITH months AS (
          SELECT CAST(range AS DATE) AS month
          FROM range(DATE '2024-01-01', DATE '2025-01-01', INTERVAL 1 MONTH)
        ), sales AS (
          SELECT CAST(date_trunc('month', "OrderDate") AS DATE) AS month,
                 sum("SubTotal") AS net_sales,
                 count(DISTINCT "SalesOrderID") AS order_count
          FROM r1
          GROUP BY 1
        )
        SELECT m.month, COALESCE(s.net_sales, 0) AS net_sales,
               COALESCE(s.order_count, 0) AS order_count
        FROM months m LEFT JOIN sales s USING (month)
        ORDER BY m.month
    """)

    assert len(rows) == 12
    assert rows == session.reference("""
        SELECT DATEFROMPARTS(YEAR(OrderDate), MONTH(OrderDate), 1), SUM(SubTotal),
               COUNT(DISTINCT SalesOrderID)
        FROM Sales.SalesOrderHeader
        WHERE OrderDate >= '2024-01-01' AND OrderDate < '2025-01-01'
        GROUP BY DATEFROMPARTS(YEAR(OrderDate), MONTH(OrderDate), 1)
        ORDER BY 1
    """)


def test_period_grid_fills_months_without_orders_with_zero(session: Session) -> None:
    session.call("api.SalesOrder_List", {**YEAR_2024, "customer_id": 12698})

    rows = session.query("""
        SELECT CAST(m.range AS DATE) AS month, COALESCE(sum(o."SubTotal"), 0) AS net_sales
        FROM range(DATE '2024-01-01', DATE '2025-01-01', INTERVAL 1 MONTH) m
        LEFT JOIN r1 o ON date_trunc('month', o."OrderDate") = m.range
        GROUP BY 1
        ORDER BY 1
    """)

    assert len(rows) == 12
    assert rows == session.reference("""
        SELECT m.month, COALESCE(SUM(h.SubTotal), 0)
        FROM (SELECT DATEFROMPARTS(2024, n, 1) AS month
              FROM (VALUES (1), (2), (3), (4), (5), (6), (7), (8), (9), (10), (11), (12))
                   AS v(n)) AS m
        LEFT JOIN Sales.SalesOrderHeader AS h
          ON h.CustomerID = 12698
         AND DATEFROMPARTS(YEAR(h.OrderDate), MONTH(h.OrderDate), 1) = m.month
        GROUP BY m.month
        ORDER BY m.month
    """)


def test_pivot_of_category_sales_per_territory(session: Session) -> None:
    session.call("api.SalesOrderLine_List", YEAR_2024)

    rows = session.query("""
        SELECT * FROM (
          PIVOT (SELECT "Territory", "Category", "LineTotal" FROM r1)
          ON "Category" IN ('Bikes' AS bikes, 'Components' AS components,
                            'Clothing' AS clothing, 'Accessories' AS accessories)
          USING sum("LineTotal")
          GROUP BY "Territory"
        )
        ORDER BY "Territory"
    """)

    assert rows == session.reference("""
        SELECT t.Name,
               SUM(CASE WHEN pc.Name = 'Bikes' THEN d.LineTotal END),
               SUM(CASE WHEN pc.Name = 'Components' THEN d.LineTotal END),
               SUM(CASE WHEN pc.Name = 'Clothing' THEN d.LineTotal END),
               SUM(CASE WHEN pc.Name = 'Accessories' THEN d.LineTotal END)
        FROM Sales.SalesOrderDetail AS d
        JOIN Sales.SalesOrderHeader AS h ON h.SalesOrderID = d.SalesOrderID
        JOIN Sales.SalesTerritory AS t ON t.TerritoryID = h.TerritoryID
        JOIN Production.Product AS p ON p.ProductID = d.ProductID
        JOIN Production.ProductSubcategory AS ps ON ps.ProductSubcategoryID = p.ProductSubcategoryID
        JOIN Production.ProductCategory AS pc ON pc.ProductCategoryID = ps.ProductCategoryID
        WHERE h.OrderDate >= '2024-01-01' AND h.OrderDate < '2025-01-01'
        GROUP BY t.Name
        ORDER BY t.Name
    """)


def test_top_customers_by_net_sales_with_limit(session: Session) -> None:
    session.call("api.SalesOrder_List", YEAR_2024)

    rows = session.query("""
        SELECT "CustomerID" AS customer_id, "CustomerName" AS customer_name,
               sum("SubTotal") AS net_sales, count(*) AS order_count
        FROM r1
        GROUP BY ALL
        ORDER BY net_sales DESC, customer_id
        LIMIT 10
    """)

    assert len(rows) == 10
    assert rows == session.reference("""
        SELECT TOP 10 h.CustomerID, v.CustomerName, SUM(h.SubTotal), COUNT(*)
        FROM Sales.SalesOrderHeader AS h
        JOIN api.vCustomer AS v ON v.CustomerID = h.CustomerID
        WHERE h.OrderDate >= '2024-01-01' AND h.OrderDate < '2025-01-01'
        GROUP BY h.CustomerID, v.CustomerName
        ORDER BY SUM(h.SubTotal) DESC, h.CustomerID
    """)


def test_sales_of_customers_from_a_city_join_two_handles(
    session: Session, results: ResultService
) -> None:
    customers = session.call("api.Customer_List", {"city": "Berlin"})
    orders = session.call("api.SalesOrder_List", YEAR_2024)

    # A customer can have several addresses in the city, so the list is reduced to ids first.
    rows = session.query("""
        SELECT o."CustomerType" AS customer_type, count(DISTINCT o."CustomerID") AS customers,
               sum(o."SubTotal") AS net_sales
        FROM r2 o
        JOIN (SELECT DISTINCT "CustomerID" FROM r1) c USING ("CustomerID")
        GROUP BY 1
        ORDER BY 1
    """)

    assert rows
    assert rows == session.reference("""
        SELECT v.CustomerType, COUNT(DISTINCT h.CustomerID), SUM(h.SubTotal)
        FROM Sales.SalesOrderHeader AS h
        JOIN api.vCustomer AS v ON v.CustomerID = h.CustomerID
        WHERE h.OrderDate >= '2024-01-01' AND h.OrderDate < '2025-01-01'
          AND h.CustomerID IN (
            SELECT c.CustomerID
            FROM api.vCustomer AS c
            JOIN Person.BusinessEntityAddress AS bea ON bea.BusinessEntityID = c.BusinessEntityID
            JOIN Person.Address AS a ON a.AddressID = bea.AddressID
            WHERE a.City = N'Berlin')
        GROUP BY v.CustomerType
        ORDER BY v.CustomerType
    """)
    # The query result is r3; its lineage reaches both procedure calls, without @UserId.
    procedure_customers, procedure_orders, query = results.lineage("r3")
    assert (procedure_customers.handle, procedure_orders.handle) == (customers, orders)
    assert procedure_customers.lineage == ProcedureLineage(
        procedure="api.Customer_List",
        params={"City": "Berlin", "CountryRegionCode": None, "CustomerType": None},
        truncated=False,
    )
    assert procedure_orders.lineage == ProcedureLineage(
        procedure="api.SalesOrder_List",
        params={"DateFrom": "2024-01-01", "DateTo": "2025-01-01", "CustomerID": None},
        truncated=False,
    )
    assert isinstance(query.lineage, QueryLineage)
    assert query.lineage.sources == (customers, orders)
