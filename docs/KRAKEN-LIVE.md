# Kraken: separat manuell orderläggning

Agenter och CHEF använder fortsatt paper-portföljen i SEK. Kraken-vyn visar verkligt
saldo i EUR/BTC/ETH. Pengar överförs aldrig mellan dessa två bokföringar. Det skyddade
paper-saldot är inget bankkonto. Kopplingen är ett begränsat manuellt pilotläge,
inte en autonom livehandelsmotor. Inga riktiga order har skickats under utvecklingen.

## Konfigurera i Ubuntu/WSL

1. Starta servern en gång så att databas och lokal ägarnyckel skapas. Stoppa med Ctrl+C.
2. Skapa en separat Kraken Spot-API-nyckel för denna installation. Tillåt läsning av
   saldo, öppna/stängda order och affärer samt skapande/annullering av order.
   **Ge inte nyckeln uttagsbehörighet.** Använd inte samma nyckel i en annan process;
   den beständiga nonce-räknaren gäller denna databas.
3. Kör från projektmappen i Ubuntu-terminalen:

   ```bash
   python3 -m backend.ops configure-kraken
   python3 -m backend.server
   ```

   Nyckel och secret matas in dolt och sparas i `.jarvis/kraken.credentials.json`
   med rättighet 0600. Använd projektet i Ubuntu-hemkatalogen, exempelvis
   `~/Jarvis-Trading`, där Linux-filrättigheter fungerar. Klistra aldrig in hemligheter
   i chatten eller dashboarden. Fullständiga `KRAKEN_API_KEY` och `KRAKEN_API_SECRET`
   i serverprocessens miljö stöds också och har företräde framför filen.
4. Öppna **Kraken-konto**, välj **Kontrollera konto** och kontrollera att saldot
   motsvarar Kraken. Förhandsgranska därefter en order och välj **Validera hos Kraken**.
   Validering använder `AddOrder` med `validate=true` och lägger ingen order.
   Denna kontrollerade anslutning har inte kunnat provas här eftersom nycklar saknas.

Med annan databas ska `--db /sökväg/paper.sqlite3` stå före CLI-underkommandot och
samma databas ska användas av servern. Roterade nycklar ersätts med
`configure-kraken --replace`. Kör bara en server per databas och en installation per nyckel.

## Aktivera faktisk orderläggning

Efter att konto och validering kontrollerats lokalt: stoppa servern och starta om i Ubuntu:

```bash
JARVIS_KRAKEN_LIVE=1 JARVIS_KRAKEN_MAX_ORDER_EUR=25 python3 -m backend.server
```

Startflaggan möjliggör manuella order. Den startar inte agenter i livehandel.
Varje order kräver fortfarande följande:

1. Förhandsgranska exakt par, köp/sälj, antal och limitpris. Alla priser är EUR.
2. Validera samma order hos Kraken. Förhandsgranskning gäller fem minuter,
   validering högst två minuter.
3. Kopiera biljett-ID:t och kör i en andra Ubuntu-terminal:

   ```bash
   python3 -m backend.ops approve live-order --ticket LIVE-EXAKT-BILJETT-ID
   ```

   CLI:t visar ordersammanfattningen. Kontrollera den och klistra in engångskoden
   när **Godkänn & skicka live** frågar efter den. Koden gäller 120 sekunder för
   exakt denna order. Ett klick kan då köpa eller sälja verkliga tillgångar.
4. Välj **Stäm av order** för status, delavslut, faktiska avgifter och avslutens ID.
   Kontoavstämning är manuell; **Kontrollera konto** hämtar nya saldon.

För annullering används en ny kod med `approve live-cancel --ticket …` och knappen
**Annullera liveorder**. Att annullera en redan fylld order ångrar inte affären.
Annullering är tillåten även när liveflaggan stängts av.

**Pausa paper**, stoppad server eller stängd webbläsare annullerar inte Kraken-order.
Kontrollera och annullera dem i Kraken vid behov. Alla skickade order har GTD och
begärd giltighetstid 60 sekunder, men slutstatus måste ändå hämtas från börsen.

## Gränser och felhantering

- Endast spot-limitorder i BTC/EUR och ETH/EUR; ingen hävstång eller blankning.
- Standardtak 25 EUR/order; konfigurerbart upp till ett hårt pilottak 100 EUR/order.
- Högst 40 % samlad kryptoexponering och 25 % per instrument, inklusive öppna köp.
  EUR-avgiftsreserv 1 %. Bara verkligt tillgängligt saldo används. Kontroll sker igen
  före sändning, med aktuella parregler, minbelopp, pris-/volymsteg och orderdjup.
- Kontot får bara innehålla EUR, BTC och ETH; övriga saldon, marginal och okända
  ordertyper stoppar pilotens riskvärdering. Köp blockeras under CHEF:s riskpaus.
