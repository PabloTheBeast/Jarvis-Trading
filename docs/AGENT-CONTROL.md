# Agentkontroll och kapitalstyrning — v0.8

Python-backendens livekontroll är separat från den gamla simulatorns kapital,
karantän och påhittade historik. Normal dashboard visar bara verklig kontodata och
verifierade affärer. Skugghandel ligger tydligt märkt i Agentkontroll.

## Budget och risk

Kontrollflödet är: agentrapport → CHEF:s oberoende strategiröster → gemensam
idékontroll → agentstatus → budget/risk → aktuell börskontroll → beständigt intent
→ validering → signerad sessionsauktorisering → order → REST-verifierade avslut.

- Pilotens kapitaltak är fortfarande 100 EUR; ordertak normalt 25 EUR. Större saldo
  ändrar inte ett redan godkänt mandat. Kontodifferenser kräver ny granskning.
- Basvikter är GAMMA 40 %, ZETA 40 %, IOTA 20 %. Negativt resultat under aktuell
  riskperiod eller begränsad provstatus halverar vikten; karantän/skugghandel ger
  noll ny budget. Outdelat utrymme blir kontanter, inte större budget åt andra.
- Kontanter som reserverats som vinst undantas före budgetfördelning och före varje
  manuell eller automatisk köporder. Äldre godkända planer kan inte kringgå spärren.
- Nya positioners planerade risk begränsas till 0,5 % av godkänt kapitaltak, inklusive
  1 % kostnadsmarginal. ATR-avståndet låses vid inträdet: tre ATR, minst 2 %, högst
  8 %. Saknad ATR ger 5 % för äldre test-/legacyunderlag. Gränsen kan inte flyttas
  ut i efterhand av en agent. Det är inte en garanterad maximal faktisk förlust.
- Samma tillgång/stapel kan inte köpas av flera agenter. Nya köp i en tillgång med
  ett redan registrerat innehav spärras: ingen automatisk snittning nedåt eller
  pyramidering. Efter förlust gäller minst 30 minuters spärr och ny färsk signal.
- BTC/ETH ingår i gemensamt exponeringstak 40 %, per instrument 25 %. Ny storlek
  halveras dessutom vid korrelation ≥0,7 med befintligt annat agentinnehav. Okänd
  korrelation behandlas konservativt som 1. Ingen hävstång eller blankning.
- Synligt orderdjup vid limitpriset måste täcka hela nya sessionsordern. Det är en
  kontroll vid sändning, ingen garanti för avslut efter nätverksfördröjning.

Budget är interna gränser, inte pengar som överförs till agentkonton. Små budgetar
kan understiga Krakens minimibelopp och då gör agenten ingen affär.

## Livscykel och granskning

Nuvarande agenter börjar med befintlig begränsad pilotbehörighet. Liveövervakningen
beräknar netto, avgifter, nedgång, snittvinst/förlust, öppet anskaffningsvärde och,
när verifierade kurser finns, orealiserat värde. Delavslut grupperas per säljorder
för uppföljning; det är inte alltid samma sak som en komplett strategiomgång.

Förlustgräns: max(1 EUR, 8 % av agentens aktuella budget) i realiserad nedgång.
Alternativt negativt netto och tre förluster i följd efter minst tio avslut.
Detta är konservativa pilotregler, inte statistiskt bevis för att en strategi är dålig.

**ACTIVE → QUARANTINE → SHADOW → REVIEW_READY → LIMITED → ACTIVE.**

Du kan även sätta en agent i karantän med orsak direkt i Agentkontroll.

Karantän blockerar nya köp och annullerar kända öppna agentköp vid nästa kontroll.
Redan sända order kan hinna fyllas. Innehav försvinner inte: exitkontroller i en
aktiv godkänd session fortsätter, medan okända order kräver kontoavstämning.
Karantänens agent får fortsätta rapportera för sitt skuggprov men röstar inte i
CHEF:s nya köpbeslut. Kontots övergripande riskpaus kvarstår som separat skydd.

Skuggprovet använder aktuella REST-orderböcker, samma instrument och riskprincip
som live, individuellt kapitalunderlag, minsta orderstorlek och kvantitetssteg.
Limitordern kan tidigast fyllas vid nästa observation. Modellen använder högst
10 % av synlig volym, uppskattar delavslut, löper ut efter 60 sekunder och tar
0,4 % avgift samt 0,1 % negativ prisglidning per sida. Ofyllda och delvis fyllda
order lämnar korrekt kvarvarande innehav. Det är en uppskattning; verklig köplats
eller exekveringskvalitet på Kraken bevisas inte av skuggresultatet.

Återkvalificering kräver minst sju dagar, 20 avslut, positivt realiserat och totalt
skuggresultat, högst 2 EUR nedgång och inga pågående skuggorder eller innehav. Rapporten fryses för ägargranskning. En ny karantän startar alltid ett nytt prov och arkiverar det tidigare. Om villkoren slutar
gälla återkallas REVIEW_READY. Granska resultatet i Agentkontroll och kör lokalt:

```bash
python3 -m backend.ops approve agent-review --agent gamma
```

Koden gäller 120 sekunder och är bunden till exakt rapport. Dashboardens godkännande
kan bara återställa begränsad provstatus, inte starta handel. En ny sessionsplan
krävs; ingen pågående session får finnas. Ny riskperiod börjar vid återstarten men
all historik bevaras. Minst sju dagar och 20 nya netto positiva avslut krävs för
normal pilotstatus igen; större budget kräver fortfarande en ny ägarsignerad plan.

