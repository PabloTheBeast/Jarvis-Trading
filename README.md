# JARVIS Trading v0.7 — godkända agentsessioner

Python kör kursbevakning och agentbedömningar utan en öppen webbläsare.
**Vanlig start visar verkligt Kraken-konto och bekräftade affärer; simulerad handel
är avstängd och gamla testaffärer visas inte.** Agenterna analyserar och rapporterar till CHEF. Automatisk orderläggning kräver
en separat förberedd, ägarsignerad och tidsbegränsad session; insättningar eller
serverstart aktiverar aldrig sådan handel. Manuella ägarsignerade Kraken-limitorder
är avstängda som standard. Se [lokal Kraken-konfiguration](docs/KRAKEN-LIVE.md).

Dashboarden visar Översikt, Marknad, Beslut, Agenter, Historik, System och
Kraken-konto. Systemtypsnitt och tabellsiffror gör datum och belopp tydliga.

## Starta på Windows med Ubuntu/WSL

I Ubuntu-terminalen, från projektmappen:

```bash
python3 -m unittest discover -s tests -v
python3 -m backend.server
```

Öppna serverns rot på port 8000 i Windows-webbläsaren och välj **Beslut**,
**Kraken-konto** eller **Historik**. Kurser och konto bevakas automatiskt. Låt Python-processen köra. Servern är avsiktligt bara åtkomlig
på loopback. Molnserverns loopback nås inte från en annan dator utan en tunnel.

Krav: Python 3.12+ på Linux/WSL, SQLite, tidszonsdata. Inga pip-/npm-paket eller
Kraken-nycklar krävs. Node behövs bara för JS-syntaxkontrollen.

Vid uppgradering: stoppa den gamla servern med Ctrl+C innan du byter kodbranch.
Behåll `.jarvis/`. Befintlig v0.5-databas säkerhetskopieras automatiskt före
schema 2-migreringen. Historik och agenter behålls; inga pengar nollställs.
Historiska felaktigt dubblerade dagsrapporter bevaras men räknas inte mot ny expansion.
Migreringen avrundar gamla flyttal till nano-SEK och journalför eventuell minimal
avstämningsjustering. För stor differens stoppar uppgraderingen.

Efter **varje processomstart** är den arkiverade testmotorn pausad. Den vanliga
kurs- och kontobevakningen startar automatiskt; inga riktiga order skickas automatiskt. En global riskpaus ligger kvar tills skuggtest och ägargodkännande
är klara. Övervakningstjänsten får återstarta processen men inte återuppta handeln.

## Bevakning utan insättning eller handel

Stoppa servern och starta den i Ubuntu/WSL med:

```bash
JARVIS_MONITOR_ONLY=1 python3 -m backend.server
```

Detta är ett separat bevakningsläge: publika BTC/EUR- och ETH/EUR-kurser och analys
uppdateras automatiskt cirka var 15:e sekund och privat kontosaldo/öppna order
cirka var 60:e sekund. Nätverkstid tillkommer. Servern måste vara igång, men
webbläsaren kan stängas. Ingen insättning behövs; nollsaldo är giltigt. Kontoläsning
kräver den lokala Kraken-nyckeln och behörighet att läsa medel och öppna order.

Start av paper-motorn, manuella handelssteg samt nya Kraken-order inklusive
preview/validate blockeras i detta läge. Liveflaggan åsidosätts till avstängd även
om den råkar finnas i miljön. Befintliga riktiga order annulleras inte automatiskt;
ägarsignerad annullering och avstämning är fortfarande möjliga. Pausa paper stoppar
inte kursbevakningen; avsluta servern med Ctrl+C för att stoppa bevakningen.

Detta är periodiska snapshots, inte en börsens tick-för-tick-ström. Vid nätverksfel
visar befintliga tidsstämplar att kurserna blivit gamla; ett fel i kursbevakningen
stoppar inte kommande försök till kontoläsning. Uppdateringar går sekventiellt och
långa nätverksanrop kan fördröja nästa läsning. Inga Kraken-nycklar behövs för publika
kurser; kontovyn visar konfigurationsfel tills en nyckel finns.

