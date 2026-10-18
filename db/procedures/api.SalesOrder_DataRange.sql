-- First and last order date. The backend reads it once at startup for all users (plan
-- KTD15), so unlike the other procedures it takes no @UserId.
CREATE OR ALTER PROCEDURE api.SalesOrder_DataRange
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        CAST(MIN(OrderDate) AS date) AS MinOrderDate,
        CAST(MAX(OrderDate) AS date) AS MaxOrderDate
    FROM Sales.SalesOrderHeader;
END;
GO
