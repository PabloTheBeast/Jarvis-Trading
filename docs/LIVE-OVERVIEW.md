# Estman Investments · liveöversikt

Översikt visar endast verkligt Kraken-konto och verifierad Estman-historik. Ingen
insättning behövs för marknadsbevakningen. Detta ändrar inte orderrättigheter eller
startar en handelssession.

## Datakällor och uppdatering

- Webbläsaren läser `/api/overview` varannan sekund, separat från kommandon och
  andra flikar. Endpointen använder cachade snapshots och gör inga börsanrop.
- Kraken REST-marknad uppdateras normalt cirka var 15:e sekund; konto och öppna
  order cirka var 60:e sekund, även när webbläsaren är stängd. En aktiv session och
  valbar privat händelseström kan ge tätare kontokontroller.
- Knappen **Uppdatera kontot** gör en uttrycklig läskontroll och orderavstämning.
  Den skickar inga köp, försäljningar eller annulleringar.
- Server, konto och marknad har separata statusmarkeringar. Tidsstämplar visas i
  Stockholmstid; ålder räknas med serverns tid och webbläsarens monotona klocka.
- Konto äldre än 90 sekunder, marknad äldre än 30 sekunder, framtida/okänd tid eller
  ett avbrott i översiktens anrop visas som ej aktuellt. Senast kända värden markeras.
  Saknat konto visas som **—**, ett verifierat nollsaldo som **0,00 EUR**.

Detta är periodisk bevakning, inte en garanti för tick-för-tick-data. Valbar
WebSocket visas separat; orderböcker och saldon verifieras fortfarande via REST.

## Vad beloppen betyder

Kontovärde = verkligt EUR-saldo + verklig BTC/ETH-mängd värderad till senast
verifierat köpbud i EUR, före eventuell framtida säljavgift. Saknad kurs för ett
icke-noll innehav gör totalsumman okänd i stället för att värdera innehavet till noll.

Tillgängligt EUR är Kraken-saldot efter börsens orderreservationer. Den interna
vinstreserven visas separat. EUR efter intern reserv är ett tillgänglighetsmått,
inte ett godkänt handelsmandat eller ett automatiskt fördelat kapital.

Dagens realiserade netto beräknas från kända FIFO-matchade Estman-avslut, med köp-
och säljavgifter, för kalenderdagen i Europe/Stockholm. Det är inte hela kontots
insättningsjusterade avkastning eller komplett extern handelshistorik. Saknade
inköp och ofullständig avslutshistorik markeras. Orealiserat resultat visas bara
när registrerad kostnadsbas motsvarar hela innehavets mängd; annars är det okänt.

## Order, agenter och CHEF

- Öppna Kraken-order och lokala okända/ej avstämda utfall visas. Samma kända
  börsorder visas inte dubbelt som en separat lokal order. Högst 100 öppna
  orderdetaljer visas, med kontots fulla antal separat.
- Senaste tolv verkliga avslut visar börsens tid, agent, mängd, pris och avgift.
  Signaler och ofyllda order räknas aldrig som avslut.
- GAMMA, ZETA och IOTA visar livebehörighet, planerad/aktiv budget och rapporter.
  Aktuell marknad betyder inte automatiskt att strategisignalen är färsk.
- CHEF visar sessionstillstånd, orsak, gränser och planerad sluttid. Kontrollerna
  för signerad start, stopp och valbar efterbevakning finns kvar i Översikt. En
  stoppknapp finns även överst, så avslut kan begäras utan att skrolla till planen.

Ingen ny handelsstrategi, finansierad börsverifiering eller börslagrad skyddsorder
har lagts till i detta steg. Tidigare begränsningar i AGENT-CONTROL.md gäller.

## Verifiering

168 enhets-/API-tester passerar. Nya regressionsfall omfattar noll kontra saknat
saldo, värdering av innehav utan kostnadsbas, saknade kurser, gamla/framtida tider,
reserv kontra budget, okända order, dubbletter och dagens netto i Stockholmstid.
Chromium testar översiktens avbrott, gamla uppgifter, nollsaldo, verkliga ordervyer,
mobilbredd och JavaScript-fel med simulerade börssvar. Befintliga sessions-,
historik- och legacy-webbkontroller passerar. Inga riktiga order skickas i testerna.
