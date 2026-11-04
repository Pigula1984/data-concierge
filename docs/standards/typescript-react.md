# TypeScript i React: frontend w `frontend/`

Zasady dla kodu interfejsu w przeglądarce. Duch jest ten sam co w [python-czysty-kod.md](python-czysty-kod.md): małe jednostki z jedną odpowiedzialnością, zależności podawane z zewnątrz, czytelne nazwy, brak powtórzeń. Architekturę interfejsu opisuje [`003-interfejs-uzytkownika.md`](../architecture/003-interfejs-uzytkownika.md).

## 1. Język

- Kod, nazwy, komentarze i komunikaty błędów w konsoli piszemy po angielsku.
- Teksty, które czyta użytkownik (etykiety, przyciski, komunikaty na stronie), piszemy po polsku.

## 2. Struktura

- **Komponenty funkcyjne z jedną odpowiedzialnością.** Komponent pokazuje jedną rzecz (`AnswerView`, `ResultGrid`, `PromptForm`) i dostaje dane przez propsy. Komponenty funkcyjne z hookami to idiom Reacta; to nie jest odejście od obiektowości, bo logika nie żyje w komponentach.
- **Logika bez UI w klasach.** Klient API, formatowanie wartości, budowanie kolumn siatki czy treści promptu to klasy z zależnościami w konstruktorze (`ApiClient` dostaje funkcję `fetch`). Testujemy je bez renderowania.
- **Bez luźnych funkcji pomocniczych w modułach.** Zachowanie należy do klasy, która jest właścicielem pojęcia. Hooki Reacta (`useConversation`) i komponenty są wyjątkiem, bo ich kształt narzuca biblioteka.
- **Moduły według funkcji:** `api/`, `format/`, `grid/`, `chat/`. Testy leżą obok kodu jako `*.test.ts` albo `*.test.tsx`.

## 3. Typy

- TypeScript w trybie `strict` z `noUncheckedIndexedAccess`. Bez `any` i bez `as` do obchodzenia typów; wyjątek wymaga komentarza z powodem.
- Typy kontraktu backendu pochodzą z `src/api/schema.d.ts`, generowanego z OpenAPI (`npm run api:types`). Nie piszemy ich ręcznie. Plik nie podlega ESLint ani Prettierowi.
- Wyniki, które mogą się nie udać, zwracamy jako unię z polem rodzaju (`kind: "response" | "rejected" | "failed"`), a nie przez wyjątki przechodzące przez komponenty.

## 4. Bezpieczeństwo

- Teksty z backendu i wartości z bazy pokazujemy wyłącznie jako tekst: JSX, `textContent`, domyślne renderery komórek siatki. `dangerouslySetInnerHTML` blokuje reguła ESLint.
- Żadnych zasobów z CDN: biblioteki trafiają do zbudowanych plików. Strona działa pod nagłówkiem CSP ze `script-src 'self'`.
- Strona nie przechowuje ani nie odsyła historii rozmowy. Wysyła tylko nowy prompt, `user_id` i `session_id`.

## 5. Testy

- Vitest z `jsdom` i Testing Library. Testujemy zachowanie widoczne dla użytkownika (role, teksty, wywołania API), a nie wewnętrzny stan komponentów.
- Zależności zewnętrzne podstawiamy przez konstruktor albo propsy (atrapa `fetch`, atrapa klienta API), bez mockowania modułów, gdy da się tego uniknąć.

## 6. Bramki przed commitem

W katalogu `frontend/`:

| Bramka | Polecenie |
|--------|-----------|
| Instalacja z lockfile | `npm ci` |
| Lint | `npm run lint` |
| Formatowanie | `npm run format:check` (poprawki: `npm run format`) |
| Typy | `npm run typecheck` |
| Testy | `npm run test` |
| Budowanie | `npm run build` |
| Aktualność typów kontraktu | `npm run api:check` |

Wersje paczek są przypięte dokładnie (bez `^`), a `package-lock.json` jest w repozytorium. Zależności dodajemy świadomie, jedną paczką naraz, i sprawdzamy `npm audit`.
