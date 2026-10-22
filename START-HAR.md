# Fortsätt med Estman Investments

Hela projektet ligger i `/workspace/Estman-Investments` i denna Codex-miljö.
Mappen innehåller Python-backend, dashboard, tester, dokumentation och Git-historik.
GitHub-repot heter fortfarande PabloTheBeast/Jarvis-Trading.

## Starta webbplatsen i Codex

```bash
cd /workspace/Estman-Investments
JARVIS_PAPER_MODE=0 JARVIS_MONITOR_ONLY=1 python3 -m backend.server
```

Öppna http://localhost:8000 i samma miljö. En server i Codex är inte automatiskt
åtkomlig via localhost på din Windows-dator. Där behöver du starta projektet i Ubuntu/WSL.

## Hämta designen i din befintliga Windows/Ubuntu-kopia

Stoppa eventuell gammal server med Ctrl+C och kör i din projektmapp:

```bash
git fetch origin
git switch feature/estman-investments-design
JARVIS_PAPER_MODE=0 JARVIS_MONITOR_ONLY=1 python3 -m backend.server
```

Öppna http://localhost:8000. Kommandot startar bevakning utan verklig orderläggning.
Typsnitten ligger lokalt i assets/fonts, så webbläsaren behöver inte Google Fonts.
Chakra Petch används för gränssnitt och rubriker, IBM Plex Mono för siffror och kod.
Båda stödjer svenska tecken. Licenser finns bredvid fontfilerna.

## Fortsatt utveckling

- `backend/`: Python, marknadsdata, orderflöden och agentkontroll.
- `index (2).html`, `dashboard.css`, `dashboard.js`: webbplatsen.
- `tests/`: automatiska tester och Chromium-kontroller.
- `docs/AGENT-CONTROL.md`: funktioner och kvarstående begränsningar.

Behåll API-nycklar lokalt. Ignorerade databaser och konfigurationsfiler ska inte
läggas till i Git eller skickas vidare. Namnbytet ändrar webbplatsens varumärke;
kommandon, API-headernamn och JARVIS_* variabler är kompatibla med tidigare versioner.
