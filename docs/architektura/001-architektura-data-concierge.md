# Data Concierge: opis architektury backendu (wersja 3, happy-flow tylko do odczytu)

Status: opis koncepcji, bez implementacji. Data: 2026-10-10.
Podstawa: notatki z `docs/prompts/podsumowanie-agent.md`, research źródłowy (sekcja 14), sprawdzenie danych i metadanych w lokalnej bazie AdventureWorks2025.

> **Historia zmian.**
> **v2:** źródłem danych jest katalog procedur składowanych, a nie zbiory danych sprzedaży. Stack: Python. Modele: wyłącznie Anthropic.
> **v3:** (1) osobny plik YAML z opisami procedur pisanymi pod agenta; (2) silnik obliczeń: DuckDB; (3) zakres zawężony do backendu: wejściem jest prompt, wyjściem dane w JSON, bez UI, wykresów i streamingu; (4) brak filtrowania w aplikacji: każdy filtr musi być parametrem procedury.
> **v3.1:** model `claude-haiku-5-5` z `effort: medium`; specyfikacja obliczeń niezależna od silnika (DuckDB teraz, Polars jako możliwa wersja alternatywna); komentarz w v1 pisany przez model swobodnie, bez placeholderów i bez kontroli liczb (do zrobienia później, razem z ochroną przed prompt injection); projekt testowy z jednym właścicielem.
> **v3.2:** pętla agenta na **Pydantic AI** zamiast samego Anthropic SDK (kod od razu gotowy na inne modele); filtrowanie po pobraniu w DuckDB/Polars jako przewidziane rozszerzenie (nie w v1); dedykowane konto SQL z minimalnymi uprawnieniami (sekcja 5.5); wyjaśnienie, czy DuckDB to sandbox (sekcja 7.4a).

## 1. Cel i zakres

Backend asystenta AI: przyjmuje prompt użytkownika i zwraca dane. Baza udostępnia dane wyłącznie przez procedury, które zwracają **surowe wiersze**. Grupowanie, sumy i pivoty robi backend, a nie kolejne procedury. Frontend jest poza zakresem tego ćwiczenia, a interfejsem jest funkcja lub endpoint `prompt → JSON`.

Szczęśliwa ścieżka v1:

1. backend dostaje prompt (i tożsamość użytkownika),
2. agent wybiera procedurę z katalogu i ustala jej parametry (w razie potrzeby zwraca pytanie doprecyzowujące),
3. backend wywołuje procedurę i zapisuje wynik,
4. agent opisuje, **co** policzyć, a backend liczy to deterministycznie,
5. backend zwraca status, tabele z danymi, krótki komentarz i proweniencję (jak policzono).

Przykładowe prośby:

| # | Prośba | Co musi umieć system |
|---|--------|----------------------|
| P1 | „Daj mi listę klientów z miejscowości Berlin” | wybór procedury, parametr miasta, obsługa niejednoznacznej nazwy |
| P2 | „Pokaż sprzedaż klienta Mohamed Pal w 2024” | wyszukanie klienta, dopytanie przy kilku trafieniach, druga procedura |
| P3 | „Zrób raport sprzedaży po miesiącach za 2024” | pobranie surowych zamówień, deterministyczna agregacja po miesiącu |
| P4 | „Pokaż strukturę materiałową produktu HL Mountain Frame” | inna dziedzina, istniejąca procedura `uspGetBillOfMaterials`, dowód, że projekt nie jest „pod sprzedaż” |

Poza zakresem v1: frontend, zapisy, procesy w tle, strażnicy, zatwierdzanie, obrona przed iniekcją. Sekcja 12 pokazuje, jak v1 zostawia na nie miejsce.

## 2. Wymagania

- **Dane tylko przez procedury** z katalogu. Model nie pisze SQL do bazy.
- **Żadnych procedur „pod pytanie”.** Procedury zwracają dane w naturalnym ziarnie (klient, zamówienie, pozycja), a zestawienia robi backend.
- **Filtry w v1 tylko w procedurach.** Zakres danych zawężają parametry procedury. Jeśli filtra nie da się wyrazić parametrem, agent w v1 mówi, że tego nie obsługuje (status `unsupported`). **Docelowo** brakujące filtry wykona backend (krok `filter` w DuckDB lub Polars, sekcja 7.2). Projekt jest na to przygotowany, ale w v1 tego nie włączamy.
- **Obliczenia deterministyczne.** Model nie liczy sum ani średnich i nie przepisuje liczb „z głowy” (sekcja 7).
- **Python.** Na start **modele Anthropic**, ale kod od początku niezależny od dostawcy, żeby później dało się użyć innych modeli (OpenAI, Azure, lokalne) bez przepisywania.
- **Wymienna baza danych.** Zależność od silnika siedzi w jednym adapterze, który wywołuje procedury.
- **Agent działa w imieniu użytkownika.** Tożsamość jest przekazywana do procedur już w v1, nawet jeśli procedury jeszcze jej nie sprawdzają.

## 3. Najważniejsze decyzje w skrócie

| Decyzja | Wybór dla v1 | Dlaczego |
|---------|--------------|----------|
| Skąd agent wie o procedurach | **Osobny plik YAML z opisami pisanymi pod agenta** (kiedy użyć, kiedy nie, przykłady, powiązania), walidowany przy starcie z metadanymi bazy | Wąski, opisany kontekst poprawia trafność [1]. Opis techniczny procedury i opis dla agenta to różne rzeczy (sekcja 5.1). |
| Jak model wywołuje procedury | Jedno narzędzie na procedurę, generowane z YAML, ze ścisłym schematem parametrów | Typowane parametry, walidacja przed wywołaniem, model nie wymyśla nazw [2]. |
| Duża liczba procedur | Własne narzędzie `find_procedures` przeszukujące YAML | Działa z każdym modelem. Tool search z Claude API byłby wygodniejszy, ale jest specyficzny dla Anthropic (sekcja 6.3). |
| Gdzie są dane | W magazynie wyników backendu, pod uchwytami. Model widzi schemat, statystyki i kilka wierszy. | Koszt, opóźnienie, limit kontekstu [4][5]. |
| Jak liczyć | Model pisze **deklaratywną specyfikację przekształcenia** (JSON), backend ją waliduje, tłumaczy na SQL i wykonuje w **DuckDB**. Specyfikacja nie zależy od silnika, więc później można dodać wykonawcę w Polars. | Ten sam wynik dla tych samych danych i tej samej specyfikacji. Model wybiera „co”, kod liczy „ile”. |
| Liczby w komentarzu | v1: model pisze komentarz swobodnie, korzystając z faktów policzonych przez backend. Bez kontroli. | Prostota na start. Ryzyko pomyłki przy przepisywaniu liczb jest znane i odłożone na później (sekcja 7.5). |
| Pętla agenta | **Pydantic AI** z modelem `anthropic:claude-haiku-5-5` | Jeden kod dla wielu dostawców (zmiana modelu = zmiana konfiguracji) [6]. Vercel AI SDK jest w TypeScript, więc nie pasuje do backendu w Pythonie (sekcja 11). |
| Uprawnienia DB | Dedykowane konto SQL z samym `EXECUTE` na wybranych procedurach, bez dostępu do tabel, od v1 | Przy dostępie wyłącznie przez procedury kosztuje to tyle samo co konto read-only (sekcja 5.5). |

## 4. Komponenty

