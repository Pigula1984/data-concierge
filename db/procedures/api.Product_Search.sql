-- Products whose name contains @Name; every colour and size variant is a separate product.
CREATE OR ALTER PROCEDURE api.Product_Search
    @Name nvarchar(100),
    @UserId int
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        p.ProductID,
        p.ProductNumber,
        p.Name AS ProductName,
        p.Color,
        p.Size,
        ps.Name AS Subcategory,
        pc.Name AS Category,
        p.ListPrice
    FROM Production.Product AS p
    LEFT JOIN Production.ProductSubcategory AS ps
        ON ps.ProductSubcategoryID = p.ProductSubcategoryID
    LEFT JOIN Production.ProductCategory AS pc ON pc.ProductCategoryID = ps.ProductCategoryID
    WHERE p.Name LIKE N'%' + @Name + N'%'
    ORDER BY p.Name, p.ProductID;
END;
GO
