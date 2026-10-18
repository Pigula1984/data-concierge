# Skrypty bazy Data Concierge

Skrypty T-SQL wdrażają w lokalnej bazie `AdventureWorks2025` procedury odczytu, z których korzysta agent, i konto `agent_reader` z minimalnymi uprawnieniami (sekcje 5.4 i 5.5 architektury, plan U8).

| Katalog | Zawartość |
|---------|-----------|
| `schema/` | schemat `api` należący do `dbo` (ownership chaining z tabelami AdventureWorks) |
| `views/` | `api.vCustomer`: klient jako osoba albo sklep, z nazwą, typem i terytorium, wspólny dla procedur |
| `procedures/` | procedury `api.*`, po jednej w pliku |
| `security/` | login i użytkownik `agent_reader` z `EXECUTE` na schemacie `api` i czterech procedurach odczytu `dbo` |

Procedury mają `SET NOCOUNT ON`, zwracają jeden zestaw wyników i nie używają tabel tymczasowych ani dynamicznego SQL. Dzięki temu `sys.dm_exec_describe_first_result_set_for_object` opisuje ich kolumny, a łańcuch właścicielski się nie przerywa. Każda procedura poza `api.SalesOrder_DataRange` przyjmuje `@UserId`, na razie bez sprawdzania. Parametry opcjonalne mają wartość domyślną `NULL`, ale backend i tak przekazuje wszystkie parametry z katalogu.

## Warunki wstępne

1. Lokalna usługa `MSSQLSERVER` z odtworzoną bazą `AdventureWorks2025` oraz `sqlcmd` z ODBC Driver 18.
2. **Tryb mieszany uwierzytelniania.** `agent_reader` to login SQL z hasłem, więc instancja musi przyjmować loginy SQL (`SERVERPROPERTY('IsIntegratedSecurityOnly') = 0`). Włączenie: SSMS → właściwości serwera → Security → „SQL Server and Windows Authentication mode”, potem restart usługi. Alternatywnie, jako administrator:

   ```sql
   EXEC xp_instance_regwrite N'HKEY_LOCAL_MACHINE',
       N'Software\Microsoft\MSSQLServer\MSSQLServer', N'LoginMode', REG_DWORD, 2;
   ```

   i restart usługi (`Restart-Service MSSQLSERVER`). `deploy.ps1` sprawdza tryb na początku i bez niego kończy się komunikatem, zanim cokolwiek utworzy.
3. Hasło konta w `DC_SQL_PASSWORD`: w zmiennej środowiskowej albo w pliku `.env` w katalogu głównym repozytorium (wzór w `.env.example`). Backend łączy się z tym samym hasłem, więc konto i konfiguracja się nie rozjadą. Hasło nie może zawierać apostrofu. Dla instancji lokalnej z certyfikatem samopodpisanym ustaw też `DC_SQL_TRUST_SERVER_CERTIFICATE=true`.

## Wdrożenie

```powershell
./db/deploy.ps1                                   # localhost, AdventureWorks2025
./db/deploy.ps1 -Server localhost -Database AdventureWorks2025
```

Skrypt uruchamia `sqlcmd` z uwierzytelnianiem Windows w kolejności `schema`, `views`, `procedures`, `security` i zatrzymuje się na pierwszym błędzie. Wszystkie skrypty są idempotentne (`CREATE OR ALTER`, `IF … IS NULL`), więc ponowne uruchomienie kończy się sukcesem bez zmian. Ponowne uruchomienie ustawia hasło loginu na wartość z konfiguracji i po odtworzeniu bazy z kopii łączy użytkownika z loginem na nowo. Hasło trafia do `sqlcmd` przez zmienną środowiskową, a nie przez linię poleceń.

AdventureWorks ma wyzwalacz DDL, który przy każdym `CREATE`/`ALTER` wypisuje wiersz w rodzaju `CREATE_PROCEDURE - api.Customer_List`. To nie jest błąd.

## Zapytania kontrolne

Fakty z sekcji 5.4 architektury, sprawdzone 2026-10-18 kontem administracyjnym. Te same liczby sprawdzają testy integracyjne (`uv run pytest -m integration`).

```sql
-- 3 customers named "Mohamed Pal": 12698, 20550, 28763
SELECT c.CustomerID
FROM Sales.Customer AS c
JOIN Person.Person AS p ON p.BusinessEntityID = c.PersonID
WHERE p.FirstName + N' ' + p.LastName = N'Mohamed Pal'
ORDER BY 1;

-- "Berlin" in 5 regions (StateProvince)
SELECT COUNT(DISTINCT sp.Name)
FROM Person.Address AS a
JOIN Person.StateProvince AS sp ON sp.StateProvinceID = a.StateProvinceID
WHERE a.City = N'Berlin';

-- 10 variants of "HL Mountain Frame"
SELECT COUNT(*) FROM Production.Product WHERE Name LIKE N'HL Mountain Frame%';

-- Order range 2022-05-30 .. 2025-06-29, 31 465 orders, 121 317 lines
SELECT CAST(MIN(OrderDate) AS date), CAST(MAX(OrderDate) AS date), COUNT(*)
FROM Sales.SalesOrderHeader;
SELECT COUNT(*) FROM Sales.SalesOrderDetail;

-- 2024: 14 244 orders, SubTotal 43 671 889.5037; January 2024: 400 orders, 2 087 872.4623
SELECT COUNT(*), SUM(SubTotal) FROM Sales.SalesOrderHeader
WHERE OrderDate >= '2024-01-01' AND OrderDate < '2025-01-01';
SELECT COUNT(*), SUM(SubTotal) FROM Sales.SalesOrderHeader
WHERE OrderDate >= '2024-01-01' AND OrderDate < '2024-02-01';
```

Te same fakty przez procedury, kontem `agent_reader`:

```sql
EXEC api.Customer_Search @Name = N'Mohamed Pal', @UserId = 1;   -- 3 rows
EXEC api.Customer_List @City = N'Berlin', @UserId = 1;          -- 5 StateProvince, people and stores
EXEC api.SalesOrder_DataRange;                                  -- 2022-05-30, 2025-06-29
```

Odmowy dostępu dla `agent_reader`:

```sql
SELECT TOP 1 SalesOrderID FROM Sales.SalesOrderHeader;  -- Msg 229: SELECT permission denied
EXEC HumanResources.uspUpdateEmployeeLogin 1, NULL, N'x', '2020-01-01', 0;  -- Msg 229: EXECUTE denied
```
