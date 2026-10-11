# Wzorce projektowe w Data Concierge

Każdy wzorzec poniżej ma konkretne miejsce w architekturze ([001-architektura-data-concierge.md](../architecture/001-architektura-data-concierge.md)). Nie wprowadzamy wzorców „na zapas”: jeśli wzorzec nie rozwiązuje istniejącego problemu (wymiana implementacji, testowalność, usunięcie duplikacji), zostaje zwykły kod.

| Wzorzec | Gdzie w projekcie | Problem, który rozwiązuje |
|---------|-------------------|---------------------------|
| Repository | dostęp do procedur, magazyn wyników, katalog | oddzielenie logiki od źródła danych |
| Adapter | wykonawca procedur per silnik bazy | wymienna baza danych |
| Strategy | silnik przekształceń (DuckDB / Polars) | wymienny silnik obliczeń |
| Registry + Factory | rejestr narzędzi generowany z YAML | narzędzia z jednego źródła prawdy |
| Unia dyskryminowana + polimorficzne kroki | specyfikacja `transform` | walidacja i kompilacja bez `if op == ...` |
| Builder | składanie zapytania SQL z kroków | czytelne budowanie CTE |
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

Konkretna implementacja repozytorium dopasowuje zewnętrzne API do naszego interfejsu. `SqlServerProcedureRepository` zamienia wywołanie na `EXEC` przez pyodbc, a wiersze na `ResultTable` z typami z katalogu. To **jedyne** miejsce zależne od silnika bazy (wymaganie z sekcji 2 architektury). Inna baza = nowy adapter, reszta kodu bez zmian.

## 3. Strategy

Wymienny algorytm za wspólnym interfejsem. Silnik przekształceń (sekcja 7.4 architektury):

```python
class TransformExecutor(Protocol):
    def run(self, spec: TransformSpec, source: ResultTable) -> TransformResult: ...


class DuckDBExecutor:  # v1: compiles spec to SQL, runs in a locked-down DuckDB connection
    ...


class PolarsExecutor:  # optional: compiles the same spec to Polars expressions
    ...
```

Walidacja specyfikacji jest wspólna i działa **przed** wykonawcą, więc obie strategie dostają ten sam, sprawdzony wejściowy obiekt. Uruchomienie obu strategii na tych samych danych to gotowy test porównawczy.

## 4. Registry + Factory

Rejestr narzędzi powstaje z katalogu YAML (sekcje 4 i 6.1 architektury):

- **Factory**: `ProcedureToolFactory.create(entry) -> Tool` buduje narzędzie Pydantic AI (nazwa, opis z `summary`/`use_when`/`not_for`, model parametrów z `pydantic.create_model`, pominięte parametry `inject`). Ten sam model daje schemat narzędzia i walidację argumentów.
- **Registry**: `ToolRegistry` trzyma narzędzia procedur i narzędzia stałe (`transform`, `describe_result`), udostępnia je agentowi i (później) wyszukuje je dla `find_procedures`.

Dodanie procedury = nowy wpis w YAML, bez nowego kodu. To DRY w praktyce: opis procedury istnieje w jednym miejscu.

## 5. Unia dyskryminowana i polimorficzne kroki specyfikacji

Specyfikacja `transform` to lista kroków rozróżnianych polem `op` (sekcja 7.2–7.3). Każdy krok jest osobną klasą Pydantic, która sama wie, jak się zwalidować względem schematu i jak zmienić schemat wyjściowy. Kompilator nie ma długiego `if op == "derive" ... elif ...`.

```python
class DeriveStep(BaseModel, frozen=True):
    op: Literal["derive"]
    name: str
    fn: DeriveFn
    column: str

    def output_schema(self, schema: TableSchema) -> TableSchema:
        schema.require(self.column, role=ColumnRole.TIME)
        return schema.with_column(self.name, ColumnType.DATE, ColumnRole.TIME)


type TransformStep = Annotated[
    DeriveStep | AggregateStep | FillPeriodsStep | PivotStep | SortStep | LimitStep | SelectStep,
    Field(discriminator="op"),
]
```