## Veckovis vinstreserv och förlustgranskning

Sjudagarsperioderna börjar när livekontrollen först installeras i databasen. Efter
en avslutad period reserveras 20 % av ny realiserad nettovinst över tidigare
vinsttopp, efter faktiska avgifter. Tidigare förluster måste återhämtas innan mer
reserveras. Insättningar och orealiserade vinster ingår aldrig. Delar av cent sparas
som restbelopp så avrundning inte tappar små vinster. Befintlig verifierad
JARVIS-historik ingår i den första beräkningen.

Om tillgängliga EUR inte räcker skjuts reserveringen upp. Saknad anskaffningshistorik
blockerar beräkningen. Driftstopp över flera perioder ger en sammanlagd rapport för
den senast avslutade perioden; rapporten visar faktisk periodsluttid och kan avse
flera veckor, inte påhittade veckosiffror.

Veckorapporten visar matchade köp/sälj, netto, fördelade avgifter, köp-/säljunderlag
och strategiversion där den finns. Den skiljer negativ prisrörelse från fall där
avgifter åt upp ett positivt bruttoresultat. Förslag om lägre kostnader eller andra
signaler är hypoteser, inte bevisade orsaker eller automatiskt aktiverade ändringar.

**Reserven ligger fortfarande på samma Kraken-konto.** Den spärrar JARVIS-köp men
är inte ett bankkonto eller en Kraken-spärr. För pengar utanför systemets åtkomst
behövs en separat destination utanför API-nyckelns behörighet. Inga uttagsmetoder
eller uttagsrättigheter har lagts till. Kontrolljournalen kan exporteras som JSONL.

## Marknadsström

REST fungerar utan extra paket. För valbar WebSocket-ström:

```bash
python3 -m pip install -r requirements.txt
JARVIS_KRAKEN_STREAM=1 python3 -m backend.server
```

Publik ström: Kraken v2 ticker och genomförda marknadsaffärer i BTC/EUR och ETH/EUR.
Orderdjup och instrumentregler hämtas och verifieras fortfarande via REST före order.
Strömmen ger inga orderrättigheter. När aktiverad ström är frånkopplad eller äldre
än 15 sekunder blockeras nya automatiska köp; försäljningar kräver fortfarande
verifierbart REST-underlag. Ingen syntetisk fallback används.

Privata orderhändelser är separat valbara:

```bash
JARVIS_KRAKEN_STREAM=1 JARVIS_KRAKEN_PRIVATE_STREAM=1 python3 -m backend.server
```

Nyckeln måste då även tillåta WebSocket-gränssnittet på Kraken. En kortlivad token
stannar i serverminnet. Händelser utlöser REST-avstämning, men skapar aldrig
bokförda avslut direkt. Frånkoppling återansluter med ny snapshot; REST fortsätter
även utan privata WebSocket-händelser. Privata order anropas aldrig om automatiskt.

## Efterbevakning och verkligt skydd

Välj efterbevakning innan du granskar och godkänner sessionsplanen. Valet och dess
sluttid ingår i ägarsignaturen och CLI-sammanfattningen. Standard är avstängt.

Efter avslut och avstämning går sessionen till WATCHING om du godkänt efterbevakning
 och det finns innehav. Inga köp får skickas. Färsk säljsignal eller låst riskgräns
kan ge säljorder för egna verifierade agentinnehav. Mandatet gäller till högst
24 timmar efter planerad sessionssluttid. Klicka **Avsluta bevakning** för att
återkalla även detta mandat. Omstart återställer aldrig efterbevakning automatiskt.

Detta kräver fungerande server och nätverk. **Börslagrade skyddsorder är inte
implementerade.** Pilotens modell förutsätter en aktiv order globalt och känner
bara limitorder. Att lägga till skyddsorder kräver stöd för samtidiga villkorade
order, deras kvantitetsreservationer, delavslut och dubbel-säljskydd. Att bara skicka
en stoporder i den nuvarande modellen vore ett felaktigt skydd. Efter terminal
slutstatus hanteras inga order för behållna positioner.

## Research och verifiering av strategiidéer

Agentkontroll tar emot källa, hypotes, köp-/sälj- och riskregler. Text och länkar är
opålitligt underlag och kan inte bli Python-kod, ändra risk eller nå kontohemligheter.
Samma idé sparas bara en gång. URL:er hämtas inte automatiskt av servern.

Importerad JSON-historik har Krakens OHLC-format, minst 300 sammanhängande avslutade
femminutersstaplar. Ett separat CLI testar befintliga regelstrategier:

```bash
python3 -m backend.ops research-test historik.json --strategy Trend --idea IDE-ID
```

70 % av historiken utgör första testperiod; 30 % är separat testperiod. Signalen
använder bara föregående avslutade staplar och simulerad order använder nästa
stapels öppningskurs. Avgift 0,4 % och prisglidning 0,1 % per sida, kvantitetssteg
och minbelopp redovisas. Datahash och parametrar sparas i rapporten. Inga optimerade
parametrar eller godtyckliga nya strategier aktiveras från texten. Resultatet är
alltid liveEligible=false. Testmodellen saknar bland annat faktisk orderkö.

Automatisk YouTube-transkribering, autonom internetresearch, generering av nya
strategier och anslutning till svenska aktier ingår **inte** i denna implementation.
Det finns inga dolda anslutningar eller påstått självlärande. Sådana anslutningar
behöver valda källor, datarättigheter och separata verifierade adaptrar.
