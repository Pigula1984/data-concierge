-- Customer addresses (people and stores), optionally narrowed to a city, country and type.
-- One row per address: a store can have several.
CREATE OR ALTER PROCEDURE api.Customer_List
    @City nvarchar(30) = NULL,
    @CountryRegionCode nvarchar(3) = NULL,
    @CustomerType varchar(10) = NULL,
    @UserId int
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        v.CustomerID,
        v.AccountNumber,
        v.CustomerName,
        v.CustomerType,
        adt.Name AS AddressType,
        a.City,
        sp.Name AS StateProvince,
        cr.Name AS CountryRegion,
        v.Territory
    FROM api.vCustomer AS v
    JOIN Person.BusinessEntityAddress AS bea ON bea.BusinessEntityID = v.BusinessEntityID
    JOIN Person.AddressType AS adt ON adt.AddressTypeID = bea.AddressTypeID
    JOIN Person.Address AS a ON a.AddressID = bea.AddressID
    JOIN Person.StateProvince AS sp ON sp.StateProvinceID = a.StateProvinceID
    JOIN Person.CountryRegion AS cr ON cr.CountryRegionCode = sp.CountryRegionCode
    WHERE (@City IS NULL OR a.City = @City)
      AND (@CountryRegionCode IS NULL OR sp.CountryRegionCode = @CountryRegionCode)
      AND (@CustomerType IS NULL OR v.CustomerType = @CustomerType)
    ORDER BY v.CustomerID, a.AddressID
    -- Optional filters: compile the plan for the values of this call.
    OPTION (RECOMPILE);
END;
GO
