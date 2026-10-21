# Ändringslogg

## v0.5.1 — härdad paper trading

- Stoppar dubbla dagsavslut och strategiavsikter; skiljer sjudagars vinstfördelning från fem börsdagars expansion.
- Separerar strategier, order, paper-mäklare och exakt kapitalbokföring i nano-SEK.
- Lägger till lokala signerade engångsgodkännanden med belopp, aktör och revisionsspår.
- Exekverar paper-order mot publikt Kraken-djup med spread, slippage, delad likviditet och expiry.
- Inför XSTO-helgdagar/halvdagar, kapital-/journalexport, JSON-loggar och backup-/hälsokommandon.
- Migrerar efter automatisk backup och testar hårda krascher före/efter commit.
- Återstartad process lämnar alltid handelsmotorn pausad.
- Fortsatt enbart paper trading; se checklistan för återstående krav före livehandel.

## v0.5 — Python paper backend

- Flyttar alla handelsbeslut och kontorörelser från browsern till Python.
- Sparar kapital, order, riskstatus, kommandonycklar och journal i SQLite.
- Hämtar Kraken BTC/ETH i EUR och ECB EUR/SEK för dashboardens SEK-redovisning.
- Behåller dashboard, strategier och riskhantering; rättar redovisnings-,
  reservations-, delavsluts- och återstartsproblem beskrivna i utvecklingsplanen.
- Lägger till API, serialiserad runtime, tester och GitHub Actions.
- Behåller gamla localStorage-data orörda; import och destruktiv reset ingår inte.
- Ingen funktion för verklig orderläggning eller privata Kraken-anrop.
