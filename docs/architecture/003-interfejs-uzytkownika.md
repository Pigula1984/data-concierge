# Data Concierge: interfejs użytkownika (etap 3)

Status: **kroki 3A i 3B wdrożone** według planów [`2026-11-04-1810-feat-interfejs-czat-i-tabele-plan.md`](../plans/2026-11-04-1810-feat-interfejs-czat-i-tabele-plan.md) i [`2026-11-08-1015-feat-wykresy-wybierane-przez-model-plan.md`](../plans/2026-11-08-1015-feat-wykresy-wybierane-przez-model-plan.md); krok 3C to projekt. Data: 2026-11-08. [`001-architektura-data-concierge.md`](001-architektura-data-concierge.md) odsyła tu w sekcjach 1, 4 i 12. Odejścia od pierwotnego projektu i ustalenia z implementacji są w sekcjach 13 (3A), 14 (poprawki po sprawdzeniu w przeglądarce) i 15 (3B).

## 1. Cel i zakres

Prosty interfejs w przeglądarce do rozmowy z asystentem. Dwa kroki:

| Krok | Co użytkownik dostaje |
|------|-----------------------|
| **3A: czat i tabele** | Okno rozmowy: prompt, odpowiedź (`message`), założenia, pytanie doprecyzowujące z kandydatami. Tabele wyników w siatce z sortowaniem i przewijaniem, także przy kilku tysiącach wierszy. |
| **3B: wykresy** | Użytkownik prosi o wykres („pokaż to na wykresie”). Model wybiera rodzaj wykresu i kolumny, a interfejs go rysuje. |

Poza zakresem: uwierzytelnianie, wdrożenie na serwerze, wiele procesów, aplikacja mobilna, edycja danych.

## 2. Co już mamy

- **Kontrakt odpowiedzi** (001, sekcja 9) ma wszystko, czego potrzebuje 3A: `status`, `message`, `tables` z typami kolumn, `assumptions`, `clarification` z kandydatami, `provenance` i `session_id`. Sekcja 12 dokumentu 001 zakładała, że frontend skorzysta z niego bez zmian.
- **Wejście HTTP:** `POST /ask` (`prompt`, `user_id`, `session_id?`) i `GET /health` w aplikacji FastAPI z `CompositionRoot.http_app()`. Błąd tury to `status: error` w odpowiedzi 200, a za długi prompt to 422.
- **Sesje** w pamięci procesu, z właścicielem. Historię rozmowy trzyma backend: wersję dla modelu, z podsumowaniami wyników zamiast wierszy i szkicem z kotwicami zamiast wartości (002, sekcja 6.3). `data-concierge chat` to tylko pętla, która woła `ask` z `session_id` z poprzedniej odpowiedzi; osobnego wejścia „chat” nie ma i nie jest potrzebne. Uchwyty z poprzednich tur zostają w DuckDB sesji, więc prośba „pokaż to na wykresie” w kolejnej turze nie musi pobierać danych od nowa.
- **Limity:** do 10 000 wierszy na tabelę w odpowiedzi (z flagą `truncated`). Kwoty przychodzą jako tekst (`"1234567.8900"`), daty jako ISO.
- **Czas tury:** średnio ~7,9 s, z czego ~93% to rundy z modelem ([`../ideation/2026-10-28-szybsza-tura.md`](../ideation/2026-10-28-szybsza-tura.md)). Interfejs musi pokazać, że coś się dzieje.

## 3. Ograniczenia, które zawężają wybór

Te zasady obowiązują już dziś. Każdy wariant interfejsu musi je spełnić.

1. **Interfejs rozmawia z `AskService` i dostaje `Response`.** Wszystkie kontrole żyją na tej ścieżce: strażnik, kotwice, `ResponseAssembler`, faza komentarza, właściciel sesji, role, znacznik instrukcji, log audytowy. Nic nie może wołać `Agent` bezpośrednio.
2. **Teksty to zwykły tekst** (001, sekcje 8.2 i 9): `message`, tytuły, `assumptions`, pytanie, opisy kandydatów i wartości w komórkach. Bez Markdown, bez HTML, bez klikalnych linków. Wartości pochodzą z bazy (także kolumny `sensitive` z tekstem wpisanym przez osoby spoza firmy), a teksty z modelu.
3. **Model nie widzi więcej danych niż dziś** (002). W trybie `none` wybór wykresu musi wystarczyć na schemacie, liczbie wierszy i zakresach dat.
4. **Stały początek rozmowy** (001, sekcja 11.3). Wsparcie wykresów jest częścią schematu `present_result` budowanego przy starcie, a nie narzędziem dodawanym w turze, w której użytkownik prosi o wykres.
5. **Jeden proces pisze do logu audytowego** (001, sekcja 8.1). Interfejs działa w tym samym procesie co `POST /ask` albo woła go przez HTTP, a nie buduje drugiego `AskService` w innym procesie.
6. **Kandydaci wracają do modelu tylko jako `id`.** Opis kandydata pochodzi z wierszy, także z kolumn wrażliwych, i jest przeznaczony dla użytkownika (002, sekcja 4.3). Przycisk wyboru kandydata wysyła więc w kolejnym prompcie identyfikator, który model już zna, a nie tekst opisu. Inaczej numer konta czy adres trafiłby do dostawcy modelu.

## 4. Warianty techniczne

### 4.1. Odrzucone na start: gotowe interfejsy Pydantic AI

