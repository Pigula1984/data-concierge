-- Sales order headers with OrderDate in [@DateFrom, @DateTo), optionally for one customer.
CREATE OR ALTER PROCEDURE api.SalesOrder_List
    @DateFrom date,
    @DateTo date,
    @CustomerID int = NULL,
    @UserId int
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        h.SalesOrderID,
        CAST(h.OrderDate AS date) AS OrderDate,
        h.CustomerID,
        v.CustomerName,
        v.CustomerType,
        t.Name AS Territory,
        h.SubTotal,
        h.TotalDue
    FROM Sales.SalesOrderHeader AS h
    JOIN api.vCustomer AS v ON v.CustomerID = h.CustomerID
    JOIN Sales.SalesTerritory AS t ON t.TerritoryID = h.TerritoryID
    WHERE h.OrderDate >= @DateFrom
      AND h.OrderDate < @DateTo
      AND (@CustomerID IS NULL OR h.CustomerID = @CustomerID)
    ORDER BY h.SalesOrderID
    OPTION (RECOMPILE);
END;
GO
