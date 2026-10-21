# Verifiering av JARVIS v0.5.1

Utförd 2026-10-21 i molnmiljön med Python 3.12.14, Node 24.19.0 och systemets Chromium.

- **57 enhets-/API-tester passerade**, inga överhoppade tester.
- 500-stegstest samt 2 400 steg över åtta simulerade kalenderdygn passerade med
  exakt kapitalavstämning och unika dagsrapporter. Detta är simulerad tid, inte ett
  bevis på flera dagars drift eller månaders lagringskapacitet.
- Hårda subprocesskrascher efter state-skrivning före commit respektive efter commit
  lämnade antingen den gamla eller den fullständigt nya transaktionen. SQLite:s
  integritetskontroll passerade och inga halva kommando-/journalposter accepterades.
- Nekad SQL-journalskrivning återställde även state-skrivningen. SQL-journalens
  UPDATE/DELETE-skydd verifierades. En backup återställdes med avstämt kapital.
- Migration från schema 1 skapade en separat verifierad backup; okänd nyare version
  avvisades utan att låsa ute nästa försök. Faktisk v0.5-databas provades först som
  kopia och därefter i den ordinarie utvecklingsinstansen: saldo/historik bevarades.
- Signerade engångsgodkännanden testades för belopp, åtgärd, expiry, fel nyckel,
  återanvändning, API-retry och attribuerad kapitaljournal.
- Chromium-smoke passerade för dashboardflikar, insättning, signerat uttag med kod
  från ägar-CLI:t, kapital-/journalnedladdningar, riskpaus, körning med stängd sida,
  mobil återanslutning och nödstopp, utan JavaScript-fel.
- Publika Kraken EUR-tickers och 10 nivåers orderdjup på båda sidor för BTC och ETH
  samt ECB EUR/SEK hämtades med TLS-verifiering. Datum på ECB-kurs: 2026-10-21.
- Funktionellt API-steg i den uppgraderade instansen gick från steg 13 till 14 med
  giltiga Kraken/ECB-data och orderdjup. Motorn lämnades pausad.
- Affärs-CSV, kapital-CSV och JSONL-journal validerades via HTTP. Lokal hälsokontroll passerade.
- JavaScript-syntaxkontroll, git diff --check och systemd-analyze verify för
  deploy/jarvis-paper.service passerade. Systemd-tjänsten installerades inte här.
- XSTO-reglerna jämfördes med exchange_calendars referenskod. Direkt Nasdaq-webbåtkomst
  var blockerad; kalenderbegränsningen och behovet av årlig granskning är dokumenterade.

Inga riktiga order eller uttag skickades. Ingen privat Kraken-nyckel användes.
Verifiering i en ny publicerad molnuppgift eller i användarens Windows/WSL har inte
utförts av agenten. Kör kommandona i README för lokal verifiering efter uppdatering.