```mermaid
flowchart LR
    C["Klient wywołujący<br/>(test, CLI, później UI)"]
    API["Backend API<br/>ask(prompt, user) → JSON"]
    LOOP["Pętla agenta<br/>(Pydantic AI)"]
    LLM[("Model<br/>(na start Claude API)")]
    REG["Rejestr narzędzi<br/>(generowany z YAML)"]
    CAT["Katalog agenta<br/>(YAML + walidacja z metadanymi DB)"]
    EXE["Wykonawca procedur<br/>(adapter per silnik)"]
    DB[("Baza<br/>procedury, konto EXECUTE")]
    RS["Magazyn wyników<br/>(uchwyty, tabele DuckDB)"]
    TR["Silnik przekształceń<br/>(walidacja → SQL → DuckDB)"]
    OUT["Składanie odpowiedzi<br/>(komentarz, dane, proweniencja)"]
    PROV["Proweniencja"]

    C --> API --> LOOP
    LOOP <--> LLM
    LOOP --> REG
    REG --> CAT
    REG --> EXE --> DB
    EXE --> RS
    REG --> TR <--> RS
    LOOP --> OUT
    OUT --> RS
    OUT --> API
    EXE --> PROV
    TR --> PROV
    PROV --> OUT
```

| Komponent | Odpowiedzialność |
|-----------|------------------|
| **Backend API** | `ask(prompt, user, session_id?) → Response`. Buduje `RequestContext` (user_id, role, id korelacji), trzyma historię sesji (potrzebna przy pytaniach doprecyzowujących). W v1 może to być funkcja Pythona i cienki endpoint HTTP. |
| **Pętla agenta** | Agent Pydantic AI: wysyła rozmowę do modelu, wykonuje wywołania narzędzi, pilnuje limitu kroków, kończy wynikiem `present_result` (typ `Response`). Bez logiki biznesowej. |
| **Katalog agenta** | Plik YAML: opisy procedur pod agenta, parametry, kolumny wyniku z typami i rolami (sekcja 5). |
| **Rejestr narzędzi** | Generuje definicje narzędzi z YAML, waliduje parametry, dokleja parametry wstrzykiwane przez backend (np. `@UserId`). |
| **Wykonawca procedur** | `EXEC` z parametrami, limit wierszy i czasu, zamiana wyniku na tabelę z typami z katalogu (bez utraty precyzji), zapis do magazynu. Jedyny komponent zależny od silnika bazy. |
| **Magazyn wyników** | Wyniki sesji pod uchwytami `r1`, `r2`… jako tabele w DuckDB (w pamięci procesu), ze schematem i statystykami. |
| **Silnik przekształceń** | Waliduje specyfikację względem schematu uchwytu, tłumaczy ją na SQL DuckDB i wykonuje (sekcja 7). |
| **Składanie odpowiedzi** | Łączy komentarz modelu z pełnymi danymi wskazanych uchwytów i proweniencją. Docelowo także kontrola liczb w komentarzu (sekcja 7.5). |
| **Proweniencja** | Zapisuje każde wywołanie procedury, każdą specyfikację i wygenerowany SQL. Pozwala odtworzyć wynik bez modelu. Zalążek logu audytowego. |

## 5. Katalog agenta (YAML)

### 5.1. Dlaczego osobny plik pod agenta

Dokumentacja techniczna procedury („zwraca nagłówki zamówień z kolumnami…”) jest pisana dla programisty. Agent potrzebuje innych informacji: **kiedy** sięgnąć po procedurę, kiedy **nie**, skąd wziąć wartości parametrów i jak interpretować kolumny. Dlatego katalog agenta to osobny plik (np. `catalog/agent-procedures.yaml`), utrzymywany razem z promptem i testami agenta.

- Plik jest **jedynym źródłem** opisów dla modelu: z niego powstają definicje narzędzi i skrót katalogu w instrukcji systemowej.
- Plik **nie dubluje** metadanych bazy. Typy parametrów i kolumn są w nim zadeklarowane, ale przy starcie backend porównuje je z bazą (sekcja 5.3), więc rozjazd wychodzi od razu.
- Zmiana w pliku to zmiana zachowania agenta, więc każdą zmianę sprawdza zestaw ewaluacyjny (sekcja 13).

### 5.2. Przykładowy wpis

```yaml
- procedure: api.SalesOrder_List          # nazwa w bazie
  tool: sales_order_list                  # nazwa narzędzia dla modelu
  access: read
  summary: Zamówienia sprzedaży (nagłówki) w okresie, opcjonalnie dla jednego klienta.
  use_when:
    - pytanie dotyczy sprzedaży, obrotów lub zamówień w czasie
    - potrzebny jest raport sprzedaży po dniach, miesiącach, latach, klientach lub terytoriach
  not_for:
    - sprzedaż w podziale na produkty lub kategorie → użyj sales_order_line_list
    - wyszukanie klienta po nazwie → najpierw customer_search
  grain: jeden wiersz = jedno zamówienie, dane nie są zgrupowane
  synonyms: [sprzedaż, zamówienia, obroty]
  max_rows: 100000
  params:
    - name: DateFrom
      arg: date_from
      type: date
      required: true
      hint: Początek okresu, włącznie (OrderDate). Dla „2024” podaj 2024-01-01.
    - name: DateTo
      arg: date_to
      type: date
      required: true
      hint: Koniec okresu, wyłącznie. Dla „2024” podaj 2025-01-01.
    - name: CustomerID
      arg: customer_id
      type: int
      required: false
      hint: Id klienta. Wartość bierz wyłącznie z wyniku customer_search, nigdy nie zgaduj.
    - name: UserId
      type: int
      inject: request_context.user_id      # model tego parametru nie widzi
  columns:
    - {name: SalesOrderID, type: int,           role: id}
    - {name: OrderDate,    type: date,          role: time,      hint: Data zamówienia}
    - {name: CustomerID,   type: int,           role: id}
    - {name: CustomerName, type: string,        role: dimension}
    - {name: CustomerType, type: string,        role: dimension, values: [individual, store]}
    - {name: Territory,    type: string,        role: dimension}
    - {name: SubTotal,     type: decimal(19,4), role: measure,   hint: Sprzedaż netto, bez podatku i frachtu, default_for: [sprzedaż]}
    - {name: TotalDue,     type: decimal(19,4), role: measure,   hint: Kwota z podatkiem i frachtem}
  data_range: {column: OrderDate}           # min/max odczytywane przy starcie
  examples:
    - prompt: "sprzedaż za I kwartał 2025"
      call: {date_from: 2025-01-01, date_to: 2025-04-01, customer_id: null}
    - prompt: "raport sprzedaży po miesiącach za 2024"
      call: {date_from: 2024-01-01, date_to: 2025-01-01, customer_id: null}
      then: "transform: miesiąc z OrderDate, suma SubTotal"
```

Pola istotne dla agenta:

- **`use_when` / `not_for`** kierują wyborem procedury i odsyłają do właściwej, gdy pytanie jest „obok”.
- **`hint` przy parametrze** mówi, skąd wziąć wartość (np. „bierz id z `customer_search`, nie zgaduj”) i jak zamienić język naturalny na wartość (okres → daty).
- **`role` kolumny** (`id`, `dimension`, `measure`, `time`) mówi silnikowi przekształceń, co wolno z kolumną zrobić: sumować można tylko `measure`, ziarno czasu stosować tylko do `time`.
- **`default_for`** rozstrzyga typowe niejednoznaczności („sprzedaż” = SubTotal). Agent pokazuje to w `assumptions`.
- **`inject`** oznacza parametry doklejane przez backend (tożsamość). Model ich nie widzi ani nie ustawia.
- **`data_range`** trafia do kontekstu modelu. Względne okresy poza zakresem danych kończą się statusem `no_data` z informacją o dostępnym zakresie.
- **`examples`** to przykłady pytanie → wywołanie. Pełnią rolę few-shot, podobnie jak przykłady w Fabric data agent [1], i od razu nadają się do zestawu testów.

Każdy filtr potrzebny użytkownikom musi istnieć jako parametr procedury. Gdy agent dostaje prośbę z filtrem, którego żadna procedura nie obsługuje, zwraca `unsupported` z wyjaśnieniem. Lista takich odmów to gotowa informacja, jakie parametry warto dodać do procedur.

### 5.3. Walidacja z metadanymi bazy

