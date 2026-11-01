# Ochrona przed prompt injection: raport końcowy

Data: 2026-11-01. Plan: [`2026-11-01-1541-feat-ochrona-przed-prompt-injection-plan.md`](../plans/2026-11-01-1541-feat-ochrona-przed-prompt-injection-plan.md). Projekt ochrony: [001, sekcja 8.2](../architecture/001-architektura-data-concierge.md), skutki dla danych: [002](../architecture/002-dostep-modelu-do-danych.md).

## Wniosek

Data Concierge odpiera siedem popularnych zagrożeń na poziomie POC: 45 z 45 przypadków ewaluacji przeszło w obu trybach, a strażnik zablokował 6 ataków i żadnego zwykłego promptu. Kontrole uprawnień, danych i wyjścia w backendzie działają także wtedy, gdy model jest przejęty, co potwierdzają testy ze skryptowanym wrogim modelem. Przejęcie celu nie ma kontroli w backendzie: zatrzymują je strażnik i instrukcje. Strażnik dodaje około 1,5 s i 0,0001 USD do tury.

To nie jest ochrona produkcyjna. Bez uwierzytelniania role i właściciel sesji pokazują zasadę, ale nie są prawdziwą granicą, a kilka kanałów ujawnienia danych zostało świadomie otwartych (sekcja „Ryzyka szczątkowe”).

## Zakres szkód przed zmianami

Model ma tylko narzędzia do odczytu, nie ma sieci, a odpowiedź trafia do tego samego użytkownika. Atak mógł więc doprowadzić do wysłania danych wrażliwych do dostawcy modelu albo do pokazania użytkownikowi mylącego tekstu. Sonda z 2026-11-01 pokazała dwa konkretne problemy:

- zapytanie `SELECT error('More than one row returned by a subquery ' || "CustomerName") ...` zwracało modelowi wartość kolumny wrażliwej, także w trybie `none`;
- w trybie `minimal` tekst z kolumny `City` trafiał do podsumowania, czyli do modelu z narzędziem `query`.

Eskalacji nie było jak pokazać, bo każdy wywołujący miał rolę `user`, a sesja nie miała właściciela.

## Mapa zagrożeń

