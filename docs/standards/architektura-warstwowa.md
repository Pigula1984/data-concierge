# Modułowość i warstwy controller → service → repository

## 1. Zasada

Kod dzielimy na **moduły według funkcji** (core, catalog, procedures, results, query, agent, api), a wewnątrz modułu na trzy warstwy:

| Warstwa | Odpowiedzialność | Czego nie robi |
|---------|------------------|----------------|
| **controller** | Wejście do systemu: przyjmuje żądanie, waliduje i mapuje je na DTO, woła serwis, mapuje wynik lub błąd na odpowiedź | logiki biznesowej, dostępu do danych |
| **service** | Logika biznesowa i orkiestracja: reguły, walidacja semantyczna, łączenie kilku repozytoriów, proweniencja | nie zna HTTP ani Pydantic AI, nie wykonuje SQL ani I/O bezpośrednio |
| **repository** | Dostęp do danych: procedury składowane, magazyn wyników, pliki katalogu, metadane bazy | reguł biznesowych, decyzji „co dalej” |

**Kierunek zależności jest jeden: controller → service → repository.** Repozytorium nie importuje serwisu, serwis nie importuje controllera. Serwis zależy od interfejsu repozytorium (`Protocol`), a nie od implementacji.

## 2. Co jest controllerem w backendzie agenta

To nie jest klasyczny CRUD, dlatego controllery są dwa rodzaje:

1. **Controller zewnętrzny**: endpoint HTTP i CLI `ask(prompt, user, session_id?) → Response`. Buduje `RequestContext`, woła `AskService`, zamienia wyjątki na status `error`.
2. **Narzędzia agenta (Pydantic AI tools)**: dla modelu są punktami wejścia tak samo jak endpoint dla klienta HTTP. Dlatego traktujemy je jak controllery: są **cienkie**, przyjmują i walidują argumenty od modelu, wołają serwis i zwracają DTO (`ResultSummary`) albo czytelny błąd do poprawki przez model. Logika (walidacja parametrów procedury, wstrzykiwanie `@UserId`, zapis do magazynu, proweniencja) siedzi w serwisach.

Serwisy nie znają Pydantic AI, więc to warstwa narzędzi tłumaczy błędy domenowe na wyjątki Pydantic AI. Błędy wejścia (`ParameterValidationError`, `UnknownHandleError`) i wszystkie błędy zapytania (`QueryValidationError`, `QueryExecutionError`, `QueryTimeoutError`) stają się `ModelRetry`: model dostaje komunikat jako błąd narzędzia i może poprawić wywołanie albo SQL. Błędy wykonania procedur (`ProcedureError`, `ProcedureTimeoutError`) stają się `ToolFailed`, który nie zużywa budżetu ponowień i mówi modelowi, żeby nie powtarzał tego samego wywołania. Robi to wspólny dekorator `retry_on_domain_error` ([wzorce-projektowe.md, sekcja 10.2](wzorce-projektowe.md)), a nie `try/except` w każdym narzędziu. Tak samo endpoint tłumaczy wyjątki na status `error` w odpowiedzi.

Narzędzia są funkcjami synchronicznymi. Pydantic AI uruchamia je w wątkach roboczych, więc serwisy, z których korzystają, muszą być bezpieczne przy wywołaniu z innego wątku (sekcja 11.2 architektury).

```python
# controller: thin tool, delegates to the service; the decorator maps domain errors for the model
@agent.tool
@retry_on_domain_error
def query(ctx: RunContext[AgentDeps], sql: str) -> QuerySummary:
    """Run one read-only SELECT in DuckDB SQL over this session's result handles."""
    return ctx.deps.query_service.run(sql)
```

## 3. Mapowanie komponentów architektury na warstwy

| Moduł | controller | service | repository |
|-------|------------|---------|------------|
| `api` | endpoint HTTP, CLI | `AskService` (fasada) | `SessionRepository` (sesje w pamięci procesu: historia rozmowy i połączenie DuckDB) |
| `agent` | narzędzia: `query`, `describe_result`, narzędzia procedur | `AgentService` (konfiguracja i uruchomienie pętli), `ResponseAssembler` (`AgentAnswer` → `Response`), `ToolRegistry` | — |
| `catalog` | — | `CatalogService` (walidacja YAML z metadanymi przy starcie, zakresy danych) | `CatalogRepository` (YAML), `ProcedureMetadataRepository` (metadane SQL Server) |
| `procedures` | — | `ProcedureService` (walidacja parametrów, `inject`, limity, proweniencja) | `ProcedureRepository` (adapter SQL Server) |
| `results` | — | `ResultService` (zapis, odczyt i opis uchwytów według trybu: schemat, zakresy dat, `ids`, odczyt komórki dla kotwic) | `ResultStore` (tabele DuckDB pod uchwytami) |
| `query` | — | `QueryService` (parsowanie → reguły → typy wyniku → pełne `ORDER BY` → wykonanie i zapis przez `ResultService`) | `SqlParser` (parser DuckDB na własnym połączeniu bez danych) |

Moduły bez controllera są wewnętrzne: korzystają z nich serwisy innych modułów.