| Element | Źródło w bazie | Uwagi |
|---------|----------------|-------|
| Parametry (nazwa, typ) | `sys.parameters` | Sprawdzone: działa dla wszystkich procedur w bazie. |
| Kolumny wyniku (nazwa, typ) | `sys.dm_exec_describe_first_result_set_for_object` | Sprawdzone na `dbo.uspGetEmployeeManagers` (zwraca 7 kolumn z typami). Nie działa dla procedur z tabelami tymczasowymi, dynamicznym SQL lub wieloma zestawami wyników. Wtedy obowiązuje lista z YAML, sprawdzana przy pierwszym wywołaniu. |
| Opisy pod agenta | tylko YAML | W bazie procedury nie mają `MS_Description` (0 extended properties). |

Przy starcie backend porównuje YAML z bazą. Brakująca procedura, inny typ parametru albo niezgodna lista kolumn to błąd konfiguracji, a nie niespodzianka w rozmowie. Ten sam mechanizm może wygenerować szkielet wpisu YAML dla nowej procedury (parametry i kolumny), a człowiek dopisuje opisy pod agenta.

### 5.4. Zestaw procedur do testów na AdventureWorks2025

W bazie są 4 procedury zwracające dane i 6 procedur zapisu lub pomocniczych. Do katalogu v1 trafiają tylko procedury odczytu, a uzupełniamy je kilkoma nowymi, które zwracają surowe wiersze.

| Procedura | Status | Zwraca |
|-----------|--------|--------|
| `dbo.uspGetBillOfMaterials(@StartProductID, @CheckDate)` | istniejąca | struktura materiałowa produktu |
| `dbo.uspGetWhereUsedProductID(@StartProductID, @CheckDate)` | istniejąca | gdzie użyto komponentu |
| `dbo.uspGetEmployeeManagers(@BusinessEntityID)` | istniejąca | łańcuch przełożonych |
| `dbo.uspGetManagerEmployees(@BusinessEntityID)` | istniejąca | podwładni |
| `api.Customer_Search(@Name)` | nowa | klienci (osoby i sklepy) pasujący do nazwy, z numerem konta i terytorium |
| `api.Customer_List(@City, @CountryRegionCode, @CustomerType)` | nowa | klienci z adresem (miasto, region, kraj). Musi obejmować adresy osób **i** sklepów. |
| `api.SalesOrder_List(@DateFrom, @DateTo, @CustomerID)` | nowa | nagłówki zamówień z klientem i terytorium |
| `api.SalesOrderLine_List(@DateFrom, @DateTo, @CustomerID, @ProductCategoryID)` | nowa | pozycje z produktem, podkategorią i kategorią |
| `api.Product_Search(@Name)` | nowa | produkty pasujące do nazwy (potrzebne do P4) |
| `uspUpdateEmployee*`, `uspLogError`, `uspPrintError` | pominięte | zapis lub obsługa błędów (`access: write`, poza v1) |

Fakty z bazy, które wpływają na projekt:

- **Zakres zamówień: 2022-05-30 do 2025-06-29** (31 465 zamówień, 121 317 pozycji). Bez `data_range` w kontekście „ostatni kwartał” zwróci pustkę.
- **Nazwy nie są unikalne.** „Mohamed Pal” to 3 różnych klientów (CustomerID 12698, 20550, 28763). 31 nazw miast występuje w więcej niż jednym regionie, np. „Berlin” w 5 regionach Niemiec. „HL Mountain Frame” to 10 wariantów produktu.
- **Klient to osoba albo sklep.** 18 484 osoby, 1 336 sklepów, w tym 635 z osobą kontaktową.
- **Kwoty to `money`/`decimal`.** To ważne dla deterministyczności obliczeń (sekcja 7.4).

### 5.5. Konto bazodanowe agenta

Backend łączy się z SQL Server dedykowanym kontem (np. login i użytkownik `agent_reader`), używanym tylko przez agenta:

- `GRANT EXECUTE ON SCHEMA::api TO agent_reader` dla nowych procedur oraz `GRANT EXECUTE` osobno na każdą z czterech istniejących procedur odczytu w `dbo`.
- Brak `SELECT` na tabelach i widokach, brak ról typu `db_datareader`, brak uprawnień do procedur zapisu.
- Procedury i tabele mają tego samego właściciela (`dbo`), więc procedura czyta tabele dzięki mechanizmowi ownership chaining, choć konto samo nie ma do nich dostępu. Wyjątek: procedury z dynamicznym SQL (`sp_executesql`) przerywają ten łańcuch i wymagałyby `EXECUTE AS` lub podpisu certyfikatem. Nowe procedury piszemy więc bez dynamicznego SQL.
- Hasło lub inny sekret konta trzymamy w konfiguracji backendu, nigdy w prompcie. Model nie zna danych logowania i nie ma narzędzia, które by je ujawniało.
- Do walidacji katalogu przy starcie (sekcja 5.3) potrzebny jest odczyt metadanych procedur. Najprościej użyć do tego osobnego konta technicznego albo dać `VIEW DEFINITION` na schemacie `api`. Do sprawdzenia w prototypie, co minimalnie wystarcza.

## 6. Narzędzia widziane przez model

### 6.1. Narzędzia procedur (generowane)

Każda procedura z katalogu to osobne narzędzie, np. `sales_order_list`. Opis narzędzia składa się z `summary`, `use_when`, `not_for`, `grain` i zakresu danych. Schemat parametrów pochodzi z `params` i jest ścisły (`additionalProperties: false`, a jeśli framework i model na to pozwalają, także tryb `strict` dostawcy). Backend i tak sprawdza zakresy i wartości, a błąd wraca do modelu jako `tool_result` z `is_error` i czytelnym opisem.

```json
{
  "name": "sales_order_list",
  "description": "Zamówienia sprzedaży (nagłówki) w okresie, opcjonalnie dla jednego klienta. Jeden wiersz = jedno zamówienie, dane nie są zgrupowane. Używaj do: sprzedaż/obroty/zamówienia w czasie, raporty po okresach, klientach, terytoriach. Nie używaj do: sprzedaży po produktach (sales_order_line_list), wyszukiwania klienta (customer_search). Dane: 2022-05-30 – 2025-06-29.",
  "strict": true,
  "input_schema": {
    "type": "object",
    "properties": {
      "date_from":   {"type": "string", "format": "date", "description": "Początek okresu, włącznie. Dla „2024” podaj 2024-01-01."},
      "date_to":     {"type": "string", "format": "date", "description": "Koniec okresu, wyłącznie. Dla „2024” podaj 2025-01-01."},
      "customer_id": {"type": ["integer", "null"], "description": "Id klienta wyłącznie z wyniku customer_search."}
    },
    "required": ["date_from", "date_to", "customer_id"],
    "additionalProperties": false
  }
}
```

Wynik wywołania procedury zwracany modelowi (nie pełne dane):

```json
{
  "handle": "r1",
  "procedure": "api.SalesOrder_List",
  "row_count": 14244,
  "truncated": false,
  "columns": [{"name": "OrderDate", "type": "date", "role": "time"}, {"name": "SubTotal", "type": "decimal(19,4)", "role": "measure"}, "..."],
  "stats": {"OrderDate": {"min": "2024-01-01", "max": "2024-12-31"}, "CustomerType": {"distinct": ["individual", "store"]}},
  "preview": ["… do 10 wierszy …"]
}
```

### 6.2. Narzędzia stałe

| Narzędzie | Rola |
|-----------|------|
| `transform` | Przyjmuje specyfikację przekształcenia (sekcja 7) dla uchwytu i zwraca nowy uchwyt z podglądem i faktami. |
| `describe_result` | Pełny schemat i statystyki uchwytu (gdy podgląd nie wystarcza). |
| `present_result` | Koniec tury: status, komentarz, uchwyty do zwrócenia, założenia, ewentualne pytanie doprecyzowujące (sekcja 9). W Pydantic AI to nie zwykłe narzędzie, tylko **typ wyniku agenta** (`output_type`): framework udostępnia go modelowi jako specjalne narzędzie wyjściowe (nazwę `present_result` można mu nadać przez `ToolOutput`) i sam prosi o poprawkę, gdy odpowiedź nie pasuje do schematu [6]. |

