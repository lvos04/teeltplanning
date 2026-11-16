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
