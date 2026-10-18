-- Sales order lines with OrderDate in [@DateFrom, @DateTo), with product, subcategory and
-- category, optionally for one customer or one product category.
CREATE OR ALTER PROCEDURE api.SalesOrderLine_List
    @DateFrom date,
    @DateTo date,
    @CustomerID int = NULL,
    @ProductCategoryID int = NULL,
    @UserId int
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        h.SalesOrderID,
        d.SalesOrderDetailID,
        CAST(h.OrderDate AS date) AS OrderDate,
        h.CustomerID,
        v.CustomerName,
        t.Name AS Territory,
        d.ProductID,
        p.Name AS ProductName,
        ps.Name AS Subcategory,
        pc.Name AS Category,
        d.OrderQty,
        d.UnitPrice,
        d.LineTotal
    FROM Sales.SalesOrderDetail AS d
    JOIN Sales.SalesOrderHeader AS h ON h.SalesOrderID = d.SalesOrderID
    JOIN api.vCustomer AS v ON v.CustomerID = h.CustomerID
    JOIN Sales.SalesTerritory AS t ON t.TerritoryID = h.TerritoryID
    JOIN Production.Product AS p ON p.ProductID = d.ProductID
    LEFT JOIN Production.ProductSubcategory AS ps
        ON ps.ProductSubcategoryID = p.ProductSubcategoryID
    LEFT JOIN Production.ProductCategory AS pc ON pc.ProductCategoryID = ps.ProductCategoryID
    WHERE h.OrderDate >= @DateFrom
      AND h.OrderDate < @DateTo
      AND (@CustomerID IS NULL OR h.CustomerID = @CustomerID)
      AND (@ProductCategoryID IS NULL OR ps.ProductCategoryID = @ProductCategoryID)
    ORDER BY h.SalesOrderID, d.SalesOrderDetailID
    OPTION (RECOMPILE);
END;
GO
