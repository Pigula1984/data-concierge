# Data Concierge: dostęp modelu do danych (etap 2)

Status: projekt zatwierdzony przez właściciela (decyzje D1–D8 z 2026-10-24), przed planem wdrożenia. Wersja 0.2, data: 2026-10-24.
Rozszerza [`001-architektura-data-concierge.md`](001-architektura-data-concierge.md) (v3.7), który opisuje v1. Ten dokument przejmuje sprawy, które 001 odkładał na później: odwołania zamiast liczb w komentarzu (001, sekcja 7.5), konfigurację tego, co z danych trafia do modelu (001, sekcje 11.3 i 12), oraz oznaczanie kolumn w katalogu. Kontrola liczb w komentarzu zostaje odłożona (sekcja 12).

## 1. Cel i zakres

Usługa dostaje **przełącznik dostępu modelu do danych** z dwoma trybami:

| Tryb | Ustawienie | Model widzi | Komentarz dla użytkownika |
|------|------------|-------------|---------------------------|
| **Zamknięty** | `model_data_access: none` | Tylko metadane: katalog, schematy wyników, liczby wierszy, zakresy dat, klucze techniczne i SQL, który sam napisał. Żadnych nazw (klientów, produktów, osób) ani wartości i ilości sprzedaży. | Szablon z kotwicami, np. `Sprzedaż netto w 2024: {r3.net_sales}.`, a wartości wstawia backend. |
| **Ograniczony** | `model_data_access: minimal` | Metadane oraz wartości z jawnej listy dozwolonych: wartości wymiarów do filtrów, zakresy dat i na końcu tury tabele, które idą do użytkownika. Kolumny oznaczone w katalogu jako wrażliwe nigdy nie trafiają do modelu. | Pełniejszy komentarz pisany po obejrzeniu wyników (trendy, najlepszy okres), z liczbami przez kotwice. |

Tryb zamknięty jest dla klientów, którzy nie chcą wysyłać danych do dostawcy modelu. Tryb ograniczony daje lepsze odpowiedzi, a do modelu wysyła tylko to, co jest potrzebne do konkretnej czynności (sekcja 5.1).

Zachowanie z v1, czyli podgląd do 10 wierszy (do 50 przy małym wyniku) i statystyki wszystkich kolumn po każdym narzędziu, nie zostaje trzecim trybem (decyzja D5).

Poza zakresem: ochrona przed prompt injection, kontrola liczb w komentarzu, DuckDB w osobnym procesie, logi backendu (zawierają dane, ale nie opuszczają backendu) oraz model uruchamiany w chmurze klienta (Bedrock, Vertex). Ten ostatni to sprawa wdrożenia, a nie kodu: Pydantic AI zmienia dostawcę ustawieniem.

## 2. Co model widzi w v1

Po każdym wywołaniu procedury i każdym `query` model dostaje `ResultSummary` (`results/service.py`) z następującymi polami:

- schemat kolumn z rolami,
- statystyki: min i max dla kolumn `time` i `measure`, a dla `dimension` do 20 wartości albo samą liczbę różnych wartości,
- podgląd: cały wynik do 50 wierszy, przy większym pierwsze 10.

Do tego dochodzą komunikaty błędów DuckDB i tekst błędu sterownika SQL Server (`core/sqlserver.py`), które mogą cytować wartości z danych. Historia sesji niesie to wszystko do kolejnych tur. Dane widzi więc model w trakcie całej pętli, a nie tylko przy odpowiedzi.

## 3. Przełącznik

Tryb to ustawienie procesu (`Settings.model_data_access`, zmienna środowiskowa), czytane przy starcie. Nie da się go zmienić per żądanie ani per sesja, z trzech powodów:

- **Stały początek rozmowy.** Instrukcje i opisy narzędzi zależą od trybu: `query` i `describe_result` dziś mówią o podglądzie, a tryb zamknięty potrzebuje instrukcji o kotwicach i braku danych. Powstają raz przy starcie, bo od tego zależy cache i wiązanie bloków myślenia (001, sekcja 11.3).
- **Historia.** Sesja w trybie ograniczonym ma w historii wartości z danych. Przełączenie jej na tryb zamknięty dalej wysyłałoby te wartości do modelu.
- **Prostota.** Jeden tryb na proces to jedna ścieżka w testach na proces.