### 6.3. Gdy procedur jest dużo

Dopóki procedur jest kilkanaście, wszystkie definicje mieszczą się w kontekście i są cache'owane (prompt caching). Gdy katalog urośnie, model dostaje na start tylko narzędzie `find_procedures(query)`, które przeszukuje `summary`, `use_when` i `synonyms` z YAML i zwraca kilka pasujących procedur. Narzędzia znalezionych procedur są udostępniane w kolejnym kroku (zestaw narzędzi ustalany dynamicznie, do sprawdzenia, jak najprościej zrobić to w Pydantic AI). Rozwiązanie działa z każdym modelem.

Claude API ma do tego wbudowany **tool search** (`defer_loading`) [8], ale dokumentacja Pydantic AI opisuje tool search tylko dla Claude na Bedrock, a nie dla bezpośredniego Claude API. Na teraz zostajemy przy własnym `find_procedures`. Próg przełączenia trzeba zmierzyć na zestawie testowym.

## 7. Deterministyczne obliczenia: jak to zrobić

### 7.1. Zasada

**Model decyduje, co policzyć. Kod liczy.** Model nigdy nie dostaje pełnych danych do zsumowania „w głowie” i nigdy sam nie wpisuje wyniku obliczeń. Opisuje przekształcenie w zamkniętym języku (specyfikacja JSON). Backend sprawdza specyfikację względem rzeczywistego schematu danych, tłumaczy ją na SQL i wykonuje w DuckDB. Te same dane wejściowe i ta sama specyfikacja dają zawsze ten sam wynik.

```
procedura ──► surowe wiersze (r1, tabela DuckDB) ──► specyfikacja od modelu ──► walidacja ──► SQL ──► DuckDB ──► r2 + fakty ──► odpowiedź JSON
```

### 7.2. Język specyfikacji (v1)

Specyfikacja to lista kroków nad jednym uchwytem. Zestaw kroków jest zamknięty i rozszerzany świadomie, gdy pojawi się potrzeba.

| `op` | Co robi | Przykład parametrów |
|------|---------|---------------------|
| `derive` | Nowa kolumna z funkcji z białej listy: `date_trunc`, `year`, `month`, `quarter`, arytmetyka dwóch kolumn | `{"name": "miesiac", "fn": "date_trunc", "unit": "month", "column": "OrderDate"}` |
| `aggregate` | Grupowanie i miary: `sum`, `count`, `count_distinct`, `avg`, `min`, `max` | `{"group_by": ["miesiac"], "measures": [...]}` |
| `fill_periods` | Uzupełnienie brakujących okresów zerami w **zadanym** zakresie | `{"column": "miesiac", "unit": "month", "from": "2024-01-01", "to": "2025-01-01"}` |
| `pivot` | Obrót: wiersze × kolumny × miara | `{"rows": "miesiac", "columns": "Territory", "measure": "sprzedaz_netto"}` |
| `sort`, `limit` | Kolejność i top N (np. 10 najlepszych klientów) | `{"by": [{"column": "sprzedaz_netto", "dir": "desc"}]}`, `{"n": 10}` |
| `select` | Wybór i zmiana nazw kolumn w wyniku | `{"columns": {"CustomerName": "Klient"}}` |

Do tego sekcja `facts`, czyli nazwane wartości skalarne liczone z wyniku (`sum`, `avg`, `min`, `max`, `argmax`, `count`). Tylko z nich model może korzystać w komentarzu.

**Przewidziane rozszerzenie: `filter`.** Filtr wierszy na pobranych danych (porównania, `in`, `between`, `is_null`, łączone `and`/`or`), np. `{"column": "CustomerType", "in": ["store"]}`, dla filtrów, których procedura nie ma jako parametru. Wchodzi do języka specyfikacji bez zmian w architekturze: walidacja sprawdza kolumny i typy, a wykonawca dodaje `WHERE` (DuckDB) lub `.filter()` (Polars). W YAML procedura dostanie wtedy listę kolumn, po których wolno filtrować w backendzie (`app_filters`). Warunek: procedura nadal musi mieć sensowne parametry zawężające (np. okres), bo filtr w backendzie oznacza pobranie większej liczby wierszy, a `max_rows` i flaga `truncated` pilnują, żeby wynik nie był po cichu niepełny. W v1 krok `filter` jest wyłączony. Nie ma też `join` dwóch uchwytów. Oba dojdą, gdy konkretna prośba będzie ich wymagać.

### 7.3. Walidacja przed wykonaniem

1. **Schemat:** specyfikacja jest modelem Pydantic (unia dyskryminowana po `op`). Z niego generowany jest `input_schema` narzędzia `transform`. Czy cały schemat mieści się w ograniczeniach trybu `strict`, trzeba sprawdzić w prototypie. Jeśli nie, zostaje walidacja Pydantic po stronie backendu.
2. **Semantyka, krok po kroku:** walidator śledzi schemat po każdym kroku. Sprawdza, czy kolumna istnieje (także ta utworzona w poprzednim kroku), czy `sum`/`avg` dotyczy kolumny `measure` o typie liczbowym, czy `date_trunc` dotyczy kolumny `time` i czy `pivot` nie wygeneruje setek kolumn (limit).
3. **Błąd wraca do modelu** jako `tool_result` z `is_error: true` i konkretnym komunikatem, np. „`sum` wymaga kolumny liczbowej; `CustomerName` to `string`. Dostępne miary: SubTotal, TotalDue”. Model poprawia specyfikację w kolejnym kroku pętli.

### 7.4. Silnik: DuckDB

**Czym jest DuckDB:** bazą analityczną (OLAP) uruchamianą wewnątrz procesu Pythona, jako biblioteka (`pip install duckdb`). Nie ma serwera ani instalacji. Dane trzyma w pamięci, a operuje się na nich zwykłym SQL. Jest zoptymalizowana pod grupowania i agregacje na dużych tabelach.

**Dlaczego DuckDB, a nie Polars czy pandas:**

| | DuckDB | Polars | pandas |
|---|--------|--------|--------|
| Czym jest | SQL-owa baza analityczna w procesie | Biblioteka DataFrame (tabele w pamięci) z własnym API wyrażeń, napisana w Rust | Klasyczna biblioteka DataFrame w Pythonie |
| Język operacji | SQL | Metody Pythona (`group_by`, `agg`, `pivot`…) | Metody Pythona |
| Typ dziesiętny | `DECIMAL(p,s)`, pełne wsparcie | Typ `Decimal` jest, ale stan wsparcia operacji trzeba by sprawdzić | Tylko jako obiekty Pythona (wolno) albo `float` (niedokładnie) |
| Czytelność proweniencji | Wygenerowany SQL jest czytelny dla każdego, kto zna SQL | Kod Polars trzeba znać | Kod pandas trzeba znać |
| Droga do wariantu B (sekcja 7.7) | Model pisze SQL nad uchwytem, ten sam silnik | Inny mechanizm | Inny mechanizm |

Polars jest bardzo szybki i popularny, ale na start nie daje nic ponad DuckDB, a wymagałby nauki nowego API. SQL jest znany, więc DuckDB jest naturalnym wyborem na v1.

**Wersja alternatywna w Polars.** Specyfikacja (sekcja 7.2) nie zawiera SQL, tylko opis operacji, więc silnik jest wymienny. Silnik przekształceń to interfejs `Executor` z jedną metodą `run(spec, tables) -> Result`. W v1 jest jedna implementacja (`DuckDBExecutor`, tłumaczy specyfikację na SQL). Wersja w Polars (`PolarsExecutor`, tłumaczy ją na wyrażenia Polars) korzysta z tej samej walidacji, tych samych testów i tego samego magazynu wyników, bo DuckDB i Polars wymieniają dane przez Arrow bez kopiowania. Dodatkowy zysk: uruchomienie obu wykonawców na tych samych specyfikacjach i porównanie wyników to mocny test poprawności każdego z nich. Warunek: obsługa `Decimal` w Polars musi zostać sprawdzona, zanim wyniki z Polars uznamy za równoważne.

