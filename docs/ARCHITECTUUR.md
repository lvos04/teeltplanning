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

## Fase 2: afdrukken

```
teeltplanning/
  xlsxpakket.py      # XLSX-pakket bewerken zonder openpyxl-opslag: waarden in werkblad-XML, formules bevriezen,
                     # afdrukinstellingen, afdrukkopie met alleen de gevraagde bladen, bladen kopiëren
  beeld.py           # pdf → grijswaardenafbeelding (pdftoppm) en pixelvergelijking (controle, niet in de image)
  kern/teeltkaart.py # meetmomenten, LD-dag, seizoen, streeflengte, behandelingen (zuiver)
  afdruk/kaart.py    # instellingen → KaartInstellingen; kaart/rondeblad → cel → waarde
  afdruk/dienst.py   # plan → gevuld sjabloon (route A) → pdf (route B), paginacontrole
  afdruk/pdf.py      # LibreOffice-conversie: eigen profiel, tijdslimiet, één tegelijk, geen netwerk, lettertypen
  afdruk/sjabloon.py # sjabloon uploaden/controleren, lettertypen
  afdruk/controle.py # het werkboek in LibreOffice herberekenen voor N kaarten en vergelijken; CLI
  web/routes_afdruk.py
```

| Onderwerp | Besluit | Reden |
|---|---|---|
| Afdrukkopie | Alleen de af te drukken bladen in `workbook.xml`; andere werkbladen, externe koppelingen, draaitabelcaches en namen eruit; formules in de gevulde bladen bevroren of leeggemaakt | LibreOffice drukt ook verborgen bladen af (proef 2); formules zouden naar weggelaten bladen wijzen |
| Formules die de app niet vult | Standaard **leegmaken** (instelling), en in Beheer → Sjabloon gemeld | een gecachte waarde uit het sjabloon hoort bij de kap die er toevallig in stond |
| Celtoewijzing | Rondeblad = kolomtoewijzing van de importer; teeltkaart = eigen toewijzing in het register, tweede kaart = verschuiving in kolommen | importeren en afdrukken gebruiken dezelfde cellen; Leon kan een verplaatste cel zelf aanpassen |
| Netwerk bij conversie | `unshare -rn` als het mag, anders een proxy naar `127.0.0.1:9`; externe koppelingen zitten niet in de kopie | in de container (Docker-standaardprofiel) is een eigen netwerknaamruimte niet toegestaan |
| Controle tegen het werkboek | Het werkboek zelf door LibreOffice laten herberekenen (met `fullCalcOnLoad` en een profiel dat altijd herberekent) | een onafhankelijke rekenweg; geen eigen formule-interpreter |

## Fase 3: licht, energie en dichtheid

```
teeltplanning/
  kern/licht.py        # lichtsom (Rekenaar met prefixsommen per kap), lampgroepen, weeknormalen
  kern/energie.py      # kWh, kosten, per kap per week, scenariovergelijking
  kern/gewas.py        # + LBE-keuze, dichtheidsvoorstel, takgewicht, afronden
  voorstel/lbe.py      # kalibratie parameter_lbe, dichtheidsreeks uit de historie, ijkpunten belichtingsblad
  importers/duur.py, belichting.py, belichting_dienst.py   # §7.2, achtergrondtaak
  bronnen/http.py, open_meteo.py, knmi.py, energyzero.py, kluis.py, dienst.py
  web/routes_licht.py
```

| Onderwerp | Besluit | Reden |
|---|---|---|
| Prijsscenario (§5 `prijsscenario`) | De instellingen *Stroomprijs voor de planning* en *Opslagen en belasting* **met ingangsdatum** vormen samen het prijsscenario per periode; geen aparte tabel | het register heeft al reeksen met ingangsdatum, audit en export |
| Lampgroep ↔ kap | Eén regel per groep met `kap` leeg (= alle kappen) en `module_van`; module voor kap k = module_van + k − 1. Een regel voor één kap kan ook | 40 regels per groep is onnodig; afwijkende kappen blijven mogelijk |
| Onbekende periode | `geldig_vanaf` leeg = onbekend: de groep maakt de lichtsom onbekend in plaats van te worden genegeerd | niets verzinnen (§0.2) |
| EnergyZero | De openbare prijzen-API rechtstreeks via `bronnen/http.py` in plaats van het pakket `energyzero` | het pakket is async (aiohttp); zo één HTTP-laag met één nep-HTTP-testpunt en geen extra afhankelijkheden |
| KNMI | Alleen sleutel (versleuteld) en verbindingstest; waarden inlezen staat uit | variabele, eenheid en formaat (NetCDF 4) niet geverifieerd (B23); geen NetCDF-lezer in de image |
| Geheimen | Fernet met `DATA_DIR/geheim.key`, zoals het Bedrijfsdashboard | sleutels nooit in het register, de export of het auditlog |
| Belichtingsimport | Lezen als achtergrondtaak (één tegelijk); de dagaggregaten als JSON naast het origineel; alleen de laatst gestarte leesbeurt mag het voorbeeld schrijven | het tabblad data is groot; bevestigen hoeft het niet opnieuw te lezen |
| Rekentijd | Per kap prefixsommen over het hele plan; snelle weg voor het gewone geval (verleden, volledige dagdata); een globale controle slaat het dagwerk over als alles toch onbekend is | hele plan met licht < 200 ms (test) |