Klient, który potrzebuje obu trybów, uruchamia dwie instancje usługi. Domyślna wartość: `none` (decyzja D7).

## 4. Tryb zamknięty (`none`)

### 4.1. Podsumowanie wyniku bez wartości

Podsumowanie uchwytu po procedurze, `query` i `describe_result` ma tylko strukturę:

```json
{
  "handle": "r1",
  "row_count": 14244,
  "truncated": false,
  "columns": [{"name": "OrderDate", "type": "date", "role": "time"}, {"name": "SubTotal", "type": "decimal(19,4)", "role": "measure"}, "..."],
  "stats": {"OrderDate": {"min": "2024-01-01", "max": "2024-12-31"}},
  "ids": {"CustomerID": [11000, 11001, "..."]},
  "sources": ["..."],
  "sql": "..."
}
```

Bez `preview`. Ze statystyk zostaje tylko min i max kolumn `time`. Pole `ids` ma wartości kluczy technicznych (sekcja 4.2).

**Zasada właściciela projektu (D4):** model w trybie zamkniętym nie widzi nazw (klientów, produktów, osób) ani wartości i ilości sprzedaży. Zakresy dat może widzieć, bo bez nich nie rozwiąże prośby typu „trzy ostatnie miesiące”. Dlatego zostają:

- min i max kolumn `time` w podsumowaniu;
- zakres `data_range` z katalogu, czyli min i max dat wspólne dla wszystkich użytkowników, czytane przy starcie;
- `row_count`, bo bez niego model nie rozpozna pustego wyniku (`no_data`), wielu trafień wyszukiwania (`needs_clarification`) ani obcięcia. To liczba wierszy wyniku, a nie miara z danych, choć dla listy zamówień oznacza liczbę zamówień.

### 4.2. Klucze między procedurami

Prośba „sprzedaż klienta Aaron Wright w 2024” wymaga przekazania `CustomerID` z wyniku `customer_search` do parametru `customer_id` w `sales_order_list`. Bez wartości model tego nie zrobi. Rozważone drogi:

| Droga | Ocena |
|-------|-------|
| **A. Wartości kolumn `role: id` widoczne** (pole `ids` w podsumowaniu, tylko dla wyników do 50 wierszy, bez kolumn `sensitive`) | Proste, bez zmian w narzędziach procedur. Klucze techniczne (`CustomerID 12698`) same nic nie mówią o kliencie. **Wybrane (D1).** |
| B. Kotwice w parametrach (`customer_id: "{r1.CustomerID}"`) | Działałoby, ale parametr typu `integer` nie przyjmie tekstu w trybie `strict`. Unia `int | string` w każdym parametrze `id` zużywa budżet unii trybu `strict` (16 na żądanie, 001, sekcja 6.1). Możliwe rozszerzenie, gdyby klucze techniczne też miały zniknąć z kontekstu. |
| C. Zamiast parametru `JOIN` w DuckDB (jak P5) | Wymaga pobrania sprzedaży wszystkich klientów. Przy obcięciu przez `max_rows` nie działa (001, sekcja 7.3). |

Kolumna `id`, która coś mówi o kliencie lub produkcie (numer konta, numer produktu), dostaje w katalogu `sensitive: true` i nie jest widoczna (sekcja 5.3).

### 4.3. Pytanie doprecyzowujące z uchwytu

Dziś model sam przepisuje kandydatów (`Candidate.id`, `description`) z wierszy, które widział. W trybie zamkniętym ich nie widzi, więc zamiast listy wskazuje uchwyt:

```json
{"question": "Znaleziono {r2.row_count} klientów o tej nazwie. Którego wybierasz?",
 "candidates_from": {"handle": "r2", "id_column": "CustomerID"}}
```