**Jak to działa w praktyce:**

1. Wykonawca procedur odbiera wiersze (pyodbc zwraca `Decimal` dla `money`/`decimal` i `date`/`datetime` dla dat), buduje tabelę z typami z katalogu (np. Arrow z `decimal128(19,4)`) i rejestruje ją w DuckDB jako `r1`.
2. Silnik przekształceń tłumaczy zwalidowaną specyfikację na zapytanie. Nazwy kolumn pochodzą wyłącznie ze schematu uchwytu (biała lista, cytowane identyfikatory), a wartości idą jako parametry zapytania. Model nigdy nie dostarcza tekstu SQL.
3. Wynik zapisuje się jako `r2`, razem z wygenerowanym SQL w proweniencji.

SQL wygenerowany dla raportu miesięcznego z sekcji 10 (sprawdzony na DuckDB na danych testowych: sumy wychodzą jako dokładny `DECIMAL`, puste miesiące jako 0):

```sql
WITH s1 AS (
  SELECT *, CAST(date_trunc('month', "OrderDate") AS DATE) AS "miesiac" FROM r1
), s2 AS (
  SELECT "miesiac",
         SUM("SubTotal")                 AS "sprzedaz_netto",
         COUNT(DISTINCT "SalesOrderID")  AS "liczba_zamowien"
  FROM s1 GROUP BY "miesiac"
), periods AS (
  SELECT CAST(range AS DATE) AS "miesiac" FROM range(DATE '2024-01-01', DATE '2025-01-01', INTERVAL 1 MONTH)
)
SELECT p."miesiac",
       COALESCE(s2."sprzedaz_netto", 0)  AS "sprzedaz_netto",
       COALESCE(s2."liczba_zamowien", 0) AS "liczba_zamowien"
FROM periods p LEFT JOIN s2 USING ("miesiac")
ORDER BY p."miesiac";
```

**Warunki deterministyczności:**

- **Typy dokładne:** kwoty przez całą ścieżkę są `DECIMAL`. Rzutowanie na `float` daje sumy niedokładne i zależne od kolejności wierszy, czyli niedeterministyczne. Ścieżkę pyodbc → Arrow → DuckDB → JSON trzeba potwierdzić w prototypie.
- **Kolejność:** SQL bez `ORDER BY` nie gwarantuje kolejności wierszy. Generator zawsze dodaje `ORDER BY` (po kluczach grupowania, jeśli specyfikacja nie podaje własnego).
- **Brakujące okresy:** `fill_periods` korzysta z zakresu z prośby, a nie z min/max danych, bo miesiąc bez sprzedaży na początku lub końcu roku inaczej zniknąłby z raportu.
- **Testy:** każdy `op` ma testy jednostkowe porównujące wynik z równoważnym zapytaniem w SQL Server na tych samych danych.

### 7.4a. Czy DuckDB to sandbox?

Nie, i w wariancie A nie musi nim być. DuckDB to biblioteka działająca w procesie backendu, podobnie jak sterownik bazy. Sandbox jest potrzebny wtedy, gdy wykonuje się **kod napisany przez model**. Tu model nie dostarcza ani SQL, ani kodu, tylko specyfikację JSON z zamkniętej listy operacji. SQL buduje nasz kod: nazwy kolumn biorą się z białej listy schematu, a wartości idą jako parametry zapytania. Model nie ma jak „przemycić” polecenia do DuckDB. To samo dotyczy Polars.

Mimo to połączenie DuckDB jest od początku zablokowane (tania obrona w głąb, sprawdzone na DuckDB 1.5.6):

```sql
SET enable_external_access = false;      -- brak dostępu do plików i sieci (read_csv, COPY, ATTACH, HTTP)
SET autoinstall_known_extensions = false;
SET autoload_known_extensions = false;
SET memory_limit = '512MB';
SET threads = 2;
SET lock_configuration = true;           -- ustawień nie da się już zmienić w tym połączeniu
```

Po tych ustawieniach `read_csv('C:/Windows/win.ini')` i `COPY ... TO 'x.csv'` kończą się błędem uprawnień, a próba ponownego włączenia dostępu zwraca błąd konfiguracji. Ładowanie danych z procedury przez parametryzowany `INSERT` działa normalnie (ścieżkę przez Arrow trzeba sprawdzić w prototypie). Każda sesja ma osobne połączenie, a długie zapytanie da się przerwać (`interrupt()`) po przekroczeniu limitu czasu.

Prawdziwy sandbox (osobny proces lub kontener z limitami) będzie potrzebny dopiero przy wariancie B lub C z sekcji 7.7, czyli gdy model sam pisze SQL lub kod.

### 7.5. Liczby w komentarzu

Tabele w odpowiedzi liczy DuckDB, ale krótki komentarz tekstowy (np. „Sprzedaż netto w 2024 wyniosła …, najlepszy był czerwiec”) pisze model. Nawet jeśli tylko przepisuje liczbę z wyniku, może ją źle zaokrąglić albo pomylić.

**v1:** model pisze komentarz swobodnie. Instrukcja systemowa każe mu brać liczby wyłącznie z faktów (`facts`) i małych wyników zwróconych przez `transform`, a nie liczyć samodzielnie. Nie ma mechanizmu, który by to wymuszał. Źródłem prawdy są tabele i fakty w odpowiedzi, a komentarz jest pomocniczy.

**Docelowo (razem z innymi zabezpieczeniami, np. przed prompt injection):**

- **Kontrola liczb:** backend wyszukuje liczby w komentarzu i sprawdza, czy występują w faktach, wynikach lub prośbie. Liczba spoza nich oznacza prośbę do modelu o poprawkę, a przy kolejnej porażce odpowiedź bez komentarza.
- **Odwołania zamiast liczb:** model pisze w komentarzu odwołanie do faktu, który sam zdefiniował w specyfikacji tego pytania (np. `{r2.suma_roczna}`), a backend wstawia wartość. Szablonów nie przygotowuje się z góry, bo model tworzy je przy każdym pytaniu. To mocniejsza gwarancja niż kontrola, ale wymaga od modelu dyscypliny w pisaniu komentarza.
- Zestaw testowy sprawdza od początku, czy liczby w komentarzu zgadzają się z faktami, więc skalę problemu będzie widać, zanim trzeba będzie go rozwiązać.

### 7.6. Proweniencja

Każdy wynik niesie łańcuch: procedura + parametry → uchwyt → specyfikacja + wygenerowany SQL → uchwyt. Backend zwraca go w odpowiedzi (pole `provenance`). Łańcuch:

- pozwala sprawdzić decyzję modelu (np. suma `TotalDue` zamiast `SubTotal`),
- pozwala odtworzyć wynik bez modelu (ponowne wykonanie zapisanego łańcucha),
- jest gotowym zapisem do późniejszego logu audytowego.

### 7.7. Porównanie z alternatywami

| Podejście | Deterministyczne wykonanie | Kto ustala logikę obliczeń | Audyt i powtarzalność | Gdzie są dane | Ocena |
|-----------|----------------------------|-----------------------------|------------------------|---------------|-------|
| **A. Specyfikacja deklaratywna → SQL w DuckDB** (rekomendowane) | Tak | Model wybiera z zamkniętego zestawu operacji, a kod je realizuje | Pełne | W backendzie | **v1** |
| **B. Model pisze SQL dla DuckDB nad uchwytem** | Tak (wykonanie) | Model, dowolny SQL | Średnie: SQL trzeba czytać | W backendzie | Opcja na v2 dla przypadków, których A nie obsłuży. Wymaga parsowania (tylko `SELECT`, tylko tabele uchwytów) i wyłączenia dostępu DuckDB do plików i sieci. Wzorzec: przykład data-analyst w Pydantic AI [5]. |
| **C. Model pisze Python w sandboxie** (code execution / programmatic tool calling w Claude API) | Tak (wykonanie) | Model, dowolny kod | Słabe: kod jest inny przy każdym pytaniu | W kontenerze Anthropic: pełne wiersze opuszczają backend | Nie dla v1. Sandbox to narzut i ryzyko [4], a programmatic tool calling nie łączy się ze `strict: true` [8]. |
| **D. Model liczy sam z wierszy** | Nie | Model | Brak | W kontekście modelu | Odrzucone. |

