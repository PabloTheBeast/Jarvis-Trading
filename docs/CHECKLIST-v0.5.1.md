# Checklista för v0.5.1 — genomförda förbättringar och gräns mot livehandel

## Genomförande

| Checklistepunkt | Leverans | Verifiering |
| --- | --- | --- |
| Handelsmotor 1 | strategies.py ger signaler; orders.py hanterar livscykel/risk; accounting.py bokför; broker.py simulerar exekvering; Engine orkestrerar | Strategi-, order-, kontotester och funktionell körning |
| Handelsmotor 2 | Beständigt intent-ID/order-ID, unikt fill-ID och explicita statusar | Upprepade intents/fills och persistens |
| Handelsmotor 3 | Deduplicering även efter omstart, reservationer per agent/instrument/korrelation och kontroll vid varje fill | Dubblett-, kontant-, reservations- och gränstester |
| Handelsmotor 4 | Depth-baserade paper-delavslut, bid/ask, slippage, avgift och 60 s giltighet | Delad likviditet, expiry, adverse slippage och testavvisningar |
| Kapital 1 | Insättningar/uttag separat från handelsresultat | Depositioner och exakt avstämning |
| Kapital 2 | Integer nano-SEK med Decimal-konvertering; gemensam ägarkostnadsbas; förluståterhämtning före nytt vinstskydd | 1 000 små insättningar, förlustserie, kostnadsbas och exakta balansidentiteter |
| Kapital 3 | Sju kalenderdagars period; 20 % nytt realiserat netto, bara fria ej reserverade kontanter | Sjudagarsregel, upprepade dagsavslut, reservationer, inga dubbla vinstavsättningar |
| Kapital 4 | Order saknar väg att spendera vault; release kräver signerat engångsgodkännande | Fel belopp, åtgärd, signatur, expiry och återanvändning; attribution i export |
| CHEF 1 | Fem unika avslutade XSTO-börsdatum per granskning; historiska dubbletter räknas inte efter migration | Samma-datum-fel reproducerat och blockerat; helger och upprepade granskningar |
| CHEF 2 | Riskpaus bevaras i SQLite; under 10 % drawdown och signerat manuellt godkännande krävs | Databasomstart och nekade osignerade godkännanden |
| CHEF 3 | Befintliga exponeringsgränser behålls och alla fills omprövas | Total-, instrument-, korrelations-, budget- och delavslutstester |
| CHEF 4 | Nya agenter börjar i karantän och kräver godkänt skuggtest; skuggpriser använder bid/ask/slippage | Karantän, granskning och ingen huvudkontopåverkan; samma snapshot kan inte accelerera steg |
| Drift 1 | Utökad offline-svit och CI; åtta kalenderdygn simulerade | Testkommandon och resultat i VALIDATION.md |
| Drift 2 | Atomisk state/journal/kommandolagring; automatisk backup före migration; verifierbar backup/restore | Subprocess avslutas hårt före respektive efter commit; journalskrivning nekas; backup återöppnas |
| Drift 3 | Affärs-CSV, kapital-CSV och full JSONL-journal med tidsstämplar, ägare, godkännande-ID och exakta belopp | API-exporttester och verkliga browsernedladdningar |
| Drift 4 | Stockholm-kalender med helgdagar/halvdagar och DST; roterande JSON-loggar; hälsokontroll och systemd-exempel | Kalender-, logg-/API-kontroller och lokal hälsokontroll |

## Datumregler och migration

En manuell dagsstängning är slutlig för det Stockholm-datumet. Vid datumskifte
avslutas senast observerade dag; dagar då processen inte körde fabriceras inte i efterhand.
Veckofördelningen körs vid nästa dagsavslut på/efter periodens deadline. Efter lång frånvaro
skapas högst en avsättning av faktiskt nytt realiserat resultat, inga uppdiktade handelsveckor.
Extra manuell avsättning flyttar inte ordinarie perioddatum.

Den gamla v0.5-versionens dubbla rapporter och tidigare agentbeslut ändras inte retroaktivt.
Migrering startar en ny expansionsräknare och en ny sjudagarsperiod, behåller saldon och
journalför avrundningen till fast precision. En SQLite-backup skapas före migreringen.
Nyare okänd schemaversion avvisas. Varje processomstart lämnar motorn pausad.

## Kalenderkällor

- Nasdaq Nordic trading hours / holiday schedule:
  https://www.nasdaq.com/solutions/european-markets-trading-hours
- XSTO-reglerna är korsgranskade mot exchange_calendars:
  https://github.com/gerrymanoim/exchange_calendars/blob/master/exchange_calendars/exchange_calendar_xsto.py

Implementationen är självständig och använder inga externa kalenderpaket. Helgdagar och
halvdagar är regelbaserade, testade för 2025–2027 och kräver årlig granskning för framtida år.
Extraordinära börsstängningar hämtas inte automatiskt. Direkt Nasdaq-webbåtkomst var
blockerad i molnmiljön; XSTO-referenskoden kunde hämtas och granskas. Detta är ingen
verifiering av en livekalender eller särskilda framtida börsbeslut.

## Valfri processövervakning i Ubuntu/WSL

Om WSL har systemd aktiverat och projektet ligger i ~/Jarvis-Trading:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/jarvis-paper.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now jarvis-paper
systemctl --user status jarvis-paper
```

Stoppa först en terminalstartad JARVIS-process som använder samma databas/port.
Tjänsten återstartar en kraschad process, men handelsmotorn förblir pausad tills ägaren
startar den igen. Windows-vila eller avstängd WSL stoppar fortfarande driften.
Systemd-exemplet är statiskt verifierat, inte installerat eller startat i molnmiljön.

## Krävs separat före verklig handel

1. Privat read-only kontoavstämning mot verkliga Kraken-order, fills, saldon och avgifter.
2. Först därefter en separat, explicit godkänd orderadapter med begränsade API-rättigheter,
   börsens precision/lotstorlek, minsta order, rate limits och säkert hanterad okänd orderstatus.
3. Verkliga uttag ska ligga utanför agenterna och ha egen autentisering/godkännandeprocess.
4. Skydd mot extraordinära börsstängningar och kalenderuppdateringar från aktuella officiella källor.
5. Normaliserad/paginerad historik, lagringsbudget, backup-policy och verifierad drift under långvarig last.
6. Separat verifiering av fel i maskin/disk/nätverk, driftmiljö och strategiernas ekonomiska antaganden.

Ingen liveadapter, privat Kraken-nyckel, riktig banköverföring eller internetexponerad
dashboard införs här. Lokal operatörssignering autentiserar innehavaren av en 0600-nyckel;
det ersätter inte fleranvändarautentisering och skyddar inte mot en komprometterad OS-användare.
Kvantiteter/priser och riskanalys använder fortfarande flyttal; verklig handelsadapter
behöver börsens decimalprecision även där. Hela historiken ligger ännu i state-bilden,
så åttadygnssimulering är inte ett belastningsbevis för månaders 24/7-drift.
