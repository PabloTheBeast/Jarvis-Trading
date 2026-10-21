# JARVIS Trading v0.5

Enbart **paper trading**. Python kör strategiagenterna, JARVIS CHEF, ordermotorn,
riskkontrollerna och kapitaljournalen oberoende av webbläsaren. Den befintliga
svenska dashboarden med fem flikar använder samma server för data och kontroller.

## Kom igång

Krav: Python 3.12+ på Linux (inklusive SQLite och tidszonsdata). Inga Python-paket,
API-nycklar, Node-paket eller Kraken-konton behövs för backend.

```bash
python -m unittest discover -s tests -v
python -m backend.server
```

Öppna serverns rot på port 8000 på din egen dator och välj **Starta servermotor**.
Första starten är pausad med 10 000 SEK i simulerat kapital. Därefter fortsätter
motorn när fliken stängs, så länge Python-processen och datorn körs.
Start-/stoppstatus återställs från databasen efter en processomstart.
Molnuppgifter kan stoppa processer när själva uppgiften avslutas; detta är ingen
garanti om permanent drift.

Öppna inte HTML-filen direkt: den behöver API:t. Servern binder enbart till loopback.
Använd en SSH-tunnel vid fjärrutveckling. Tjänsten saknar fleranvändarautentisering
och ska inte exponeras direkt på internet.

```bash
python -m backend.server --port 8001 --db /valfri/sökväg/paper.sqlite3
```

SQLite, WAL och låsfil ligger normalt i ignorerade `.jarvis/`. En process per databas.
Varje lyckat kommando och handelssteg sparar tillstånd och journal i samma transaktion.
Stoppa servern före filbaserad backup eller använd SQLite:s backup-API.
Radera aldrig databasen för att uppdatera programmet.

## Marknadsdata och order

- BTC och ETH: Krakens **publika EUR-tickers**, omräknade till SEK med ECB:s
  daterade dagliga EUR/SEK-referenskurs. Kraken erbjuder inte de önskade direkta
  SEK-paren. FX-kursen visas i API:t och referensdatum i dashboarden.
- `BTC-SIM`/`ETH-SIM` behålls som interna ID:n för kompatibilitet. Kurserna är
  marknadsdata; innehav, avgifter och alla order är simulerade.
- Aktier/ETF är uttryckligen **syntetiska**, inte riktiga svenska värdepapper.
  Autoläge följer vardagar 09:00–17:30 Europe/Stockholm; helgdagar/halvdagar saknas.
  Avmarkerat autoläge tillåter manuella syntetiska aktiesteg även utanför börstid.
- Högst en Kraken-hämtning per fem sekunder, även vid manuella steg. Tillåtna
  autointervall är 5 eller 30 sekunder. ECB-kursen cachas en timme och får vara högst
  sju kalenderdagar gammal. Referenskursen är ingen exekverbar valutakurs.
- Vid saknade/ogiltiga/otillgängliga data sker **inget steg och inga avslut**, även för
  syntetiska aktier. Status visar fel och automatiken försöker igen efter intervallet.
  Ingen tyst övergång till syntetiska kryptopriser.
- Inga privata Kraken-anrop eller funktioner för verklig orderläggning finns.

HTTPS krävs till `api.kraken.com` och `www.ecb.europa.eu`; Git/PR använder GitHub.
TLS-verifiering behålls. Inga hemligheter behövs.

## Risk och redovisning

Originalets regler behålls: högst 70 % investerat, 25 % per instrument, 40 % i en
korrelerad grupp (korrelation över 0,75, minst 12 förändringar), agentbudget,
dynamiskt ordertak högst 2 000 SEK, avgift 0,15 %, ingen belåning eller blankning.
Köp begränsas vid orderläggning **och vid varje delavslut**. Prisrörelser kan ändra
exponeringen efter avslut; gränserna förbjuder ytterligare köp, inte marknadsrörelser.

CHEF försöker sälja agentinnehav vid 5 % förlust. Tre förlustavslut eller 8 % förlust
mot agentens riskbudget leder till karantän. Skugghandel påverkar inte huvudkapitalet
eller delat erfarenhetsminne. Granskning efter minst 40 steg, därefter var 20:e steg:
minst tre affärer, positivt netto, minst 50 % vinster, högst 40 SEK skuggdrawdown.

Global paus vid 10 % drawdown stoppar nya köp och avbryter väntande köp.
Säljorder tillåts fortfarande. Globalt skuggtest kräver minst fem affärer, positivt
netto, minst 50 % vinster och högst 100 SEK skuggdrawdown. Manuell återstart krävs
och drawdown måste vara **under 10 %**, så godkännandet inte omedelbart återutlöser
pausen. Detta rättar originalets motstridiga återstartsgräns på 12 %.
Nödstopp stoppar automatiken och avbryter alla väntande order, även efter dygnsskifte.
Ingen återstart sker automatiskt nästa dag.

Ägarinsättningar är aldrig handelsvinster. Var femte avslutade dag flyttas 20 % av ny
realiserad nettovinst över tidigare avsatt vinsttopp, begränsat till fria kontanter,
till skyddat konto. Manuell återföring kräver uttryckligt godkännande.
Högst två nya agenter var femte lönsamma rapportdag, enligt ursprungliga kapitalnivåer,
upp till 30; de börjar i karantän.

## Tester och API

```bash
python -m unittest discover -s tests -v
node --check dashboard.js
```

Tester kör utan extern trafik och täcker strategier, avgifter, ägarfördelning,
delavslut, riskgränser, korrelation, karantän, återstart, vinstskydd, expansion,
marknadsschema, Kraken/ECB-fel, SQLite-återställning, kommandorepriser, samtidighet
och serverdrift utan webbläsare. Testet med 500 steg verifierar kapitalavstämning.
Node behövs bara för JS-syntaxkontrollen, inte för att starta appen.

Valfritt verkligt Chromium-test (kräver Playwright och installerad Chromium):

```bash
CHROMIUM_PATH=/usr/bin/chromium python tests/browser_smoke.py
```

`GET /api/state` ger dashboardens tillstånd, agenter, instrument och `paperOnly`.
`GET /api/health` visar processtatus och separat marknadsdatastatus; processhälsa
innebär inte att marknadsdata fungerar. `POST /api/commands/<namn>` tar ett JSON-objekt,
`X-Jarvis-Client: dashboard` och en unik `Idempotency-Key`. Återanvänd samma nyckel
vid transportretry av samma kommando; ett annat innehåll med samma nyckel avvisas.
Tillåtna kommandon: `step`, `start`, `stop`, `pause`, `approve`, `close-day`, `sweep`,
`deposit`, `withdraw`, `release`, `settings`, `read-notices`, `save`.
`release` kräver `approved: true`. Inget publikt order- eller livehandels-API finns.

## Begränsningar och utvecklingsplan

Se [analys och plan för v0.5](docs/JARVIS-v0.5.md) samt [ändringslogg](CHANGELOG.md).
Gamla localStorage-data skrivs inte över och importeras inte automatiskt.
Återställning, scenariobyte och webbläsaraviseringar är avstängda i serverläget;
spara/exportera gammal historik i originalversionen före en eventuell framtida import.
Agenterna är regelbaserade prototyper med adaptiv storleksjustering, inte tränade
språkmodeller. Resultaten visar inte förväntad avkastning.