Backend buduje listę `candidates` w odpowiedzi: `id` z kolumny `id_column`, a `description` z wartości pozostałych kolumn uchwytu złączonych przecinkami. Kształt opisu model ustala zapytaniem `query`, np. `SELECT "CustomerID", "AccountNumber", "Territory", count(*) AS order_count …`. Kontrakt odpowiedzi (001, sekcja 9) się nie zmienia.

Ten sam mechanizm działa w trybie ograniczonym. To jedna ścieżka zamiast dwóch, a model nie przepisuje danych (D8).

Wybór użytkownika wraca w kolejnej turze jako tekst albo id kandydata. To wartość, którą podał użytkownik, a nie odczyt z bazy.

### 4.4. `PIVOT` bez wartości wymiaru

`PIVOT … FOR <kolumna> IN (…)` wymaga wartości wymiaru, które w v1 model brał ze statystyk uchwytu. Uproszczony `PIVOT … ON` z automatycznym wykryciem wartości odpada, bo nie jest instrukcją `SELECT` (001, sekcja 7.3). W trybie zamkniętym wartości mogą pochodzić tylko z katalogu: pole `values` przy kolumnie (już istnieje, np. `CustomerType: [individual, store]`). Właściciel katalogu dopisuje je dla wymiarów o małej i stałej liczbie wartości, np. `Category: [Accessories, Bikes, Clothing, Components]`. To metadane, które właściciel świadomie wystawia, a nie odczyt z danych.

Wymiar bez `values` w katalogu zostaje w wyniku w postaci długiej (wiersz na okres i wartość), bez obrotu.

### 4.5. Czego model nie zrobi

- **Sprawdzenie sensu wyniku.** Nie zauważy zdublowanych wierszy po `JOIN` ani dziwnych sum. Widzi tylko liczbę wierszy.
- **Filtr po wartości, której nie zna.** „Klienci z Berlina” działa, bo miasto jest parametrem procedury. Filtr po wartości kolumny bez parametru (np. terytorium w zamówieniach) wymaga dokładnej pisowni, której model nie sprawdzi. Pomaga `values` w katalogu albo `ILIKE`.
- **Obserwacje w komentarzu.** „Najlepszy był czerwiec” model może napisać tylko przez kotwicę do wyniku, który sam policzył, np. `{r3.best_month}` z jednowierszowego zapytania z `ORDER BY … LIMIT 1`.

Skalę tych strat mierzy zestaw ewaluacyjny (sekcja 9).

## 5. Tryb ograniczony (`minimal`)

### 5.1. Lista dozwolonych

Minimum oznacza, że każda wartość wysyłana do modelu ma swój cel:

| Co model widzi | Kiedy | Po co |
|----------------|-------|-------|
| Wszystko z trybu zamkniętego (struktura, `row_count`, min i max kolumn `time`, `ids`) | po każdym narzędziu | wybór procedur, łańcuch kluczy, `no_data`, sprawdzenie okresu |
| Wartości wymiarów, gdy jest ich do 20 | po każdym narzędziu | filtry w `query`, listy `IN` w `PIVOT`, poprawna pisownia |
| Tabele zwracane użytkownikowi i uchwyty użyte w kotwicach szkicu, do 50 wierszy | raz, na końcu tury (faza komentarza, sekcja 5.2) | pełniejszy komentarz: trendy, najlepszy okres, wyróżniające się pozycje |

Model nie widzi:

- wierszy wyników procedur,
- podglądów pośrednich wyników `query`,
- min i max miar,
- żadnej wartości kolumny `sensitive`,
- tabel większych niż 50 wierszy (z nich tylko struktura i `row_count`).

### 5.2. Dwie fazy tury

1. **Faza narzędzi.** Agent z narzędziami jak dziś, z podsumowaniami według sekcji 5.1. Kończy się `AgentAnswer` z kompletem pól, a `message` jest szkicem z kotwicami.
2. **Faza komentarza.** Osobny agent Pydantic AI bez narzędzi, z własną stałą instrukcją, na tym samym modelu. Dostaje prompt użytkownika, szkic odpowiedzi (status, tytuły, założenia, komentarz) i dane tabel z sekcji 5.1, bez kolumn `sensitive`. Zwraca nowy `message` z kotwicami.

