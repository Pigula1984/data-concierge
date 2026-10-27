# Scenariusz demo POC (28.10.2026)

Trzy prompty w CLI: jedna procedura, dwie procedury ze złączeniem w DuckDB (w obu trybach) i pytanie doprecyzowujące. Wszystkie polecenia dla PowerShella w katalogu repozytorium. Oczekiwane wyniki pochodzą z przebiegu kontrolnego z 27.10.2026 i z ewaluacji z 25.10.2026. Model może inaczej sformułować komentarz, ale liczby w tabelach muszą być te same.

Prezentacja online: https://claude.ai/artifact/J3C5Yx8bJCcawxwWD3QyPv (eksport do PDF i PPTX w menu Share › Export).

Prezentacja offline: [`prezentacja.html`](prezentacja.html) otwierasz w przeglądarce. Strzałki przełączają slajdy, `N` pokazuje notatki prelegenta, `F` włącza pełny ekran, a druk do PDF daje jeden slajd na stronę.

## 1. Przed spotkaniem

1. SQL Server (PowerShell jako administrator, gdy usługa ma start ręczny):

   ```powershell
   net start MSSQLSERVER
   ```

2. `.env` ma `DC_SQL_PASSWORD`, `ANTHROPIC_API_KEY` i `DC_SQL_TRUST_SERVER_CERTIFICATE=true`. Środowisko: `uv sync`.
3. W **każdym** nowym oknie PowerShella, które pokażesz na demo:

   ```powershell
   $OutputEncoding = [Console]::InputEncoding = [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
   $env:DC_AUDIT_LOG_PATH = "logs/demo.jsonl"
   ```

   Pierwszy wiersz jest potrzebny, bo CLI czyta i pisze UTF-8, a PowerShell 5.1 domyślnie wysyła do potoku ASCII, przez co „Pokaż” dochodzi jako „Poka?”. Drugi wiersz daje osobny log audytowy tylko z demo. Polecenie `audit` czyta ten sam plik, więc ustaw go w każdym oknie.