Pydantic AI 2.55 ma trzy gotowe drogi do przeglądarki: `create_web_app` (gotowa strona czatu z endpointem), `VercelAIAdapter` (protokół strumienia Vercel AI dla `useChat`) i adapter AG-UI (protokół dla klientów AG-UI, np. CopilotKit). Wszystkie stoją na wspólnej klasie `UIAdapter`, która bierze z żądania listę wiadomości od klienta, zamienia ją na `ModelMessage`, woła `agent.run_stream_events(...)` i tłumaczy zdarzenia agenta na zdarzenia protokołu. Pomagają wystawić **agenta** do przeglądarki, a u nas tura to więcej niż agent. Problemy (sprawdzone w kodzie zainstalowanego pakietu):

**1. Uruchamiają agenta, a nie turę.** Tura to `AskService.ask`, a agent jest tylko jej środkiem (ograniczenie 1):

| Krok tury | Gdzie żyje | Z adapterem |
|---|---|---|
| Limit długości promptu, właściciel sesji | `AskService` | brak |
| Strażnik Haiku przed agentem | `AskService` | brak |
| Wiersz `Current date:` w prompcie | `AskService` | brak |
| Kontrola kotwic i adresów URL w odpowiedzi modelu | walidator wyniku agenta | działa |
| Faza komentarza (`minimal`) | `AskService` | brak |
| Dane tabel, wartości kotwic, kandydaci, proweniencja | `ResponseAssembler` | brak |
| Znacznik instrukcji w gotowej odpowiedzi | `AskService` | brak |
| Szkic zamiast komentarza w historii | `AskService` | brak |
| Zdarzenia `request`, `guard`, `comment`, `response` w logu audytowym | `AskService` | brak |
| Usunięcie sesji po błędzie DuckDB | `AskService` | brak |

Klient dostałby surowy `AgentAnswer`: `Sprzedaż netto: {r3.net_sales}` zamiast liczby i uchwyt `r2` zamiast tabeli. Naprawa wymagałaby przeniesienia składania odpowiedzi do strumienia zdarzeń, czyli napisania własnego endpointu w cudzym formacie.

**2. Historię rozmowy przysyła klient.** W obu protokołach przeglądarka przy każdym żądaniu wysyła całą rozmowę. Pydantic AI traktuje ją jako niezaufaną i czyści funkcją `sanitize_messages`, która usuwa instrukcje systemowe, adresy plików spoza http(s), przesłane pliki, odwołania do workspace i niezakończone wywołania narzędzi na końcu historii. Wyników narzędzi ani wcześniejszych odpowiedzi modelu nie usuwa. Zostają dwie złe drogi:

- *Ufamy historii od klienta.* Klient może podrzucić fałszywy wynik narzędzia albo zmyśloną odpowiedź asystenta. To kanał prompt injection z pominięciem strażnika, który ocenia tylko nowy prompt. Klient musiałby też przechowywać wersję dla modelu (kotwice, szkic, bez wartości wrażliwych), a nie to, co widzi użytkownik.
- *Ignorujemy historię od klienta i bierzemy ją z sesji.* Z protokołu zostaje sam format zdarzeń, a adapter i tak dokleja wiadomości klienta za historią z serwera (`[*message_history, *frontend_messages]`), więc trzeba usuwać duplikaty.

**3. Zależności tury są per sesja.** Nasze `deps` to magazyn DuckDB sesji, `RequestContext` z `user_id` i rolą oraz ślad audytu z identyfikatorem korelacji. `create_web_app` przyjmuje jedno `deps` dla wszystkich żądań, a jego protokół nie zna tożsamości użytkownika. `VercelAIAdapter` przyjmie `deps` per żądanie, ale wtedy endpoint i tak piszemy sami.

**4. Gotowa strona nie pokazuje naszych danych.** `create_web_app` domyślnie pobiera stronę z jsDelivr, a wersja `dist` dociąga przy działaniu także style i fragmenty kodu z CDN (jest wariant `offline`). Strona ma wybór modelu i wbudowanych narzędzi dostawcy, których nie chcemy wystawiać. To ogólny czat dla tekstu modelu: bez siatki z typami i polskim formatowaniem, przycisków kandydatów i wykresów. Dodanie ich to fork aplikacji React.

**5. Główna zaleta, streaming tekstu modelu, u nas prawie nic nie daje.** Argumenty `present_result` przychodzą jednym kawałkiem ~1 s przed końcem tury (sekcja 8). Więcej daje postęp narzędzi, a ten musi wyjść z naszego kodu.

**Kiedy mogą wrócić.** Format zdarzeń AG-UI albo Vercel AI może posłużyć jako transport w kroku 3C (sekcja 8): nasz endpoint woła `AskService`, wysyła postęp i na końcu pełny `Response`, a historia zostaje w sesji. Wtedy da się użyć gotowego klienta protokołu. `create_web_app` mógłby służyć lokalnie do rozmowy z surowym agentem przy strojeniu promptów, ze świadomością, że omija strażnika i składanie odpowiedzi; to samo dają już `data-concierge chat --diagnostic` i log audytowy.

### 4.2. Porównanie