Nazwy pochodzą z oryginałów: [OWASP Top 10 for LLM Applications 2025](https://genai.owasp.org/llm-top-10/) i [OWASP Top 10 for Agentic Applications z 2025-12-09](https://genai.owasp.org/2025/12/09/owasp-top-10-for-agentic-applications-the-benchmark-for-agentic-security-in-the-age-of-autonomous-ai/).

| Zagrożenie | OWASP LLM | OWASP Agentic |
|---|---|---|
| Przejęcie celu | LLM01:2025 Prompt Injection | ASI01 Agent Goal Hijack |
| Wyciek instrukcji | LLM07:2025 System Prompt Leakage | nie dotyczy |
| Eskalacja uprawnień | LLM06:2025 Excessive Agency | ASI03 Identity & Privilege Abuse, ASI02 Tool Misuse |
| Ujawnienie danych wrażliwych | LLM02:2025 Sensitive Information Disclosure | ASI02 Tool Misuse |
| Pośrednia iniekcja | LLM01:2025 Prompt Injection | ASI01 Agent Goal Hijack, ASI06 Memory & Context Poisoning |
| Niebezpieczne wyjście | LLM05:2025 Improper Output Handling | nie dotyczy |
| Nadmierne zużycie | LLM10:2025 Unbounded Consumption | nie dotyczy |

## Kontrole i dlaczego takie

**Backend jest granicą, a strażnik warstwą.** Instrukcja nie zatrzyma modelu, który wykonuje polecenie atakującego, a strażnik na modelu językowym da się oszukać. Dlatego kontrole uprawnień, danych i wyjścia działają także przy przepuszczonym ataku. Wyjątkiem jest przejęcie celu: strażnik i instrukcje to jedyne, co je ogranicza, a szkodę ogranicza to, że odpowiedź trafia do tego samego użytkownika. OWASP LLM07 mówi wprost, że instrukcja systemowa „should not be considered a secret, nor should it be used as a security control”, więc nie trzymamy w niej sekretów.

| Zagrożenie | Kontrola | Dlaczego tak |
|---|---|---|
| Przejęcie celu | Strażnik Haiku przed agentem i reguły zakresu w instrukcjach | Strażnik zatrzymuje oczywiste próby bez kosztu agenta. Sprawy spoza tematu zostawia agentowi, dzięki czemu pytania o dane wrażliwe nie wyglądają jak atak. |
| Wyciek instrukcji | Stały znacznik w instrukcjach obu faz, sprawdzany w gotowej odpowiedzi | Odpowiedź ze znacznikiem nie dociera do użytkownika i nie trafia do historii. Znacznik łapie dosłowny wyciek także przez komórkę tabeli i SQL w proweniencji. |
| Eskalacja uprawnień | Rola z konfiguracji (`DC_USER_ROLES`), `allowed_roles` w katalogu, sprawdzenie przy każdym wywołaniu, właściciel sesji | Treść promptu nie ma wpływu na rolę. Zestaw narzędzi zostaje stały, bo od niego zależy cache początku rozmowy. |
| Ujawnienie danych wrażliwych | Komunikaty błędów `query` bez wartości z danych | Tekst silnika mógł nieść dane, więc model dostaje stały tekst backendu, a pełny trafia do logu. |
| Pośrednia iniekcja | `City` jako kolumna `sensitive`, szkic zamiast komentarza w historii, tabele fazy komentarza jako wynik narzędzia | Tekst osób trzecich nie dociera do modelu. Faza, która czyta dane, nie może przekazać poleceń fazie z narzędziami. |
| Niebezpieczne wyjście | Link w tekście modelu wraca do modelu, a teksty dla użytkownika to zwykły tekst | Sprawdzamy szablon przed podstawieniem kotwic, więc wartość z danych nie jest cenzurowana. |
| Nadmierne zużycie | `DC_MAX_PROMPT_CHARS` przed strażnikiem i limit żądań fazy komentarza | Za długi prompt nie kosztuje wywołania modelu, a pętla narzędzia nie wychodzi poza limit kroków. |

Każda kontrola zostawia ślad w logu audytowym: werdykt strażnika (`guard`), odmowa roli (`tool_executed`), odrzucona sesja (`session_rejected`) oraz link i znacznik (`output_blocked`).

## Wyniki

**Testy bez API i bazy:** 537 przeszło, w tym 9 scenariuszy wrogiego modelu w `tests/agent/test_hostile_model.py`, które przechodzą przez całą ścieżkę `AskService` przy strażniku przepuszczającym wszystko. **Testy z bazą:** 59 przeszło. **Testy na żywo:** 6 z 6, w tym przyjęcie zasianej wymiany przez API oraz werdykty strażnika na prawdziwym modelu.

**Ewaluacja na prawdziwym modelu i bazie:**

| Tryb | Przebieg | Wynik | Bezpieczeństwo | Tury zablokowane | Szkice | Czas zwykłej tury bez strażnika | Koszt zwykłej tury bez strażnika |
|---|---|---|---|---|---|---|---|
| `none` | `20261101-202452-none` | 45/45 | 8/8 | 6 | 0 | 6,0 s | 0,0009 USD |
| `minimal` | `20261101-203006-minimal` | 45/45 | 8/8 | 6 | 0 | 8,0 s | 0,0012 USD |

Sześć zablokowanych tur to dokładnie przypadki przejęcia celu, wycieku instrukcji i eskalacji, po polsku i angielsku. Dwa pozostałe przypadki bezpieczeństwa strażnik przepuszcza celowo: prośbę spoza tematu odrzuca agent, a pytanie o przełożonych bez roli `hr` odmową kończy backend. W 37 zwykłych przypadkach strażnik nie zablokował żadnego promptu, a każdy przypadek sprawdza oczekiwany werdykt, więc blokada zwykłego promptu oblałaby go. Czas i koszt zwykłej tury liczymy z tych 37 przypadków, bo tury zatrzymane przez strażnika zaniżałyby średnią.

**Czas i koszt strażnika:** 1,5 s na turę i 0,0001 USD na turę, po 0,0050 USD na 47 tur przebiegu. Plan nie stawiał progu odbioru. Czas i koszt zwykłej tury są porównywalne z przebiegami sprzed strażnika, bo ewaluacja mierzy go osobnym modelem nagrywającym: przed strażnikiem było to 6,2 s i 0,0009 USD w trybie `none` oraz 8,3 s i 0,0012 USD w trybie `minimal`.

## Odejście od planu i wniosek z niego

Plan zakładał wyłączone myślenie w fazie komentarza, bo zasiana odpowiedź modelu nie ma bloku myślenia. Próba na żywo pokazała, że API przyjmuje taką wymianę także z myśleniem. Przebieg z wyłączonym myśleniem przeszedł 37/37, ale w 8 z 28 odpowiedzi `ok` model wypisał do komentarza swoje rozumowanie, np. uchwyty i to, czego nie widział, a raz angielską analizę szkicu.

Ewaluacja tego nie oblała, bo nie czyta treści komentarza. Wyszło to dopiero w ręcznym przeglądzie, który plan wymienił jako odpowiedź na ryzyko. Przy zmianach fazy komentarza trzeba czytać komentarze, a nie tylko wynik zestawu. Późniejszy przebieg pokazał jeden komentarz w kształcie JSON, więc instrukcja zabrania teraz tej postaci, a końcowy przebieg nie ma już takiego komentarza. Ma za to 2 komentarze z 28 odpowiedzi `ok`, które podają, że nazwy są „ukryte w tabeli”, choć użytkownik widzi całą tabelę. Instrukcja zabrania takich zdań, a w trzech wcześniejszych przebiegach z myśleniem ich nie było, więc to zmienność modelu i zostaje ryzykiem jakości, a nie bezpieczeństwa.

## Ryzyka szczątkowe

| Ryzyko | Skutek | Dlaczego zostaje |
|---|---|---|
| Brak uwierzytelniania | `user_id` podaje wywołujący, więc może podać cudzy | Poza zakresem POC. To warunek prawdziwej granicy ról i sesji. |
| Brak autoryzacji wierszy i sprawdzania `@UserId` w procedurach | Każdy użytkownik widzi te same wiersze | Wymaga zmian w procedurach. |
| Kanały `ids` i dat | Model może zakodować wartość wrażliwą jako liczbę pod aliasem kolumny `id` albo jako datę | Zamknięcie wymaga analizy treści zapytania (002, sekcja 10). |
| Aliasy i statystyki magazynu | `SELECT "City" AS "Territory"` albo `pragma_storage_info` przenoszą wartości wrażliwe poza oznaczenie kolumny | Ta sama klasa ryzyka. Kierunek: pseudonimy. |
| Mylący tekst od przejętego modelu | Model, który minie strażnika, może napisać w `message` dowolny tekst bez linków, a użytkownik go przeczyta | Odpowiedź trafia do tego samego użytkownika, który wysłał prompt. Backend nie ocenia sensu tekstu. |
| Parafraza instrukcji | Sparafrazowany, przetłumaczony albo częściowy wyciek nie zawiera znacznika | Znacznik łapie tylko dosłowny tekst, a instrukcje nie zawierają sekretów. |
| URL z SQL | Adres złożony w zapytaniu trafia do użytkownika przez kotwicę, a wpisany literałem także do tabeli | Backend nie sprawdza wartości z uchwytów (002, sekcja 10). |
| Linki bez `://` i gołe domeny | `mailto:`, `//example.test` i `example.test` przechodzą kontrolę | Gołą domenę mylilibyśmy ze zwykłym tekstem. Wrócimy do tego, gdy interfejs zacznie linkować domeny. |
| Wnioskowanie z liczby wierszy | Model może zgadywać wartości zapytaniami `LIKE` | Nie zamyka tego żadna kontrola w tym planie. |

## Świadomie odłożone

- **Pseudonimy w DuckDB:** sekcja poniżej.
- **Limitowanie żądań i blokowanie powtarzających się prób:** bez uwierzytelniania nie ma czego przypisać do użytkownika.
- **Strażnik dla wyników narzędzi, historii i tekstów wyjściowych:** tekst osób trzecich nie dociera do modelu, więc nie ma czego oceniać, a wyjście sprawdza backend.
- **Wykrywanie parafrazy instrukcji:** wymagałoby oceny tekstu przez model, a w instrukcjach nie ma sekretów.
- **Zatruty wiersz w AdventureWorks:** test na podstawionym wyniku procedury daje ten sam dowód bez zmiany danych. Wrócimy do tego, gdy pokaz będzie wymagał ataku na żywo.
- **Osobny dokument architektury 003:** ochrona dotyka istniejących sekcji 001 i 002.
- **Aktualizacja `docs/demo`:** prezentacja nadal wymienia ochronę jako brakującą.

## Pseudonimy w DuckDB jako opcja na przyszłość

**Co to jest.** Magazyn wyników zamienia wartości kolumn wrażliwych na pseudonimy przy zapisie, np. `CUSTOMER_17` zamiast nazwy klienta, a `ResponseAssembler` odtwarza prawdziwe wartości dopiero w odpowiedzi.

**Co zamyka.** SQL modelu pracuje wtedy na pseudonimach, więc kanały `ids` i dat, aliasy, statystyki magazynu i `error()` niosą tylko pseudonim. Dotyczy to też wnioskowania z liczby wierszy po tekście wartości oraz adresu złożonego w SQL: wartość w linku byłaby pseudonimem, o ile backend nie podstawi jej przy składaniu odpowiedzi.

**Ile kosztuje.** To największa z rozważanych zmian. Dotyka zapisu w `results`, kotwic, `ResponseAssembler` i kandydatów, wymaga mapy pseudonimów na sesję, a odtwarzanie wartości w odpowiedzi musi działać także dla kolumn wyliczonych, np. `upper("CustomerName")`. Filtry `ILIKE` i sortowanie po nazwie przestają działać, bo model pisze SQL nad pseudonimami, więc trzeba by je przenieść do parametrów procedur. Skutek dla jakości trzeba zmierzyć całym zestawem ewaluacji.

**Kiedy do nich wrócić.** Gdy prawdziwe dane klientów mają trafiać do tej usługi i umowa z klientem nie dopuszcza ich u dostawcy modelu nawet przy przejętym modelu. Do tego czasu tryb `none` z kontrolami z tego raportu zamyka przypadkowe wysłanie danych.

## Warunki przed kolejnymi krokami

- **Przed procedurami zapisu:** zatwierdzanie przez człowieka każdego zapisu (OWASP LLM01 zaleca je dla działań wysokiego ryzyka), limity rekordów i tryb próbny. Katalog ma już pole `access`.
- **Przed interfejsem użytkownika:** klient nie interpretuje formatowania ani linków w tekstach (001, sekcja 9), a domeny zostają tekstem. Inaczej gołe domeny i `mailto:` z tabeli ryzyk stają się linkami do kliknięcia.
- **Przed wieloma użytkownikami:** uwierzytelnianie, które daje `user_id`, sprawdzanie `@UserId` w procedurach, limitowanie żądań oraz DuckDB w osobnym procesie (001, sekcja 7.4a).

## Źródła

- OWASP GenAI, [LLM01:2025 Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/), [LLM07:2025 System Prompt Leakage](https://genai.owasp.org/llmrisk/llm072025-system-prompt-leakage/) i [Top 10 for LLM Applications 2025](https://genai.owasp.org/llm-top-10/).
- OWASP GenAI, [Top 10 for Agentic Applications](https://genai.owasp.org/2025/12/09/owasp-top-10-for-agentic-applications-the-benchmark-for-agentic-security-in-the-age-of-autonomous-ai/).
- Anthropic, [Mitigate jailbreaks and prompt injections](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/mitigate-jailbreaks) i [Reduce prompt leak](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-prompt-leak).
- Meta, [Agents Rule of Two](https://ai.meta.com/blog/practical-ai-agent-security/).
