# Szybsza tura: pomiar i pomysły

Data: 2026-10-28. Status: pomysły do dalszej pracy (jeszcze nie brainstorm ani plan).

## Skąd temat

Pytanie: czy przekazanie wyboru narzędzia do modelu decyzyjnego (Microsoft-Decision-1) przyspieszy turę. Odpowiedź: nie.

- Decision-1 to dotrenowany Qwen3.5-9B (Microsoft Foundry, OpenRouter), czyli model spoza Anthropic. Prompt użytkownika trafiałby do drugiego dostawcy, co łamie zasadę „tylko modele Anthropic” i zmienia założenia dokumentu 002.
- Wybór procedury działa: w ewaluacji trafność procedur i parametrów wynosi 37/37.
- Decision-1 tylko ocenia podane opcje. Nie wypełni parametrów, nie napisze `query` ani `present_result`, więc Haiku i tak robi tę samą rundę. Router dokłada jedno wywołanie sieciowe i nie oszczędza żadnej rundy.

## Pomiar (2026-10-28, tryb `none`, 37 przypadków, 39 tur)

Metoda: doraźna wtyczka pytest (bez zmian w repo) owijała `RecordingModel.request`, `RecordingProcedureRepository.execute`, `QueryService.run`, `AskService.ask` i `CommentService.write`. Mierzyła czas każdego zapytania do modelu z tokenami i wywołanymi narzędziami, a także czas procedur, zapytań DuckDB i całej tury. Przebieg: 37/37, 308 s, $0.03, średnio **7,9 s na turę**.

| Składnik | Czas | Udział |
|---|---|---|
| Model (112 zapytań, 2,87 na turę) | 287 s | ~93% |
| Procedury SQL Server | ~20 s (zegar ścienny) | ~6% |
| DuckDB (`query`) | 0,4 s | 0,1% |
| Reszta (Pydantic AI, podsumowania, składanie odpowiedzi) | ~1 s | ~0,3% |

| Krok modelu | Liczba | Udział w czasie | Średnio | Tokeny wyjścia |
|---|---|---|---|---|
| `present_result` | 42 | 36% | 2,6 s | 362 |
| wybór procedury + parametry | 38 | 35% | 2,8 s | ~140 |
| `query` | 32 | 23% | 2,2 s | 281 |

Wnioski:

- Jedno zapytanie do modelu kosztuje ~1,7 s stałego narzutu plus ~3,2 ms na token wyjścia. O czasie tury decyduje **liczba rund**, a nie ich treść.
- Cache promptu działa: 91% tokenów wejścia to odczyt z cache'u.
- Udział myślenia nie jest znany. Pierwsza wersja tego dokumentu mówiła, że myślenia jest mało (60–90 znaków), ale to długość streszczenia, a nie myślenia modelu. Próba z 2026-11-01 (mała sztuczna tura, po jednym albo dwa przebiegi na wariant) dała 330–430 tokenów myślenia na ~650–740 tokenów wyjścia i turę 2,6 s bez myślenia wobec 3,8–4,3 s z myśleniem. Trzeba to zmierzyć na zestawie ewaluacyjnym, razem z wpływem na jakość.
- Najwolniejsza tura: `obciety-wynik-produkty`, 31,5 s. Model wywołał 4× `api.SalesOrderLine_List` równolegle i każde trwało ~14 s, a pojedyncze wywołanie tej procedury ~1 s. Wygląda to na rywalizację o zasoby w SQL Server.
- `kategorie-2024`: jedno zapytanie do modelu trwało 10,5 s przy 204 tokenach, czyli wahanie po stronie API.

Wzorce rund w turach (P = procedura, Q = `query`, A = `present_result`):

| Wzorzec | Tur | Rund |
|---|---|---|
| P → Q → A | 19 | 3 |
| P → A | 5 | 2 |
| P → Q → Q → A | 5 | 4 |
| A (doprecyzowanie, `unsupported`) | 5 | 1 |
| P → P → Q → A i podobne | 5 | 4–5 |

## Pomysł 1: mniej rund z modelem (odłożony)