**Svenska aktier och ETF:er är fortfarande syntetiska.** En svensk datakälla och
dess åtkomst/licens måste väljas innan riktig aktiebevakning kan anslutas. De
syntetiska instrumenten visas uttryckligen som sådana och är inte börsdata.
Se [Nordnet och återstående anslutning](docs/NORDNET.md).
För godkända verkliga budgetar och start-/avslutsflöde, se
[Sessionshandel på Kraken](docs/SESSION-TRADING.md). En insättning aktiverar aldrig agenthandel.

## Starta och avsluta godkänd trading

Översikt har en separat sessionspanel. Förbered en budgetplan, kontrollera kapital,
förlustgräns, sluttid och avslutspolicy, och godkänn Starta trading med en lokal
`session-start`-kod. Avsluta trading stoppar nya köp och genomför vald behåll-/säljbedömning.
Standardstart och `JARVIS_MONITOR_ONLY=1` ger ingen automatisk orderrätt. Sessionshandel
kräver både `JARVIS_KRAKEN_LIVE=1` och `JARVIS_AUTO_TRADING=1` utan bevaknings-/testflagga.
Läs [hela sessionsguiden](docs/SESSION-TRADING.md) före aktivering. Svenska aktier är
ännu inte anslutna. Finansierad exekvering har inte testats i utvecklingsmiljön.

## Agentrapporter och meddelanden från CHEF

I **Beslut** visas aktuell kryptobevakning även när paper-motorn är pausad och
Kraken-kontot saknar pengar. GAMMA granskar BTC/ETH med momentum, ZETA granskar
BTC/ETH med mean reversion och IOTA granskar BTC med trendstrategin. Övriga
agenter följer fortsatt syntetiska aktier/ETF. Senaste läsning, avslutad stapel,
regelunderlag och köp-/säljvillkor visas per agent. Det är befintliga regelbaserade
strategier; inga externa språkmodeller anropas.

CHEF visar möjligt köp-/säljläge först när minst två olika strategier är överens
och ingen behörig granskare ger motsatt signal. Klonade agenter ger inte fler
röster för samma strategi. Karantänsagenter visas men räknas inte som stöd;
riskpaus blockerar köp. Oenighet, otillräckligt underlag eller gamla data ger
**Avvakta**. Fler agenter betyder därför inte automatiskt fler köpsignaler.

Meddelanden sparas i **Beslut → Meddelanden från CHEF** och **Historik**. Nya
signaländringar ger en toast på den öppna sidan. Samma signal på samma stapel
skickas inte om efter polling, riskpausväxling eller processomstart. Oförändrat
läge på nästa stapel ger inget nytt meddelande. Signaler löper ut vid nästa
femminutersgräns och presenteras inte som aktuella när kurserna blivit gamla.

Knappen **Aktivera skrivbordsnotiser** begär webbläsarens tillåtelse, efter ditt
klick. Den gäller den öppna sidans session; sidan måste vara öppen, även om den
ligger i bakgrunden. Ingen push-tjänst, e-post eller SMS är ansluten. Om sidan är
stängd fortsätter Python samla rapporter/meddelanden i bevakningsläget, men inga
skrivbordsnotiser levereras då. Historiken visas när du öppnar sidan igen och
spelas inte upp som nya notiser vid omladdning.

Bedömningarna utför inga order och förbrukar inte paper-motorns handelsbeslut.
De kontrollerar inte verkligt saldo, innehav eller orderrisk. Ett säljläge betyder
att granska en eventuell befintlig position, inte att blanka. Referenspris är
stapelns slutkurs i EUR, inte ett föreslaget limitpris. Det finns ingen garanti
att signalen leder till en lönsam affär.

## Verkliga affärer med köp- och säljdatum

