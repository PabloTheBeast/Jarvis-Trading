# Godkända agentsessioner på Kraken — v0.7

Sessioner gäller bara BTC/EUR och ETH/EUR. Svenska aktier är inte anslutna. Kodens
privata börsflöden har testats med simulerad börs; finansierad exekvering och faktiska
API-behörigheter har inte verifierats i utvecklingsmiljön. Inga riktiga order har
skickats under utvecklingen. Lönsamhet eller begränsad faktisk förlust garanteras inte.

## Vad start innebär

Pengarna ligger kvar på Kraken. CHEF skapar interna kostnads-/köpgränser för
GAMMA (momentum), ZETA (mean reversion) och IOTA (BTC-trend). En insättning startar
aldrig handel. Du granskar först en plan och godkänner sedan uttryckligen att
sessionen får lägga köp och försäljningar utan separata koder för varje order.

Planen innehåller kapitaltak, godkända nya köp totalt inklusive avgifter, varje
agents budget, ordertak, förlustgräns, sluttid och avslutspolicy. Engångskoden är
bunden till exakt denna plan och gäller bara för start i 120 sekunder. Planen är
startbar i fem minuter. Ingen kod, secret eller API-nyckel skickas till andra än
den lokala servern; skriv aldrig hemligheter i chatten.

## Starta

1. Konfigurera Kraken lokalt enligt KRAKEN-LIVE.md. Läsbehörighet för medel och
   öppna/stängda order/affärer samt skapa/annullera spotorder krävs. Ingen uttagsrätt.
   Använd ett dedikerat konto/nyckel och gör ingen parallell extern handel under sessionen.
2. Vanlig start/bevakningsläge skickar inga automatiska order. För att uttryckligen
   möjliggöra sessionshandel, stoppa servern och kör i Ubuntu från projektmappen:

   ```bash
   unset JARVIS_MONITOR_ONLY JARVIS_PAPER_MODE
   JARVIS_KRAKEN_LIVE=1 JARVIS_AUTO_TRADING=1 python3 -m backend.server
   ```

   Serverstarten startar ingen session. En tidigare aktiv session blir RECOVERY
   efter varje processomstart och återupptas aldrig automatiskt.
3. Öppna Översikt → CHEF · Starta och avsluta trading. Välj kapitaltak (pilot högst
   100 EUR), varaktighet (15–720 minuter) och avslutspolicy. Klicka **Granska
   budgetfördelning**. Nollsaldo ger en nollplan som inte går att starta.
4. Kontrollera planen. I en andra Ubuntu-terminal kör du:

   ```bash
   python3 -m backend.ops approve session-start --session SESSION-EXAKT-ID
   ```

   CLI:t visar exakt mandat inklusive budgetar och säljbedömning vid avslut.
5. Klicka **Starta trading**, bekräfta sammanfattningen och ange engångskoden.
   Status blir RUNNING. Nästa worker-uppdatering kan nu lägga riktiga köp/sälj.
   Låt servern vara igång. Att stänga webbläsaren stoppar inte sessionen.

Med annan databas ska `--db /sökväg/paper.sqlite3` stå före CLI-underkommandot,
och servern måste använda samma databas. Den gamla simulatorn och live-sessioner
kan inte aktiveras tillsammans via normal Runtime-konfiguration.

## Kapital och risk

- Kapitaltak 100 EUR för denna pilot. Ursprungliga gränser kvarstår: högst 40 %
  samlad kryptoexponering och 25 % per instrument, ingen hävstång/blankning.
- Nya köp begränsas av lediga kontanter, exponeringsutrymme, godkänt kapitaltak
  minus tidigare agentinnehav samt avgiftsreserv. Nya köp inklusive faktiska
  avgifter får totalt inte överskrida planens nya-köp-budget. Sälj återställer inte
  denna omsättningsgräns; en ny session kräver nytt godkännande.
- Nya budgetmedel fördelas 40 % till GAMMA, 40 % till ZETA och 20 % till IOTA.
  Tidigare verifierade agentinnehavs kostnadsbas räknas in i respektive budget.
  Med bara 100 EUR kontanter och inga innehav kan cirka 39,60 EUR bli ny köpbudget,
  inte hela insättningen. Resten ligger kvar som kontanter. Börsens minbelopp kan
  göra en liten agentbudget oanvändbar för ett visst par.