- Spread över 1 %, limitpris mer än 2 % från mittkurs och gammalt djup avvisas.
- Varje order har ett unikt Kraken `cl_ord_id`. Bara en pågående lokal livebiljett
  tillåts. En ofylld förhandsgranskning reserverar inte pengar hos Kraken.
- `SENDING`, förbrukad ägarsignatur och kommandonyckel sparas atomärt **före** nätanropet.
  Privata anrop återförsöks aldrig automatiskt. Timeout, tjänstefel och oklart svar
  ger `UNKNOWN`. Omstart återställer oavslutad sändning till `UNKNOWN`.
- **Skicka aldrig om en UNKNOWN-order.** Stäm av med öppna/stängda order och
  klient-ID, eller känt börs-ID. Saknad träff bevisar inte att ordern aldrig lades;
  biljetten blockerar fortsatt handel. Kontrollera då Kraken direkt. Avstämningen
  kräver att börsens par, riktning, antal, pris och klient-ID matchar biljetten.
- Nya orderförhandsgranskningar krävs efter paper-stopp/omstart. Riskpaus i paper är
  ett extra köpskydd; separat automatisk live-drawdownövervakning finns inte.
- Livehändelser lagras i `live_events` med append-only-triggers; biljetter, orderstatus,
  avslut och avgifter är åtskilda från paper-journalen. SQLite-backup inkluderar dem.
  CSV-exporterna i Historik gäller paper-portföljen. Ingen automatisk skattebokföring.

## Återställning och granskning

Återställ inte en gammal databas och börja handla direkt: den kan sakna order och
nonce som redan använts. Stoppa livehandel, kontrollera alla verkliga order i Kraken
och använd en ny dedikerad API-nyckel innan fortsatt drift. En ny lokal ägarnyckel
ersätter inte denna avstämning. Det finns avsiktligt ingen knapp som tvångsrensar
ett okänt orderutfall.

Verifierad mot Krakens officiella SDK-källor för
[signering](https://github.com/krakenfx/kraken-api-sdk/blob/main/rust/src/auth/signer.rs),
[orderparametrar](https://github.com/krakenfx/kraken-api-sdk/blob/main/rust/src/api/trade/types/requests.rs),
[kontomodeller](https://github.com/krakenfx/kraken-api-sdk/blob/main/rust/src/api/account/types.rs)
och [order-ID:n](https://github.com/krakenfx/kraken-api-sdk/blob/main/rust/src/types/ids.rs).
Enhetstester använder SDK:ns referenssignatur och simulerade privata svar. Detta
ersätter inte verifiering av konto, behörigheter och verklig exekvering lokalt.

## När kontokontrollen nekas

Uppdatera koden och starta om servern för att få känd fullständig felkod samt
namnet på anropet, exempelvis `BalanceEx: EGeneral:Permission denied`.
Råa okända börssvar visas inte, eftersom de kan innehålla känsliga uppgifter.

- `Permission denied`: kontrollera behörigheterna på just den nyckel som sparats
  för JARVIS. `BalanceEx` behöver läsrätt för medel; `OpenOrders` behöver läsrätt
  för öppna order. Att ändra en annan nyckel hjälper inte.
- `Invalid key`: kontrollera att nyckeln är aktiv och korrekt inmatad.
- `Invalid signature`: API key och API secret måste höra till samma nyckel.
- `Invalid nonce`: använd en dedikerad nyckel för en installation; återställ inte
  en äldre databas som lösning.

Vid behov mata in rätt nyckelpar på nytt lokalt med
`python3 -m backend.ops configure-kraken --replace`. Miljövariablerna
`KRAKEN_API_KEY` och `KRAKEN_API_SECRET` har företräde framför filen om de är satta;
att ersätta filen ändrar inte dessa. Dela bara felkoden/anropsnamnet vid felsökning,
aldrig nyckelfilen, API-hemligheter eller innehållet i miljövariablerna.

## Bara bevakning, utan finansiering

Använd `JARVIS_MONITOR_ONLY=1 python3 -m backend.server` för automatisk läsning av
kryptokurser (cirka 15 sekunder) och konto/öppna order (cirka 60 sekunder).
Nollsaldo är tillåtet. Ingen order skickas, paper-handelssteg blockeras och inga
nya livebiljetter får förberedas eller valideras. En eventuell liveflagga åsidosätts.
Tidigare order på Kraken påverkas inte. Ägarsignerad annullering och avstämning
är fortfarande möjliga. Minsta nyckelbehörighet för bevakning är Fråga för medel
samt Fråga efter öppna order och affärer. Publika kurser kräver ingen nyckel.

Bevakningen fördelar inte kapital till agenter och är inte autonom livehandel.
Svenska aktiekurser är ännu syntetiska och kräver en separat datakälla.
