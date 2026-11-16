# Wijzigingen

## 0.1.0 — fase 0: proeven en vragen

* Lege app met `/health`; image (gebruiker 568, healthcheck, LibreOffice Calc zonder scherm).
* CI: lint, opmaak, tests, docker build + rooktest; image naar `ghcr.io/lvos04/teeltplanning`; automatische release.
* Synthetische testwerkboeken (`tests/synthetisch.py`).
* Proef 1 (duurcellen) en proef 2 (sjabloon vullen + pdf), zie STATUS.md.
* OPEN_VRAGEN.md.

## 0.2.0 — fase 1: rekenkern, import en raster

* Rekenkern `kern/`: kalender (ISO-weken, week 53), datumketen per kap, aantallen, `bereken_plan` (±25 ms voor
  34 × 40 teelten). Geen imports van buiten `kern/` en geen harde getallen (tests).
* Instellingenregister (`teeltplanning/instellingen.py`) met alle groepen uit §4.3; scherm *Beheer →
  Instellingen* wordt eruit gegenereerd (zoeken, startwaarde, terug naar startwaarde, ingangsdatum, audit,
  export/import als JSON).
* Importer `Teeltprogramma maken` met controlevoorbeeld, ISO-jaar per ronde, bevestigen in één transactie,
  idempotent, handmatige waarden blijven bij herimport; vergelijkingsrapport met de werkboekcache (52-wekenfout).
* Schermen Ronde (raster met toetsenbordbediening), Kap (ketting met tijdbalk), Teelt (drie lagen); Beheer voor
  kappen en rassen (CSV), gebruikers, MFA, auditlog, back-up/herstel/export.
* Beveiliging overgenomen uit Gewichtsverwachting (zie TRUENAS.md).
* Database: migratie `0001`.

Nieuwe instellingen: zie *Beheer → Instellingen* of `teeltplanning/instellingen.py`; alle startwaarden staan in
het register, onbekende waarden zijn `null` met een verwijzing naar OPEN_VRAGEN.md.

## 0.3.0 — fase 2: afdrukken en teeltkaart

* Sjabloonbeheer (Beheer → Sjabloon): eigen werkboek uploaden naar `/data/sjabloon/`, controle van bladen,
  afdrukbereik, lettertypen (uploaden naar `/data/lettertypen/`) en niet-gevulde formules; controleafdruk met
  veldnamen.
* Teeltkaart volgens §9.2 (`kern/teeltkaart.py`): meetmomenten, LD-dag, streeflengte per seizoen van de plantweek.
* Afdrukken (scherm *Afdrukken*): rondeblad en teeltkaart als Excel (gevuld sjabloon, route A) en als pdf via
  LibreOffice in de container (route B), alle 40 kaarten in één bestand (20 bladen), kaart op te vragen met alleen
  kap en ronde; paginacontrole; bij een mislukte pdf een melding en het Excel-bestand.
* Controle tegen het werkboek: `python -m teeltplanning.afdruk.controle` (tien steekproefkaarten via LibreOffice
  herberekend) en overleggen als afbeelding (tests).
* Nieuwe instellingen in de groep Afdrukken (afdrukbereik, richting, papier, celtoewijzing teeltkaart, tijdslimiet
  120 s, metrisch gelijke lettertypen, …). Validatie van celverwijzingen en toegestane velden in toewijzingen.
* Database: migratie `0002` (tabel `sjabloon`).
* CI: poppler-utils voor pdf-tekst en overleggen; rooktest met pdf-conversie in de container.

## 0.4.0 — fase 3: licht, energie en dichtheid

* Lampgroepen (§6.4) als beheertabel met de vijf bevestigde groepen; maanden onbekend (vraag 1).
* Importer *Belichting winterronde* (§7.2) als achtergrondtaak: weekuren, kaptabel (LBE-ijkpunten),
  kostenvergelijking, teeltschema en het tabblad data streamend → dimgewogen uren per dag, met meldingen over
  ontbrekende intervallen, dubbele tijdstempels, volgorde en zomer-/wintertijd.
* Weerbronnen (§10), standaard uit: Open-Meteo (historie, verwachting), weeknormalen P10/P50/P90, KNMI (sleutel
  versleuteld, verbindingstest; inlezen wacht op B23), EnergyZero (dagprijzen). Scherm Beheer → Bronnen.
* Rekenkern: lichtsom zon + lampen (§6.3), LBE-keuze met terugval, dichtheidsvoorstel en gewichtsprognose (§6.5),
  energie en kosten (§6.6), scenariovergelijking; ontbrekend licht = onbekend met reden.
* Schermen: Energie (per week, per kap per week), Scenariovergelijking, Beheer → LBE en dichtheid (kalibratie,
  ijkpunten, dichtheidsreeks), teeltpagina met licht/LBE/energie.
* Nieuwe instellingen in Licht, Gewas, Energie, Import en Bronnen. Database: migratie `0003`.
* Nieuwe afhankelijkheid: `cryptography` (versleutelde bronsleutels). Image 830 MB.
