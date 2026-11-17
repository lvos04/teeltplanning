# Back-up en herstel

Zoals Gewichtsverwachting. De app maakt automatisch een lokale back-up (instelling *Systeem →
Back-upinterval*, start 24 uur) en vóór elke schemamigratie van een bestaande database. Een back-up is een zip
met een consistente SQLite-kopie (`app.db`, via de backup-API en `PRAGMA integrity_check`), de bronbestanden
uit `/data/bron/` en een SHA-256-manifest. Back-ups staan in `/data/backups/` en worden niet opnieuw in een
back-up opgenomen. Bewaartermijn: *Systeem → Bewaartermijn back-ups* (0 = onbeperkt; advies 30 dagen); de
nieuwste blijft altijd staan.

**Beheer → Back-up en export** toont aantal, totale grootte, nieuwste, vrije schijfruimte en adviezen.
Downloaden en terugzetten vragen opnieuw om het wachtwoord (en de MFA-code als die aanstaat), net als de
volledige export.

Een back-up staat op dezelfde dataset als de app. Laat TrueNAS (Cloud Sync of Replication) een kopie op een
andere locatie maken.

## Terugzetten via Beheer

1. **Beheer → Back-up en export → Back-up terugzetten**: kies het zip-bestand en bevestig met je wachtwoord.
2. De app controleert manifest, checksums, databaseintegriteit en verwijzingen en zet de back-up klaar in
   `/data/herstel/klaar/`. Een beschadigde back-up wordt afgekeurd en verandert niets.
3. **Herstart de app** (TrueNAS → Apps → teeltplanning → Restart). Bij het starten worden de huidige bestanden
   verplaatst naar `/data/herstel/oud-<tijd>/` en de back-up op zijn plaats gezet; daarna draaien de migraties.

## Offline terugzetten (opdrachtregel)

Naar een **nieuwe, lege map**, zonder een draaiende database te overschrijven:

```bash
python -m teeltplanning.backups restore /pad/teeltplanning-…zip /pad/nieuwe-map
```

Start daarna dezelfde imagetag met de dataset op die map (eerst op een andere poort om te controleren).

## Volledige export

Alle bedrijfstabellen als CSV (puntkomma, UTF-8 met BOM, Excel-leesbaar; cellen die met `=`, `+`, `-` of `@`
beginnen krijgen een apostrof) plus `instellingen.json`. Zonder wachtwoordhashes, sessies en MFA-geheimen.

## Noodbeheer

Telefoon met de authenticator kwijt en geen andere beheerder: in de container
`python -m teeltplanning.beheer_cli mfa-uit <gebruikersnaam>`.

## Vóór een update

Maak een back-up, noteer de huidige imagetag, zet de nieuwe vaste tag. Terugrollen naar een oudere image: herstel
ook de bijpassende back-up (een oudere image direct op een nieuwer schema openen is geen veilige rollback).

## Geheime sleutels

`/data/geheim.key` versleutelt de bronsleutels (zoals KNMI) en zit in de back-up. Wordt een back-up teruggezet
zonder dit bestand, of gaat het verloren, vul de sleutels dan opnieuw in bij Beheer → Bronnen.
