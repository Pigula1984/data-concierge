# Wzorce projektowe w Data Concierge

Każdy wzorzec poniżej ma konkretne miejsce w architekturze ([001-architektura-data-concierge.md](../architecture/001-architektura-data-concierge.md)). Nie wprowadzamy wzorców „na zapas”: jeśli wzorzec nie rozwiązuje istniejącego problemu (wymiana implementacji, testowalność, usunięcie duplikacji), zostaje zwykły kod.

| Wzorzec | Gdzie w projekcie | Problem, który rozwiązuje |
|---------|-------------------|---------------------------|
| Repository | dostęp do procedur, magazyn wyników, katalog | oddzielenie logiki od źródła danych |
| Adapter | wykonawca procedur per silnik bazy | wymienna baza danych |
| Registry + Factory | rejestr narzędzi generowany z YAML | narzędzia z jednego źródła prawdy |
| Unia dyskryminowana | pochodzenie uchwytu (procedura albo zapytanie) | typowana proweniencja bez `if step == ...` rozsianych po kodzie |
| Dependency Injection + Composition Root | cała aplikacja, `deps` w Pydantic AI | testowalność, brak stanu globalnego |
| Value Object / DTO | `RequestContext`, `Response`, wpisy katalogu | bezpieczne przekazywanie danych między warstwami |
| Facade | `AskService` / `ask()` | jeden prosty punkt wejścia |
| Decorator (obiektowy i dekoratory Pythona) | obudowy repozytoriów, narzędzia agenta, modele | zachowania przekrojowe bez zmiany kodu bazowego |

## 1. Repository

Ukrywa szczegóły źródła danych za interfejsem w języku domeny. W tym projekcie repozytorium **nie** opakowuje tabel (agent nie ma do nich dostępu), tylko:

- **procedury składowane**: `ProcedureRepository.execute(entry, params) -> ResultTable`,
- **magazyn wyników**: `ResultStore.put(table) -> Handle`, `get(handle)`, `describe(handle)`,
- **katalog agenta**: `CatalogRepository.load() -> Catalog` (YAML) i `ProcedureMetadataRepository` (metadane z `sys.parameters` i `sys.dm_exec_describe_first_result_set_for_object`).

```python
class ProcedureRepository(Protocol):
    def execute(self, entry: CatalogEntry, params: ProcedureParams) -> ResultTable: ...


class ResultStore(Protocol):
    def put(self, table: ResultTable) -> Handle: ...
    def get(self, handle: Handle) -> ResultTable: ...
```

Serwisy znają tylko `Protocol`, a testy dostają implementację w pamięci.

## 2. Adapter

Konkretna implementacja repozytorium dopasowuje zewnętrzne API do naszego interfejsu. `SqlServerProcedureRepository` zamienia wywołanie na `EXEC`, a wiersze na tabelę Arrow z typami z katalogu. Wykonuje je przez `SqlServerClient` z `core/sqlserver.py`, jedyny moduł, który importuje pyodbc. Z tego samego klienta korzysta `SqlServerMetadataRepository`. Te dwa repozytoria i klient to **jedyne** miejsca zależne od silnika bazy (wymaganie z sekcji 2 architektury). Inna baza = nowy adapter, reszta kodu bez zmian.

## 3. Strategy: reguły zapytania jako obiekty (wycofane w v3.6)

Do architektury v3.5 każda reguła walidatora zapytania `query` była osobną klasą. Od v3.6 backend nie analizuje treści zapytania (sekcja 7.3 architektury), a kilka zostałych kontroli (nazwy i typy kolumn wyniku, obcięte źródła) mieści się w jednej klasie `QueryPolicy`. Wzorzec wróci, gdy kontroli przybędzie i będą niezależne od siebie.

## 4. Registry + Factory

Rejestr narzędzi powstaje z katalogu YAML (sekcje 4 i 6.1 architektury):

- **Factory**: `ProcedureToolFactory.create(entry) -> Tool` buduje narzędzie Pydantic AI (nazwa, opis z `summary`/`use_when`/`not_for`, model parametrów z `pydantic.create_model`, pominięte parametry `inject`). Ten sam model daje schemat narzędzia i walidację argumentów.
- **Registry**: `ToolRegistry` trzyma narzędzia procedur i narzędzia stałe (`query`, `describe_result`), udostępnia je agentowi i (później) wyszukuje je dla `find_procedures`.