Magazyn wyników to osobny moduł, bo korzystają z niego trzy inne: `procedures` zapisuje wyniki procedur, `query` czyta uchwyty i zapisuje wynik zapytania pod nowym uchwytem, a `agent` (narzędzie `describe_result`, `ResponseAssembler`) czyta uchwyty. Wszyscy idą przez `ResultService`, nie przez `ResultStore`. Moduł `results` jest właścicielem zablokowanego połączenia DuckDB sesji (sekcja 7.4a architektury). `QueryService` dostaje przez konstruktor `ResultService` sesji i przez niego wiąże (`DESCRIBE`), wykonuje i zapisuje zapytania, więc blokada i limity działają w jednym miejscu.

Parser SQL trafia do warstwy repository, bo woła DuckDB (`json_serialize_sql`, `json_deserialize_sql`). Do parsowania nie potrzebuje danych, więc ma własne zablokowane połączenie bez tabel i nie trzyma blokady magazynu sesji. Reguły i normalizacja (czysta logika na drzewie zapytania, bez I/O) są klasami pomocniczymi serwisu w module `query`.

## 4. Zasady współpracy modułów

- Moduł udostępnia na zewnątrz **serwisy i DTO** (przez `__init__.py`), nie repozytoria. Inny moduł nie sięga do cudzego repozytorium bezpośrednio.
- Serwis może wołać serwis innego modułu (np. `ProcedureService` korzysta z `CatalogService`), ale bez cykli. Cykl oznacza, że granica modułów jest źle postawiona.
- Wspólne typy domenowe (`TableSchema`, `ColumnType`, `ColumnRole`) i hierarchia wyjątków żyją w module `core`, który nie zależy od żadnego innego modułu.
- Tam też żyje `SqlServerClient`, z którego korzystają dwa repozytoria. Zablokowane połączenie DuckDB tworzy sam `DuckDBResultStore`, jego jedyny użytkownik. Klient SQL Server otwiera połączenie na każde wywołanie, ustawia limit czasu i zamienia błędy sterownika na `ProcedureError` bez danych logowania. Korzystają z niego `SqlServerProcedureRepository` (moduł `procedures`) i `SqlServerMetadataRepository` (moduł `catalog`), więc żadne repozytorium nie sięga do cudzego.
- Konfiguracja (`Settings` z `pydantic-settings`, moduł `core`) jest wczytywana raz w composition root i wstrzykiwana. Żaden moduł nie czyta zmiennych środowiskowych samodzielnie.

## 5. Układ katalogów

```
src/data_concierge/
├── core/                  # shared domain types, errors, settings
│   ├── types.py           # ColumnType, ColumnRole, TableSchema, SQL Server and DuckDB type mapping
│   ├── errors.py
│   ├── settings.py
│   ├── context.py         # RequestContext
│   ├── sql.py             # quote_identifier: the one module-level function (used by two layers)
│   └── sqlserver.py       # SqlServerClient: the only module that imports pyodbc
├── catalog/
│   ├── models.py          # CatalogEntry, ParamSpec, ColumnSpec
│   ├── repository.py      # CatalogRepository, ProcedureMetadataRepository
│   └── service.py         # CatalogService
├── procedures/
│   ├── models.py
│   ├── repository.py      # ProcedureRepository protocol + SqlServer adapter
│   └── service.py         # ProcedureService
├── results/
│   ├── models.py          # Handle, ResultTable, ResultSummary, ProcedureLineage | QueryLineage
│   ├── repository.py      # ResultStore protocol + DuckDB implementation (owns the connection)
│   └── service.py         # ResultService
├── query/
│   ├── models.py          # QuerySummary
│   ├── policy.py          # QueryPolicy: result column names and types, truncated sources
│   └── service.py         # QueryService
├── agent/
│   ├── deps.py            # AgentDeps
│   ├── tools.py           # tools = controllers for the model
│   ├── registry.py        # ProcedureToolFactory, ToolRegistry
│   ├── prompts.py
│   ├── anchors.py         # Anchors: check and fill {r3.net_sales} in texts for the user
│   ├── formatting.py      # ValueFormatter: values as the user reads them
│   ├── service.py         # AgentService
│   └── assembler.py       # ResponseAssembler: AgentAnswer -> Response
├── api/
│   ├── controller.py      # HTTP endpoint
│   ├── cli.py
│   ├── schemas.py         # Response, request DTOs
│   ├── sessions.py        # SessionRepository
│   └── service.py         # AskService
└── bootstrap.py           # composition root: wires everything together

tests/                     # mirrors src/data_concierge/
catalog/agent-procedures.yaml
```

Gdy moduł urośnie, pliki `repository.py` / `service.py` zamieniamy na pakiety (`repository/`, `service/`) bez zmiany zasad.

## 6. Testowanie warstw

| Warstwa | Jak testujemy |
|---------|---------------|
| controller | wywołanie z atrapą serwisu: mapowanie wejścia, statusy, błędy |
| service | atrapy repozytoriów (implementacje `Protocol` w pamięci): reguły i orkiestracja |
| repository | testy integracyjne na prawdziwym silniku: DuckDB w pamięci, lokalny SQL Server |
| całość | zestaw ewaluacyjny z sekcji 13 architektury przez fasadę `ask()` |