**Historik** visar bara verifierade avslut för order skickade via JARVIS. Kraken
`QueryOrders` ger orderstatus och avsluts-ID; `QueryTrades` ger faktisk avslutstid,
pris, antal och avgift. En skickad/öppen order är ingen affär. Tidpunkter lagras i
UTC och visas med år, månad, dag och sekunder i Europe/Stockholm inklusive sommartid.
Kontobevakning och knappen Uppdatera från Kraken stämmer av kända JARVIS-order.

Köp matchas enligt FIFO mot försäljningar, separat per par och ansvarig agent.
Vyn visar köpt datum/tid, sålt datum/tid, matchat antal och netto efter avgifter.
Kvarvarande köp visas separat. Försäljning utan känt inköp får okänt inköpsdatum
 och inget gissat resultat. Historiken omfattar inte automatiskt externa Kraken-order
eller äldre kontoinnehav. Den är inte en skatte-/portföljbok.

Manuella order märks **Manuell**. Godkända sessionsorder får rätt agent-ID och
sessions-ID genom hela order-/avslutskedjan. Inga affärer tillskrivs en annan agent.

Gamla simulerade pengar, köp/sälj, avkastning och testkontroller visas inte i den
vanliga dashboarden. Befintlig databas och journal bevaras som tekniskt arkiv;
ingen historik eller riskpaus raderas under uppgradering. `JARVIS_PAPER_MODE=1`
är ett uttryckligt utvecklingsläge för den gamla simulatorns regressionstester,
inte den vanliga driftinställningen. Den tidigare simulatorreferensen nedan gäller
bara detta arkiverade testläge och ska inte tolkas som verklig kontoaktivitet.

## Arkiverad simulator: data och exekvering

- Kraken publika BTC/ETH-tickers och orderdjup i EUR; ECB:s daterade dagliga
  EUR/SEK-referenskurs används för SEK-redovisning. Ingen verklig valutaväxling sker.
- Nya marknadssnapshots hämtas högst var femte sekund. Samma snapshot får inte
  skapa flera handelssteg; upprepade manuella klick kan inte påskynda skuggtest.
- Paper-köp använder asks, paper-försäljningar bids, tillgänglig volym i hämtat djup
  och 5 punkters (bps) extra adverse slippage. Djupet delas mellan agenternas order
  i samma snapshot. Otillräcklig volym kan ge delavslut; order löper ut efter 60 sekunder.
- Aktier/ETF är fortfarande **syntetiska**. De använder en uttrycklig modell med
  10 bps spread och 5 bps extra slippage. Krypto faller aldrig tyst tillbaka till den modellen.
- Varje order har ett beständigt intent-ID och order-ID, varje avslut ett fill-ID.
  Samma strategiavsikt återanvänds säkert även efter en omstart. En redan avslutad
  order exekveras inte igen. Testavvisningar/delavslut är avstängda i vanlig drift.
- Misslyckad datahämtning innebär inget handelssteg eller avslut. Båda kryptoparen,
  giltigt djup och giltig FX-kurs krävs. Crossed books, spread över 5 % och ogiltiga
  priser avvisas. Snapshot får inte vara äldre än 30 sekunder när steget tillämpas.
- ECB-kursen cachas en timme och får vara högst sju kalenderdagar gammal.

HTTPS krävs till `api.kraken.com` och `www.ecb.europa.eu`. TLS-verifiering behålls.
Privata Kraken-anrop används bara i den separata kontovyn efter lokal konfiguration.
Inga agentbeslut skickas till Kraken och inga uttagsfunktioner finns.

## Analys av riktig marknadsdata

Marknad visar avslutade femminutersstaplar i ursprunglig EUR-kurs från Kraken,
SMA20/50, Wilder RSI14, ATR14 och momentum. Minst 60 sammanhängande avslutade
staplar krävs. Pågående staplar påverkar inte beslut; gammal historik eller luckor
stoppar nya kryptosignaler. Strategierna fattar högst ett beslut per agent, par och
stapel, oberoende av dashboardens uppdateringsintervall. Korrelation jämför samma
tidsstämplar; okänd korrelation räknas konservativt som gemensam exponering.