| | **A. Statyczna strona z FastAPI** | **B1. NiceGUI** | **B2. Streamlit** | **B3. Gradio** | **C. React + TypeScript + Vite** |
|---|---|---|---|---|---|
| Język interfejsu | JavaScript (moduły ES, bez kroku budowania) | Python | Python | Python | TypeScript |
| Jak woła backend | `fetch('/ask')`, to samo API co inni klienci | W procesie (`await AskService.ask`) albo HTTP | HTTP (osobny proces) | HTTP albo w procesie | `fetch('/ask')`, to samo API co inni klienci (bez `useChat`, sekcja 4.1) |
| Czat jako zwykły tekst | Tak, `textContent` | Tak, domyślnie `text_html=False` [U3] | `st.write`/`st.markdown` renderują Markdown; trzeba pilnować `st.text` w każdym miejscu [U4] | `render_markdown=True` domyślnie, da się wyłączyć [U5] | Tak: JSX escapuje tekst, o ile nie użyjemy `dangerouslySetInnerHTML` |
| Kontrakt z backendem | Ręcznie w JS | Typy Pydantic w procesie | Ręcznie | Ręcznie | Typy TS generowane z `/openapi.json` FastAPI: zmiana `Response` psuje kompilację |
| Siatka | Tabulator albo AG Grid Community | `ui.aggrid` (AG Grid) [U3] | `st.dataframe` | `gr.Dataframe` | AG Grid React (Community) albo TanStack Table z wirtualizacją |
| Wykresy | ECharts | `ui.echart` (ECharts) [U3] | Vega-Lite (`st.vega_lite_chart`) | `gr.Plot` | ECharts |
| Model działania | Klient trzyma stan rozmowy | Stan na serwerze, WebSocket na każdą interakcję [U3] | Cały skrypt wykonuje się od nowa przy każdej interakcji | Bloki z kolejką zdarzeń | Klient trzyma stan widoku, historia dla modelu zostaje w sesji backendu |
| Nowe zależności | Dwie biblioteki JS jako pliki statyczne | `nicegui` (własny serwer na FastAPI) | `streamlit` + osobny proces | `gradio` + osobny proces lub montowanie | Node, npm, Vite, TypeScript, ESLint, osobny build |
| Streaming (3C) | `fetch` z SSE | Push przez WebSocket, łatwy | Ograniczony do `st.write_stream` | Generatory | `fetch` z SSE, pełna swoboda |
| Przyszłość | Kod do wyrzucenia przy prawdziwym frontendzie | UI zszyte z procesem backendu, nie przechodzi do osobnego frontendu | Jak B1 | Jak B1 | POC jest zaczątkiem produkcyjnego frontendu |
| Ryzyko | Kod JS poza standardami i bez narzędzi | Ciężka zależność, świeże wydanie 4.0 | Model przebiegu źle pasuje do czatu z sesją po stronie backendu | Biblioteka pod dema ML | Drugi ekosystem i setki paczek npm do utrzymania |

Chainlit (czat pod aplikacje LLM) nie był sprawdzany w tym rozpoznaniu. Ma własne abstrakcje rozmowy i domyślnie renderuje Markdown, więc ma te same wady co B2 i B3.

### 4.3. Decyzja: wariant C (2026-11-04)

**Dlaczego C.** Projekt ma dostać kiedyś produkcyjny frontend w React, rozwijany przez nas. Interfejs z etapu 3 jest więc pierwszą wersją tej aplikacji, a nie demem do wyrzucenia. Wariant A odpada, bo nie zostaje w Pythonie ani nie daje kodu wielokrotnego użytku. NiceGUI byłby najprostszy dla POC (sam Python, 3C prawie za darmo), ale jego kod nie przeszedłby do osobnego frontendu.

Niezależnie od frameworka najtrwalszą częścią etapu 3 jest kontrakt backendu: `Response` (sekcja 9 dokumentu 001), specyfikacja wykresu (sekcja 7.2), zdarzenia postępu z 3C i historia w sesji po stronie serwera.

**Stos (szkic do potwierdzenia w planie, wersje do przypięcia w planie):**

| Element | Wybór |
|---------|-------|
| Narzędzia | Node w przypiętej wersji, npm z `package-lock.json` w repozytorium, Vite |
| Język | TypeScript w trybie `strict` |
| UI | React, komponenty funkcyjne |
| Kontrakt | Typy generowane z `/openapi.json` (np. `openapi-typescript`), wygenerowany plik w repozytorium, a test w CI sprawdza, że jest aktualny |
| Wywołania | Zwykły `fetch` w klasie `ApiClient` (`POST /ask`, później strumień 3C). Bez Vercel AI SDK |
| Siatka | AG Grid React (Community) albo TanStack Table z TanStack Virtual (decyzja D5) |
| Wykresy | Apache ECharts przez cienki własny komponent albo `echarts-for-react` |
| Jakość | ESLint, Prettier, `tsc --noEmit`, testy Vitest; Playwright dla przepływu czatu później |

**Układ i serwowanie.** Kod w katalogu `frontend/` w tym samym repozytorium. `npm run build` tworzy pliki statyczne, które aplikacja FastAPI serwuje pod `/`, więc w działaniu ta sama domena co `POST /ask`, bez CORS. Przy pracy lokalnej Vite działa jako serwer deweloperski z proxy do uvicorna. Biblioteki trafiają do zbudowanych plików, a nie są ładowane z CDN.

**Kod poza Pythonem dostaje własny standard** w `docs/standards/` (TypeScript i React): komponenty funkcyjne z jedną odpowiedzialnością, logika bez UI (klient API, formatowanie kwot, mapowanie specyfikacji wykresu na ECharts) w klasach testowanych przez Vitest, zakaz `dangerouslySetInnerHTML` i HTML w formatterach, nazwy po angielsku jak w Pythonie. Komponenty funkcyjne zamiast klas to idiom Reacta, a nie odejście od zasady obiektowości: klasy zostają tam, gdzie jest logika.

**Koszty, które przyjmujemy:** drugi ekosystem obok `uv` (Node, npm, osobne polecenia), aktualizacje i przegląd bezpieczeństwa paczek npm (`npm audit`) oraz więcej pracy na start niż w A albo NiceGUI.

## 5. Okno czatu (3A)