Faza komentarza działa tylko przy statusie `ok`. Gdy zawiedzie (błąd, zła kotwica po wyczerpaniu ponowień), odpowiedź dostaje szkic z fazy narzędzi, a przypadek trafia do logu. Koszt: jedno dodatkowe wywołanie modelu na turę, z krótkim kontekstem.

W trybie zamkniętym jest tylko faza narzędzi, a jej `message` idzie do użytkownika.

### 5.3. Wrażliwe kolumny w katalogu

Kolumna w katalogu dostaje opcjonalne `sensitive: true` (domyślnie `false`):

```yaml
- {name: CustomerName, type: string, role: dimension, sensitive: true}
- {name: AccountNumber, type: string, role: id, sensitive: true}
```

Wartość kolumny wrażliwej nie trafia do modelu w żadnym trybie: ani w statystykach, ani w `ids`, ani w fazie komentarza. Kolumna jest widoczna tylko w schemacie. Do użytkownika trafia normalnie, w tabelach i przez kotwice.

**Kolumny wyników `query`.** Backend nie analizuje treści zapytania (001, sekcja 7.3), więc wrażliwość kolumny wyliczonej ustala tak jak rolę:

- kolumna o tej samej nazwie i typie co kolumna źródłowa dziedziczy jej `sensitive`;
- inna kolumna tekstowa jest wrażliwa, gdy którykolwiek uchwyt źródłowy ma kolumnę wrażliwą;
- inna kolumna liczbowa lub z datą nie jest wrażliwa.

To chroni przed przypadkowym wysłaniem danych, a nie przed modelem, który celowo przekształci wartość wrażliwą (np. `length("CustomerName")`). Taki scenariusz należy do ochrony przed prompt injection, która jest poza zakresem.

Domyślnie kolumna jest widoczna, a właściciel oznacza wrażliwe (decyzja D6). Zasada oznaczania: wrażliwe są nazwy i opisy, które wskazują konkretnego klienta, produkt lub osobę, oraz identyfikatory biznesowe, których model nie potrzebuje do łączenia wyników. Wymiary potrzebne do filtrów i zestawień (kategoria, terytorium, miasto, typ klienta) zostają widoczne.

Wstępne oznaczenie katalogu `catalog/agent-procedures.yaml` (wejdzie do pliku razem z obsługą pola w kodzie, bo dziś nieznany klucz jest błędem katalogu):

| Kolumna | Procedury | Dlaczego |
|---------|-----------|----------|
| `CustomerName` | `customer_search`, `customer_list`, `sales_order_list`, `sales_order_line_list` | nazwa klienta (osoba lub sklep) |
| `AccountNumber` | `customer_search`, `customer_list` | numer konta wskazuje klienta, a model łączy wyniki po `CustomerID` |
| `ProductName` | `sales_order_line_list`, `product_search` | nazwa produktu |
| `ProductNumber` | `product_search` | numer katalogowy wskazuje produkt, a model łączy po `ProductID` |
| `ComponentDesc` | `get_bill_of_materials`, `get_where_used_product` | nazwa komponentu, czyli produktu |
| `FirstName`, `LastName`, `ManagerFirstName`, `ManagerLastName` | `get_employee_managers`, `get_manager_employees` | imiona i nazwiska pracowników |

Bez oznaczenia zostają m.in.:

- klucze techniczne (`CustomerID`, `ProductID`, `SalesOrderID`, `BusinessEntityID` i inne);
- daty;
- wymiary opisowe: `CustomerType`, `Territory`, `CountryRegion`, `City`, `StateProvince`, `AddressType`, `Category`, `Subcategory`, `Color`, `Size`, `OrganizationNode`, poziomy struktury;
- miary.

Miary nie są wrażliwe w sensie tego pola. W trybie zamkniętym model i tak ich nie widzi, a w ograniczonym widzi je tylko w fazie komentarza (sekcja 5.1).

## 6. Elementy wspólne obu trybów