**Uppdatera marknad** uppdaterar analys och marknadsvärdering utan att lägga order
eller flytta handelsdagen. Beslut visar regelbaserade signaler och deras underlag;
dessa är beskrivningar av historiska data, inte prognoser eller vinstlöften.

## Arkiverad simulator: risk, datum och kapital

- Köpgränser: högst 70 % totalt investerat, 25 % per instrument, 40 % korrelerad
  exponering, agentbudget och dynamiskt ordertak högst 2 000 SEK. Alla delavslut
  kontrolleras igen. Ingen belåning/blankning. Simulerad avgift 0,15 %.
- CHEF: stop-loss vid 5 %, karantän efter tre förlustavslut eller 8 % budgetförlust,
  global köp-/riskpaus vid 10 % drawdown. Befintliga positioner behålls; sälj tillåts.
- Återstart: minst 40 steg före granskning, positivt skuggnetto, minst fem globala
  skuggavslut, minst 50 % vinster, högst 100 SEK skuggdrawdown och under 10 % faktisk
  drawdown. Ägargodkännande krävs. Enskild karantän har motsvarande 3-avsluts-/40-SEK-krav.
- Dagar är **unika Stockholm-datum**. Manuell stängning låser dagen tills nästa datum.
  Ett datum kan inte bokföras flera gånger. Inga 100-stegsdagar används längre.
- Veckofördelning: efter **sju kalenderdagar**, 20 % av ny realiserad nettovinst efter
  tidigare förluster. Uttag och överföringar använder bara kontanter som inte är
  reserverade för köp. En extra manuell vinstfördelning flyttar inte veckodatumet.
- Expansion: granskning efter varje grupp om **fem unika avslutade börsdagar**, positivt
  netto på dessa dagar, tillräckligt kapital och ingen riskpaus. Högst två nya agenter,
  max 30; alla börjar i skuggläge. Helgdagar/helger räknas inte som börsdagar.
- Kalender: Nasdaq Stockholm-regler för 2025–2027, inklusive helgdagar, halvdagar
  till 13:00 och svensk sommartid. Senare år stängs för syntetiska aktier i autoläge
  tills kalendern granskats. Krypto kör 24/7. Se kalenderkällor i checklistan.
- Kontanter, skyddat kapital, avgifter, kostnadsbas och realiserat resultat bokförs
  i **heltals-nano-SEK**. Konvertering sker med Decimal; dashboardtal är projektioner.
  Insättningar är inte vinster. Skyddat konto är ett internt paper-saldo, inget bankkonto.

## Arkiverad simulator: godkänn återstart eller frigör testkapital

Ett vanligt API-fält med `approved: true` räcker inte. Skapa en ägarsignerad engångskod
i en **andra Ubuntu-terminal**, från samma projektmapp och som samma Ubuntu-användare:

```bash
# Efter att CHEF:s globala skuggtest är godkänt:
python3 -m backend.ops approve resume

# Exempel: återför exakt 100 simulerade SEK från skyddat konto:
python3 -m backend.ops approve release --amount 100
```

Klicka sedan på motsvarande knapp i dashboarden och klistra in koden när den efterfrågas.
Koden gäller i 120 sekunder, för angiven åtgärd och exakt belopp, och kan användas en gång.
Godkännare, tid, åtgärd, belopp och godkännande-ID journalförs. Koden sparas inte i loggar.

Ägarnyckeln skapas lokalt med filrättighet 0600 bredvid databasen och är git-ignorerad.
Detta är ett lokalt ägarskydd, inte publik fleranvändarautentisering. Samma OS-användare
kan läsa nyckeln. Exponera inte tjänsten på internet och dela inte nyckel eller koder.
Vid annan databas används `--db /sökväg/paper.sqlite3` **före** `approve`.

## Export, backup och drift

