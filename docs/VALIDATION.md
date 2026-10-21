# Verifiering av JARVIS v0.8

Utförd 2026-10-21 UTC (22 oktober i Stockholm), Python 3.12 och Chromium.

- **157 enhets-/API-tester passerar.** De 130 tidigare testerna är kvar; regressionen
  för två köp på samma par/stapel har uppdaterats till den nya gemensamma spärren.
- Nya tester verifierar realiserad vinstreserv, förluståterhämtning, otillräckliga
  kontanter, okänd kostnadsbas, manuell köpväg genom reservspärren, karantän före
  order, annullering av öppet agentköp, exakt ägarsignerad återkvalificering,
  WebSocket-färskhet och köpblockering, manipulerad exitplan, efterbevakningens
  säljbehörighet, stopp och omstart, isolerat historiktest samt HTTP-skydd/export.
- Separata skuggtester täcker nästa observations avslut, delavslut, utgångna
  limitorder, kvarvarande innehav, avgifter/prisglidning och minbelopp.
- Fem Chromium-skript passerar: browser_smoke.py, browser_advisory.py,
  browser_trade_history.py, browser_sessions.py och browser_governance.py.
  De kontrollerar bland annat plan/start/stopp, lokal ägarkod, riktig historikvy,
  separat agentkontroll, säker visning av opålitlig text och mobilbredd.
- JavaScript-syntax, Python-kompilering och git diff --check passerar.
- **Alla privata saldon, order och avslut är simulerade i testerna.** Ingen
  finansierad order har skickats. Tester bevisar varken lönsamhet, faktisk
  exekveringskvalitet eller långvarig produktionsdrift.
- Utvecklingsservern startades lokalt med LIVE=0, AUTO_TRADING=0 och MONITOR_ONLY=1.
  Verklig publik REST-data från Kraken fungerade. WebSocket-anslutningen blockerades
  av molnmiljöns proxy (HTTP 403); ingen fungerande extern WebSocket-anslutning eller
  privat ström med verkliga API-behörigheter är därmed verifierad här.
- Research stöder isolerade strategiidéer och deterministiskt test av befintliga
  regler på importerad historik. Automatisk webb-/YouTube-inhämtning, autonoma nya
  strategier, svenska aktier och börslagrade skyddsorder är inte implementerade.

Se [funktioner och begränsningar](AGENT-CONTROL.md).

---

## Tidigare verifiering av v0.7

# Verifiering av JARVIS v0.7

Utförd 2026-10-21 i molnmiljön med Python 3.12.14, Node 24.19.0 och systemets Chromium.

- **130 enhets-/API-tester passerade**, inga överhoppade tester. Befintliga tester för
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
Automatisk sessionshandel är implementerad men bara verifierad med simulerad börs. Windows/WSL eller en ny publicerad
molnuppgift har inte testats från denna maskin.

Tidigare kalender-/driftbegränsningar i [v0.5.1-checklistan](CHECKLIST-v0.5.1.md)
gäller fortsatt. Skyddade paper-saldon och exporterna där avser simulerade pengar.

Tillägg: säker visning av kända Kraken-felkoder testas utan exponering av råa
okända felmeddelanden. Bevakningsläge testas i bakgrund utan webbläsare med
nollsaldo och simulerad börs: inga avslut/handelssteg, bara BalanceEx/OpenOrders,
blockerade ordervägar och avstängd liveflagga även vid motstridiga miljövariabler.
Kursfel stoppar inte kontoläsning. Ingen finansierad order skickades.

Ett separat Chromium-test av bevakningsläget passerade med simulerat nollsaldo:
läget visas i Kraken-vyn, preview/submit är avstängda, inga JavaScript-fel och
bara läsanrop till fake-börsen. Nordnet-åtkomst har inte verifierats eller anslutits.

Agentbevakning: separata rapporter och konsensus testas utan order eller förbrukat
handelsintent. Tester täcker klonade röster, oenighet, karantän, riskpaus, gamla
staplar, köp-/villkorad säljsignal, samma signal på nästa stapel, deduplicering vid
omstart och integration med publik observe. Kontoanslutning eller pengar krävs
inte för dessa bedömningar.

Chromium-testet `tests/browser_advisory.py` passerade med helt simulerade börsdata:
fem agentrapporter, konsensus, opt-in skrivbordsnotis via teststub, ingen dubblering
vid polling, kvarvarande historik utan notisreplay efter omladdning, utgången
bedömning, mobil layout och inga order eller JavaScript-fel. Desktop-layouten
granskades visuellt. Inga riktiga skrivbordsnotiser eller privata börsanrop skickades.