4. Funkcja do czytelnego wyświetlania odpowiedzi (wklej w tym samym oknie):

   ```powershell
   function Show-Answer($r) {
     $r | Select-Object status, session_id, message | Format-List
     if ($r.assumptions) { "Założenia:"; $r.assumptions | ForEach-Object { "  - $_" } }
     if ($r.clarification) { $r.clarification.question; $r.clarification.candidates | Format-Table id, description -AutoSize }
     foreach ($t in $r.tables) {
       "`n$($t.title) ($($t.row_count) wierszy)"
       $t.rows | Select-Object -First 15 | ForEach-Object {
         $row = $_; $o = [ordered]@{}
         for ($i = 0; $i -lt $t.columns.Count; $i++) { $o[$t.columns[$i].name] = $row[$i] }
         [pscustomobject]$o
       } | Format-Table -AutoSize | Out-String -Width 300
     }
   }
   ```

   Funkcja pokazuje pierwsze 15 wierszy każdej tabeli. Pełny JSON zobaczysz bez niej: `uv run data-concierge ask --user 1 "..."`.

5. Rozgrzewka kilka minut przed demo, np. prompt 1. Pierwsze wywołanie modelu po przerwie zapisuje cache promptu (ok. 8,8 tys. tokenów), więc jest wolniejsze.

## 2. Tryby: jak uruchomić usługę w `none` i `minimal`

Tryb czyta proces przy starcie z `DC_MODEL_DATA_ACCESS`. Zmienna środowiskowa ma pierwszeństwo przed `.env`.

| Tryb | Ustawienie w oknie PowerShella | Model widzi |
|------|--------------------------------|-------------|
| `none` (domyślny) | `$env:DC_MODEL_DATA_ACCESS = "none"` | metadane: schemat, liczba wierszy, zakresy dat, klucze techniczne |
| `minimal` | `$env:DC_MODEL_DATA_ACCESS = "minimal"` | to samo oraz wartości wymiarów i na końcu tury tabele dla użytkownika (do 50 wierszy, bez kolumn wrażliwych) |

- **CLI:** każde `ask` to nowy proces, więc zmiana zmiennej działa od następnego polecenia. `chat` trzeba zamknąć i uruchomić ponownie.
- **HTTP:** po zmianie zmiennej zatrzymaj (`Ctrl+C`) i uruchom ponownie serwer:

  ```powershell
  uv run uvicorn data_concierge.bootstrap:create_app --factory
  ```

- **Powrót do wartości z `.env`:** `Remove-Item Env:DC_MODEL_DATA_ACCESS`.
- **Dwa tryby obok siebie:** dwa okna, w każdym własny tryb i własny log (`logs/demo-none.jsonl`, `logs/demo-minimal.jsonl`), bo dwa procesy nie powinny pisać do jednego pliku jednocześnie.
- **Sprawdzenie trybu:** `uv run data-concierge audit <session_id>` w pierwszym kroku pokazuje `mode none` albo `mode minimal`.

## 3. Prompty

### Prompt 1: dane z jednej procedury

Tryb `none`.

```powershell
$env:DC_MODEL_DATA_ACCESS = "none"
$r = uv run data-concierge ask --user 1 "daj mi listę klientów z miejscowości Berlin" | Out-String | ConvertFrom-Json
Show-Answer $r
```

Oczekiwany wynik: status `ok`, tabela „Klienci z miejscowości Berlin” z **198** wierszami. W założeniach jest informacja, że Berlin występuje w kilku regionach (Brandenburg, Hessen, Hamburg, Nordrhein-Westfalen, Saarland), więc tabela ma kolumnę regionu.

Co pokazać:

- jedna procedura `api.Customer_List` z parametrem `City = Berlin` (pole `provenance`);
- użytkownik widzi nazwy klientów i numery kont, choć model ich nie widział: to kolumny `sensitive`;
- 198 to liczba wierszy (adresów), a nie klientów. Jeden klient może mieć kilka adresów.

### Prompt 2: dwa źródła, złączenie i przekształcenie, w obu trybach

Najpierw `none`:

```powershell
$env:DC_MODEL_DATA_ACCESS = "none"
$r = uv run data-concierge ask --user 1 "Sprzedaż po miesiącach w 2024 dla klientów z Berlina" | Out-String | ConvertFrom-Json
Show-Answer $r
$r.provenance | Format-List step, handle, name, rows, submitted_sql
```

Potem ten sam prompt w `minimal`:

```powershell
$env:DC_MODEL_DATA_ACCESS = "minimal"
$r = uv run data-concierge ask --user 1 "Sprzedaż po miesiącach w 2024 dla klientów z Berlina" | Out-String | ConvertFrom-Json
Show-Answer $r
```

Oczekiwany wynik w obu trybach: tabela z **12** miesiącami 2024, suma roczna **237 350,54**, **145** zamówień, najlepszy miesiąc październik (**91 192,37**), najsłabszy kwiecień (**782,99**, 1 zamówienie).

Przykładowe komentarze:

- `none`: „Sprzedaż netto klientów z Berlina w 2024 roku wyniosła 237 350,54, w 145 zamówieniach. Najwięcej sprzedaży przypadło na miesiąc 2024-10-01 (91 192,37).”
- `minimal`: „Sprzedaż netto klientów z Berlina w 2024 roku wyniosła 237 350,54 w 145 zamówieniach. Najsłabszy był kwiecień (2024-04-01, 782,99), a najlepszy październik (2024-10-01, 91 192,37). Sprzedaż wyraźnie wzrosła w drugiej połowie roku, z dużym skokiem w lipcu do 52 575,55.”

Co pokazać:

- w `provenance` dwie procedury (`api.Customer_List`, 198 wierszy, i `api.SalesOrder_List`, 14 244 wierszy) oraz zapytanie z `JOIN` po `CustomerID` i `GROUP BY` miesiąca. Nie ma procedury „sprzedaż klientów z miasta”;
- `SELECT DISTINCT "CustomerID"`, bo lista klientów ma wiersz na adres;
- tabela jest identyczna w obu trybach, różni się tylko komentarz;
- `uv run data-concierge audit $r.session_id` pokazuje krok po kroku decyzje modelu, wykonane procedury i SQL, a w `minimal` także fazę komentarza.

### Prompt 3: pytanie doprecyzowujące (Mohamed Pal)

Tryb `none`, rozmowa w `chat`, bo druga tura musi trafić do tej samej sesji, a ta żyje w pamięci procesu:

```powershell
$env:DC_MODEL_DATA_ACCESS = "none"
uv run data-concierge chat --user 1
```

Wpisz kolejno (każdy wiersz zatwierdź `Enter` i poczekaj na JSON):

```text
Pokaż sprzedaż klienta Mohamed Pal w 2024
Chodzi o klienta 12698
```

Koniec rozmowy: `Ctrl+Z`, potem `Enter`.

Oczekiwany wynik:

1. Status `needs_clarification`, pytanie w rodzaju „Znaleziono 3 klientów o tej nazwie. Którego wybierasz?” i kandydaci:

   | id | description |
   |----|-------------|
   | 12698 | AW00012698, Australia, Australia, 3 |
   | 20550 | AW00020550, United Kingdom, United Kingdom, 1 |
   | 28763 | AW00028763, Southwest, United States, 1 |

   Ostatnia liczba w opisie to liczba zamówień klienta.

2. Po „Chodzi o klienta 12698”: status `ok`, 1 zamówienie z 2024-10-28 (nr 58889), sprzedaż netto **2294,99**, z podatkiem i frachtem **2535,96**.

Co pokazać:

- model nie wybiera klienta sam: przy kilku trafieniach pyta;
- listę kandydatów buduje backend z uchwytu wyniku wyszukiwania, więc numery kont trafiają do użytkownika, ale nie do modelu;
- w drugiej turze `CustomerID` 12698 przechodzi do `api.SalesOrder_List` jako parametr.

Zapasowo, gdy nie chcesz pisać na żywo (obie wiadomości w jednym poleceniu):

```powershell
"Pokaż sprzedaż klienta Mohamed Pal w 2024", "Chodzi o klienta 12698" | uv run data-concierge chat --user 1
```

**Uwaga:** `audit` uruchamiaj po wyjściu z `chat` (`Ctrl+Z`, `Enter`), jako osobne polecenie:

```powershell
uv run data-concierge audit <session_id>
```

Wpisany wewnątrz `chat` jest zwykłym promptem: model odpowie `unsupported`.

## 4. Gdy coś nie działa

| Objaw | Przyczyna i rozwiązanie |
|-------|-------------------------|
| Błąd konfiguracji przy starcie, brak połączenia | SQL Server nie działa: `net start MSSQLSERVER` jako administrator. |
| Polskie znaki jako `?` albo krzaczki | Brak pierwszego wiersza z punktu 1.3 w tym oknie. |
| `ConvertFrom-Json` zgłasza błąd | `chat` wypisuje kilka dokumentów JSON naraz. `Show-Answer` działa tylko z `ask`. |
| Tura trwa ponad 15 s | Pierwsze wywołanie po przerwie zapisuje cache. Kolejne powinny trwać 7–10 s. |
| Status `error` z informacją o wygasłej sesji | Sesje żyją w pamięci procesu. Rozmowa wymaga jednego `chat` albo jednego serwera HTTP. |
| Brak sieci lub API | Pokaż oczekiwane wyniki z tego pliku i przebieg z `uv run data-concierge audit <session_id>` z wcześniejszej próby. |
