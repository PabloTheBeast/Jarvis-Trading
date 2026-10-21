# Kodgranskning och ändringar för v0.6

Granskningen utgick från v0.5.1:s Python-backend, order-/kapitalmoduler,
persistens, kalender, CLI, dashboard, tester och befintlig dokumentation.

| Problem | Ändring |
| --- | --- |
| Strategier använde ett antal pollingsteg; fem och trettio sekunders uppdatering betydde olika tidsfönster. | Kryptosignaler använder avslutade femminutersstaplar i EUR, minst 60 sammanhängande staplar och beständigt beslut per agent/par/stapel. Även inlärningens marknadsregim använder stapelanalysen. |
| Prislistor kunde jämföras utan gemensamma tidsstämplar och med FX-brus. | Korrelation på matchande EUR-staplar; okänd eller blandad syntetisk/kryptoexponering räknas konservativt. |
| Nya agenters kapitalbaslinjer kunde saknas efter skugggranskning. | Agentinitialisering registrerar omedelbart realiserat resultat, riskbas och ägarkapital i boken. Regressionstest aktiverar en ny agent. |
| Saknat kryptodjup kunde nå en syntetisk modell. | En riktig Kraken-snapshot får aldrig använda syntetisk kryptofyllning. |
| Senast handlat pris kan vara äldre än aktuell köp-/säljkurs. | Marknadsvärdering använder orderbokens mittkurs; ticker sparas separat. Djup, FX, parstatus och regler valideras. |
| Ansluten dashboard kunde uppfattas som färska börsdata. | Separat server-/datahälsa, serverbaserad tidsålder och väntande/stale-status. Äldre revisionssvar får inte skriva över nyare tillstånd. |
| Liten tät text och många kontrollfält på första sidan. | Systemtypsnitt Segoe UI, tabellsiffror, större text, nio tydliga flikar, tangentbordsnavigering och mobil layout. |
| Kraken-konto och verkliga order saknades. | Separat adapter med lokala hemligheter, kontoavstämning, validering och ägarsignerade manuella limitorder. Beständig idempotens, signerad exakt order och okända utfall utan omsändning. |

Riskhantering, paper-kapitaljournal och befintliga agentskuggtester behålls.
Public market-refresh utför inga avslut. Verkliga avgifter/delavslut hämtas från
Kraken och skrivs aldrig till paper-kapitalet. Backuper inkluderar båda domänerna.

## Nästa utvecklingssteg

1. Verifiera konfiguration och `validate=true` mot användarens dedikerade Kraken-konto.
   Efter separat bedömning: en liten manuellt signerad order med avstämning, avgift
   och annullering. Det har inte genomförts i utvecklingsmiljön.
2. Före autonom livehandel: separat live-portföljbok, kontinuerlig avstämning,
   extern kontroll av saldon/order, automatisk live-drawdownpaus och larm. Agenter
   har i denna version ingen rätt att skicka riktiga order.
3. Långvariga drifttester och större historik: normalisera state/historik i SQL,
   paginera journal och övervaka svarstider, utrymme och nätverksbegränsningar.
4. Behåll årlig granskning av XSTO-kalendern och märk fortsatt aktier/ETF syntetiska.
   Kraken-staplar är kryptodata och ger ingen riktig aktieanalys.

Övriga gränser: lokal OS-ägarsignatur är inte fleranvändarautentisering, loopback
ska behållas, ingen automatisk import av gamla localStorage-data, ingen faktisk
sparkontoöverföring eller uttagsfunktion. Indikatorer och historik bevisar inte lönsamhet.