Po `query` model w trybie `none` dostaje tylko schemat i liczbę wierszy. Nazwy kolumn wybrał sam, a uchwyt da się przewidzieć. Mimo to czeka całą rundę (~2,5 s), żeby napisać `present_result`.

- **A. `query` i `present_result` w jednej odpowiedzi.**
  - Wykonanie optymistyczne: backend wykonuje zapytanie, potem sprawdza odpowiedź i kotwice.
  - Przy błędzie SQL, 0 wierszy przy statusie `ok` albo złej kotwicy odrzuca odpowiedź i oddaje modelowi wynik `query` jak dziś.
  - Mechanizm: `end_strategy="exhaustive"` w Pydantic AI. Do sprawdzenia, czy narzędzia wykonują się przed walidacją wyjścia. Opiera się na zachowaniu biblioteki, więc jest kruchy.
- **B. SQL wprost w `present_result` (rekomendowany).**
  - `tables` przyjmuje uchwyt albo SQL nad uchwytami z tytułem. Backend wykonuje go tym samym zabezpieczonym silnikiem co `query`.
  - Kotwice wskazują te tabele. Błąd SQL albo kotwicy daje `ModelRetry` na wyjściu.
  - `query` zostaje do kroków pośrednich (kandydaci do doprecyzowania, filtr przed JOIN).
- **C. Cała tura w jednej rundzie (plan, potem wykonanie).**
  - Model w jednej odpowiedzi woła procedurę i pisze `present_result` z SQL nad jej przyszłym uchwytem. Kolumny procedur zna z katalogu.
  - Do pętli wraca tylko przy błędzie, obcięciu albo pustym wyniku.
  - Nie zadziała, gdy kolejny krok zależy od danych, np. id klienta z wyszukiwania.

Szacunek z pomiaru (poprawiony 2026-11-01). Scalenie rund usuwa tylko stały narzut jednej rundy (~1,6 s), bo tokeny SQL i odpowiedzi model i tak musi wygenerować, a nie całą rundę (~2,5 s), jak zakładała pierwsza wersja tej tabeli. Dopasowanie jest zgrubne (R² ~0,2–0,36):

| Wariant | Rund na turę | Czas tury |
|---|---|---|
| dziś | 2,87 | 7,9 s |
| A lub B (−1 runda w 28 z 39 tur, kończących się Q → A) | ~2,15 | ~6,8 s |
| B + C | ~1,7 | ~6,0 s |

Ryzyka:

- **Jakość.** Model traci informację zwrotną przed odpowiedzią: nie wie o pustym wyniku albo o liczbie wierszy innej niż oczekiwał. Część łapie backend: `ok` przy 0 wierszach, `{r3.x}` przy wielu wierszach. Resztę musi pokazać ewaluacja (punkt odniesienia: 37/37).
- **Tryb `minimal`.** Faza komentarza i tak jest osobną rundą, więc względny zysk będzie mniejszy.
- **Architektura.** Zmiana dotyczy sekcji 6, 7 i 9 dokumentu 001 oraz promptów, `AgentAnswer` i `ResponseAssembler`.

**Status: odłożone (decyzja właściciela, 2026-11-01).** Zysk ~1,1 s na turę przy zmianie kontraktu odpowiedzi i ryzyku dla jakości. Do tego pomysłu można wrócić później; zapisane niżej ustalenia zostają aktualne.

Jak wyglądałoby scalenie rund 2 i 3 (szkic kształtu, jeszcze nie projekt):

```json
{
  "status": "ok",
  "message": "W Berlinie znaleziono {t1#rows} klientów.",
  "tables": [
    {"name": "t1", "title": "Klienci z Berlina", "sql": "SELECT ... FROM r1 ORDER BY ..."}
  ],
  "assumptions": []
}
```

Przykład `lista-berlin`: dziś rundy 2 i 3 trwają 2,98 s + 2,00 s (239 + 284 tokeny wyjścia). Po scaleniu model nadal generuje ~523 tokeny, ale znika jeden stały narzut. Daje to ~3,4 s zamiast ~5,0 s, czyli turę ~5,2 s zamiast 6,8 s.

