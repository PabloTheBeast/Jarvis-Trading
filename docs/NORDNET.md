# Svenska aktier och Nordnet

Nordnet är önskad källa för svenska aktier. Den är **inte ansluten** i JARVIS ännu.
Ett vanligt konto är inte bevis på beviljad API-åtkomst eller rätt att hämta och
visa realtidskurser i en extern applikation. Inga Nordnet-lösenord, BankID-koder
eller sessionskakor ska matas in i JARVIS.

Nordnet publicerar [officiella API v2-exempel](https://github.com/nordnet/next-api-v2-examples).
Deras README hänvisar till [Nordnets tjänstesida](https://www.nordnet.se/se/tjanster/handelsapplikationer#nordnet-api).
Vid kontroll 2026-10-21 kunde de officiella GitHub-källorna läsas, men Nordnets
webbplats var blockerad i utvecklingsmiljön. Aktuella kundkrav, tillgänglighet,
avgift och marknadsdatavillkor har därför inte verifierats.

Nästa steg är att bekräfta med Nordnet:

- Om användaren kan få officiell API-åtkomst för sitt konto och ett externt Python-program.
- Hur autentisering och separat läsbehörighet fungerar i dagens API.
- Om Nasdaq Stockholm-kurser är realtid eller fördröjda, vilka instrument som täcks
  och vilka licens-/abonnemangsvillkor som gäller.
- Om kontosaldo och innehav kan läsas utan orderbehörighet.

Därefter kan en separat adapter byggas och testas för instrumentidentifiering,
SEK-kurser, tidsstämplar, marknadsstatus och fördröjningsmarkering. Den ska inte
skrapa inloggade webbsidor eller använda det äldre, uttryckligen deprecated
`nordnet-next-api`-biblioteket som om det vore en aktuell stödd integration.

Tills tillgången bekräftats förblir svenska instrument tydligt syntetiska.
Krakens kryptodata kan bevakas automatiskt oberoende av denna anslutning, utan
finansierat konto. Eventuell framtida Nordnet-orderläggning och CHEF:s livebudgetar
kräver ett separat genomförande; en insättning ska aldrig automatiskt aktivera dem.