## 8. Przebieg żądania

```mermaid
sequenceDiagram
    participant C as Klient wywołujący
    participant API as Backend API
    participant A as Pętla agenta
    participant M as Claude
    participant T as Narzędzia
    participant DB as Baza (procedury)
    participant RS as Magazyn wyników (DuckDB)

    C->>API: ask("Raport sprzedaży po miesiącach za 2024", user)
    API->>A: prompt + RequestContext + historia sesji
    A->>M: instrukcje + skrót katalogu + narzędzia
    M-->>A: sales_order_list(date_from=2024-01-01, date_to=2025-01-01, customer_id=null)
    A->>T: walidacja parametrów, dołączenie @UserId
    T->>DB: EXEC api.SalesOrder_List @DateFrom, @DateTo, @CustomerID, @UserId
    DB-->>T: 14 244 wiersze
    T->>RS: zapis r1 (typy z katalogu, DECIMAL)
    T-->>A: r1: schemat, statystyki, podgląd
    A->>M: wynik narzędzia
    M-->>A: transform(r1, spec)
    A->>T: walidacja specyfikacji względem schematu r1
    T->>RS: SQL → r2 (12 wierszy) + fakty
    T-->>A: r2: dane (małe), fakty
    A->>M: wynik narzędzia
    M-->>A: present_result(status ok, komentarz, zwróć r2)
    A->>RS: pełne dane r2
    A-->>API: odpowiedź
    API-->>C: JSON: status, komentarz, tabela r2, proweniencja
```

Zasady ustalania parametrów:

| Sytuacja | Zachowanie |
|----------|------------|
| Brak wymaganego parametru (np. okresu) | `needs_clarification`. |
| Okres względny („ostatni kwartał”) | Rozwiązać względem daty bieżącej i pokazać daty w `assumptions`. Jeśli wypada poza `data_range`, zwrócić `no_data` z informacją o dostępnym zakresie. |
| Wyszukiwanie zwraca 0 trafień | `no_data` z propozycją doprecyzowania. |
| Wyszukiwanie zwraca kilka encji (klient, produkt) | `needs_clarification` z listą kandydatów. Nie wybierać samodzielnie. |
| Parametr niejednoznaczny, ale wynik może to pokazać (miasto w kilku regionach) | Zwrócić wszystko z kolumną rozróżniającą i zapisać to w `assumptions`. |
| Filtr, którego nie obsługuje żadna procedura | v1: `unsupported` z wyjaśnieniem, czego brakuje. Docelowo: krok `filter` w backendzie, jeśli kolumna jest na liście `app_filters`. |
| Niejasna miara („sprzedaż”) | `default_for` z katalogu, nazwa kolumny w `assumptions`. |

Limity v1 (propozycja): maks. 10 kroków pętli, `max_rows` z katalogu (domyślnie 100 000), 30 s na procedurę, podgląd dla modelu do 10 wierszy (cały wynik, jeśli ma do 50 wierszy, np. raport 12 miesięcy), maks. 10 000 wierszy na tabelę w odpowiedzi (z flagą `truncated`).

## 9. Kontrakt odpowiedzi backendu

Pętla kończy się narzędziem `present_result`. Backend składa z niego odpowiedź i dołącza dane:

```json
{
  "status": "ok | no_data | needs_clarification | unsupported | error",
  "session_id": "…",
  "message": "Sprzedaż netto w 2024: 32 145 678,12. Najlepszy miesiąc: 2024-06.",
  "tables": [
    {
      "handle": "r2",
      "title": "Sprzedaż netto po miesiącach, 2024",
      "columns": [
        {"name": "miesiac", "type": "date"},
        {"name": "sprzedaz_netto", "type": "decimal(19,4)"},
        {"name": "liczba_zamowien", "type": "int"}
      ],
      "rows": [["2024-01-01", "1234567.8900", 1102], ["…"]],
      "row_count": 12,
      "truncated": false
    }
  ],
  "assumptions": ["sprzedaż = SubTotal (netto)", "okres: 2024-01-01 – 2024-12-31"],
  "clarification": null,
  "provenance": [
    {"step": "procedure", "name": "api.SalesOrder_List", "params": {"DateFrom": "2024-01-01", "DateTo": "2025-01-01", "CustomerID": null}, "rows": 14244, "handle": "r1"},
    {"step": "transform", "source": "r1", "spec": {"…": "…"}, "sql": "WITH s1 AS (…)", "rows": 12, "handle": "r2"}
  ]
}
```

(Wartości liczbowe w przykładzie są ilustracyjne.)

- **Kwoty dziesiętne jako tekst** (`"1234567.8900"`), żeby JSON nie zgubił precyzji. Typ podaje `columns`.
- **`clarification`** zawiera pytanie i listę kandydatów z id, np. trzech klientów „Mohamed Pal”. Kolejne wywołanie `ask` z tym samym `session_id` i odpowiedzią użytkownika kontynuuje rozmowę.
- **Koniec tury bez wymuszania narzędzia:** Sonnet 5.5 i Opus 5.5 nie pozwalają wymusić wywołania konkretnego narzędzia (`tool_choice` `any` lub `tool` zwraca błąd 400) [8], a Pydantic AI nie wymusza narzędzia przy włączonym myśleniu. Projekt na tym nie polega: odpowiedź to `output_type` agenta, a gdy model odpowie czymś, co nie pasuje do schematu, Pydantic AI prosi go o poprawkę [6]. Po wyczerpaniu ponowień backend zwraca `error` i loguje przypadek.

## 10. Przykłady przepływu

**P1: „Daj mi listę klientów z miejscowości Berlin”**

1. `customer_list(city="Berlin", country_region_code=null, customer_type=null)` → `r1` z kolumnami m.in. `CustomerName`, `CustomerType`, `City`, `StateProvince`, `CountryRegion`.
2. Statystyki `r1` pokazują 5 różnych wartości `StateProvince`. Według zasad z sekcji 8 nie trzeba dopytywać, wystarczy zwrócić kolumnę regionu.
3. `transform(r1, {steps: [select(Klient, Typ, Miasto, Region), sort(Region, Klient)], facts: [count]})` → `r2`.
4. Odpowiedź: `ok`, tabela `r2`, komentarz z liczbą klientów (fakt `count`), w `assumptions` informacja, że „Berlin występuje w 5 regionach, zwrócono wszystkie”.

**P2: „Pokaż sprzedaż klienta Mohamed Pal w 2024”**

1. `customer_search(name="Mohamed Pal")` → 3 trafienia (12698, 20550, 28763).
2. Odpowiedź: `needs_clarification` z trzema kandydatami (numer konta, terytorium, liczba zamówień).
3. Kolejne `ask` z wyborem → `sales_order_list(date_from="2024-01-01", date_to="2025-01-01", customer_id=12698)` → `r1`.
4. `transform(r1, {steps: [select, sort(OrderDate)], facts: [sum(SubTotal), count]})` → `r2`.
5. Odpowiedź: tabela zamówień, w komentarzu suma z faktów.

**P3: „Zrób raport sprzedaży po miesiącach za 2024”**

1. `sales_order_list(date_from="2024-01-01", date_to="2025-01-01", customer_id=null)` → `r1`, 14 244 wiersze.
2. `transform` ze specyfikacją:

