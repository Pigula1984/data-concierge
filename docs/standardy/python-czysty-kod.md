# Python: obiektowo, czysto, bez powtórzeń

## 1. Język w kodzie

Kod, nazwy, komentarze, docstringi, komunikaty wyjątków i logów piszemy po angielsku. Dotyczy to także przykładów kodu w polskich dokumentach.

## 2. Obiektowość

- **Jedna klasa, jedna odpowiedzialność.** Klasa ma jeden powód do zmiany. `ProcedureRepository` wywołuje procedury, ale nie waliduje katalogu i nie buduje odpowiedzi.
- **Zależności przez konstruktor.** Klasa nie tworzy sama swoich zależności (połączeń, klientów, repozytoriów), tylko dostaje je w `__init__`. Dzięki temu w testach podstawiamy atrapy bez patchowania.
- **Interfejsy jako `typing.Protocol`.** Kod zależy od abstrakcji (`ProcedureRepository`, `TransformExecutor`), a nie od konkretnej implementacji. `Protocol` nie wymaga dziedziczenia, więc atrapa w teście to zwykła klasa z tymi samymi metodami. `abc.ABC` stosujemy tylko wtedy, gdy klasa bazowa dostarcza wspólną implementację.
- **Kompozycja zamiast dziedziczenia.** Dziedziczenie tylko dla prawdziwej relacji „jest” i płytkich hierarchii (najwyżej jeden poziom poniżej klasy abstrakcyjnej).
- **Niemutowalne obiekty wartości.** DTO, konfiguracja, kontekst żądania i kroki specyfikacji to modele Pydantic z `frozen=True` (lub `@dataclass(frozen=True, slots=True)` dla prostych struktur wewnętrznych). Zmiana = nowy obiekt.
- **Brak stanu globalnego.** Żadnych singletonów modułowych z połączeniami. Obiekty składamy w jednym miejscu (composition root, patrz [architektura-warstwowa.md](architektura-warstwowa.md)).

```python
class ProcedureService:
    """Validates a procedure call and stores its result under a handle."""

    def __init__(
        self,
        catalog: CatalogRepository,
        procedures: ProcedureRepository,
        results: ResultStore,
    ) -> None:
        self._catalog = catalog
        self._procedures = procedures
        self._results = results
```

## 3. Czysty kod

- **Nazwy mówią, co rzecz robi.** Funkcje to czasowniki (`load_catalog`, `compile_spec`), klasy to rzeczowniki (`TransformCompiler`). Bez skrótów poza powszechnymi (`id`, `sql`, `db`). Wartości logiczne: `is_`, `has_`, `can_`.
- **Małe funkcje na jednym poziomie abstrakcji.** Funkcja robi jedną rzecz. Jeśli ma sekcje oddzielone komentarzami, to są kandydaci na osobne metody.
- **Mało argumentów.** Więcej niż 3–4 argumenty to sygnał, że brakuje obiektu parametrów (modelu Pydantic).
- **Wczesny powrót zamiast zagnieżdżeń.** Warunki brzegowe obsługujemy na początku funkcji.
- **Brak magicznych wartości.** Limity, nazwy, progi idą do konfiguracji lub stałych z nazwą (`MAX_PREVIEW_ROWS = 10`).
- **Komentarze wyjaśniają „dlaczego”, nie „co”.** Kod ma być czytelny bez komentarza. Docstring dla publicznych klas i metod: jedno zdanie o odpowiedzialności, szczegóły tylko gdy są nieoczywiste.
- **Bez martwego kodu.** Nie zostawiamy zakomentowanego kodu ani nieużywanych parametrów „na przyszłość”.

## 4. DRY, ale rozsądnie