- **Wiadomość użytkownika** idzie do `POST /ask` z `session_id` z poprzedniej odpowiedzi. Pierwsza wiadomość idzie bez niego. Strona robi to samo co pętla `data-concierge chat`: wysyła tylko nowy prompt, a nie całą rozmowę. Wiadomości na ekranie to widok dla użytkownika z podstawionymi wartościami. Nie wracają do backendu, więc klient nie przechowuje wersji z kotwicami i nie decyduje, co zobaczy model. Z tego powodu odpada też protokół `useChat` (sekcja 4.1), w którym klient przy każdym żądaniu odsyła całą historię.
- **Odpowiedź:** `message`, pod nim tabele, potem `assumptions` jako lista, przy statusie `needs_clarification` pytanie i przyciski kandydatów. Status `no_data`, `unsupported` i `error` dostaje inny kolor ramki. Wszystko jako tekst w JSX, bez `dangerouslySetInnerHTML`.
- **Kandydaci:** przycisk pokazuje opis, a wysyła prompt z samym identyfikatorem: `Wybieram 12698.` (ograniczenie 6). Sprawdzone na żywo 2026-11-04 na przykładzie P2: model kontynuuje rozmowę ze wskazanym klientem. Przypadki ewaluacyjne dla tego promptu dochodzą w 3B.
- **Oczekiwanie:** wskaźnik z licznikiem sekund od wysłania i zablokowane pole do czasu odpowiedzi.
- **Proweniencja:** zwinięta sekcja „Jak policzono” pod odpowiedzią: procedury z parametrami i SQL. Przydaje się na demo i kosztuje mało.
- **Tożsamość:** brak uwierzytelniania, więc strona ma pole `user_id` (domyślnie 1, zapamiętane w `localStorage`; niecałkowita wartość blokuje wysyłanie). Zmiana `user_id` zaczyna nową rozmowę, bo sesja ma właściciela. Na demo pozwala pokazać rolę `hr`.
- **Wygasła sesja:** po restarcie procesu backend zwraca `error` z informacją o wygasłej sesji. Strona pokazuje przycisk „Nowa rozmowa”, który czyści `session_id`.
- **Stan strony:** rozmowa żyje w pamięci strony, więc odświeżenie zaczyna nową rozmowę. Zachowanie samego `session_id` po odświeżeniu dałoby modelowi historię, której użytkownik już nie widzi.

## 6. Siatka z danymi (3A)

| Wymaganie | Rozwiązanie |
|-----------|-------------|
| Do 10 000 wierszy | Wirtualizacja wierszy: renderowane są tylko widoczne wiersze. Ma ją AG Grid Community (MIT) [U1], a przy TanStack Table daje ją TanStack Virtual. Małe tabele (np. do 15 wierszy) bez stałej wysokości, żeby nie było pustego miejsca. |
| Kwoty jako tekst | Formatowanie i sortowanie według `columns[].type`. Wartość zostaje tekstem, a liczba służy tylko do porównania przy sortowaniu. |
| Polskie formatowanie | Klasa `ValueFormatter` przeniesiona z Pythona (`data-concierge chat`): przecinek dziesiętny, grupy cyfr od pięciu cyfr tylko dla `decimal` (odpowiedź nie niesie ról kolumn, więc `int` może być identyfikatorem), skala z tekstu kwoty. Działa na tekście, więc nie traci precyzji; `Intl.NumberFormat` nie jest potrzebny. Tekst sortuje się w polskiej kolejności (`Intl.Collator('pl')`). |
| `truncated` | Pasek nad tabelą: „Pokazano 10000 z N wierszy”, gdy limit odpowiedzi obciął wiersze, albo „Wynik niepełny”, gdy obcięta była procedura źródłowa. |
| Eksport | Pobranie CSV z siatki (wbudowane w AG Grid Community; przy TanStack Table kilka linii własnego kodu). Opcjonalne. |
| Wartości jako tekst | Domyślny renderer komórek AG Grid wstawia tekst: test sprawdza, że `<img src=x onerror=…>` w komórce zostaje tekstem. |

**Siatka po stronie serwera** (stronicowanie po uchwycie sesji przez nowy endpoint) odpada na start. 10 000 wierszy to kilkaset KB JSON (455 KB dla zamówień z 2024), a nowy endpoint potrzebowałby kontroli właściciela sesji i własnych limitów. Wraca, gdy limit odpowiedzi wzrośnie.

**AG Grid React czy TanStack Table (D5):** oba na licencji MIT. AG Grid Community to gotowa siatka z sortowaniem, filtrami, zmianą szerokości kolumn i eksportem CSV, ale jest ciężka, a grupowanie, pivot i model wierszy po stronie serwera są w płatnej wersji Enterprise [U1]. TanStack Table to logika tabeli bez wyglądu: pełna kontrola nad renderowaniem (łatwo zagwarantować zwykły tekst) i mały rozmiar, za to więcej własnego kodu (wygląd, wirtualizacja przez TanStack Virtual, eksport). **Decyzja (D5, 2026-11-04): AG Grid React (Community)**, bo na start daje najwięcej bez pisania kodu. Siatka siedzi w jednym komponencie (`ResultGrid`), więc przejście na TanStack Table, gdyby przyszły system komponentów produkcyjnego frontendu tego wymagał, dotyczy tylko jego. Tabulator (rozważany w wariancie A [U2]) odpada, bo przy React obie powyższe biblioteki są naturalniejsze.

## 7. Wykresy (3B)

### 7.1. Kto wybiera rodzaj wykresu