Wariant: gdy kroki muszą być kompilowane dla kilku silników (DuckDB i Polars), logikę kompilacji trzymamy w wykonawcach jako **Visitor** (`compile_derive`, `compile_aggregate`…), a nie w samych krokach. Kroki znają wtedy tylko walidację. Nowy `op` (np. `filter`) to nowa klasa i nowa metoda w każdym wykonawcy, a brak metody wychodzi w kontroli typów.

## 6. Builder

`SqlQueryBuilder` składa zapytanie z kolejnych CTE (`s1`, `s2`, …, `periods`), cytuje identyfikatory z białej listy schematu i zbiera parametry zapytania osobno od tekstu SQL. Zapewnia też zawsze obecne `ORDER BY` (wymóg deterministyczności). Model nigdy nie dostarcza fragmentów SQL.

```python
query = (
    SqlQueryBuilder(source="r1")
    .add_cte(derive_sql)
    .add_cte(aggregate_sql)
    .order_by(["month"])
    .build()  # -> CompiledQuery(sql: str, params: list[object])
)
```

## 7. Dependency Injection i Composition Root

Wszystkie zależności przechodzą przez konstruktory. Obiekty składamy w jednym miejscu (`bootstrap.py` / `container.py`): konfiguracja → repozytoria → serwisy → controllery i agent. Nie używamy frameworka DI, wystarczy zwykła funkcja fabrykująca.

Po stronie agenta zależności żądania (`RequestContext`, `ResultStore` sesji, serwisy) trafiają do narzędzi przez `deps` Pydantic AI (`RunContext[AgentDeps]`), a nie przez zmienne globalne.

## 8. Value Object / DTO

Niemutowalne modele Pydantic (`frozen=True`) do przekazywania danych między warstwami i do modelu:

- `RequestContext` (user_id, rola, id korelacji),
- `CatalogEntry`, `ProcedureParams`, `TransformSpec`,
- `ResultSummary` (to, co widzi model: uchwyt, schemat, statystyki, podgląd),
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
procedures = LoggingProcedureRepository(SqlServerProcedureRepository(connection), logger)
```

Inne miejsca: `CachingCatalogRepository` (katalog YAML czytany raz na proces).

### 10.2. Dekoratory Pythona

**Z bibliotek i biblioteki standardowej**, używane zgodnie z przeznaczeniem:

| Dekorator | Do czego |
|-----------|----------|
| `@agent.tool` (Pydantic AI) | rejestracja narzędzia agenta (controller dla modelu) |
| `@field_validator`, `@model_validator` (Pydantic) | walidacja pojedynczego pola i reguł między polami w DTO i krokach specyfikacji |
| `@dataclass(frozen=True, slots=True)` | proste niemutowalne struktury wewnętrzne |
| `@override` (`typing`) | oznaczenie nadpisanej metody; kontrola typów wykrywa literówki i zmiany w klasie bazowej |
| `@classmethod` | alternatywne konstruktory, np. `CatalogEntry.from_yaml(node)` |
| `@property`, `@cached_property` | wartości pochodne obiektu bez jawnego wywołania (np. `TableSchema.measures`) |
| `@functools.cache` | cache czystych funkcji bez stanu (np. mapowanie typów SQL → Arrow) |

**Własne dekoratory** dla powtarzalnej obsługi przekrojowej. Przykład: każde narzędzie agenta musi zamienić błąd domenowy na wyjątek Pydantic AI. Błąd wejścia staje się `ModelRetry` (model poprawia wywołanie), a błąd wykonania `ToolFailed` (nie zużywa budżetu ponowień, model nie powtarza tego samego wywołania). Zamiast `try/except` w każdym narzędziu (powtórzenie) jest jeden dekorator. Narzędzia są synchroniczne, bo Pydantic AI uruchamia je w wątkach roboczych (sekcja 11.2 architektury):

```python
INPUT_ERRORS = (ParameterValidationError, SpecValidationError, UnknownHandleError)


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
def transform(ctx: RunContext[AgentDeps], spec: TransformSpec) -> ResultSummary:
    return ctx.deps.transform_service.apply(spec, ctx.deps.request)
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