```json
{
  "source": "r1",
  "steps": [
    {"op": "derive", "name": "miesiac", "fn": "date_trunc", "unit": "month", "column": "OrderDate"},
    {"op": "aggregate", "group_by": ["miesiac"], "measures": [
      {"name": "sprzedaz_netto", "fn": "sum", "column": "SubTotal"},
      {"name": "liczba_zamowien", "fn": "count_distinct", "column": "SalesOrderID"}
    ]},
    {"op": "fill_periods", "column": "miesiac", "unit": "month", "from": "2024-01-01", "to": "2025-01-01",
     "fill": {"sprzedaz_netto": 0, "liczba_zamowien": 0}},
    {"op": "sort", "by": [{"column": "miesiac", "dir": "asc"}]}
  ],
  "facts": [
    {"name": "suma_roczna", "fn": "sum", "column": "sprzedaz_netto"},
    {"name": "najlepszy_miesiac", "fn": "argmax", "column": "sprzedaz_netto", "return": "miesiac"}
  ]
}
```

   Backend tłumaczy ją na SQL z sekcji 7.4 i wykonuje w DuckDB.
3. Wynik `r2` (12 wierszy) wraca do modelu w całości razem z faktami.
4. Odpowiedź: `ok`, tabela `r2`, komentarz oparty na faktach, w `provenance` procedura, specyfikacja i SQL.

Wariant „po miesiącach i kategoriach”: `sales_order_line_list` → `derive(miesiac)` → `aggregate(miesiac, Category; sum LineTotal)` → `pivot(rows=miesiac, columns=Category)`.

**P4: „Pokaż strukturę materiałową produktu HL Mountain Frame”**

1. `product_search(name="HL Mountain Frame")` → 10 wariantów (Black/Silver × rozmiary 38–48), więc `needs_clarification`.
2. Po wyborze: `get_bill_of_materials(start_product_id=…, check_date=<dziś lub data z prośby>)` → `r1`.
3. `transform(select + sort po poziomie)` → odpowiedź z tabelą.

Żaden z tych przepływów nie wymagał nowej procedury dla nowego pytania. Raport miesięczny, sprzedaż klienta i sprzedaż za okres korzystają z tej samej `api.SalesOrder_List`.

## 11. Pętla agenta: Pydantic AI

### 11.1. Dlaczego Pydantic AI, a nie Vercel AI SDK

| | Pydantic AI | Vercel AI SDK | Anthropic SDK |
|---|-------------|---------------|---------------|
| Język | **Python** | TypeScript / JavaScript | Python (i inne) |
| Wielu dostawców | Tak: model wybierany tekstem `dostawca:model`, np. `anthropic:claude-haiku-5-5`, `openai:…`, `azure:…`, `ollama:…`, `vllm:…`; endpointy zgodne z API OpenAI bez własnej klasy [6] | Tak (nie sprawdzone w researchu) | Nie, tylko Claude |
| Pasuje do backendu w Pythonie | Tak | Nie: wymagałby backendu w TypeScript albo osobnej usługi | Tak |
| Funkcje specyficzne dla Claude | Część dostępna przez ustawienia `anthropic_*` (sekcja 11.2) | Nie dotyczy | Wszystkie |

**Vercel AI SDK** jest popularny, ale to biblioteka TypeScript. Wybór oznaczałby przepisanie backendu na Node.js albo osobną usługę tylko do rozmowy z modelem, podczas gdy reszta (DuckDB, pyodbc, walidacja) zostałaby w Pythonie. Nie ma to sensu w projekcie, który i tak jest w Pythonie. Vercel AI SDK może się natomiast pojawić **po stronie frontendu**: Pydantic AI ma adapter `VercelAIAdapter`, który mówi protokołem strumienia Vercel AI, więc frontend w React z `useChat` może rozmawiać z agentem w Pythonie bez zmian w UI [6].

**LiteLLM** (bramka ujednolicająca API wielu dostawców) to inna możliwa droga, ale żadne twierdzenie o nim nie przeszło weryfikacji w researchu. Może się przydać później jako osobna bramka (klucze, limity, routing), a nie jako framework agenta.

### 11.2. Jak to wygląda w Pydantic AI

- **Agent:** `Agent('anthropic:claude-haiku-5-5', output_type=Response, tools=[…])`. Zmiana modelu to zmiana tego tekstu w konfiguracji.
- **Narzędzia:** zwykłe funkcje Pythona z typowanymi parametrami. Narzędzia procedur są generowane z YAML. Walidacja parametrów, wywołanie procedury i zapis proweniencji dzieją się w ciele narzędzia, więc framework nie musi o nich wiedzieć.
- **Kontekst żądania:** `RequestContext`, magazyn wyników i połączenie DuckDB trafiają do narzędzi przez mechanizm zależności Pydantic AI (`deps`), ten sam, którego przykład data-analyst używa do przechowywania DataFrame'ów [5].
- **Wynik:** `output_type=Response` (sekcja 9) zastępuje własne narzędzie `present_result`. Framework sam prosi model o poprawkę, gdy odpowiedź nie pasuje do schematu [6].
- **Historia sesji:** Pydantic AI zwraca listę wiadomości z przebiegu, którą zapisujemy w sesji i przekazujemy przy kolejnym `ask` (np. po pytaniu doprecyzowującym).

**Ustawienia Claude dostępne w Pydantic AI** (według dokumentacji modelu Anthropic w Pydantic AI): `anthropic_thinking`, `anthropic_effort`, `anthropic_task_budget` oraz ustawienia cache (`anthropic_cache`, `anthropic_cache_instructions`, `anthropic_cache_tool_definitions`). Dla innego dostawcy te ustawienia się nie stosują, więc konfiguracja modelu jest per dostawca, a kod agenta wspólny.

**Czego nie widać w dokumentacji Pydantic AI dla Claude API:** tool search z `defer_loading` (opisany tylko dla Bedrock), tryb `strict` dla zwykłych narzędzi (jest opcja strict dla narzędzia wyjściowego) i serwerowe `fallbacks`. Do sprawdzenia w prototypie. Żadna z tych rzeczy nie jest konieczna w v1.

### 11.3. Model i ustawienia

- **Model:** start na `claude-haiku-5-5` (następca Haiku 4.5) z `effort: medium`, ustawionym jawnie (`anthropic_effort`). Cena: $0,10 / $0,50 za milion tokenów wejścia/wyjścia przy promptach do 100 tys. tokenów, powyżej $0,50 / $2,50. Okno kontekstu 1M. Myślenie jest adaptacyjne i włączone domyślnie, a jego głębię steruje się `effort`. Bloki `thinking` muszą wracać do modelu bez zmian razem z wynikami narzędzi. Historią wiadomości zarządza framework, ale poprawność tego przy Haiku 5.5 sprawdzamy w prototypie. Gdy Haiku nie radzi sobie z wyborem procedury lub specyfikacją, kolejny krok to `anthropic:claude-sonnet-5-5`. Koszt liczyć na całe zadanie (cała pętla), a nie na pojedyncze wywołanie.
- **Odmowy:** odmowa modelu kończy się statusem `error`. Dla Haiku 5.5 nie ma automatycznego przełączenia na inny model po stronie serwera.
- **Prompt caching:** stała część (definicje narzędzi w ustalonej kolejności, instrukcja systemowa, skrót katalogu) idzie na początek i jest cache'owana przez ustawienia `anthropic_cache*`. Data bieżąca i rzeczy zmienne trafiają na koniec.
- **Claude Agent SDK się nie nadaje.** To harness Claude Code z narzędziami do plików i powłoki, a tu potrzebne są wyłącznie nasze narzędzia.
- **Co trafia do dostawcy modelu:** instrukcje, opisy procedur, parametry, schematy, statystyki i podglądy do 10 wierszy (lub małe wyniki zagregowane). Pełne zbiory danych zostają w backendzie. Przy późniejszej klasyfikacji danych wrażliwych da się to zawęzić, bo podgląd jest konfigurowany per kolumna.

