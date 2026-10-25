# Data Concierge

Backend asystenta danych (POC). Przyjmuje prompt użytkownika po polsku i wybiera procedury składowane z katalogu. Wyniki liczy deterministycznie w DuckDB, a odpowiedź zwraca jako JSON z tabelami, komentarzem i pochodzeniem danych. Model (Claude Haiku 5.5 przez Pydantic AI) decyduje, które procedury wywołać i jakie zapytanie napisać nad wynikami. Nie widzi przy tym wierszy danych: liczby do komentarza wstawia backend.

Działa na bazie demonstracyjnej Microsoft `AdventureWorks2025` (SQL Server).

Dokumenty:

- [`docs/architecture/001-architektura-data-concierge.md`](docs/architecture/001-architektura-data-concierge.md): architektura całości.
- [`docs/architecture/002-dostep-modelu-do-danych.md`](docs/architecture/002-dostep-modelu-do-danych.md): co z danych trafia do dostawcy modelu (tryby `none` i `minimal`, kotwice, kolumny wrażliwe) i wyniki ewaluacji.
- [`db/README.md`](db/README.md): procedury, konto bazodanowe i wdrożenie.

## Wymagania

- Windows z lokalnym SQL Server (usługa `MSSQLSERVER`) i bazą `AdventureWorks2025` odtworzoną z kopii Microsoftu. Instancja musi mieć **tryb mieszany uwierzytelniania** (konto agenta to login SQL), co opisuje [`db/README.md`](db/README.md).
- `sqlcmd` i ODBC Driver 18 for SQL Server.
- Python 3.14 i [uv](https://docs.astral.sh/uv/).
- Klucz API Anthropic.

## Instalacja

```powershell
uv sync
Copy-Item .env.example .env
```

W `.env` uzupełnij co najmniej:

| Zmienna | Znaczenie |
|---------|-----------|
| `DC_SQL_PASSWORD` | hasło konta `agent_reader`, które utworzy skrypt wdrożenia |
| `DC_SQL_TRUST_SERVER_CERTIFICATE` | `true` dla instancji lokalnej z certyfikatem samopodpisanym |
| `ANTHROPIC_API_KEY` | klucz Anthropic |
| `DC_MODEL_DATA_ACCESS` | `none` (domyślnie) albo `minimal`, zobacz [Tryby dostępu modelu do danych](#tryby-dostępu-modelu-do-danych) |

Pozostałe ustawienia (model, limity, ścieżka katalogu) mają w `.env.example` wartości domyślne z opisem.

Wdrożenie procedur `api.*` i konta `agent_reader` (uwierzytelnianie Windows, skrypt idempotentny):

```powershell
./db/deploy.ps1
```

Gdy usługa SQL Server ma start ręczny, uruchom ją przed pracą w PowerShellu jako administrator: `net start MSSQLSERVER`.

Przy starcie usługa sprawdza katalog `catalog/agent-procedures.yaml` z metadanymi procedur w bazie. Jeśli się nie zgadzają, kończy się błędem konfiguracji.

## Uruchomienie

### Wiersz poleceń

Jedno pytanie:

```powershell
uv run data-concierge ask --user 1 "raport sprzedaży po miesiącach za 2024"
```

Rozmowa: każdy wiersz standardowego wejścia to kolejna wiadomość w tej samej sesji, np. odpowiedź na pytanie doprecyzowujące.

```powershell
"Pokaż sprzedaż klienta Mohamed Pal w 2024", "Chodzi o klienta 12698" | uv run data-concierge chat --user 1
```

`uv run data-concierge chat --user 1` bez potoku czyta wiadomości z klawiatury. Koniec rozmowy to `Ctrl+Z`, potem `Enter`.

Każda tura wypisuje odpowiedź JSON. Kod wyjścia `ask` to 1 przy statusie `error`.

### HTTP

```powershell
uv run uvicorn data_concierge.bootstrap:create_app --factory
```

```powershell
$body = @{ prompt = "sprzedaż po kategoriach produktów w 2024"; user_id = 1 } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/ask -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

Kolejną wiadomość w tej samej rozmowie wysyłasz z `session_id` z poprzedniej odpowiedzi. `GET /health` zwraca `{"status": "ok"}`. Błąd przetwarzania to status `error` w odpowiedzi HTTP 200, a 422 dostaje tylko niepoprawne żądanie.

### Odpowiedź

```json
{
  "status": "ok",
  "session_id": "…",
  "message": "Sprzedaż netto w 2024: 43 671 889,51, najwięcej w miesiącu 2024-06-01 (5 063 798,38).",
  "tables": [{"handle": "r2", "title": "…", "columns": ["…"], "rows": ["…"], "row_count": 12, "truncated": false}],
  "assumptions": ["Miara: sprzedaż netto (SubTotal, bez podatku i frachtu).", "Okres: 2024-01-01 – 2024-12-31."],
  "clarification": null,
  "provenance": [{"step": "procedure", "name": "api.SalesOrder_List", "…": "…"}, {"step": "query", "sql": "…", "…": "…"}]
}
```

`status` to `ok`, `no_data`, `needs_clarification` (z pytaniem i kandydatami w `clarification`), `unsupported` albo `error`. Kwoty w tabelach są tekstem, żeby nie tracić precyzji. `provenance` ma każde wywołanie procedury z parametrami i każde zapytanie DuckDB, więc wynik da się odtworzyć bez modelu. Pełny kontrakt opisuje 001, sekcja 9.

## Tryby dostępu modelu do danych

Tryb ustawia `DC_MODEL_DATA_ACCESS` dla całego procesu. Zmiana wymaga restartu.

| | `none` (domyślny) | `minimal` |
|---|---|---|
| Model widzi | katalog, schematy wyników, liczby wierszy, zakresy dat, klucze techniczne (`CustomerID`) i SQL, który sam napisał | to samo oraz wartości wymiarów (np. typ klienta, terytorium) i na końcu tury tabele zwracane użytkownikowi |
| Model nie widzi | nazw klientów, produktów i osób ani kwot i ilości | wartości kolumn oznaczonych w katalogu jako wrażliwe (`sensitive: true`) |
| Komentarz | szablon z kotwicami, np. `Sprzedaż netto w 2024: {r3.net_sales}`, wartości wstawia backend | pełniejszy (trend, najlepszy i najsłabszy okres), pisany w drugiej fazie po obejrzeniu tabel, też z kotwicami |

W obu trybach użytkownik dostaje pełne tabele, także z kolumnami wrażliwymi. Kotwice formatują liczby po polsku (`43 671 889,50`). Szczegóły, ryzyka i decyzje zawiera dokument 002.

## Scenariusz demo

Prompty z zestawu ewaluacyjnego (`evals/cases.yaml`). Dane obejmują zamówienia od 2022-05-30 do 2025-06-29.

| Prompt | Co pokazuje |
|--------|-------------|
| `daj mi listę klientów z miejscowości Berlin` | procedura z parametrem i lista; Berlin występuje w kilku regionach, więc wynik ma kolumnę regionu |
| `raport sprzedaży po miesiącach za 2024` | zapytanie DuckDB nad wynikiem procedury i komentarz z sumą roczną i najlepszym miesiącem przez kotwice |
| `tabela sprzedaży w 2024 - kwartały w wierszach, kategorie produktów w kolumnach` | `PIVOT` z wartościami kategorii z katalogu |
| `Pokaż sprzedaż klienta Mohamed Pal w 2024`, potem `Chodzi o klienta 12698` | pytanie doprecyzowujące z kandydatami zbudowanymi przez backend, potem druga procedura z `CustomerID` |
| `Sprzedaż po miesiącach w 2024 dla klientów z Berlina` | dwie procedury złączone w jednym zapytaniu |
| `5 klientów z największą sprzedażą netto w 2024` | w trybie `minimal` komentarz wymienia klientów i kwoty przez kotwice, choć model nie widzi nazw |
| `Jaki jest stan magazynowy produktu HL Fork?` | status `unsupported`, bo żadna procedura nie zwraca stanów magazynowych |

Te same prompty w obu trybach (`none`, potem `minimal`) pokazują różnicę w komentarzu przy tych samych tabelach.

## Testy i ewaluacja

```powershell
uv run ruff format --check
uv run ruff check
uv run mypy
uv run pytest                    # testy jednostkowe, bez bazy i modelu
uv run pytest -m integration     # z lokalnym SQL Server
```

Zestaw ewaluacyjny (37 przypadków) idzie przez prawdziwy model i bazę, a każdy przebieg kosztuje kilka centów:

```powershell
$env:DC_MODEL_DATA_ACCESS = "none"; uv run pytest -m eval tests/evals
$env:DC_MODEL_DATA_ACCESS = "minimal"; uv run pytest -m eval tests/evals
```

Raport z trafnością według grup oraz miarami (ponowienia, liczby wpisane poza kotwicami, czas i koszt tury) pojawia się na końcu przebiegu. Raport i pełny zapis każdego przypadku trafiają do `evals/runs/<początek>-<tryb>/`, poza gitem. Ostatnie wyniki: 37/37 w obu trybach (002, sekcja 9.1).

## Struktura

| Katalog | Zawartość |
|---------|-----------|
| `src/data_concierge/` | moduły `core`, `catalog`, `procedures`, `results`, `query`, `agent`, `api`; w każdym warstwy controller → service → repository |
| `catalog/agent-procedures.yaml` | katalog procedur pisany pod agenta: kiedy użyć, parametry, kolumny z rolami i wrażliwością |
| `db/` | skrypty T-SQL i `deploy.ps1` |
| `evals/` | przypadki ewaluacji; przebiegi w `evals/runs/` (poza gitem) |
| `tests/` | testy jednostkowe, integracyjne i ewaluacja |
| `docs/` | architektura, plany, standardy kodu |

Zasady pracy nad kodem (język, standardy, uv i ruff, cykl Compound Engineering) są w [`CLAUDE.md`](CLAUDE.md).

## Ograniczenia POC

- Brak uwierzytelniania: `user_id` przekazuje wywołujący, a procedury przyjmują `@UserId` bez sprawdzania.
- Sesje żyją w pamięci jednego procesu i znikają przy restarcie.
- Tylko odczyt: brak procedur zapisu.
- Treści komentarza (np. ocen typu „duża część przychodu”) nic nie sprawdza. Ewaluacja mierzy tylko liczby wpisane poza kotwicami.
- Ochrona przed prompt injection i wrażliwość kolumn odporna na aliasy w zapytaniach są odłożone (002, sekcje 10 i 12).
