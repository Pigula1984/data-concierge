# Data Concierge

Backend asystenta AI: przyjmuje prompt użytkownika, wybiera procedurę składowaną z katalogu, wywołuje ją, deterministycznie liczy zestawienia (DuckDB) i zwraca JSON. Projekt systemu: [`docs/architecture/001-architektura-data-concierge.md`](docs/architecture/001-architektura-data-concierge.md). To źródło prawdy o architekturze; zmiany projektu zaczynamy od aktualizacji tego dokumentu.

## Język

- **Rozmowa z użytkownikiem: po polsku.**
- **Dokumenty po polsku:** analizy, plany, brainstormy, wnioski (`docs/solutions/`), standardy, opisy architektury. Dotyczy to także dokumentów tworzonych przez skille Compound Engineering, nawet jeśli skill domyślnie pisze po angielsku.
- **Po angielsku:** kod, nazwy (moduły, klasy, funkcje, zmienne), komentarze, docstringi, komunikaty logów i wyjątków, nazwy testów, commity, opisy PR.
- Fragmenty kodu wewnątrz polskich dokumentów też piszemy po angielsku (identyfikatory i komentarze).
- Nazwy kolumn w danych też są po angielsku: aliasy kolumn w zapytaniach `query`, które agent pisze nad uchwytami wyników (np. `month`, `net_sales`, `order_count`), w przykładach i w instrukcjach dla modelu. Po polsku zostają tylko teksty dla użytkownika (`message`, `title`, `assumptions`, pytania doprecyzowujące).

## Sposób pracy: Compound Engineering

Pracujemy skillami z pluginu `compound-engineering` (włączony w `.claude/settings.json`). Typowy cykl:

1. `ce-brainstorm`: doprecyzowanie, co budujemy (wymagania) → `docs/brainstorms/`
2. `ce-plan`: plan wdrożenia → `docs/plans/`
3. `ce-work`: realizacja planu
4. `ce-code-review` (lub `ce-simplify-code` przed review): przegląd zmian
5. `ce-commit` / `ce-commit-push-pr`: commit, ewentualnie PR
6. `ce-compound`: zapis nieoczywistej wiedzy z rozwiązanego problemu → `docs/solutions/`

Pomocniczo: `ce-debug` (błędy), `ce-ideate` (pomysły), `ce-doc-review` (przegląd planów i dokumentów), `ce-explain` (wyjaśnienia). Katalogiem bazowym artefaktów CE jest `docs/` (`.compound-engineering/config.yaml` nie ustawia `docs_root`).

Pracujemy tylko na modelach Anthropic. `ce-code-review` nie wysyła kodu do modeli innych dostawców (`cross_model_review_mode: off` w `.compound-engineering/config.yaml`), a soczewkę adwersarialną pokrywa lokalny recenzent.

Nie commitujemy bez prośby użytkownika.

## Kod: Python, obiektowo, czysto

Obowiązują zasady z [`docs/standards/`](docs/standards/README.md). Najważniejsze:

- Kod obiektowy: klasy z jedną odpowiedzialnością, zależności wstrzykiwane przez konstruktor, interfejsy jako `typing.Protocol`.
- Bez luźnych funkcji na poziomie modułu: zachowanie to metoda klasy, która jest właścicielem pojęcia (metoda prywatna, `classmethod` jako fabryka, metoda modelu). Wyjątki tylko z komentarzem `# module-level: <powód>` ([`python-czysty-kod.md`](docs/standards/python-czysty-kod.md), sekcja 2). Hook po edycji pliku w `src/` zgłasza nowe funkcje modułowe bez tego komentarza.
- Czysty kod i DRY: małe funkcje, czytelne nazwy, brak duplikacji logiki, pełne type hints.
- Modele danych i DTO na granicach warstw: Pydantic (niemutowalne, `frozen=True`, tam gdzie to możliwe).
- Wzorce projektowe tylko tam, gdzie rozwiązują konkretny problem. Katalog wzorców dla tego projektu: [`docs/standards/wzorce-projektowe.md`](docs/standards/wzorce-projektowe.md).

## Architektura: controller → service → repository

Usługa jest modułowa (moduły według funkcji: `core`, `catalog`, `procedures`, `results`, `query`, `agent`, `api`), a wewnątrz modułu obowiązuje podział na warstwy:

- **controller**: wejścia do systemu, czyli endpoint/CLI `ask()` **oraz narzędzia agenta Pydantic AI**. Cienkie: walidacja wejścia, mapowanie DTO, delegacja do serwisu, zamiana błędów domenowych na odpowiedź: w narzędziach `ModelRetry` dla błędów, które model może poprawić (parametry, uchwyt, SQL `query`), i `ToolFailed` dla nieudanego wykonania procedury; w endpoincie i CLI status `error`.
- **service**: logika biznesowa i orkiestracja.
- **repository**: dostęp do danych (procedury składowane, magazyn wyników DuckDB, katalog YAML).

Zależności idą tylko w jedną stronę: controller → service → repository. Szczegóły i przykłady: [`docs/standards/architektura-warstwowa.md`](docs/standards/architektura-warstwowa.md).

## Python, uv i ruff

- **Python 3.14** (przypięty w `.python-version`). Korzystamy z jego składni (PEP 695, leniwe adnotacje PEP 649), bez `from __future__ import annotations`.
- **Ruff** formatuje i lintuje kod: `uv run ruff format` i `uv run ruff check --fix`. Kod przed commitem przechodzi oba bez błędów. Konfiguracja tylko w `pyproject.toml`.
- Zależności dodajemy przez `uv add <pakiet>`, narzędzia deweloperskie przez `uv add --dev <pakiet>`, usuwamy przez `uv remove`.
- Środowisko: `uv sync`. Polecenia uruchamiamy przez `uv run <polecenie>` (np. `uv run pytest`).
- Nie używamy `pip install` ani ręcznie tworzonych virtualenvów. `pyproject.toml` i `uv.lock` trafiają do repozytorium.
