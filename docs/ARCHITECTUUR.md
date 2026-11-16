# Architectuur

Zelfde stack als Gewichtsverwachting (BOUWPLAN §3): Python 3.12, FastAPI, SQLAlchemy 2, Alembic, SQLite (WAL),
pydantic 2, openpyxl, defusedxml; één container, één Uvicorn-proces (`--workers 1 --no-proxy-headers`), poort
8000, gebruiker 568:568, alle persistente gegevens onder `DATA_DIR=/data`, `TZ=Europe/Amsterdam`.

## Mappen

```
teeltplanning/
  __init__.py      # versie (enige bron)
  main.py          # FastAPI-app: migraties, startgegevens, beveiligingskoppen, verzoekgrens 1 MB, routes
  instellingen.py  # het instellingenregister (§4.3): definities, validatie, opslag met ingangsdatum, export/import
  planning.py      # planningsdienst: database ⇄ kern, cache (vervalt bij elke relevante wijziging), velden wijzigen
  auth.py, mfa.py  # aanmelden, sessies, rollen, oorsprongcontrole, aanmeldbeperking, audit; TOTP (uit Gewichtsverwachting)
  backups.py       # back-up, controle, klaarzetten en bij start toepassen van een herstel
  onderhoud.py     # dagelijkse opschoning, automatische back-ups, back-upstatus
  beheer_cli.py    # noodbeheer (MFA uitzetten)
  kern/            # zuivere rekenkern: typen, kalender, keten, gewas (aantallen), plan.bereken_plan
  voorstel/        # (fase 3–4) modellen voor voorstellen
  importers/       # werkboek.py en kalenderhulp.py (gekopieerd uit Gewichtsverwachting), programma.py
                   # (§7.1, alleen lezen), vergelijking.py (cache tegenover eigen keten), dienst.py (importstroom)
  afdruk/          # (fase 2) sjabloon vullen
  bronnen/         # (fase 3/6) externe bronnen, standaard uit
  db/              # basis.py (engine, WAL), modellen.py, migraties/ (Alembic, vanaf 0001)
  web/             # routes_*.py, templates/ (Jinja2), static/ (app.css, raster.js, htmx.min.js)
proeven/           # fase 0: proef 1 (duurcellen) en proef 2 (sjabloon vullen); meegeleverd in de image
                   # zodat ze op de NAS tegen de echte bestanden kunnen draaien
tests/             # pytest; tests/synthetisch.py maakt de synthetische werkboeken; browser_rooktest.py
```

## Beslissingen

| Onderwerp | Besluit | Reden |
|---|---|---|
| Sjabloon vullen (§9.3 route A) | Waarden rechtstreeks in de werkblad-XML schrijven; overige pakketonderdelen ongewijzigd | openpyxl laden + opslaan herschrijft stijlen en alle werkbladen en laat afbeeldingen/vormen vallen (proef 2) |
| Pdf (§9.3 route B) | LibreOffice Calc zonder scherm in de image; afdrukkopie met alleen het af te drukken blad | LibreOffice drukt verborgen bladen ook af (proef 2) |
| Lettertypen | Liberation en Carlito/Caladea in de image; ontbrekende lettertypen later uploadbaar | metrisch gelijk aan Arial/Calibri; welke het werkboek gebruikt is open vraag 13 |
| Gedeelde importcode | De parsers uit Gewichtsverwachting worden in fase 1 **gekopieerd** naar `teeltplanning/importers/` met bronvermelding. **Kandidaat om later als gedeeld pakket te delen**; nu niet, om beide apps los te kunnen uitbrengen | §2 |
| Solver | Geen externe solver; **OR-Tools CP-SAT** is de kandidaat als er harde eisen bijkomen (bijv. een arbeidsplafond per dag) | §8.2 |
| Frontend | Server-gerenderde Jinja2-pagina's, HTMX (lokaal meegeleverd, 0BSD-licentie) en een klein eigen script; geen Node-bouwstap; geen Handsontable/HyperFormula | §3 (licenties) |
| Afgeleide waarden | Niet opgeslagen; bij elke vraag berekend met `bereken_plan` en gecachet tot de volgende relevante wijziging (ook na een teruggedraaide transactie vervalt de cache) | §4.1 |
| Herimport | Een handmatige waarde onthoudt zijn bron (`gebruiker` of `import:<nr>`); een herimport overschrijft alleen waarden die van een import kwamen | §4.2, fase 1 |
| Instellingen | Eén regel per wijziging in tabel `instelling` (met optionele ingangsdatum); de nieuwste geldt; "terug naar startwaarde" is ook een regel, zodat de historie compleet blijft | §4.3 |
| Herstel via Beheer | Gecontroleerd klaarzetten, toepassen bij de volgende start (nooit over een draaiende database) | §12 |
