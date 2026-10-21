# Verifiering av v0.5

Utförd 2026-10-21 i molnmiljön med Python 3.12.14 och Node 24.19.0.

- 31 enhets-/API-tester passerade, inga överhoppade tester.
- 500-stegstest körde verkliga strategi-/risk-/ordervägar med kontrollerade priser
  och avstämd kapitalidentitet.
- JavaScript-syntaxkontroll och git diff --check passerade.
- Chromium-smoke verifierade flikar, kapitalinsättning, riskpaus, journal,
  serverstart, stängd sida, återanslutning i mobilstorlek och nödstopp utan JS-fel.
- Publika Kraken EUR-priser och ECB EUR/SEK hämtades med TLS-verifiering.
  ECB:s referensdatum var 2026-10-21.
- Verklig server körde 12 steg inklusive automatisk bakgrundskörning utan dashboard.
  Motorn stoppades sedan och startades om som process. Tickräknare, saldo och
  pausstatus återställdes; ett nytt Kraken/ECB-steg gav steg 13.
- Inga riktiga order skickades. Kort verklig körning verifierar data och drift,
  inte strategiavkastning eller långvarig lagringskapacitet.

Upprepa offlinekontrollerna med kommandona i README. Browser-smoke behöver
Playwright och Chromium och är separat från den beroendefria CI-sviten.
Verifiering i en ny publicerad molnuppgift har inte utförts här.