- **Jedno źródło prawdy dla wiedzy.** Opis procedury żyje w YAML katalogu; definicje narzędzi, schematy i skrót katalogu w prompcie są z niego **generowane**, a nie przepisywane. Schemat specyfikacji przekształceń to model Pydantic, z którego powstaje JSON Schema narzędzia `transform`.
- **Wspólna logika do jednej klasy lub funkcji.** Mapowanie typów (SQL Server → Arrow → DuckDB → JSON) jest w jednym miejscu, a nie w każdym wykonawcy.
- **Reguła trzech.** Dwa podobne fragmenty mogą zostać; przy trzecim wydzielamy abstrakcję. Lepsza drobna duplikacja niż zła abstrakcja, która wiąże niezależne rzeczy.
- DRY dotyczy wiedzy i logiki, nie przypadkowo podobnego kodu. Dwa kawałki, które dziś wyglądają tak samo, ale zmieniają się z różnych powodów, zostają osobno.

## 5. Typowanie

- Pełne type hints w sygnaturach publicznych i prywatnych metod. Kod przechodzi statyczną kontrolę typów.
- Python 3.14 i jego składnia: `list[str]`, `X | None`, aliasy `type Alias = ...`, generyki `class Box[T]: ...` (PEP 695), `@override` z `typing` przy nadpisywaniu metod.
- Adnotacje są w 3.14 wyliczane leniwie (PEP 649), więc nie dodajemy `from __future__ import annotations` i nie piszemy typów w cudzysłowach dla odwołań w przód.
- Unikamy `Any` i `dict[str, Any]` w logice. Dane z zewnątrz (YAML, odpowiedź modelu, wiersze z bazy) zamieniamy na modele Pydantic na granicy.
- Kwoty jako `Decimal`, nigdy `float` (wymóg deterministyczności z architektury, sekcja 7.4).

## 6. Błędy i wyjątki

- **Własna hierarchia wyjątków** z klasą bazową (np. `DataConciergeError`) i podklasami według przyczyny: `CatalogError`, `ProcedureError`, `SpecValidationError`.
- **Wyjątki niosą konkretny komunikat**, bo część z nich wraca do modelu jako błąd narzędzia i musi pozwolić mu się poprawić („`sum` requires a numeric column; `CustomerName` is `string`”).
- Nie łapiemy gołego `except Exception` poza warstwą controllera, która zamienia błędy na status odpowiedzi.
- Nie połykamy wyjątków. Przy przepakowaniu używamy `raise ... from exc`.

## 7. Testy

- Testy jednostkowe serwisów z atrapami repozytoriów (implementacje `Protocol` w pamięci).
- Testy integracyjne repozytoriów na prawdziwych silnikach (DuckDB w pamięci, lokalny SQL Server).
- Nazwy testów opisują zachowanie: `test_aggregate_rejects_sum_on_text_column`.
- Struktura katalogu `tests/` odzwierciedla moduły w `src/`.

## 8. Narzędzia

**Ustalone:**

| Cel | Narzędzie | Uruchomienie |
|-----|-----------|--------------|
| Wersja Pythona | 3.14 (`requires-python = ">=3.14"`, `.python-version`) | `uv python pin 3.14` |
| Zależności i środowisko | uv | `uv add`, `uv sync`, `uv run` |
| Formatowanie i lint | ruff | `uv run ruff format`, `uv run ruff check --fix` |

**Ruff** to jedyne narzędzie do formatowania, sortowania importów i lintu (zastępuje black, isort, flake8). Konfiguracja w `pyproject.toml` (`[tool.ruff]`). Zasady:

- Kod przed commitem przechodzi `uv run ruff format` i `uv run ruff check` bez błędów.
- Nie wyłączamy reguł w kodzie (`# noqa`) bez komentarza, dlaczego. Wyjątki dla całych katalogów (np. `tests/`) ustawiamy w `per-file-ignores`.
- Zestaw reguł rozszerzamy świadomie w `pyproject.toml`, a nie lokalnie w plikach.

**Propozycje do zatwierdzenia:**

| Cel | Narzędzie | Uruchomienie |
|-----|-----------|--------------|
| Konfiguracja | pydantic-settings | wczytywana raz w composition root |
| Typy | mypy lub pyright | `uv run mypy src` |
| Testy | pytest | `uv run pytest` |

Narzędzia deweloperskie instalujemy jako zależności deweloperskie (`uv add --dev`), konfiguracja w `pyproject.toml`.