Dodanie procedury = nowy wpis w YAML, bez nowego kodu. To DRY w praktyce: opis procedury istnieje w jednym miejscu.

## 5. Unia dyskryminowana: pochodzenie uchwytu

Uchwyt pochodzi z procedury albo z zapytania (sekcja 7.6 architektury). Oba warianty to osobne modele Pydantic rozróżniane polem `step`, a kod, który potrzebuje źródeł uchwytu (graf proweniencji, flaga `truncated`), pyta model, a nie sprawdza `step` ręcznie.

```python
class ProcedureLineage(BaseModel, frozen=True):
    step: Literal["procedure"] = "procedure"
    procedure: str
    params: dict[str, JsonValue]
    truncated: bool

    @property
    def sources(self) -> tuple[Handle, ...]:
        return ()


class QueryLineage(BaseModel, frozen=True):
    step: Literal["query"] = "query"
    sources: tuple[Handle, ...]
    submitted_sql: str
    sql: str


type Lineage = Annotated[ProcedureLineage | QueryLineage, Field(discriminator="step")]
```

## 6. Visitor: jedno przejście drzewa zapytania (wycofane w v3.6)

Do architektury v3.5 walidator przechodził drzewo parsera DuckDB (`json_serialize_sql`). Od v3.6 drzewa nie analizujemy: bezpieczeństwo daje zablokowane połączenie DuckDB, a źródła zapytania podaje sam silnik w planie zapytania (`EXPLAIN`).

## 7. Dependency Injection i Composition Root

Wszystkie zależności przechodzą przez konstruktory. Obiekty składamy w jednym miejscu (`bootstrap.py` / `container.py`): konfiguracja → repozytoria → serwisy → controllery i agent. Nie używamy frameworka DI, wystarczy zwykła funkcja fabrykująca.

Po stronie agenta zależności żądania (`RequestContext`, `ResultStore` sesji, serwisy) trafiają do narzędzi przez `deps` Pydantic AI (`RunContext[AgentDeps]`), a nie przez zmienne globalne.

## 8. Value Object / DTO

Niemutowalne modele Pydantic (`frozen=True`) do przekazywania danych między warstwami i do modelu:

- `RequestContext` (user_id, rola, id korelacji),
- `CatalogEntry`, `ProcedureParams`, `ProcedureLineage`, `QueryLineage`,
- `ResultSummary` i `QuerySummary` (to, co widzi model: uchwyt, schemat, statystyki, podgląd, a dla zapytania także źródła i SQL wykonany),
- `AgentAnswer` (wynik modelu, `output_type` agenta: status, komentarz, uchwyty z tytułami, założenia, pytanie doprecyzowujące),
- `Response` (kontrakt z sekcji 9 architektury, składany przez `ResponseAssembler` z `AgentAnswer`, danych uchwytów i proweniencji).

Repozytoria zwracają obiekty domenowe, controllery zwracają DTO. Nie przekazujemy surowych `dict` między warstwami.

## 9. Facade

`AskService.ask(prompt, context, session_id)` ukrywa całą maszynerię (sesja, agent, rejestr, magazyn, składanie odpowiedzi) za jedną metodą. Endpoint HTTP, CLI i testy ewaluacyjne korzystają z tej samej fasady.

## 10. Decorator

Dwa pokrewne mechanizmy, oba do zachowań przekrojowych (błędy, logowanie, pomiar czasu, cache), a nie do logiki biznesowej.

### 10.1. Decorator obiektowy (GoF)

Klasa, która implementuje ten sam `Protocol` co obiekt, który opakowuje, i dodaje zachowanie przed lub po wywołaniu. Kod bazowy się nie zmienia, a obudowy składamy w composition root.

```python
class LoggingProcedureRepository:
    """Logs procedure calls with duration and row count."""

    def __init__(self, inner: ProcedureRepository, logger: Logger) -> None:
        self._inner = inner
        self._logger = logger

    def execute(self, entry: CatalogEntry, params: ProcedureParams) -> ResultTable:
        started = time.perf_counter()
        result = self._inner.execute(entry, params)
        self._logger.info(
            "procedure executed",
            extra={
                "procedure": entry.procedure,
                "rows": result.row_count,
                "ms": round((time.perf_counter() - started) * 1000),
            },
        )
        return result


# bootstrap.py
procedures = LoggingProcedureRepository(SqlServerProcedureRepository(client), logger)
```