Zachowanie backendu po odebraniu takiej odpowiedzi:

- SQL poprawny i kotwice pasują: tura się kończy bez kolejnej rundy.
- Błąd SQL albo zła kotwica: `ModelRetry` z komunikatem, czyli dodatkowa runda.
- Pusty wynik przy statusie `ok`: model dostaje tę informację i zmienia status na `no_data`.

Sprawy do rozstrzygnięcia przy powrocie do tematu:

- Model pisze komentarz, zanim SQL się wykona, więc reakcję na liczbę wierszy muszą zastąpić kontrole backendu. Czy to wystarcza, pokaże ewaluacja.
- Sumy w komentarzu często wymagają osobnego zapytania z jednym wierszem (wzorzec P → Q → Q → A w 5 turach). Odpowiedź musiałaby mieć tabele pokazywane użytkownikowi oraz zapytania pomocnicze tylko do kotwic.
- `query` zostaje do kroków pośrednich, np. budowy kandydatów do doprecyzowania, gdzie model musi zobaczyć `ids`.

**Wymaganie do zachowania: model dalej nie widzi danych.**

- W wariancie B nie dostaje nawet podsumowania końcowej tabeli, bo tura kończy się bez kolejnej rundy.
- W wariancie C nie dostaje podsumowania wyniku procedury przed napisaniem SQL.
- Przy błędzie albo powrocie do pętli dostaje to samo co dziś (komunikat kontroli, podsumowanie uchwytu).
- Faza komentarza w trybie `minimal` działa bez zmian (002).

To, co trafia do dostawcy modelu, nie może się zwiększyć.

## Pomysł 2: streaming (odłożony do frontendu)

**Status: odłożone (decyzja właściciela, 2026-11-01).** Wracamy do tego przy prostym frontendzie projektu, bo tam streaming ma większy sens i więcej możliwości, np. cofanie wygenerowanego tekstu przy błędnej kotwicy. W `data-concierge chat` nic nie zmieniamy. Pomysł zakłada krótszy odczuwalny czas przy tym samym czasie tury.

- **Strumień postępu od pierwszej sekundy:** „Pobieram zamówienia za 2024…”, „Liczę sumy po miesiącach…”. Zdarzenia o wywołaniach narzędzi już istnieją (tam, gdzie zapisuje log audytowy). Najtańsze i bez problemu z kotwicami.
- **Tabele przed komentarzem:** pole `tables` przed `message` w `AgentAnswer`. Backend wysyła tabele z danymi, zanim model skończy komentarz.
- **Streaming tekstu komentarza:**
  - Pydantic AI streamuje częściowe argumenty narzędzia wyjściowego.
  - Backend musi wstrzymywać tekst od `{` do `}` i podstawiać kotwice; dane są już w DuckDB.
  - Otwarte pytanie: co z błędną kotwicą, gdy użytkownik widział już część tekstu (dziś `ModelRetry`). Albo strumień umie „odrzuć i zacznij od nowa”, albo zła kotwica staje się znacznikiem.
  - W trybie `minimal` streamujemy fazę komentarza, a nie `message` z pierwszej fazy.
- `ask()`, CLI i `POST /ask` zwracają dziś cały JSON, więc potrzebny jest nowy kontrakt strumieniowy (SSE albo `VercelAIAdapter`, sekcje 9 i 11 dokumentu 001).

Kolejność: postęp narzędzi, potem tabele przed komentarzem, a streaming tekstu komentarza na koniec.

### Wyniki próby Pydantic AI i API (2026-11-01)

Pydantic AI 2.55.0, `claude-haiku-5-5`. Próby na sztucznej turze, bez bazy i bez danych klientów, po 1–3 przebiegi na wariant. Liczby są orientacyjne.

Co działa:

- `agent.run(..., event_stream_handler=...)` streamuje bez przebudowy pętli agenta. Hooki `AuditCapability` (`before_model_request`, `after_model_request`) działają też na ścieżce strumieniowej (ze źródeł biblioteki, nie z próby).
- Postęp jest wykonalny przy obecnych ustawieniach. `FunctionToolCallEvent` niesie pełne argumenty, a początek pierwszego wywołania narzędzia był widoczny po ~0,8–0,9 s od startu tury.
- Argumenty `present_result` przychodzą w kolejności `status`, `message`, `tables`, więc status jest znany przed pierwszym fragmentem tekstu. Częściowy JSON da się czytać przez `pydantic_core.from_json(..., allow_partial="trailing-strings")`, bez własnego parsera.

Główne ograniczenie: czas pisania komentarza (`present_result`) zależy od myślenia.

| Wariant myślenia | Czas pisania komentarza | Przebiegi |
|---|---|---|
| `display: "summarized"` (dziś) | 0,00–0,08 s, czyli blok naraz na końcu rundy | 2 |
| `summarized` + `anthropic_eager_input_streaming` | 0 s w 2 przebiegach, 1,41 s w jednym | 3 |
| `display: "omitted"` | ~0,7 s | 3 |
| myślenie wyłączone | ~0,8–0,9 s | 2 |

Wnioski dla frontendu:

- Przy dzisiejszych ustawieniach streaming komentarza praktycznie nic nie daje. Tekst płynie najwyżej niecałą sekundę, a pojawia się ~1 s przed końcem tury (a nie ~2 s, jak zakładała wcześniejsza rozmowa).
- `omitted` zabiera streszczenia myślenia z logu audytowego, które dokument 001 (sekcja 8.1) wybrał świadomie. Przy tej zmianie trzeba zaktualizować tę sekcję.
- `eager` dodaje pole do definicji wszystkich narzędzi, więc zmienia początek rozmowy i cache. Nie sprawdzono go z narzędziami procedur w trybie `strict`.
- W fazie komentarza (`minimal`, `output_type=str`) tekst płynie zwykłym `text_delta` przez ~1,2–2 s, bez `eager`. Tryb `minimal` może więc streamować lepiej niż `none`. To wynik surowego strumienia SDK; nie sprawdzono tego przez `CommentService`.
- `RecordingModel` w `tests/evals/harness.py` nadpisuje tylko `request`. Uruchomienie strumieniowe idzie przez `request_stream`, więc koszt i liczba ponowień kotwic po cichu wyszłyby zero. Streaming musi być włączany tylko dla wywołującego, który go chce, albo `RecordingModel` musi obsłużyć `request_stream`. Trzeba też sprawdzić modele używane w testach jednostkowych.
- Frontend może rozwiązać sprawę błędnej kotwicy przez cofnięcie już pokazanego tekstu, czego terminal nie potrafi bez utraty prostoty.

## Drobniejsze

- Zbadać rywalizację przy równoległych wywołaniach `api.SalesOrderLine_List` (blokady, plan, równoległość w SQL Server) albo ograniczyć współbieżność procedur.
- Sprawdzić jednym przebiegiem ewaluacji `effort: low` oraz turę bez myślenia (czas, trafność, skutek dla logu audytowego). Krótsze odpowiedzi dadzą ułamki sekundy.
- Rozbicie czasu tury (model, procedury, DuckDB) mogłoby na stałe trafić do raportu ewaluacji, żeby każda zmiana pokazywała swój wpływ.

## Następne kroki

Żaden krok nie jest teraz w toku. Temat wraca przy prostym frontendzie projektu:

1. Pomiar udziału myślenia na zestawie ewaluacyjnym: dziś, `effort: low` i myślenie wyłączone, czas i trafność 37/37, także w trybie `minimal` (~5 min i ~$0.03 na przebieg). To może być największa dźwignia, a jest tylko zmianą konfiguracji.
2. Streaming na frontendzie: `ce-brainstorm`, zaczynając od postępu narzędzi, z wynikami próby z sekcji o pomyśle 2.
3. Jeśli będzie potrzebny: pomysł 1, wariant B, z wymaganiem „model nie widzi więcej danych niż dziś”, zaczynając od aktualizacji dokumentu 001.
