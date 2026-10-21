# Verifiering av JARVIS v0.6

Utförd 2026-10-21 i molnmiljön med Python 3.12.14, Node 24.19.0 och systemets Chromium.

- **84 enhets-/API-tester passerade**, inga överhoppade tester. Befintliga tester för
  kapital, risk, migration, backup, hårda subprocesskrascher, 500 steg och åtta
  simulerade dygn passerar fortsatt. Simulerad tid bevisar inte långvarig drift.
- Nya regressionstester täcker avslutade OHLC-staplar, luckor/gamla data, oberoende
  av pollingintervall, tidsjusterad korrelation, kapitalbaslinjer för nya agenter och
  avvisad syntetisk fallback för riktig kryptodata. Publik analysrefresh handlar inte.
- HMAC-signering jämfördes med Krakens officiella SDK-referensvärde. Testklienten
  verifierar att exakt skickad formbody signeras och att timeout inte återförsöks.
- **Alla privata börssvar är simulerade i testerna.** Tester täcker separat paper-kapital,
  preview/validate utan order, liveflagga, ägarsignatur, exakta decimalsteg, saldon,
  exponering, BTC/ETH, köp/sälj utan blankning, kommandodubbletter och stopprace.
- Timeout efter accepterad simulerad order och krasch före lokal kvittens ger
  UNKNOWN, även efter omstart; ordern skickas aldrig igen. Avstämning hittar
  klient-ID, kontrollerar exakta orderparametrar, delavslut, avgifter, trade-ID och
  annullering. Saknad träff eller avvikande parametrar blockerar nya order.
- Chromium-smoke passerade för nio flikar, kapital, signerat paper-uttag,
  exportnedladdningar, riskpaus, körning med stängd sida, återanslutning och mobil.
  Hela liveflödet preview → validate → CLI-signatur → submit → signerad cancel →
  reconcile testades med injicerad fake-börs. Inga privata nätverksanrop gjordes.
- En separat pausad HTTP-instans hämtade riktiga publika Kraken/ECB-data med
  `refresh-market`: båda paren fick 120 avslutade femminutersstaplar, status ready,
  slutdatum 2026-10-21 19:45 UTC. Steg förblev 0 och liveflaggan false.
- Desktop-/mobilvyer granskades visuellt i Chromium. Ingen horisontell sidöverströmning
  vid 390 pixlar och inga JavaScript-fel vid flikbyte. Ljusstyrka/kontrast har bedömts
  visuellt; ingen formell tillgänglighetscertifiering har utförts.
- `node --check dashboard.js` och `git diff --check` passerade.

Inga riktiga order eller uttag skickades. Ingen privat Kraken-nyckel användes.
Faktiskt saldo, API-behörigheter, credentialed validate=true och finansierad
orderexekvering återstår att verifiera lokalt enligt [Kraken-guiden](KRAKEN-LIVE.md).
Ingen autonom livehandel är implementerad. Windows/WSL eller en ny publicerad
molnuppgift har inte testats från denna maskin.

Tidigare kalender-/driftbegränsningar i [v0.5.1-checklistan](CHECKLIST-v0.5.1.md)
gäller fortsatt. Skyddade paper-saldon och exporterna där avser simulerade pengar.
