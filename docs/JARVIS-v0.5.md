# Analys och utvecklingsplan: JARVIS v0.5

## Granskad utgångspunkt

Hela projektet vid d46533d består av README.md (två rader) och index (2).html.
Ingen backend, pakethantering, tester eller CI fanns. HTML-filen innehåller både
CSS, dashboard och samtliga handels- och redovisningsfunktioner. Versionsetiketter
blandas mellan v0.2.3 och v0.3.0; localStorage använder ännu v018-nyckel.

| Område | Befintligt beteende |
| --- | --- |
| Dashboard | Fem flikar, kapital, diagram, agentdetaljer, risk, historik, CSV |
| Agenter | Tio regelbaserade Trend/ETF/Momentum/Mean reversion-agenter; expansion till 30 |
| Delat minne | Statistik per marknadsgrupp, strategi och regim; adaptiv orderstorlek |
| CHEF | 5 % stop-loss, 3 förlustavslut/8 % budgetförlust, karantän, global drawdownpaus |
| Återhämtning | Isolerad skugghandel, 40 steg före första granskning, manuell global återstart |
| Ordermotor | NEW/PARTIAL/FILLED/REJECTED/CANCELLED, avgifter, deterministiska testavvisningar |
| Kapital | Gemensamt kontantkonto, agentägda positioner, ägarrörelser, vinstkonto, dagsrapporter |
| Schema | Krypto 24/7, syntetiska svenska aktier/ETF vardagar 09–17:30, Stockholm-dygn |
| Drift | setInterval i öppen webbläsare; localStorage, ingen server eller extern marknadsdata |

## Bekräftade fel och åtgärder i denna branch

1. **Kostnadsbas:** försäljning drar en proportionell kostnad ur hela instrumentet
   men använder säljarens egen kostnad för P/L. Olika inköpspriser hos flera agenter
   ger avvikelse. Python drar samma ägarkostnad från instrument och agentsaldo.
2. **Karantän:** återaktivering nollställer agentRealized och förstör livstidsresultat.
   Separat riskBaseline återställs nu; historiskt resultat bevaras.
3. **Delavslut:** riskutrymme beräknas vid orderläggning men kontrolleras inte fullt
   vid senare avslut. Nu omprövas samtliga köpgränser vid varje exekvering.
4. **Reservationer:** originalet drar alla köporder från instrumentutrymmet men
   saknar motsvarande reservation per agent och korrelerad grupp. Nu används separata
   reservationer och avgifternas effekt på portföljvärdet tas med.
5. **Mäklaravvisning:** orderSeq används i stället för den exekverade orderns ID.
   Nu styr orderns eget löpnummer deterministiska avvisningar och delavslut.
6. **Avstämning:** ägardifferenser kan ta ut varandra mellan instrument; kontrollerna
   verifierar inte kostnadsbas/kapitalidentitet. Nu kontrolleras varje instrument,
   ägarkostnader och cash + vault = ägarnetto + realiserat - kvarvarande kostnadsbas.
7. **Nödstopp:** dagens stopp upphör nästa dag. Serverns stoppstatus är beständig;
   väntande order avbryts och inga tidsbaserade funktioner startar om den.
8. **Återstart:** originalet tillåter återstart under 12 % men pausar omedelbart igen
   vid 10 %. Samma 10 %-gräns används nu vid granskning, godkännande och riskkontroll.

## Genomförd plan i v0.5

Prioritet 1: flytta strategi-, CHEF-, skugg-, order- och kapitallogik till Python.
Behåll instrument-/agent-ID:n och dashboardens lässchema så att layouten kan återanvändas.

Prioritet 2: ensam skrivande process, serialiserade transaktioner, SQLite WAL,
append-only händelsejournal, idempotenta API-kommandon och återställning utan localStorage.
Spara bara den senaste fullständiga tillståndsbilden; journalen innehåller nya
händelser och kapitalkontrollsummor, inte en full historikkopia vid varje tick.

Prioritet 3: Kraken EUR-priser för BTC/ETH med ECB:s daterade EUR/SEK-referens.
Fel stoppar handelssteg i stället för att fabricera kurser. Inga hemligheter eller
privata handelsanrop. Syntetiska aktier/ETF förblir tydligt märkta.

Prioritet 4: koppla dashboardens kontroller till API:t, behåll fem flikar, agentkort,
diagram, journal, dagsrapporter och CSV. Servern kör även utan anslutna klienter.
Loopbackbindning, Host/Origin-kontroll och särskild klientheader begränsar lokala
webbangrepp; detta ersätter inte autentisering för en publik server.

Prioritet 5: tester för ekonomiska invarianta samband, strategier, risk, återhämtning,
persistens, kommandorepriser och samtidiga anrop; verkligt Chromium-smoke och CI.

## Nästa separata leveranser

| Prioritet | Leverans | Acceptanskriterium |
| --- | --- | --- |
| P1 | Decimal/fasta monetära enheter och schema-migreringar | Avstämning utan flyttalstolerans; versionerade DB-uppgraderingar med backup/restore-test |
| P1 | Normaliserade order-/fill-tabeller och paginerade läs-API:n | Flera månaders 24/7-körning med begränsad minnes-, DB- och svarsstorlek |
| P1 | Driftpaketering och övervakning | Processövervakare/container, backups, lagringslarm och fördröjningslarm; återstart efter maskinfel verifierad |
| P1 | Godkänd import av gammal localStorage | Validerad export, separat backup, inga importerade syntetiska positioner omvärderade till verkliga BTC-kurser |
| P2 | Historiska Kraken-data och realistisk paper-broker | Spread, slippage, datakvalitets-/illikviditetsgränser och reproducerbar backtest utan framtidsdata |
| P2 | Svenska börsdagar och valbar riktig aktiedatakälla | Helgdagar, halvdagar och tydligt separat syntetiskt läge |
| P2 | Serveraviseringar och fjärrdashboard | Autentisering, behörigheter, TLS och godkända serverkanaler innan internetexponering |

Livehandel ingår inte i planen. Språkmodeller ska inte kunna kringgå riskmotorn.

## Kända begränsningar

- Ingen automatisk migration av gamla browsersaldon. Ingen destruktiv reset via API.
- Flyttal med 1e-5 SEK tolerans i kapitalavstämningen; inte bokföringssystem för riktiga pengar.
- Tillståndet innehåller ännu hela order-/rapporthistoriken och journalen saknar retention.
  Lämpligt för prototyputveckling; normalisering/paginering behövs för lång drift.
- Pris-historik hålls till 256 observationer och portföljdiagram till 2 000 punkter.
- Dagar utan körande server simuleras inte i efterhand; första steget efter ett datumskifte
  avslutar senast observerade dag. API-hämtningstid är inte börsens trade-timestamp.
- Stop-loss sker bara på observerade kurser och kan liksom andra paper-order avvisas
  av den deterministiska testmäklaren. Ingen garanti om exekvering till stoppnivån.
- Statistik räknar förlustavslut, inklusive delavslut, enligt originalets beteende.
- ECB är daglig referens-FX; verklig växling, spread och slippage simuleras inte.
- Regeltrösklarna är portade från syntetiska steg. Kraken-priser ändras mycket mindre
  per fem sekunder, så signaler kan vara sällsynta. Ingen lönsamhet påstås.
