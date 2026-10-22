# Marknad · livekurser och high/low

Marknad visar BTC/EUR och ETH/EUR från en separat publik Kraken-källa i EUR.
Ingen insättning, privat API-nyckel eller ECB-valutakurs krävs för detta visningsflöde.
Det ger inga orderrättigheter och ändrar inte agentstrategier eller sessionsmandat.

## Uppdatering och tidsfönster

- Ticker hämtas normalt cirka var tredje sekund, OHLC-graf cirka var 15:e sekund.
  Nätverksfördröjning tillkommer; vid fel används gradvis väntan upp till 30 sekunder.
- Webbläsaren läser cachad `/api/market-live` varje sekund när Marknad är vald.
  Att läsa endpointen gör inga börsanrop. Uppdatera marknad köar en läsuppdatering;
  upprepade knapptryckningar samlas ihop.
- Senaste avslutspris, rullande 24h high/low, volym och VWAP kommer från Kraken
  Ticker. Högsta/lägsta är dess 24h-värden, inte dagens värden i index 0.
- Förändring i dag räknas från Krakens öppningspris kl. 00:00 UTC. Den är tydligt
  märkt och ska inte tolkas som rullande 24h-avkastning.
- Köp-/säljbud kan visas från valbar publik WebSocket när anslutningen är aktuell,
  annars visas verifierad REST-ticker. Dessa bud kan ha annan tid än REST-statistiken.
- REST äldre än tio sekunder, WebSocket-bud äldre än fem sekunder och OHLC äldre
  än 45 sekunder markeras ej aktuella. Gamla/felaktiga/för framtida tidsstämplar
  får inte bli gröna liveindikatorer. Vid avbrott behålls endast markerade gamla värden.

## Graf

Välj 1, 6 eller 24 timmar. Femminutersstaplarna innehåller faktisk OHLC från Kraken,
inklusive en tonad pågående stapel. Stapelns high, low och close kan ändras vid
nästa hämtning; inga staplar eller avslut simuleras för att fylla luckor.

Grafens high/low visas både på grafen och som separata värden. De gäller de
staplar som faktiskt visas, vilket skiljer sig från tickerns rullande 24h-statistik.
Periodkanten avrundas till berörda femminutersstaplar. Luckor och ofullständigt
periodunderlag markeras. Datum och klockslag visas i Stockholmstid.

Agenternas indikatorer och signaler bygger fortsatt enbart på avslutade staplar.
Den separata analysens aktualitet visas under grafen; en aktuell visningskurs gör
inte en gammal signal aktuell. Tätare polling skapar inga extra handelsbeslut.

## WebSocket

Vanlig start använder REST och fungerar utan extra paket. För att dessutom visa
strömmande köp-/säljbud och mottagna marknadsaffärer:

```bash
python3 -m pip install -r requirements.txt
JARVIS_PAPER_MODE=0 JARVIS_MONITOR_ONLY=1 JARVIS_KRAKEN_STREAM=1 python3 -m backend.server
```

Tickerströmmen prenumererar på ändringar i bästa köp-/säljbud (bbo).
Marknadsaffärer är börsens offentliga avslut, inte dina privata order. Om nätverket
blockerar WebSocket visas REST med sin faktiska aktualitet. Befintlig riskregel
blockerar automatiska nya köp när en uttryckligen aktiverad ström är inaktuell;
marknadsvyn ändrar inte denna säkerhetsregel.

## Verifiering

176 enhets-/API-tester passerar, inklusive rullande kontra dagliga statistikindex,
pris-/OHLC-validering, pågående stapel, gamla/framtida tider, WebSocket-overlay,
REST-fallback, frånvaro av data och uppdatering utan privata API-anrop.

Chromium testar high/low, instrument och grafperiod, uppdatering utan omladdning,
avbrott/återkomst, gammal data och mobilbredd med simulerade publika data.
Befintliga översikts-, sessions-, advisory- och legacy-webbkontroller passerar.
Verklig publik REST-ticker/OHLC verifierades på den lokala utvecklingssidan.
Ingen finansierad order skickades. WebSocket-nätverksåtkomst är inte verifierad i
molnmiljön, där tidigare anslutningsförsök blockerats av proxyn.
