# Ändringslogg

## v0.5 — Python paper backend

- Flyttar alla handelsbeslut och kontorörelser från browsern till Python.
- Sparar kapital, order, riskstatus, kommandonycklar och journal i SQLite.
- Hämtar Kraken BTC/ETH i EUR och ECB EUR/SEK för dashboardens SEK-redovisning.
- Behåller dashboard, strategier och riskhantering; rättar redovisnings-,
  reservations-, delavsluts- och återstartsproblem beskrivna i utvecklingsplanen.
- Lägger till API, serialiserad runtime, tester och GitHub Actions.
- Behåller gamla localStorage-data orörda; import och destruktiv reset ingår inte.
- Ingen funktion för verklig orderläggning eller privata Kraken-anrop.