Verklig affärshistorik: 9 nya tester täcker FIFO/delvolymer, avgifter, okända
inköp, isolering mellan agenter, börsens tidsstämplar, återstart/deduplicering,
append-only-skydd, ofyllda order utan köptid, felaktiga/saknade avslut, bevarad
historik vid ofullständigt svar och automatisk avstämning vid kontokontroll.
Standarddrift blockerar simulatorns muterande kommandon och bevarar dess arkiv.
Alla privata börssvar är simulerade; inga verkliga order har skickats.

Chromium: browser_trade_history.py passerade med explicit simulerade avslut:
normal vy visar inga paper-saldon, testkontroller eller gamla affärer; köp/sälj
visas med årtal, datum och sekund i Stockholm; matchat netto inkluderar avgifter;
manuella affärer är inte agentaffärer; omladdning och mobilbredd 390 px passerar
utan sidöverströmning eller JavaScript-fel. Befintligt legacy-browser-test körs
uttryckligen med paper_enabled=True. Även agent-/notis-browser-testet passerar.
Dashboarden öppnades via lokal HTTP i utvecklingsmiljön, före och efter ändringen.
Befintlig utvecklingsdatabas säkerhetskopierades före omstart; arkivdata raderades inte.

Ytterligare test verifierar 25 delavslut hämtade i två QueryTrades-batcher (20+5),
med 25 separata börstidsstämplar. Den uppgraderade ordinarie localhost-instansen
kontrollerades också med verkliga publika data och utan privata nycklar: testläge
avstängt, live avstängt, tidigare tick 14 bevarat, inga faktiska avslut påhittade,
normal historik tom och inga JavaScript-fel. API-nycklar/finansierad exekvering är
fortfarande inte verifierade i utvecklingsmiljön.

Sessionshandel v0.7: 24 nya sessionstester täcker signerad budgetplan/start, nollsaldo,
flagga/bevakningsspärr, granskad 100-EUR-fördelning, agentmärkta verkliga fill-fixtures,
bar-deduplicering, aktiva manuella order spärrade, race vid avslut/start, stopp utan
väntan på arbetstrådens nätverkslås, annullering före slutstatus, behåll-policy,
CHEF:s exit-försäljning och verifierad roundtrip, extern insättning/saldodrift,
verklig drawdown, positions-stop-loss, osäkert orderutfall utan omsändning,
processomstart, serverstopp och automatisk sluttid. Ett HTTP-test verifierar samma
origin, start utan signatur nekad, signerad start, idempotens och stopp utan ny kod.
Äldre simulerade riskfält påverkar inte verklig allokering.

Nytt Chromium-smoke browser_sessions.py passerade: granskning → lokal CLI-kod →
start → automatiskt simulerat agentköp → avslut med HOLD → agentmärkt historik.
Mobil 390 px och inga JavaScript-fel. Sessionens desktop-panel granskades visuellt.
Alla marknads-/kontosvar och order i detta test är fake-börs. Ingen faktisk
finansierad exekvering eller privat credentialed Kraken-validering har genomförts.

Tillägg: signaturen är bunden till exakta agentbudgetar; upprepat stopp förlänger
inte säljtillståndet. Utgången stapel före sändning skapar ingen order och inväntar
ny stapel utan falsk riskpaus. Sessionsbiljetter kan inte skickas genom den manuella
submit-vägen. Marknadsdatacachen hämtar om OHLC vid ny femminutersgräns så att en
cachad ofullständig stapel inte befordras till slutlig handelssignal utan ny hämtning.
Alla 130 tester passerar; ingen privat finansierad Kraken-validering har gjorts.

Den ordinarie utvecklingsinstansen öppnades i Chromium med nya sessionspanelen:
GET /api/trading-session svarar med enabled=false och session=null; start/avslut
är avstängda utan plan. Instansen lämnades med JARVIS_MONITOR_ONLY=1,
JARVIS_KRAKEN_LIVE=0 och JARVIS_AUTO_TRADING=0. Databasen säkerhetskopierades före
omstart. Ingen sessionskod eller handelsaktivering användes mot ett verkligt konto.
Fyra Chromium-script (legacy, advisory, history, sessions) passerar.