| Wariant | Opis | Ocena |
|---------|------|-------|
| **a. Tylko reguły w interfejsie** | Model nic nie wie o wykresach. Interfejs pokazuje przycisk „Wykres” przy tabeli i wybiera rodzaj z typów kolumn (data + liczba → liniowy, kategoria + liczba → słupkowy). | Najprostsze, ale nie spełnia życzenia, żeby model proponował wykres i rozumiał prośbę „porównaj kategorie na wykresie”. |
| **b. Model wybiera z zamkniętej specyfikacji** | Model w `present_result` podaje rodzaj z krótkiej listy, uchwyt i kolumny. Backend sprawdza je ze schematem, interfejs rysuje. | **Rekomendowany.** Ten sam wzorzec co tabele i kandydaci: model wskazuje uchwyt i kolumny, backend dokleja dane. |
| **c. Model pisze specyfikację Vega-Lite albo opcje ECharts** | Pełna swoboda wyglądu. | **Odrzucony.** Specyfikacja to kod dla przeglądarki: `data.url` w Vega-Lite pobiera dane z dowolnego adresu [U6], co daje kanał wycieku. Opcje ECharts przyjmują funkcje i HTML w formatterach. Walidator takiej specyfikacji to dokładnie ten rodzaj kodu, którego unikamy. |

### 7.2. Specyfikacja wykresu

Nowe pole w `AgentAnswer` (szkic, nazwy do ustalenia w planie):

```python
type ChartKind = Literal["line", "bar", "stacked_bar", "pie", "scatter"]


class ChartRequest(BaseModel, frozen=True):
    handle: Handle = Field(description="Handle of a returned table to draw, e.g. r2")
    kind: ChartKind = Field(description="...")  # when to use which kind, in the description
    x: str = Field(description="Column on the x axis, or the category of a pie")
    y: tuple[str, ...] = Field(description="Numeric columns to draw; one for a pie")
    series: str | None = Field(
        default=None, description="Column that splits the data into series (long format)"
    )
    title: str = Field(description="Chart title for the user, in Polish; data values by anchor")


class AgentAnswer(BaseModel, frozen=True):
    ...
    chart: ChartRequest | None = Field(
        default=None, description="Only when the user asks for a chart"
    )
```

`Response` dostaje odpowiadające pole `chart` (rodzaj, uchwyt, kolumny, tytuł z podstawionymi kotwicami). Dane wykresu interfejs bierze z tabeli o tym samym uchwycie w `tables`, więc nic nie jest wysyłane dwa razy.

**Kontrole backendu** (proste, na schemacie i liczbie wierszy, w stylu kontroli kotwic):

- uchwyt jest w `tables` tej odpowiedzi,
- kolumny istnieją, `y` są liczbowe (`int`, `decimal`), `x` jest datą, tekstem albo liczbą zależnie od rodzaju,
- limity wierszy według rodzaju, np. kołowy do 8 wycinków, słupkowy do 50 kategorii, liniowy do 1000 punktów,
- `title` przechodzi przez `AgentAnswer.texts`: kotwice, kontrola adresów URL i znacznika instrukcji.

Błąd wraca do modelu jako prośba o poprawkę z komunikatem bez wartości z danych (002, sekcja 6.2), np. „pie needs at most 8 rows, r2 has 12; aggregate the rest in query or choose bar”. Model może wtedy przekształcić dane zapytaniem `query` (top N z „pozostałe”, `PIVOT`, agregacja po miesiącu) i wskazać nowy uchwyt.

**Co model wie, wybierając wykres:** typy kolumn, liczbę wierszy i zakres dat uchwytu. Tyle trafia do niego już dziś (002, sekcja 4.1), więc wykres nie poszerza dostępu do danych. Liczbę kategorii w kolumnie model może poznać zapytaniem `SELECT DISTINCT` (dostaje liczbę wierszy, nie wartości).

### 7.3. Przebieg

**Wykres o istniejące dane** (najczęstszy przypadek z życzenia: „potrzebuję wykresu”):

1. Tura 1: „Sprzedaż po miesiącach za 2024” → tabela `r2`, bez wykresu.
2. Tura 2: „Pokaż to na wykresie” → model zna `r2` z historii: sesja przechowuje całą rozmowę z wynikami narzędzi, czyli podsumowaniami uchwytów, a zamienia tylko komentarz fazy 2 na szkic (002, sekcja 6.3). Model wybiera `line` (`x: month`, `y: [net_sales]`) i woła `present_result` z `tables: [r2]` i `chart`. Bez procedur i bez `query`: w trybie `none` jedna runda z modelem plus strażnik, w trybie `minimal` jeszcze runda fazy komentarza.
3. Uzasadnienie wyboru model zapisuje w `assumptions` („wykres liniowy: dane ułożone w czasie”), bo to wybór dokonany za użytkownika. Nie w `message`: w trybie `minimal` faza komentarza pisze `message` od nowa, a `assumptions` i tytuły zostawia bez zmian. Faza komentarza powinna też dostać specyfikację wykresu w szkicu, żeby jej tekst nie przeczył wykresowi.

**Wykres od razu** („Narysuj sprzedaż po kategoriach w 2024”): ten sam `present_result`, tylko po pobraniu danych.

**Przełączanie w interfejsie:** przy wykresie lista „Inny rodzaj”, która pokazuje rodzaje zgodne z typami kolumn (reguły z wariantu a). Zmiana rysuje dane od nowa bez wołania modelu. Model proponuje, użytkownik może poprawić za darmo.

**Czy model ma najpierw zapytać?** Zamiast rysować, model mógłby zwrócić `needs_clarification` („Proponuję wykres liniowy albo słupkowy. Który?”). To dodatkowa tura (~8 s) dla decyzji, którą przełącznik w interfejsie załatwia od ręki. **Decyzja (D2, 2026-11-04):** model rysuje od razu z uzasadnieniem w `assumptions`, a pyta tylko, gdy prośba jest niejasna (np. które z kilku miar pokazać).

**Wykres bez prośby:** w 3B model dodaje wykres tylko na prośbę użytkownika. Samodzielne propozycje wykresu to osobna decyzja na później.