### 6.1. Kotwice

Model wstawia wartość z danych do tekstu dla użytkownika przez kotwicę, a backend podmienia ją na wartość:

| Kotwica | Znaczenie |
|---------|-----------|
| `{r3.net_sales}` | wartość kolumny w uchwycie, który ma dokładnie jeden wiersz |
| `{r2[6].net_sales}` | wartość kolumny w wierszu 6 (numeracja od 1, w kolejności z `ORDER BY` modelu) |
| `{r2.row_count}` | liczba wierszy uchwytu |

- **Gdzie:** we wszystkich tekstach dla użytkownika: `message`, `assumptions`, tytuły tabel i `clarification.question`.
- **Sprawdzenie:** istniejący walidator wyniku agenta (`output_validator` w `AgentService`) sprawdza, czy uchwyt istnieje w sesji, kolumna jest w schemacie, a wiersz w zakresie. Kotwica `{rN.kolumna}` bez numeru wiersza na uchwycie z wieloma wierszami jest błędem. Błąd wraca do modelu jako `ModelRetry` z opisem. Backend nie skanuje tekstu w poszukiwaniu liczb spoza kotwic (sekcja 12).
- **Wstawienie:** `ResponseAssembler` podmienia kotwice po walidacji. Uchwyty użyte w kotwicach dołączają do `provenance`, tak jak uchwyty tabel.
- **Formatowanie** (decyzja D3): liczby z polskimi separatorami (spacja niełamliwa co trzy cyfry, przecinek dziesiętny), liczba miejsc po przecinku według skali typu kolumny, którą model ustala przez `CAST(… AS DECIMAL(38,2))`. Daty w ISO (`2024-06-01`), `NULL` jako „brak danych”.
- **Wartość kolumny wrażliwej** może trafić do tekstu przez kotwicę, np. `Najlepszy klient: {r4[1].CustomerName}`. Użytkownik ma prawo ją zobaczyć, a model jej nie widzi.

Kotwice działają w obu trybach. W trybie ograniczonym model widzi liczby w fazie komentarza, ale instrukcja każe mu je wstawiać kotwicami. To usuwa ryzyko złego przepisania lub zaokrąglenia (001, sekcja 7.5).

### 6.2. Komunikaty błędów bez wartości

Dotyczy obu trybów, bo tekst błędu z wartością nie pomaga modelowi poprawić wywołania:

- **DuckDB:** błędy parsera, wiązania i katalogu (`ParserException`, `BinderException`, `CatalogException`) cytują SQL modelu i schemat, więc wracają bez zmian. Pozostałe (np. `ConversionException`, `InvalidInputException`, `OutOfRangeException`) wracają jako nazwa klasy i ogólny opis, np. „Conversion Error: a value could not be converted; use TRY_CAST or filter the column first”. Pełny tekst trafia do logu z identyfikatorem zgłoszenia.
- **SQL Server:** model dostaje numer błędu i SQLSTATE bez tekstu sterownika (`core/sqlserver.py`). Pełny tekst trafia do logu.
- **Komunikaty backendu** (kontrole `query`, limity, walidacja parametrów) nie zawierają wartości z danych i zostają bez zmian. Plan to potwierdzi przeglądem.

### 6.3. Historia sesji

Historia, którą sesja zapisuje i przekazuje do kolejnej tury (`result.all_messages()` w `AgentService.run`), zawiera wyniki narzędzi w postaci podsumowań z danego trybu i argumenty `present_result` z kotwicami. Odpowiedź z podstawionymi wartościami (`Response`) nie wraca do modelu. W trybie zamkniętym historia nie zawiera więc żadnej wartości z bazy.

W trybie ograniczonym faza komentarza nie dopisuje się do historii fazy narzędzi. Plan ustali, czy szablon komentarza z fazy 2 ma zastąpić szkic w zapisanym `present_result`, żeby kolejna tura wiedziała, co użytkownik przeczytał.

### 6.4. Instrukcje i opisy narzędzi

Każdy tryb ma własną stałą część instrukcji i opisy `query`, `describe_result` i `present_result`, zbudowane przy starcie. Tryb zamknięty dodaje do instrukcji:

- informację, że model nie widzi danych;
- regułę, że każda wartość z danych w tekście idzie przez kotwicę;
- wzorzec jednowierszowego zapytania do komentarza;
- pytania doprecyzowujące przez `candidates_from`;
- `PIVOT` tylko z wartości z katalogu.

Przykłady z sekcji 10 dokumentu 001 dostają warianty dla trybu zamkniętego.

## 7. Co trafia do dostawcy modelu

| Element | v1 | `minimal` | `none` |
|---------|----|-----------|--------|
| Instrukcje, katalog, opisy narzędzi, `data_range` | tak | tak | tak |
| Prompt użytkownika i odpowiedzi na pytania doprecyzowujące | tak | tak | tak |
| Schemat wyniku, `row_count`, `truncated`, SQL `query` | tak | tak | tak |
| Wartości kolumn `id` (do 50 wierszy, bez `sensitive`) | tak (w podglądzie) | tak | tak |
| Wartości wymiarów (do 20) | tak | tak, bez `sensitive` | nie |
| Min i max kolumn `time` | tak | tak | tak |
| Min i max miar | tak | nie | nie |
| Wiersze wyników procedur | do 10 lub 50 | nie | nie |
| Wiersze pośrednich wyników `query` | do 10 lub 50 | nie | nie |
| Tabele zwracane użytkownikowi | do 10 lub 50 | do 50, raz, bez `sensitive` | nie |
| Wartości w komunikatach błędów | tak | nie | nie |

## 8. Zmiany w kodzie według modułów

| Moduł | Zmiana |
|-------|--------|
| `core` | `Settings.model_data_access` (`none` / `minimal`). Błędy SQL Server dla modelu bez tekstu sterownika. |
| `catalog` | `ColumnSpec.sensitive`. `values` dla wymiarów, po których robimy `PIVOT` (np. `Category`). |
| `results` | Podsumowanie według polityki trybu (sekcje 4.1 i 5.1) w miejsce stałego podglądu i statystyk. Wrażliwość kolumn wyników `query` (sekcja 5.3). Odczyt komórki dla kotwic. Kandydaci do pytania doprecyzowującego z uchwytu. |
| `query` | Komunikaty błędów DuckDB bez wartości (sekcja 6.2). |
| `agent` | Instrukcje i opisy narzędzi zależne od trybu. Walidacja kotwic w walidatorze wyniku. `candidates_from` w `Clarification`. Faza komentarza w trybie `minimal`. |
| `api` | `ResponseAssembler`: podstawienie kotwic, uchwyty kotwic w `provenance`, kandydaci z uchwytu. Kontrakt odpowiedzi bez zmian. |
| `evals` | Uruchomienie w obu trybach (sekcja 9). |

Polityka trybu to jedna klasa wstrzykiwana do serwisu wyników i składania odpowiedzi. Rozstrzyga, które pola podsumowania i które kolumny idą do modelu. Plan przypisze ją do modułu zgodnie z [`architektura-warstwowa.md`](../standards/architektura-warstwowa.md).

## 9. Ewaluacja

Zestaw 37 przypadków (`evals/cases.yaml`) uruchamiamy w obu trybach, a wyniki porównujemy z przebiegiem v1 z 2026-10-20 (37/37). Raport dostaje nowe miary:

- liczba ponowień przez złe kotwice,
- liczba liczb w tekście spoza kotwic,
- liczba odpowiedzi ze szkicem zamiast komentarza z fazy 2 (tylko `minimal`),
- koszt i czas tury.

Przypadki, w których oczekiwany wynik może się zmienić w trybie zamkniętym:

- **Pytania doprecyzowujące** (`klient-mohamed-pal`, `p2-mohamed-pal`, `p4-bom-hl-mountain-frame` i przypadki z grupy „trudne” ze statusem `needs_clarification`): kandydaci przychodzą z uchwytu, więc porównanie idzie po `id` kandydatów.
- **Pivoty** (`kategorie-2024`, `rowery-miesiace-2024`, `pivot-kwartaly-kategorie-2024`): działają tylko z `values` w katalogu. Bez nich poprawny jest też kształt długi.
- **Sprzedaż klienta** (`zamowienia-aaron-wright`, `suma-aaron-wright`): `CustomerID` przechodzi do drugiej procedury przez pole `ids` (D1).
- **Komentarz z liczbami** (`p3-raport-miesiace-2024` i inne, gdzie suma jest w komentarzu): suma przez kotwicę.

Przypadek może mieć oczekiwania zależne od trybu. Plan ustali format w `cases.yaml`.

## 10. Ryzyka szczątkowe

- **Wnioskowanie z liczby wierszy.** Model, który widzi `row_count`, może się dowiadywać o wartościach zapytaniami typu `WHERE "CustomerName" LIKE 'A%'`. Wymaga to celowego działania, a nie przypadku. Wiąże się z ochroną przed prompt injection.
- **Kolumny wyliczone** z wartości wrażliwych mogą przejść jako liczbowe (sekcja 5.3).
- **Prompt użytkownika** może zawierać dane (nazwisko, kwotę) i zawsze trafia do modelu.
- **`data_range`** to dane z bazy wystawione celowo przez katalog.
- **Jakość w trybie zamkniętym** będzie niższa. Ile, pokaże zestaw ewaluacyjny.

## 11. Decyzje

Decyzje właściciela projektu z 2026-10-24:

| # | Decyzja | Rozstrzygnięcie |
|---|---------|-----------------|
| D1 | Czy w trybie zamkniętym model widzi wartości kolumn `id` (bez `sensitive`) w wynikach do 50 wierszy? | **Tak.** Z kluczy technicznych model nie wyczyta wiedzy o danych, a bez nich nie działa łańcuch procedur. Kotwice w parametrach (`{r1.CustomerID}`) zostają możliwym rozszerzeniem (sekcja 4.2). |
| D2 | Tryb ograniczony: lista dozwolonych z sekcji 5.1 i dwie fazy tury z sekcji 5.2 | **Tak**, w tym kształcie. |
| D3 | Formatowanie wartości z kotwic | Polskie separatory, skala z typu kolumny (model steruje `CAST`), daty ISO, `NULL` jako „brak danych”. Przyjęte na potrzeby tego rozwiązania. |
| D4 | Co model widzi w trybie zamkniętym poza strukturą | Zakresy dat (min i max kolumn `time`, `data_range`) i `row_count`. Nie widzi nazw klientów, produktów i osób ani wartości i ilości sprzedaży (sekcja 4.1). |
| D5 | Czy zachowanie z v1 zostaje trzecim trybem (`full`)? | **Nie.** Punktem odniesienia jest zapisany przebieg ewaluacji v1. |
| D6 | Domyślna wrażliwość kolumny | Widoczna, a właściciel oznacza wrażliwe `sensitive: true`. Wstępne oznaczenie katalogu w sekcji 5.3. |
| D7 | Domyślny tryb usługi | `none`. Projekt testowy ustawia tryb jawnie. |
| D8 | Kandydaci do pytania doprecyzowującego zawsze z uchwytu, także w trybie ograniczonym | **Tak**, jedna ścieżka w obu trybach (sekcja 4.3). |

## 12. Nadal odłożone

- **Kontrola liczb w komentarzu** (001, sekcja 7.5): skanowanie tekstu i porównanie z wynikami. Przy kotwicach zestaw ewaluacyjny mierzy liczby spoza kotwic. Kontrolę wprowadzimy, jeśli pomiar pokaże problem.
- **Narzędzie wyszukiwania wartości** (np. `find_values(handle, column, text)`) do filtrów po wymiarach z wieloma wartościami. Dziś tę rolę pełnią procedury wyszukiwania z parametrem.
- **Wyłączenie kolumn wolnotekstowych z kontekstu** jako ochrona przed pośrednią iniekcją (001, sekcja 12): `sensitive` daje ten sam efekt technicznie, ale cel i oznaczenie ustalimy razem z ochroną przed iniekcją.