Inne miejsca: `CachingCatalogRepository` (katalog YAML czytany raz na proces).

### 10.2. Dekoratory Pythona

**Z bibliotek i biblioteki standardowej**, używane zgodnie z przeznaczeniem:

| Dekorator | Do czego |
|-----------|----------|
| `@agent.tool` (Pydantic AI) | rejestracja narzędzia agenta (controller dla modelu) |
| `@field_validator`, `@model_validator` (Pydantic) | walidacja pojedynczego pola i reguł między polami w DTO i wpisach katalogu |
| `@dataclass(frozen=True, slots=True)` | proste niemutowalne struktury wewnętrzne |
| `@override` (`typing`) | oznaczenie nadpisanej metody; kontrola typów wykrywa literówki i zmiany w klasie bazowej |
| `@classmethod` | alternatywne konstruktory, np. `CatalogEntry.from_yaml(node)` |
| `@property`, `@cached_property` | wartości pochodne obiektu bez jawnego wywołania (np. `TableSchema.measures`) |
| `@functools.cache` | cache czystych funkcji bez stanu (np. mapowanie typów SQL → Arrow) |

**Własne dekoratory** dla powtarzalnej obsługi przekrojowej. Przykład: każde narzędzie agenta musi zamienić błąd domenowy na wyjątek Pydantic AI. Błąd wejścia staje się `ModelRetry` (model poprawia wywołanie), a błąd wykonania `ToolFailed` (nie zużywa budżetu ponowień, model nie powtarza tego samego wywołania). Zamiast `try/except` w każdym narzędziu (powtórzenie) jest jeden dekorator. Narzędzia są synchroniczne, bo Pydantic AI uruchamia je w wątkach roboczych (sekcja 11.2 architektury):

```python
INPUT_ERRORS = (
    ParameterValidationError,
    UnknownHandleError,
    QueryError,
)  # QueryError: SQL the model can fix


def retry_on_domain_error[**P, R](tool: Callable[P, R]) -> Callable[P, R]:
    """Maps domain errors to ModelRetry (input) or ToolFailed (execution)."""

    @functools.wraps(tool)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return tool(*args, **kwargs)
        except INPUT_ERRORS as exc:
            raise ModelRetry(str(exc)) from exc
        except ProcedureError as exc:
            raise ToolFailed(str(exc)) from exc

    return wrapper


@agent.tool
@retry_on_domain_error
def query(ctx: RunContext[AgentDeps], sql: str) -> QuerySummary:
    """Run one read-only SELECT in DuckDB SQL over this session's result handles."""
    return ctx.deps.query_service.run(sql)
```

Zasady dla własnych dekoratorów:

- Zawsze `functools.wraps`, żeby zachować nazwę, docstring i sygnaturę. Pydantic AI buduje schemat narzędzia z sygnatury i docstringu, więc dekorator nie może ich zgubić (do potwierdzenia testem przy pierwszym narzędziu).
- Typowanie przez generyki PEP 695 z `ParamSpec` (`[**P, R]`), żeby kontrola typów widziała oryginalną sygnaturę.
- Dekorator nie zmienia typu wyniku ani znaczenia funkcji. Nie ukrywa logiki biznesowej ani nie ma niejawnych efektów ubocznych poza swoją jedną rolą.
- Kolejność ma znaczenie: dekorator rejestrujący (`@agent.tool`) jest zewnętrzny, obsługa przekrojowa wewnątrz.
- Najwyżej dwa własne dekoratory na jednej funkcji. Gdy potrzeba więcej, lepszy jest Decorator obiektowy albo jawny kod w serwisie.

## Wzorce, których na razie nie stosujemy

- **Unit of Work**: v1 tylko czyta dane, nie ma transakcji do koordynowania. Wróci przy procedurach zapisu.
- **Observer / event bus**: proweniencję zapisujemy jawnie w serwisach. Zdarzenia rozważymy przy logu audytowym.
- **Chain of Responsibility**: dobry kandydat na przyszłych strażników wejścia i akcji (sekcja 12 architektury), w v1 zbędny.