### 7.4. Rysowanie

Interfejs mapuje specyfikację na opcje ECharts: klasa `ChartOptionsBuilder` (jedna metoda na rodzaj, testowana w Vitest), a komponent `ChartView` tylko je rysuje. Dane idą przez `dataset` z wierszy tabeli. Kontrakt jest niezależny od biblioteki, więc inny frontend może rysować tę samą specyfikację czym innym.

| Biblioteka | Licencja | Za | Przeciw |
|------------|----------|----|---------|
| **Apache ECharts** (6.x) [U7] | Apache-2.0 | Wszystkie potrzebne rodzaje, `dataset` pasuje do tabel, renderer canvas dla wielu punktów, zapis do PNG | Większy plik. Tooltip domyślnie składa HTML. |
| Chart.js | MIT | Mały, prosty | Mniej rodzajów, słabszy przy wielu seriach |
| Vega-Lite | BSD-3 | Deklaratywny, dobry do budowania specyfikacji w Pythonie (Altair) | Specyfikacja jako powierzchnia ataku (7.1 c), większy rozmiar |

Rekomendacja: ECharts. Warunek bezpieczeństwa: nazwy kategorii i serii pochodzą z danych (także z tekstu wpisanego przez osoby spoza firmy), więc tooltip i etykiety muszą je escapować. Do sprawdzenia w planie: domyślny formatter ECharts albo własny formatter z escapowaniem, albo `renderMode: 'richText'`.

Liczby dla wykresu to `Number(value)` z tekstu kwoty. Utrata precyzji nie ma znaczenia dla rysunku, a tooltip pokazuje oryginalny tekst sformatowany jak w siatce.

### 7.5. Ewaluacja

Zestaw ewaluacyjny dostaje grupę przypadków wykresowych: oczekiwany rodzaj (albo zbiór dopuszczalnych), kolumny `x` i `y`, brak wykresu, gdy użytkownik o niego nie prosił. Dwuturowe przypadki („raport”, potem „na wykresie”) sprawdzają, że model nie pobiera danych od nowa.

## 8. Streaming i czas oczekiwania

Pomysły ze [`szybsza-tura`](../ideation/2026-10-28-szybsza-tura.md) czekały na frontend. Stan wiedzy z próby z 2026-11-01:

- Streaming tekstu komentarza przy obecnych ustawieniach (myślenie `summarized`) prawie nic nie daje: argumenty `present_result` przychodzą jednym kawałkiem, ~1 s przed końcem tury.
- Najwięcej da **postęp narzędzi**: „Sprawdzam prośbę…”, „Pobieram zamówienia (api.SalesOrder_List)…”, „Liczę zestawienie…”. Teksty buduje backend z nazw narzędzi i katalogu, a nie model, więc nie wymagają kontroli kotwic ani adresów URL.
- `RecordingModel` w zestawie ewaluacyjnym nadpisuje tylko `request`. Ścieżka strumieniowa modelu wyzerowałaby po cichu koszt i liczbę ponowień w raporcie.

**Decyzja (D3, 2026-11-04):** 3A i 3B bez streamingu, z wskaźnikiem czasu (sekcja 5). Postęp tury to osobny krok **3C**, który robimy po 3B. Szkic: endpoint strumieniowy (np. `POST /ask/stream`), który wysyła zdarzenia postępu z dekoratora narzędzi (tego samego, który zapisuje `tool_executed`) i z `AskService` (strażnik, faza komentarza), a na końcu pełny `Response`. Model nie musi streamować, więc uwaga o `RecordingModel` tego kroku nie dotyczy. Przed 3C warto zmierzyć wpływ myślenia na czas tury (pierwszy krok z dokumentu `szybsza-tura`).

Format strumienia jest otwarty i do wyboru w planie 3C:

| Wariant | Za | Przeciw |
|---------|-----|---------|
| Własne zdarzenia SSE (`progress`, `response`) | Najprostsze, pełna kontrola, klient czyta je `fetch` bez bibliotek | Własny format, bez gotowych klientów |
| Format AG-UI albo Vercel AI z naszego endpointu (sekcja 4.1) | Gotowe klienty protokołu | Protokół zakłada historię po stronie klienta i strumień tekstu modelu; nasz `Response` trzeba upchnąć w zdarzeniu niestandardowym |

## 9. Bezpieczeństwo interfejsu (POC)

| Zagrożenie | Kontrola |
|------------|----------|
| HTML lub skrypt w danych albo w tekście modelu | Tekst w JSX i formattery bez HTML. Zakaz `dangerouslySetInnerHTML` (reguła ESLint). |
| Skrypty z zewnątrz | Biblioteki w zbudowanych plikach, bez CDN. Nagłówek `Content-Security-Policy` ze `script-src 'self'` na odpowiedziach frontendu (nie na `/docs` i `/redoc` FastAPI, które ładują skrypty z CDN). Styl inline jest dozwolony (`style-src 'unsafe-inline'`), bo AG Grid wstrzykuje style motywu. |
| Łańcuch dostaw npm | `package-lock.json` w repozytorium, `npm ci` przy budowaniu, `npm audit` w przeglądzie zależności, możliwie mało paczek. |
| Wysłanie danych wrażliwych do modelu przez interfejs | Przycisk kandydata wysyła `id`, nie opis (ograniczenie 6). |
| Podszycie się pod innego użytkownika | Bez uwierzytelniania pole `user_id` jest jawne, tak jak w CLI i HTTP dziś (001, sekcja 8.2, poza zakresem POC). |
| Współbieżny zapis logu | Strona działa w procesie uvicorna z `POST /ask`. Nie uruchamiać jednocześnie `data-concierge chat` na tym samym pliku logu. |

