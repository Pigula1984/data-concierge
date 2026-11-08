# Uruchamianie i zatrzymywanie

Scenariusz krok po kroku dla interfejsu w przeglądarce. Polecenia są dla PowerShella. Pełny opis wymagań, ustawień i poleceń jest w [`README.md`](../README.md).

## Raz, na nowej maszynie

1. Zainstaluj zależności Pythona w katalogu repozytorium:

   ```powershell
   cd C:\Users\Admin\git\data-concierge
   uv sync
   ```

2. Utwórz plik ustawień:

   ```powershell
   Copy-Item .env.example .env
   ```

   W `.env` wpisz co najmniej `DC_SQL_PASSWORD`, `ANTHROPIC_API_KEY` i `DC_SQL_TRUST_SERVER_CERTIFICATE=true`.

3. Wdróż procedury `api.*` i konto `agent_reader` w bazie:

   ```powershell
   ./db/deploy.ps1
   ```

4. Zbuduj interfejs:

   ```powershell
   cd frontend
   npm ci
   npm run build
   cd ..
   ```

## Uruchamianie

1. **SQL Server.** Jeśli usługa nie startuje razem z Windows, uruchom ją w PowerShellu otwartym jako administrator:

   ```powershell
   net start MSSQLSERVER
   ```

2. **Interfejs.** Zbuduj go ponownie, tylko jeśli coś zmieniło się w `frontend/` (na przykład po `git pull`):

   ```powershell
   cd C:\Users\Admin\git\data-concierge\frontend
   npm ci
   npm run build
   ```

3. **Serwer.** Uruchom go z katalogu głównego repozytorium, bo stamtąd szuka `frontend/dist`:

   ```powershell
   cd C:\Users\Admin\git\data-concierge
   uv run uvicorn data_concierge.bootstrap:create_app --factory
   ```

   Okno zostaje zajęte przez serwer. Jeśli brakuje ustawienia w `.env` albo katalog procedur nie zgadza się z bazą, serwer kończy się od razu z błędem konfiguracji.

4. **Przeglądarka.** Otwórz `http://127.0.0.1:8000/`. W nagłówku strony jest pole `user_id` (domyślnie `1`). Przykładowe prompty:

   - `raport sprzedaży po miesiącach za 2024`
   - `Pokaż sprzedaż klienta Mohamed Pal w 2024`, potem kliknij przycisk z klientem
   - `daj mi listę zamówień z 2024` (duża tabela z sortowaniem i filtrami)

5. **Audyt (opcjonalnie).** W drugim oknie PowerShella, w katalogu repozytorium:

   ```powershell
   uv run data-concierge audit <session_id>
   ```

   `session_id` jest w sekcji „Jak policzono” pod odpowiedzią.

## Zatrzymywanie

1. **Serwer.** W oknie uvicorna naciśnij `Ctrl+C`. To zatrzymuje także interfejs, bo ten sam serwer go serwuje. Kartę przeglądarki możesz zamknąć albo zostawić.
2. **SQL Server (opcjonalnie).** Usługa może działać dalej. Jeśli chcesz ją wyłączyć, w PowerShellu jako administrator:

   ```powershell
   net stop MSSQLSERVER
   ```

## Praca nad kodem interfejsu

Przy zmianach w `frontend/` zamiast budować po każdej zmianie, uruchom serwer deweloperski Vite. Strona przeładowuje się sama.

**Uruchamianie (dwa okna PowerShella):**

1. Okno 1, serwer z katalogu głównego repozytorium:

   ```powershell
   cd C:\Users\Admin\git\data-concierge
   uv run uvicorn data_concierge.bootstrap:create_app --factory
   ```

2. Okno 2, serwer deweloperski:

   ```powershell
   cd C:\Users\Admin\git\data-concierge\frontend
   npm run dev
   ```

3. Otwórz `http://localhost:5173`. Vite przekazuje `/ask` i `/health` do serwera na porcie 8000.

**Zatrzymywanie:** `Ctrl+C` w każdym z dwóch okien. Na pytanie `Terminate batch job (Y/N)?` odpowiedz `Y`.

## Gdy coś nie działa

| Objaw | Co zrobić |
|-------|-----------|
| Pod `/` jest tekst „Interfejs w przeglądarce nie jest zbudowany.” zamiast rozmowy | Zbuduj interfejs (krok 2), potem zatrzymaj serwer i uruchom go ponownie z katalogu głównego repozytorium |
| Serwer kończy się od razu błędem połączenia z bazą | Uruchom SQL Server (krok 1) i sprawdź `DC_SQL_PASSWORD` w `.env` |
| Po wysłaniu promptu strona pokazuje „Nie udało się połączyć z asystentem.” | Serwer nie działa albo został zatrzymany; uruchom go (krok 3) i kliknij „Nowa rozmowa” |
| Zmiana w `.env` nie działa | Zatrzymaj serwer (`Ctrl+C`) i uruchom go ponownie |
