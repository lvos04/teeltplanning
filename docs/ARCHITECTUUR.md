# Architectuur

Zelfde stack als Gewichtsverwachting (BOUWPLAN §3): Python 3.12, FastAPI, SQLAlchemy 2, Alembic, SQLite (WAL),
pydantic 2, openpyxl, defusedxml; één container, één Uvicorn-proces (`--workers 1 --no-proxy-headers`), poort
8000, gebruiker 568:568, alle persistente gegevens onder `DATA_DIR=/data`, `TZ=Europe/Amsterdam`.

## Mappen

```
teeltplanning/
  __init__.py      # versie (enige bron)
  main.py          # FastAPI-app
  kern/            # zuivere rekenkern (vanaf fase 1): geen FastAPI, geen SQLAlchemy, geen I/O
  importers/       # Excel-importers (vanaf fase 1)
  web/, db/        # schermen en database (vanaf fase 1)
proeven/           # fase 0: proef 1 (duurcellen) en proef 2 (sjabloon vullen); meegeleverd in de image
                   # zodat ze op de NAS tegen de echte bestanden kunnen draaien
tests/             # pytest; tests/synthetisch.py maakt de synthetische werkboeken
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