## 10. Zmiany w kodzie według modułów

| Moduł | 3A | 3B |
|-------|----|----|
| `frontend/` (nowy, TypeScript) | Projekt Vite + React: `ApiClient`, typy z OpenAPI, okno czatu, siatka, formatowanie kwot, testy Vitest | `ChartOptionsBuilder`, `ChartView`, przełącznik rodzaju |
| `web` (nowy, Python) | Cienki kontroler serwujący zbudowane pliki z `frontend/dist` pod `/` | — |
| `bootstrap` | `http_app()` montuje moduł `web` | — |
| `api` | Bez zmian kontraktu. Polecenie `data-concierge openapi` zapisuje schemat API bez bazy i modelu | `ResponseChart` w `Response` |
| `agent` | — | `ChartRequest` w `AgentAnswer`, tytuł w `texts`, kontrola w `ResponseAssembler`, instrukcje o wyborze wykresu, wykres w szkicu dla fazy komentarza |
| `audit` | — | Zdarzenie `response` zapisuje wykres razem z odpowiedzią, bez zmian w kodzie |
| testy | Test endpointu `/` i plików statycznych | Testy kontroli specyfikacji, przypadki w zestawie ewaluacyjnym |
| `docs/standards` | Standard TypeScript i React (sekcja 4.3) | — |

Zmiana schematu `present_result` zmienia raz stały początek rozmowy i cache promptu. To jednorazowy koszt przy wdrożeniu, a nie w każdej turze.

## 11. Decyzje

| # | Pytanie | Rekomendacja |
|---|---------|--------------|
| D1 | Technologia interfejsu | **Zdecydowane: C**, React + TypeScript + Vite w `frontend/`, typy z OpenAPI, ECharts, zbudowane pliki serwowane przez FastAPI (sekcja 4.3). |
| D2 | Czy model pyta o rodzaj wykresu przed narysowaniem | **Zdecydowane:** rysuje od razu z uzasadnieniem w `assumptions`, a użytkownik przełącza rodzaj w interfejsie bez modelu. |
| D3 | Streaming teraz czy później | **Zdecydowane:** krok 3C z postępem tury, po 3B. Na start wskaźnik czasu. Format strumienia (własne SSE czy AG-UI/Vercel AI) do wyboru w planie 3C (sekcja 8). |
| D4 | Serwowanie | **Zdecydowane razem z D1:** zbudowane pliki serwowane przez tę samą aplikację i domenę co `POST /ask`, bez CDN. Przy pracy lokalnej serwer Vite z proxy. |
| D5 | Siatka | **Zdecydowane:** AG Grid React (Community), zamknięty w komponencie `ResultGrid`. TanStack Table jako ewentualna wymiana później (sekcja 6). |

## 12. Źródła

- [U1] AG Grid, licencje i porównanie wersji Community i Enterprise: https://www.ag-grid.com/javascript-data-grid/community-vs-enterprise/ (Community na licencji MIT; grupowanie, agregacja, pivot, wykresy zintegrowane i model wierszy po stronie serwera tylko w Enterprise). Wirtualizacja wierszy w Community według porównania Bryntum: https://bryntum.com/blog/the-best-javascript-data-grids-in-2026
- [U2] Tabulator, wirtualny DOM: https://tabulator.info/docs/6.2/virtual-dom (renderuje tylko widoczne wiersze, wymaga stałej wysokości tabeli); najnowsza wersja według dokumentacji: 6.5.0; licencja MIT: https://github.com/olifolkerd/tabulator
- [U3] NiceGUI: `ui.chat_message` z `text_html=False` domyślnie i `sanitize` przez DOMPurify przy HTML: https://nicegui.io/documentation/chat_message; serwer na FastAPI z WebSocketami: https://nicegui.io/documentation
- [U4] Streamlit: `st.chat_message` przyjmuje dowolne elementy, w tym wykresy: https://docs.streamlit.io/develop/api-reference/chat/st.chat_message; `st.markdown` renderuje Markdown i escapuje HTML przy `unsafe_allow_html=False`: https://docs.streamlit.io/develop/api-reference/text/st.markdown
- [U5] Gradio `Chatbot`: `render_markdown=True` i `sanitize_html=True` domyślnie, komponenty (`gr.Dataframe`, `gr.Plot`) jako treść wiadomości: https://www.gradio.app/docs/gradio/chatbot
- [U6] Vega-Lite, źródła danych (`data.url` ładuje dane z adresu): https://vega.github.io/vega-lite/docs/data.html
- [U7] Apache ECharts 6.0 (sierpień 2025), 6.1.0 jako najnowsza stabilna według trackera wydań: https://releasealert.dev/github/apache/echarts. Wersję do przypięcia potwierdzić w planie.
- Pydantic AI 2.55.0, sprawdzone w zainstalowanym pakiecie: `pydantic_ai.ui` ma `_web` (`create_web_app`, HTML z `cdn.jsdelivr.net/npm/@pydantic/ai-chat-ui`), `vercel_ai` i `ag_ui`.

Nie zweryfikowane: Chainlit i escapowanie w tooltipach ECharts (do sprawdzenia w 3B). Wersje i licencje paczek npm stosu C sprawdzone w rejestrze npm 2026-11-04 (plan 3A, KTD3).

## 13. Uzupełnienia z implementacji kroku 3A (2026-11-04)