- Ordertak är serverns pilotgräns (standard 25 EUR, hårt tak 100 EUR) och binds i
  planen. Bara en aktiv order åt gången globalt. Spot-limit med GTD +60 sekunder,
  parregler, precisionssteg, aktuellt djup, saldo och avgiftsreserv kontrolleras igen.
- Sessionsförlust/drawdown från verkligt initialt/peak-kontovärde pausar köp vid
  min(3 % av startvärdet, 10 EUR). Gränsen är ett stopputlösande villkor, ingen
  garanterad maximal faktisk förlust. Marknadsgap och behållna innehav kan ge mer.
- Fem procents positionsförlust kan ge en säljorder för ett ägt agentinnehav även
  utan en matchande strategisignal. Ingen okänd/manuell position säljs av agenten.
- Köp kräver färska slutna femminuterssignaler och CHEF:s konsensus. Intents är
  beständiga per session, agent, par, stapel och riktning. Samma beslut skickas inte igen.
- Verkliga saldon stäms av mot startbalanser plus verifierade avsluts kassaflöden.
  Insättning, uttag, extern handel eller annan oförklarad differens ger PAUSED,
  aldrig automatiskt mer budget. Avstämningstolerans är 0,01 EUR och 0,00000001 BTC/ETH. Avstämning och prisrisk kontrolleras före sändning.
- Tidigare simulerade saldon, resultat, karantän och drawdown styr inte dessa
  verkliga budgetar. Sessionen använder verkliga Kraken-saldon och verifierade avslut.

## Avsluta dagen

Klicka **Avsluta trading**. Det återkallar nya köp direkt och väntar inte på
arbetstrådens nätverkslås. En påbörjad sändning kan redan ha nått Kraken och fyllas.
Status är STOPPING (**Avslutar**) tills öppna sessionsorder annullerats/stämts av.
Om du avbryter medan startkontrollen pågår kan planen också återkallas.

- **Behåll alla:** inga nya säljorder; verifierade agentinnehav ligger kvar.
- **CHEF bedömer:** för varje verifierat agentinnehav: sälj vid färsk gemensam
  säljsignal eller 5 % positionsförlust; behåll annars. Aktuella kurser måste finnas.
  Detta är regelbaserad bedömning, inte en prognos att det blir lönsamt att hålla.
- Avslut får skicka begränsade säljorder under högst 15 minuter från avslutsbegäran,
  och aldrig senare än 15 minuter efter planerad sluttid. Därefter behålls resten.
- Börsens minbelopp, ordertak och ofyllda/utgångna limitorder kan lämna kvar innehav.
  En exit-avsikt skickas en gång per agent/par. Vid större position än ordertaket
  kan återstående del behållas. Slutvyn visar HOLD/SELL_PENDING med orsak.
- STOPPED betyder inga kvarvarande agentinnehav; STOPPED_HOLDINGS betyder att de
  ligger kvar. Innehav bevakas inte aktivt av handelssessionen efter slutstatus.
  Allmän kursbevakning fortsätter, men inga stop-loss-/säljorder skapas då.
- Nätverksfel, okända order och ofullständig historik kan göra att avslutet inte
  kan bekräftas. Status får då inte påstå att allt är klart. Kontrollera Kraken
  direkt vid behov. CTRL+C eller avstängd dator är inte en bekräftad annullering.
- Vid automatisk sluttid används samma avslutsflöde. Sluttiden är den tidigaste av
  vald varaktighet och nästa midnatt i Stockholm. Den visas före ditt godkännande.

## Paus, omstart och nästa dag

PAUSED återupptas aldrig automatiskt. RECOVERY efter omstart tillåter inga nya
köp eller automatiska slut-försäljningar. Avsluta/stäm av gamla order, granska
innehaven och skapa en ny plan med ny ägarkod. En ny dags budget omfattar verifierade
kvarvarande agentpositioner. Manuell orderförberedelse är spärrad medan en session
är aktiv, pausad eller under återställning. Ägarsignerad annullering är möjlig.

Alla sessioner, planer, engångsgodkännanden, orderintents och sessionshändelser
lagras i SQLite. Session_events är append-only. Sessionens agent-ID och ID följer
ordern och de verifierade avsluten, så Historik visar rätt agent, köptid, säljtid
 och avgifter. Okända utfall skickas aldrig om automatiskt. Kontoåtkomst eller
omstart ger inget nytt handelsmandat.
