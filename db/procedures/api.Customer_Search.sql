-- Customers whose name contains @Name, with the facts needed to tell namesakes apart.
CREATE OR ALTER PROCEDURE api.Customer_Search
    @Name nvarchar(100),
    @UserId int
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        v.CustomerID,
        v.AccountNumber,
        v.CustomerName,
        v.CustomerType,
        v.Territory,
        v.CountryRegion,
        (SELECT COUNT(*) FROM Sales.SalesOrderHeader AS h WHERE h.CustomerID = v.CustomerID)
            AS OrderCount
    FROM api.vCustomer AS v
    WHERE v.CustomerName LIKE N'%' + @Name + N'%'
    ORDER BY v.CustomerName, v.CustomerID;
END;
GO