- **Odświeżenie strony zaczyna nową rozmowę** zamiast zachowywać `session_id` w `sessionStorage` (sekcja 5): użytkownik nie widziałby wcześniejszych wiadomości, które model ma w historii.
- **Prompt kandydata:** `Wybieram {id}.`, sprawdzony na żywo na przykładzie P2.
- **CSP tylko na odpowiedziach frontendu**, ze `style-src 'unsafe-inline'` dla stylów motywu AG Grid (sekcja 9).
- **Wersje:** TypeScript 5.9, bo `typescript-eslint` i `openapi-typescript` nie wspierają jeszcze 7.0; jsdom 29, bo 30 wymaga Node 24.15. Wszystkie wersje przypięte dokładnie w `frontend/package.json`.
- **Rozmiar paczki:** AG Grid z `AllCommunityModule` daje ok. 1,4 MB JS (ok. 410 KB po gzip). Zawężenie modułów do sortowania, filtrów i modelu wierszy po stronie klienta to optymalizacja na później.
- **Teksty AG Grid** (menu filtrów, podpowiedzi) są po polsku z paczki `@ag-grid-community/locale` (od 2026-11-08, wcześniej po angielsku).
- **Montowanie pod `/`:** przy zbudowanym interfejsie zapytanie złą metodą albo z końcowym ukośnikiem (np. `GET /ask`, `GET /health/`) dostaje 404 z plików statycznych zamiast 405 albo przekierowania FastAPI. Poprawne wywołania API działają bez zmian, więc przyjmujemy to w POC.
- **`frontend/dist` i `node_modules` są poza gitem.** Po klonie interfejs trzeba zbudować (`npm ci`, `npm run build`); bez tego `/` podpowiada polecenia.

## 14. Poprawki po sprawdzeniu w przeglądarce (2026-11-08)

Właściciel poprosił o sprawdzenie strony w Chrome przez Playwright. Lista kontrolna z PR #23 (P1–P5, kandydaci, 10 000 wierszy, restart serwera, audyt) przeszła. Te poprawki dotyczą wyglądu:

- **Szerokość kolumn:** kolumny dostają szerokość swoich wartości (z nagłówkiem). Tabela węższa od siatki rozciąga się na całą szerokość, a szersza zachowuje szerokości i przewija się w bok. Wcześniej każda kolumna miała 200 px, więc wąskie tabele zostawiały pustą przestrzeń, a w szerokich ostatnie kolumny były schowane.
- **Krótkie tabele** kończą się na ostatnim wierszu. AG Grid zostawia miejsce na komunikat o braku wierszy, a pustą tabelę i tak pokazujemy tekstem „Brak wierszy.”.
- **Nagłówki kolumn liczbowych:** etykieta stoi po prawej nad wartościami, a ikona filtra zostaje przy prawej krawędzi jak w innych kolumnach. Typ `rightAligned` przenosił ikonę na lewą krawędź, gdzie wyglądała na część poprzedniej kolumny.
- **Informacja o obcięciu** grupuje cyfry jak kwoty: „Pokazano 10 000 z 14 244 wierszy”.
- **Pole promptu** czyści się po wysłaniu, bo prompt pojawia się w rozmowie. Gdy tura się nie uda (odmowa, błąd połączenia), tekst wraca do pola.
- **Opis kandydata** pomija wartość tekstową, której wszystkie słowa padły już we wcześniejszej wartości, np. kolor i rozmiar po nazwie „HL Mountain Frame - Black, 38” (002, sekcja 4.3).
- **Ikona strony** (`favicon.svg`).

## 15. Uzupełnienia z implementacji kroku 3B (2026-11-08)

Sekcja 7 opisuje projekt, a ta sekcja to, co z niego wyszło w kodzie. Podsumowanie, wyniki ewaluacji i możliwe rozszerzenia są w raporcie [`2026-11-08-wykresy.md`](../reports/2026-11-08-wykresy.md).

- **Kontrakt:** jedno pole `chart` w `AgentAnswer` i w `Response` (001, sekcja 9). Lista wykresów to rozszerzenie na później.
- **Kontrole** (`agent/charts.py`, klasa `ChartPolicy`):
  - wykres tylko przy statusie `ok` i na tabeli z `tables`;
  - tabela nie może być obcięta ani przez limit procedury, ani przez limit odpowiedzi;
  - limity według rodzaju: liniowy i punktowy do 1000 wierszy, słupkowy do 50 kategorii, kołowy do 8 wierszy, do 12 serii;
  - kołowy wymaga tekstowej kolumny `x`, punktowy liczbowej. Słupkowy skumulowany wymaga serii albo co najmniej dwóch miar.

  Ta sama tabela reguł jest na stronie (`ChartKinds`) dla przełącznika rodzaju.
- **Rysowanie:** ECharts 6.1.0 importowany modułowo (`EChartsHost`). Opcje buduje `ChartOptionsBuilder`, bez `dataset`: serie mają dane wprost, bo format długi (kolumna `series`) i tak trzeba rozłożyć na serie. Dymek ma domyślny układ ECharts, który zamienia nazwy na tekst, i tylko `valueFormatter` dla liczb.
- **Bez polskiej lokalizacji ECharts.** Plik `langPL` rejestruje się przez pełny pakiet `echarts`, którego nie importujemy. Jedyny napis widoczny dla użytkownika to podpis przycisku PNG („Zapisz jako PNG”), ustawiony wprost.
- **Tytuł wykresu** jest nagłówkiem nad wykresem (JSX), a nie tytułem ECharts, więc ma ten sam styl co tytuły tabel i jest zwykłym tekstem.
- **Rozmiar paczki:** ECharts dodał ok. 610 KB do zbudowanego JS (z 1,47 MB do 2,08 MB, gzip z 419 KB do 627 KB).
- **Terminal i audyt:** `data-concierge chat` wypisuje jedną linię „Wykres liniowy: tytuł (x; y)”, a `audit` pokazuje wykres w zdarzeniu `response`.
