-- One row per customer: a store or a person, with the name and type the procedures return.
-- A store customer with a contact person is a store; its name is the store name.
CREATE OR ALTER VIEW api.vCustomer
AS
SELECT
    c.CustomerID,
    c.AccountNumber,
    COALESCE(s.Name, p.FirstName + N' ' + p.LastName) AS CustomerName,
    CASE WHEN c.StoreID IS NULL THEN 'individual' ELSE 'store' END AS CustomerType,
    -- The business entity that owns the addresses of the customer.
    COALESCE(c.StoreID, c.PersonID) AS BusinessEntityID,
    t.Name AS Territory,
    cr.Name AS CountryRegion
FROM Sales.Customer AS c
LEFT JOIN Sales.Store AS s ON s.BusinessEntityID = c.StoreID
LEFT JOIN Person.Person AS p ON p.BusinessEntityID = c.PersonID
JOIN Sales.SalesTerritory AS t ON t.TerritoryID = c.TerritoryID
JOIN Person.CountryRegion AS cr ON cr.CountryRegionCode = t.CountryRegionCode;
GO