Dashboardens Historik har export av affärer, full kapitalhistorik och JSONL-journal.
Kapitalexporten innehåller även köp-/säljkontantrörelser, avgifter, insättningar, uttag,
vinstskydd och ägargodkännanden. Gamla poster utan datum/saldo märks uttryckligen som äldre data.

```bash
python3 -m backend.ops backup /valfri/ny-backup.sqlite3
python3 -m backend.ops check
```

Backup använder SQLite:s backup-API, inkluderar WAL och integritetskontrolleras.
Befintliga backupfiler skrivs inte över. För återställning: stoppa servern och starta
med `python3 -m backend.server --db /valfri/ny-backup.sqlite3`. En ny lokal ägarnyckel
skapas om ingen finns vid backupdatabasen; kopiera inte gamla engångskoder.

Data ligger normalt i `.jarvis/`. `system.jsonl` innehåller tidsstämplade strukturerade
loggar, kommando-/request-ID och felstatus, med rotation vid 2 MB och fem backupfiler.
SQL-journalen är append-only genom triggers; den är inte en kryptografisk revisionslogg.

Valfri systemd-användartjänst för Ubuntu/WSL med systemd finns i `deploy/jarvis-paper.service`.
Den förutsätter projektet i `~/Jarvis-Trading`; ändra sökvägen vid annan placering.
Installationskommandon finns i checklistan. Datorn/WSL måste fortfarande vara igång.

## Tester och API

```bash
python3 -m unittest discover -s tests -v
node --check dashboard.js
# Valfritt: Playwright + installerad Chromium
CHROMIUM_PATH=/usr/bin/chromium python3 tests/browser_smoke.py
CHROMIUM_PATH=/usr/bin/chromium python3 tests/browser_advisory.py
CHROMIUM_PATH=/usr/bin/chromium python3 tests/browser_trade_history.py
CHROMIUM_PATH=/usr/bin/chromium python3 tests/browser_sessions.py
```

Sviten testar bland annat hårda krascher före/efter commit, misslyckad journalskrivning,
backup/restore, migration, veckoregler, dubbletter, exakt pengaräkning och åtta simulerade dygn.
Browser-testet verifierar även signerat paper-uttag, exportnedladdningar och hela
Kraken-flödet med simulerad börs. Testet skickar inga privata nätverksanrop.

API: `GET /api/state`, `GET /api/health`, `GET /api/export/trades.csv`,
`GET /api/export/capital.csv`, `GET /api/export/journal.jsonl`.
Kommandon skickas till `POST /api/commands/<namn>` med JSON, `X-Jarvis-Client: dashboard`
och en unik `Idempotency-Key`. Återanvänd samma nyckel bara för en retry av samma avsikt.
`approve` och `release` kräver dessutom `X-Jarvis-Approval`; `release` kräver `approved: true`.

Sessions-API: `GET /api/trading-session` och `POST /api/trading-session/plan|start|stop`.
Start kräver planspecifik `X-Jarvis-Approval`; stop kräver ingen ny signatur och
får inte blockeras av orderarbetarens nätverkslås. Samma ursprungs-/idempotenskrav gäller.

API för den separata börskopplingen: `GET /api/exchange` (lokal cache) och
`POST /api/exchange/check|preview|validate|submit|reconcile|cancel`. Samma
ursprungs-/idempotenskrav gäller. `submit` och `cancel` kräver dessutom en
biljettspecifik ägarsignatur. Se [Kraken-guide](docs/KRAKEN-LIVE.md).

## Återstående gränser

Se [kodgranskning](docs/REVIEW-v0.6.md), [aktuell verifiering](docs/VALIDATION.md)
och [tidigare checklista](docs/CHECKLIST-v0.5.1.md). Liveorder är avstängda som standard.
Den privata integrationen är testad med simulerad börs; verkligt konto, behörigheter
och faktiskt finansierad exekvering har inte verifierats. Verkliga uttag stöds inte.
Normalisering/paginering av hela historiken och långvarig drift under last återstår.
Gamla localStorage-data importeras inte automatiskt. Regelbaserade agenter innebär
ingen garanti om lönsamhet.