## 12. Jak v1 zostawia miejsce na kolejne warstwy

| Przyszła warstwa | Co v1 już ma |
|------------------|--------------|
| Konto z samym `EXECUTE`, uprawnienia sprawdzane w procedurach | Konto `EXECUTE` od v1. `@UserId` wstrzykiwany przez `inject`, poza zasięgiem modelu. |
| Zapisy z zatwierdzaniem, dry-run, limity rekordów | Katalog ma pole `access: read/write`. Procedury zapisu dojdą jako narzędzia wymagające zatwierdzenia. |
| Log audytowy | Proweniencja: rzeczywiste wywołania procedur, specyfikacje i SQL, a nie opis wygenerowany przez model. |
| Niezaufane pola tekstowe (pośrednia iniekcja) | Model widzi tylko podgląd. W katalogu można oznaczyć kolumny wolnotekstowe jako wyłączone z podglądu. |
| Kontrola liczb w komentarzu | Fakty liczone przez backend są już w wyniku `transform`, więc kontrola będzie porównaniem tekstu z gotową listą wartości (sekcja 7.5). |
| Strażnik wejścia lub akcji | Pętla jest cienka, a strażnik to dodatkowy krok przed lub po wyborze narzędzia. |
| Limity kroków, tokenów, rate limiting | Limity kroków i wierszy już są. `task_budget` w Claude API to opcja na limit tokenów w pętli. |
| Frontend | Kontrakt odpowiedzi (sekcja 9) zawiera wszystko, czego UI potrzebuje: dane z typami, komentarz, pytania doprecyzowujące, proweniencję. |
| Inne modele (OpenAI, Azure, lokalne) | Zmiana `dostawca:model` w konfiguracji Pydantic AI i dostrojenie promptu. Narzędzia, katalog i obliczenia bez zmian. Jakość wywoływania narzędzi przez mniejsze modele trzeba zmierzyć tym samym zestawem testów. |
| Filtrowanie w backendzie | Krok `filter` w języku specyfikacji i pole `app_filters` w YAML są zaprojektowane (sekcja 7.2), w v1 wyłączone. |

## 13. Otwarte kwestie i zestaw ewaluacyjny

**Do decyzji:**

Brak otwartych decyzji architektonicznych. Ustalone: projekt testowy z jednym właścicielem (YAML, prompt i testy), Python, Claude API (start: Haiku 5.5, `effort: medium`), DuckDB, komentarz w v1 bez kontroli liczb. Model i `effort` dobierane na bieżąco.

**Do sprawdzenia w prototypie:**

- Czy schemat specyfikacji (unia po `op`) przechodzi w trybie `strict`.
- Ścieżka typu dziesiętnego: pyodbc → Arrow → DuckDB → JSON.
- Jak model radzi sobie z wyborem procedury przy rosnącym katalogu i od jakiej liczby procedur włączyć `find_procedures`.
- Pydantic AI z Haiku 5.5: przekazywanie bloków `thinking`, `anthropic_effort`, cache, typ wyniku jako narzędzie wyjściowe.
- Minimalne uprawnienia konta agenta (sam `EXECUTE`) i osobne uprawnienia do odczytu metadanych.
- Wczytanie wyniku procedury do zablokowanego DuckDB przez Arrow z zachowaniem `DECIMAL`.

**Zestaw ewaluacyjny:** 30–50 próśb z oczekiwaną procedurą i parametrami, oczekiwanym wynikiem (liczonym ręcznie SQL-em w SQL Server) i oczekiwanym statusem. Część przypadków pochodzi wprost z `examples` w YAML. Grupy: listy z filtrem (miasto, typ klienta), sprzedaż za okres, sprzedaż klienta, raporty po czasie i pivoty, inne dziedziny (BOM, przełożeni). Przypadki trudne: okres względny, okres poza danymi, niejednoznaczny klient lub produkt, miasto w kilku regionach, „sprzedaż” bez miary, filtr bez parametru w procedurze (`unsupported`). Ponieważ backend zwraca JSON, testy porównują odpowiedź z oczekiwaną automatycznie. Zestaw uruchamiamy przy każdej zmianie promptu, YAML lub modelu, a później dołączą do niego testy red-teamowe.

## 14. Źródła i stan weryfikacji

Research z wersji 1: 5 kierunków wyszukiwania, 22 źródła, 105 wyodrębnionych twierdzeń. 25 najważniejszych przeszło głosowanie weryfikacyjne (3 głosy): 19 potwierdzonych, 6 odrzuconych.

**Potwierdzone i nadal istotne:**

- [1] Wąski, wyselekcjonowany kontekst (opisy, przykłady, ograniczona liczba obiektów) poprawia trafność NL→dane: Uber QueryGPT, Databricks Genie, Fabric data agent (przykłady pytanie→zapytanie dobierane do pytania).
  https://www.uber.com/en-us/blog/query-gpt/ · https://docs.databricks.com/aws/en/genie/trusted-assets · https://learn.microsoft.com/en-us/fabric/data-science/data-agent-example-queries
- [2] SQL generowany przez LLM nadal wymyśla tabele i kolumny. Argument za zamkniętą przestrzenią akcji i walidacją w backendzie.
  https://www.uber.com/en-us/blog/query-gpt/
- [3] Microsoft SQL MCP Server: stały zestaw generycznych narzędzi, deterministyczny SQL, celowo bez NL2SQL, procedury jako nazwane narzędzia. Wzorzec zbieżny z tym projektem.
  https://learn.microsoft.com/en-us/azure/data-api-builder/mcp/overview
- [4] Wyniki pośrednie przechodzące przez kontekst modelu zwiększają koszt i opóźnienie. Kod pisany przez agenta wymaga sandboxa.
  https://www.anthropic.com/engineering/code-execution-with-mcp
- [5] Wzorzec uchwytów wyników z podglądem i SQL DuckDB nad przechowywanym DataFrame (przykład data-analyst w Pydantic AI).
  https://ai.pydantic.dev/examples/data-analyst

- [6] Pydantic AI: wybór modelu tekstem `dostawca:model`, endpointy zgodne z OpenAI, Azure, `VercelAIAdapter` (z researchu); wynik strukturalny przez narzędzie wyjściowe z ponowieniami oraz ustawienia `anthropic_*` (dokumentacja sprawdzona 2026-10-10).
  https://pydantic.dev/docs/ai/models/overview/ · https://pydantic.dev/docs/ai/models/anthropic/ · https://pydantic.dev/docs/ai/core-concepts/output/ · https://pydantic.dev/docs/ai/integrations/ui/vercel-ai/

**Mniej istotne po zmianie wymagań:** [7] Microsoft Agent Framework (stack .NET).

**Funkcje Claude API** [8] (tool search z `defer_loading`, `strict`, brak wymuszania narzędzia w Sonnet 5.5 i Opus 5.5, `effort` i jego wartości domyślne, `fallbacks` i ich brak dla Haiku 5.5, ceny i zachowanie myślenia w Haiku 5.5, programmatic tool calling i jego niezgodność ze `strict`) pochodzą z dokumentacji referencyjnej Claude API (stan na 2026-10-06), a nie z researchu. Przed implementacją warto je potwierdzić w aktualnej dokumentacji.

**Odrzucone w weryfikacji:** liczby trafności z benchmarków Spider 2.0, BIRD i BEAVER oraz twierdzenie o uprawnieniach użytkownika w Databricks Genie. Nie są tu przytaczane.

**Wnioski własne, nie ze źródeł:** katalog agenta i jego pola, generowanie narzędzi, język specyfikacji przekształceń, wybór DuckDB, zasady deterministyczności, proweniencja, kontrakt odpowiedzi, przebiegi. Fakty o AdventureWorks2025 (zakres dat, liczby klientów, duplikaty nazw, miast i produktów, lista procedur, działanie `dm_exec_describe_first_result_set_for_object`) pochodzą z zapytań do lokalnej bazy.
