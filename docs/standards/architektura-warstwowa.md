# Modułowość i warstwy controller → service → repository

## 1. Zasada

Kod dzielimy na **moduły według funkcji** (core, catalog, procedures, results, transform, agent, api), a wewnątrz modułu na trzy warstwy:

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

Serwisy nie znają Pydantic AI, więc to warstwa narzędzi tłumaczy błędy domenowe na wyjątki Pydantic AI. Błędy wejścia (`ParameterValidationError`, `SpecValidationError`, `UnknownHandleError`) stają się `ModelRetry`: model dostaje komunikat jako błąd narzędzia i może poprawić wywołanie. Błędy wykonania (`ProcedureError`, `ProcedureTimeoutError`) stają się `ToolFailed`, który nie zużywa budżetu ponowień i mówi modelowi, żeby nie powtarzał tego samego wywołania. Robi to wspólny dekorator `retry_on_domain_error` ([wzorce-projektowe.md, sekcja 10.2](wzorce-projektowe.md)), a nie `try/except` w każdym narzędziu. Tak samo endpoint tłumaczy wyjątki na status `error` w odpowiedzi.

Narzędzia są funkcjami synchronicznymi. Pydantic AI uruchamia je w wątkach roboczych, więc serwisy, z których korzystają, muszą być bezpieczne przy wywołaniu z innego wątku (sekcja 11.2 architektury).

```python
# controller: thin tool, delegates to the service; the decorator maps domain errors for the model
@agent.tool
@retry_on_domain_error
def transform(ctx: RunContext[AgentDeps], spec: TransformSpec) -> ResultSummary:
    return ctx.deps.transform_service.apply(spec, ctx.deps.request)
```

## 3. Mapowanie komponentów architektury na warstwy

| Moduł | controller | service | repository |
|-------|------------|---------|------------|
| `api` | endpoint HTTP, CLI | `AskService` (fasada) | `SessionRepository` (sesje w pamięci procesu: historia rozmowy i połączenie DuckDB) |
| `agent` | narzędzia: `transform`, `describe_result`, narzędzia procedur | `AgentService` (konfiguracja i uruchomienie pętli), `ResponseAssembler` (`AgentAnswer` → `Response`), `ToolRegistry` | — |
| `catalog` | — | `CatalogService` (walidacja YAML z metadanymi przy starcie, zakresy danych) | `CatalogRepository` (YAML), `ProcedureMetadataRepository` (metadane SQL Server) |
| `procedures` | — | `ProcedureService` (walidacja parametrów, `inject`, limity, proweniencja) | `ProcedureRepository` (adapter SQL Server) |
| `results` | — | `ResultService` (zapis, odczyt i opis uchwytów: schemat, statystyki, podgląd) | `ResultStore` (tabele DuckDB pod uchwytami) |
| `transform` | — | `TransformService` (walidacja specyfikacji → wykonanie → fakty) | `TransformExecutor` (DuckDB, później Polars) |

Moduły bez controllera są wewnętrzne: korzystają z nich serwisy innych modułów.

Magazyn wyników to osobny moduł, bo korzystają z niego trzy inne: `procedures` zapisuje `r1`, `transform` czyta `r1` i zapisuje `r2`, a `agent` (narzędzie `describe_result`, `ResponseAssembler`) czyta uchwyty. Wszyscy idą przez `ResultService`, nie przez `ResultStore`. Moduł `results` jest właścicielem zablokowanego połączenia DuckDB sesji (sekcja 7.4a architektury); `DuckDBExecutor` dostaje to połączenie przez konstruktor.

Silnik przekształceń trafia do warstwy repository, bo to on wykonuje zapytania na danych w DuckDB. Kompilator specyfikacji (czysta logika bez I/O) jest częścią serwisu albo osobną klasą pomocniczą w module `transform`.

## 4. Zasady współpracy modułów

- Moduł udostępnia na zewnątrz **serwisy i DTO** (przez `__init__.py`), nie repozytoria. Inny moduł nie sięga do cudzego repozytorium bezpośrednio.
- Serwis może wołać serwis innego modułu (np. `ProcedureService` korzysta z `CatalogService`), ale bez cykli. Cykl oznacza, że granica modułów jest źle postawiona.
- Wspólne typy domenowe (`TableSchema`, `ColumnType`, `ColumnRole`) i hierarchia wyjątków żyją w module `core`, który nie zależy od żadnego innego modułu.
- Konfiguracja (`Settings` z `pydantic-settings`, moduł `core`) jest wczytywana raz w composition root i wstrzykiwana. Żaden moduł nie czyta zmiennych środowiskowych samodzielnie.

## 5. Układ katalogów

```
src/data_concierge/
├── core/                  # shared domain types, errors, settings
│   ├── types.py           # ColumnType, ColumnRole, TableSchema
│   ├── errors.py
│   ├── settings.py
│   └── context.py         # RequestContext
├── catalog/
│   ├── models.py          # CatalogEntry, ParamSpec, ColumnSpec
│   ├── repository.py      # CatalogRepository, ProcedureMetadataRepository
│   └── service.py         # CatalogService
├── procedures/
│   ├── models.py
│   ├── repository.py      # ProcedureRepository protocol + SqlServer adapter
│   └── service.py         # ProcedureService
├── results/
│   ├── models.py          # Handle, ResultTable, ResultSummary, Lineage
│   ├── repository.py      # ResultStore protocol + DuckDB implementation (owns the connection)
│   └── service.py         # ResultService
├── transform/
│   ├── spec.py            # TransformSpec, step classes (discriminated union)
│   ├── validator.py       # semantic validation, step by step
│   ├── compiler.py        # SqlQueryBuilder, spec -> CompiledQuery
│   ├── executor.py        # TransformExecutor protocol, DuckDBExecutor
│   └── service.py         # TransformService
├── agent/
│   ├── deps.py            # AgentDeps
│   ├── tools.py           # tools = controllers for the model
│   ├── registry.py        # ProcedureToolFactory, ToolRegistry
│   ├── prompts.py
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
