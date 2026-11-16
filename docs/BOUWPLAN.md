# Bouwplan Teeltplanning — opdracht voor Claude Code

Versie 1.1 · 16 november 2026 · opdrachtgever: Leon (chrysantenteler, 40 kappen, rassen Antonov/Pjotr/Tutu)

Dit document is de volledige bouwopdracht. Lees het helemaal voordat je iets aanmaakt. Het achtergrondonderzoek
met bronnen staat in `Teeltplanning app chrysant onderzoek.md`; dit plan is daaruit afgeleid en is leidend bij
tegenstrijdigheden. Zet beide bestanden in `docs/` van de nieuwe repo.

---

## 0. Zo werk je

1. **Eén fase per branch en per pull request.** Bouw fase 1 af (inclusief tests en documentatie) voordat je aan
   fase 2 begint. Stop na elke fase en rapporteer: wat werkt, wat getest is, wat niet gedaan is.
2. **Verzin niets.** Ontbreekt een waarde (rasparameter, prijs, datum, offset), gebruik dan `null`, toon
   "onbekend" en zet de vraag in `docs/OPEN_VRAGEN.md`. Nooit een aangenomen getal als feit opslaan.
3. **Alles in het Nederlands**: schermteksten, foutcodes, documentatie, commitberichten, namen van datasets.
   Code-identifiers mogen Nederlands of Engels zijn, maar consequent binnen één module.
4. **Bedrijfsgegevens komen nooit in de repo.** De twee Excel-werkboeken, Priva-exports en alles wat daaruit is
   afgeleid blijven op de dataset van de NAS. Tests gebruiken synthetische werkboeken die je zelf genereert met
   dezelfde structuur. Tests die het echte werkboek nodig hebben, slaan zichzelf over als het bestand ontbreekt
   (pad via omgevingsvariabele `TEELT_ECHT_PROGRAMMA` en `TEELT_ECHT_BELICHTING`).
5. **Volg de bestaande apps.** Bij twijfel over opzet, beveiliging, migraties, CI of documentatie: kijk hoe
   `lvos04/gewichtsverwachting` het doet en doe hetzelfde.
6. **Alles is instelbaar.** Geen harde getallen, lijsten of regels in de code; zie §4.3.
7. **Elke externe verbinding staat standaard uit** en is in Beheer aan te zetten. De app moet volledig werken
   zonder internet en zonder GPU.

---

## 1. Wat er gebouwd wordt

Een zelf-gehoste webapp "Teeltplanning" die als standaard TrueNAS Custom App draait en het Excel-werkboek
`Teeltprogramma maken` vervangt. De app:

- maakt automatisch een teeltplanning voor 40 kappen over meerdere rondes;
- stelt per teelt voor: plantdatum en vakvolgorde, dagen vegetatief (LD), reactietijd, plantdichtheid, en een
  gewichts- en oogstprognose;
- laat elke voorgestelde waarde handmatig overschrijven en rekent dan alles wat ervan afhangt direct door;
- rekent licht en energie door (vervangt het rekenwerk dat nu via Delphy QMS gaat);
- stuurt op afzetpieken (feestdagen) en houdt rekening met weer (straling);
- drukt het rondeblad en de teeltkaart af in hetzelfde format als het huidige werkboek;
- wisselt gegevens uit met de andere bedrijfsapps via de Bedrijfskoppeling API v1.

### Rolverdeling met Gewichtsverwachting

| App | Eigenaar van |
|---|---|
| **Teeltplanning** (nieuw) | het plan: rondes, geplande teelten, voorstellen, scenario's, licht- en energieprognose |
| **Gewichtsverwachting** (bestaat) | de realisatie: werkelijke teelten, KD10/KD30-metingen, oogsten, gewichtsprognose op basis van metingen, Priva-branduren |

Een teelt wordt nergens dubbel ingevoerd. Teeltplanning leest realisatie via de koppeling; Gewichtsverwachting
leest het plan via de koppeling (fase 6).

---

## 2. Repo's en koppelingen

| Repo | Rol in dit project | Wat je er doet |
|---|---|---|
| `lvos04/teeltplanning` | **nieuw**, hier bouw je | alles |
| [`lvos04/gewichtsverwachting`](https://github.com/lvos04/gewichtsverwachting) | referentie voor stack, beveiliging, importadapters; bron van realisatie | lezen; in fase 6 een kleine PR (zie §11.3) |
| [`lvos04/bedrijfsdashboard`](https://github.com/lvos04/bedrijfsdashboard) | bron van het koppelcontract (`docs/koppeling-api.md`); afnemer van de nieuwe app | lezen; in fase 6 een kleine PR (zie §11.3) |
| `lvos04/scouten`, `lvos04/tuinwerk-os`, `lvos04/Milieu-registratie-leon`, `lvos04/onderhoud` | andere bedrijfsapps | niet aanraken, behalve de sluglijst in hun `docs/koppeling-api.md` als Leon daarom vraagt |

De actuele ontwikkelbranch in de bestaande repo's is `ccr-f5cf979e-9oiocr` (er staan pull requests voor open).
Controleer eerst of die al naar de hoofdbranch zijn samengevoegd en lees de nieuwste stand.

**Lees vóór je begint in `gewichtsverwachting`:** `Dockerfile`, `requirements.txt`, `constraints.txt`,
`.github/workflows/`, `app/koppeling.py`, `app/legacy_program.py`, `app/legacy_cultivation.py`,
`app/legacy_lighting.py`, `app/auth.py`, `app/mfa.py`, `app/backups.py`, `docs/TRUENAS.md`, `docs/EXCEL.md`,
`docs/koppeling-api.md`, `tests/test_koppeling.py`.

**Hergebruik van importcode.** Neem de parsers uit `legacy_program.py` en de week/dag-hulpfuncties uit
`legacy_cultivation.py` (`iso_date`, `next_week_date`, `group_plant_years`) over in de nieuwe repo als
`teeltplanning/importers/`, met bronvermelding in de modulekop. Maak nu geen gedeeld pakket; noteer in
`docs/ARCHITECTUUR.md` dat dit de kandidaat is om later te delen.

---

## 3. Stack en repo-opzet

Zelfde stack als Gewichtsverwachting, tenzij hier anders staat:

- Python 3.12, FastAPI, SQLAlchemy 2, Alembic, SQLite (WAL), pydantic 2, openpyxl, defusedxml.
- Eén container, één Uvicorn-proces (`--workers 1 --no-proxy-headers`), containerpoort 8000, gebruiker 568:568,
  alle persistente gegevens onder `DATA_DIR=/data`, `TZ=Europe/Amsterdam`, healthcheck op `/health`.
- Frontend: server-gerenderde pagina's (Jinja2) met HTMX voor gedeeltelijke verversing en een klein eigen
  script voor celbewerking en toetsenbordnavigatie. **Geen Handsontable en geen HyperFormula** (licenties staan
  bedrijfsgebruik niet gratis toe). Geen bouwstap met Node.
- Rekenkern als zuiver pakket zonder web- of databasecode.

```
teeltplanning/
  kern/            # zuivere rekenkern: geen FastAPI, geen SQLAlchemy, geen I/O
    kalender.py    # ISO-week/dag <-> datum, weergave "w-d"
    keten.py       # datumketen per kap
    licht.py       # lichtsom zon + lampen
    gewas.py       # LBE, dichtheid, gewicht, aantallen
    energie.py     # vermogen, kWh, kosten
    afzet.py       # afzetmomenten en terugrekenen
    plan.py        # bereken_plan(): alles samen
    typen.py       # dataclasses voor invoer en uitvoer
  voorstel/        # parameters en modellen die voorstellen leveren
  importers/       # Excel-importers (programma, belichting, teeltschema)
  bronnen/         # KNMI, Open-Meteo, feestdagen, energieprijs, koppeling-client
  afdruk/          # sjabloon vullen, teeltkaart
  web/             # routes, templates, static
  db/              # modellen, migraties
  koppeling.py     # Bedrijfskoppeling v1 aanbieden
tests/
docs/
```

De map `kern/` mag niets importeren uit de andere mappen. Dwing dat af met een test.

---

## 4. Kernprincipes van het ontwerp

### 4.1 Eén rekenfunctie

```python
def bereken_plan(invoer: PlanInvoer, parameters: Parameters) -> Plan: ...
```

- `PlanInvoer` bevat per teelt de **vastgezette** waarden (handmatig) en de **voorstellen**; `Parameters` bevat
  kappen, lampgroepen, rassen, weerreeksen, branduren en instellingen.
- De functie is deterministisch en heeft geen neveneffecten. Scherm, afdruk, koppeling, zoekfunctie en tests
  gebruiken allemaal deze ene functie.
- De database bewaart invoer, parameters en handmatige waarden. Afgeleide waarden worden berekend, niet als
  waarheid opgeslagen (cachen mag, mits herberekenbaar).
- Bij elke wijziging: opslaan, hele plan opnieuw berekenen, gewijzigde rijen terugsturen. Meet de rekentijd in
  een test; het hele plan (34 rondes × 40 kappen) moet ruim onder 200 ms blijven.

### 4.2 Drie lagen per veld

Voor elk van deze velden: plantdatum, LD-dagen, reactietijd, verlenging, tussenlicht, dichtheid, gewicht, uitval.

| Laag | Betekenis |
|---|---|
| voorstel | wat de app berekent of voorspelt, met bron, modelversie en bandbreedte |
| handmatig | optioneel; wat de gebruiker invult; **wint altijd** |
| effectief | handmatig als die er is, anders voorstel; hiermee rekent de kern |

- Een overschreven cel is zichtbaar gemarkeerd, toont het voorstel ernaast en heeft "terug naar voorstel".
- Importeren, hertrainen, weerdata verversen en de zoekfunctie mogen een handmatige waarde nooit wijzigen.
- Elke wijziging komt in het auditlog (wie, wanneer, oud, nieuw, optionele reden).

### 4.3 Alles instelbaar

**Geen enkele rekenwaarde, drempel, lijst of regel staat hard in de code.** Alles wat in dit plan een getal,
een keuze of een tabel is, is een instelling die de beheerder in de app kan wijzigen. De waarden in dit plan
zijn uitsluitend **startwaarden** bij een schone installatie.

Bouw hiervoor in fase 1 één instellingenregister (`teeltplanning/instellingen.py`) dat alle andere code gebruikt:

- Elke instelling heeft: sleutel, groep, label, uitleg in gewone taal, type, eenheid, startwaarde, toegestane
  grenzen of keuzes, en of `null` ("onbekend") is toegestaan.
- Het scherm **Beheer → Instellingen** wordt uit het register gegenereerd: per groep een pagina, met
  zoekfunctie, de startwaarde zichtbaar naast de huidige waarde, en "terug naar startwaarde" per instelling.
- Validatie aan de serverkant; een ongeldige waarde wordt geweigerd met een begrijpelijke melding.
- Elke wijziging komt in het auditlog (oud, nieuw, wie, wanneer) en leidt direct tot herberekening van het plan.
- Instellingen met een **ingangsdatum** waar dat inhoudelijk nodig is (lampgroepen, prijzen, oppervlak,
  transmissie), zodat historie met de waarde van toen wordt doorgerekend.
- Exporteren en importeren van alle instellingen als één JSON-bestand (zonder geheimen), en meenemen in de
  back-up.
- De rekenkern krijgt instellingen als parameter binnen; `kern/` leest zelf nooit uit de database.
- Een test faalt als er in `kern/`, `voorstel/` of `afdruk/` een getal als constante staat dat niet uit het
  register komt (uitgezonderd wiskundige constanten als 7 dagen per week, 24 uur en eenheidsomrekeningen die
  natuurkundig vastliggen; ook de omrekenfactor zon → PAR is wél een instelling).

Tabellen (rassen, kappen, lampgroepen, streeflengte, kaartmomenten, afzetmomenten, uitval per week, LBE- en
teeltparameters, prijzen, rasverdeling) krijgen elk een eigen beheerscherm met toevoegen, wijzigen, uitzetten
en importeren/exporteren als CSV.

Minimale lijst van instellingen (vul aan met alles wat je onderweg tegenkomt):

| Groep | Instellingen |
|---|---|
| Bedrijf | bedrijfsnaam, aantal kappen, oppervlak per kap, bedden per kap, vakgroepen, breedtegraad, KNMI-station |
| Kalender | toegestane plantdagen per week, toegestane oogstdagen, wisseltijd per kap, blokkadeperioden (stomen), weergaveformaat week-dag |
| Datumketen | dagen tussen licht uit en licht aan (start 12), grenzen voor LD-dagen, reactietijd en verlenging, gedrag bij overlap (waarschuwen/blokkeren) |
| Licht | omrekenfactor straling → PAR (start 0,023), transmissie (met ingangsdatum, per week mogelijk), minimale daglichtsom, bron voor toekomstige branduren (historisch gemiddelde/regel/handmatig), regelgrenzen voor afbouw van belichting |
| Lampen | per groep: type, intensiteit, rendement, kW per kap, dimbaar, modulenummer, geldig vanaf/tot |
| Gewas | doelgewicht per ras, toegestane dichtheden (reeks) en afronding, minimum aantal waarnemingen per LBE-cel, terugvalregel, uitval per week, definitie reactietijd (aftrek 2 en 3 dagen) |
| Voorstellen | per veld aan/uit, venster voor de seizoensbasis (start ±2 weken), terugkijkperiode in jaren, temperatuurcorrectie aan/uit met referentietemperatuur en gevoeligheid per ras, breedte van de bandbreedte |
| Model | welke treden actief zijn, kenmerken per trede, monotone beperkingen, drempel voor "gevalideerd", backtestperiode, automatisch hertrainen (interval of handmatig) |
| Energie | stroomprijs per periode, opslagen en belasting, kostenverdeling per tak (stek, pluizen en netten, overig), opbrengst per tak voor de scenariovergelijking |
| Afzet | per afzetmoment: datumregel, venster, belang, markt, actief; landen voor officiële feestdagen; markering van weken met lage prijs |
| Zoeken | gewichten van de vier scoreonderdelen, welke wijzigingen de zoekfunctie mag proberen en met welke stapgrootte, maximale rekentijd |
| Teeltkaart | streeflengtetabel, seizoensgrenzen in weken (start 43–52, 1–6, 7–36, 37–42), weekdagen van de meetmomenten (start ma/wo/vr), aantal momenten (start 16), offsets voor remstof, slak, pluizen en netten |
| Afdrukken | sjabloonbestand, celtoewijzing per veld, papierformaat en richting, pdf of Excel als standaard, kaarten per blad |
| Import | kolomtoewijzing per importer, groottegrenzen, controlegrenzen (zoals weekuren 0–168), tolerantie bij koppelen op plantdatum |
| Bronnen | per bron aan/uit, basis-URL, sleutel, ophaalinterval, tijdslimiet, aantal jaren historie voor weeknormalen, welke percentielen |
| Koppeling | bedrijfskoppeling aan/uit, verzoeken per minuut, welke datasets worden gedeeld, koppelbronnen en interval |
| Beveiliging | zoals Gewichtsverwachting: inlogpogingen per account en per IP, MFA verplicht, sessieduur, vertrouwde proxy's, bewaartermijn auditlog |
| Systeem | back-upschema en bewaartermijn, tijdzone, taal van getalnotatie, hostnaam voor veilige cookies |

Kolomtoewijzingen van de importers en de celtoewijzing van het sjabloon zijn ook instellingen: als Leon een
kolom in zijn werkboek verplaatst, moet hij dat in Beheer kunnen aanpassen zonder nieuwe versie.

### 4.4 Echte datums

Intern altijd `date`. Weergave en invoer in het bekende formaat `week-dag` (bijvoorbeeld `44-6`), met ISO-jaar
apart. Het werkboek rekent met 52 weken hard gecodeerd; ISO-jaar 2026 heeft 53 weken (week 53 = 28 december 2026
t/m 3 januari 2027). De app rekent dit goed en toont bij import waar het werkboek hierdoor afwijkt.

---

## 5. Datamodel

Alembic-migraties vanaf `0001`. Tabellen (kernvelden; voeg `id`, `aangemaakt_op`, `bijgewerkt_op` toe waar zinvol):

| Tabel | Kernvelden | Toelichting |
|---|---|---|
| `kap` | nummer (1–40), vakgroep (11, 12, 21, 22, 23, 31, …), oppervlak_m2, plantvolgorde, actief | oppervlak instelbaar; werkboek rekent met 726 m² (rondeblad) en 750 (kostenblok): vraag welke klopt |
| `lampgroep` | kap, naam, type (led/son_t), intensiteit_umol, rendement_umol_per_j, dimbaar, modulenummer, geldig_vanaf, geldig_tot | zie §6.4 voor de begindata |
| `ras` | code, naam, doelgewicht_g, temperatuurgevoeligheid_dagen_per_graad, actief | a/p/t/b = Antonov/Pjotr/Tutu/Baltazar; geen doelgewicht verzinnen |
| `scenario` | naam, status (concept/vastgesteld), afgeleid_van | er is altijd precies één vastgesteld plan |
| `ronde` | scenario, nummer, iso_jaar_eerste_planting, label | |
| `teelt` | ronde, kap, volgnummer (1–40), opmerking | één regel per kap per ronde |
| `teelt_ras` | teelt, ras, bedden | gemengde kappen: zes bedden per kap, code zoals `3a3p` |
| `veldwaarde` | teelt, veld, voorstel, ondergrens, bovengrens, bron, modelversie, handmatig, gewijzigd_door, gewijzigd_op, reden | drie-lagenprincipe |
| `parameter_lbe` | ras, plantweek, lichtregime, lbe_g_per_mol, aantal_waarnemingen, status | status: onvoldoende/indicatief/gevalideerd |
| `parameter_teelt` | ras, plantweek, basis_reactietijd, ld_dagen, uitval_fractie, aantal_waarnemingen | |
| `streeflengte` | ld_dag, seizoen (winter/voorjaar/zomer/najaar), cm | opzoektabel teeltkaart, zie §9.2 |
| `kaartmoment` | type, dag_offset, weekdagregel | meetmomenten ma/wo/vr, remstof, slak |
| `weerdag` | datum, straling_j_cm2, etmaaltemperatuur, soort (gemeten/verwachting/normaal), p10, p50, p90, bron | |
| `belichting_dag` | lampgroep of modulenummer, datum, branduren, dimgewogen_uren, kwaliteit, importbatch | |
| `afzetmoment` | naam, datumregel, venster_dagen_voor, gewicht, markt, actief | zie §8.1 |
| `prijsscenario` | geldig_vanaf, eur_per_mwh, opslagen | werkboek rekent met 80 en 300 €/MWh; beide als instelbare startwaarde vragen |
| `importbatch` | soort, bestandsnaam, sha256, status, meldingen, bevestigd_door | bronbestand blijft onder `/data/bron/` |
| `koppelsleutel`, `koppelbron` | volgens contract | eigen sleutels (aanbieden) en bronnen (afnemen; sleutel versleuteld opslaan zoals het dashboard doet) |
| `gebruiker`, `sessie`, `audit`, `instelling` | zoals Gewichtsverwachting | rollen beheerder/medewerker/lezer |

---

## 6. Rekenkern

### 6.1 Datumketen (`kern/keten.py`)

Uit het werkboek (rondeblad, rij 12–51):

```
licht_uit   = plantdatum + ld_dagen
oogst       = licht_uit + reactietijd + verlenging
teeltduur   = oogst − plantdatum                      (dagen)
licht_aan   = licht_uit + 12                          (tussenlicht, uit de teeltkaart)
licht_uit_2 = licht_aan + tussenlicht_dagen           (teeltkaart rij 7)
plant_volgende_teelt_in_kap = oogst + wisseltijd      (wisseltijd per kap instelbaar, standaard 0)
```

- Elke kap is een ketting van teelten. Een wijziging schuift alle latere teelten in die kap mee, tot aan de
  eerstvolgende teelt met een handmatig vastgezette plantdatum.
- In het werkboek verwijst de plantweek van de volgende ronde soms naar de bloeiweek van dezelfde regel in de
  vorige ronde (`='Ronde 2'!N12`), terwijl de plantdag los wordt ingevuld. Modelleer dat als: voorstel
  plantdatum = oogst vorige teelt + wisseltijd, afgerond naar de eerstvolgende toegestane plantdag (plantdagen
  per week instelbaar; standaard maandag t/m zaterdag).
- Signaleer overlap (plantdatum vóór oogst vorige teelt in dezelfde kap) als fout op de rij, maar blokkeer het
  opslaan niet.

### 6.2 Aantallen (`kern/gewas.py`)

```
geplant      = dichtheid × oppervlak_kap               (werkboek: M × 726)
oogstaantal  = geplant × (1 − uitval)                  (werkboek: Z − Y×Z)
```

Uitval per oogstweek komt uit het blad `productie cijfers` (8% in week 1–11, daarna 5%; importeer de hele tabel).
Maand van oogst en cumulatieven zijn afgeleide overzichten (blad `productie`: begroting tegenover laatste
schatting per maand); bouw die als rapport, niet als opgeslagen gegeven.

### 6.3 Lichtsom (`kern/licht.py`)

```
par_zon_binnen [mol/m²/dag] = straling [J/cm²/dag] × 0,023 × transmissie
par_lamp       [mol/m²/dag] = Σ over lampgroepen: intensiteit [µmol/m²/s] × dimgewogen_uren × 0,0036
lichtsom       [mol/m²]     = Σ (par_zon_binnen + par_lamp) over plantdatum … oogst
```

- `0,023` en `transmissie` zijn instellingen. Startwaarde transmissie: `null` met de vraag aan de gebruiker;
  bied een ijkfunctie aan zodra er gemeten PAR in de kas beschikbaar is.
- `dimgewogen_uren` = Σ (dimstand/100 × interval). Rekenen met "aan = volle intensiteit" is fout: de dimbare
  groep draait een groot deel van de tijd gedimd.
- Verleden: gemeten straling en geïmporteerde branduren. Toekomst: weeknormaal P50 voor straling (met P10/P90
  als band) en het eigen historische gemiddelde aan dimgewogen uren per kap en kalenderweek.
- Ontbreekt lichtdata voor een deel van de teelt, dan is de lichtsom `null` met een melding welke dagen missen.
  Geen gaten opvullen zonder dat de gebruiker dat ziet.

### 6.4 Lampgroepen: begingegevens

Door de teler bevestigd op 16 november 2026, tenzij anders vermeld. Leg vast met `geldig_vanaf`/`geldig_tot`;
laat de precieze maanden open (`null`) tot Leon ze bevestigt.

| Periode | Groep | Intensiteit | Vermogen per kap | Opmerking |
|---|---|---|---|---|
| tot circa zomer 2023 | SON-T volledig | circa 120 µmol (afgeleid, status "indicatief") | circa 50 kW | afgeleid uit het kostenblok: resterende helft = 1 MW over 40 kappen en 60 µmol |
| circa zomer 2023 – zomer 2024 | LED fase 1, modules 1–40 | 80 µmol | 20 kW (0,8 MW totaal) | |
| circa zomer 2023 – zomer 2024 | SON-T resterend (50%), modules 101–140 | 60 µmol | 25 kW (1 MW totaal) | het tabblad `belichting` (week 36 t/m 10) is waarschijnlijk winter 2023/24 |
| vanaf uiterlijk juli 2024 | LED fase 1, modules 1–40 | 80 µmol | 20 kW | staat niet in het tabblad `data`; bron voor branduren navragen |
| vanaf uiterlijk juli 2024 | LED fase 2, modules 101–140, **dimbaar** | 120 µmol | samen met fase 1: 47,5 kW (1,9 MW totaal) | ruwe 5-minutendata met dimstand in tabblad `data` vanaf 1 juli 2024 |

### 6.5 Dichtheid en gewicht (`kern/gewas.py`)

```
productie [g/m²]   = lbe(ras, plantweek, lichtregime) × lichtsom
takgewicht [g]     = productie / dichtheid
dichtheid_voorstel = lbe × lichtsom / doelgewicht
lbe_gerealiseerd   = gemiddeld_takgewicht × dichtheid / lichtsom      (per afgesloten teelt)
```

- De dichtheid is een **beslissing**: de app kiest de dichtheid die het doelgewicht raakt. Train geen model dat
  dichtheid vrij voorspelt.
- Rond het voorstel af op de dichtheden die in de praktijk voorkomen (leid de reeks af uit de historie; het
  werkboek gebruikt waarden tussen 41,1 en 58,6) en toon ook de niet-afgeronde waarde.
- LBE is geen constante: hij verschilt per ras, plantweek en lichtniveau. Gebruik de tabel `parameter_lbe`; bij
  minder dan het ingestelde minimum aan waarnemingen valt de cel terug op het seizoensgemiddelde van het ras en
  krijgt het voorstel de status "indicatief".
- Eenheidstests met gepubliceerde proefcijfers (moeten binnen 0,2 afwijking kloppen):
  `7,3 × 938 / 63,8 = 107,3 g` · `7,5 × 640 / 48,8 = 98,4 g` · `6,8 × 834 / 98 = 57,9 planten/m²`.
- Het tabblad `belichting` bevat vanaf rij 87 al een tabel per kap met LD/KD-dagen, uren LED en SON-T,
  gewichtsprognose en -realisatie, en **LBE prognose en LBE realisatie** (22 kappen; prognose 4,8–5,5, realisatie
  5,3–6,4). Importeer die als eerste ijkpunten en vergelijk de eigen berekening ermee.

### 6.6 Energie (`kern/energie.py`)

```
vermogen [W/m²]   = intensiteit / rendement            (of rechtstreeks: kW per kap uit lampgroep)
verbruik [kWh]    = kW_per_kap × dimgewogen_uren
kosten [€]        = verbruik × prijs
```

Geef per teelt, per kap per week en per week voor het hele bedrijf: kWh, kosten, en kosten per tak
(`kosten / oogstaantal`). Het werkboek bevat een vergelijking "150 tegenover 200 µmol" (uren per teelt, MWh per
kap, €/MWh, takken per m², kosten per tak, meeropbrengst). Bouw dat na als **scenariovergelijking**: twee
scenario's naast elkaar met verschil in takken, kWh, kosten en opbrengst bij een instelbare prijs per tak.

### 6.7 Reactietijd en LD-dagen (`voorstel/`)

```
reactietijd_voorstel = basis(ras, plantweek) + gevoeligheid(ras) × (referentietemperatuur − verwachte_etmaaltemperatuur)
ld_dagen_voorstel    = mediaan(ras, plantweek) uit eigen historie
```

- `basis` = mediaan van de gerealiseerde reactietijd uit het `teeltschema` (daar afgeleid als teeltduur korte
  dag − 2 − 3; neem die definitie over en documenteer hem).
- De temperatuurcorrectie staat standaard **uit** (gevoeligheid `null`) tot er temperatuurdata en een
  gekalibreerde waarde zijn.
- Lichtniveau heeft nauwelijks invloed op de reactietijd; neem het niet op als kenmerk.

---

## 7. Importers

Alle imports volgen hetzelfde patroon als in Gewichtsverwachting: uploaden → origineel met checksum opslaan →
controlevoorbeeld met meldingen → expliciet bevestigen → in één transactie wegschrijven. Een tweede identieke
bevestiging maakt niets dubbel aan. Geen macro's, geen versleutelde bestanden, groottegrenzen zoals in
`docs/EXCEL.md` van Gewichtsverwachting.

### 7.1 `Teeltprogramma maken …xlsx`

Tabbladen: `productie cijfers`, `Ronde 1` … `Ronde 34`, `productie`, `teeltkaart`, `weekplanning dekker` (leeg),
`Blad2`.

Rondeblad, rij 12–51 (40 regels), kop op rij 10–11, titel in `C8`:

| Kolom | Inhoud | Soort |
|---|---|---|
| B | vakgroep (11, 12, 21, …; staat alleen op de eerste regel van een groep) | invoer |
| C | volgnummer 1–40 | invoer |
| D | schemacode / soortcode (`a`, `p`, `t`, `3a3p`, soms vrije tekst) | invoer |
| E, F | plantdatum week, dag | invoer of verwijzing naar vorige ronde |
| G | dagen vegetatief (LD) | invoer |
| H, I | licht uit week, dag | formule |
| J | reactietijd (dagen) | invoer |
| K | tussenlicht (tekst `44-6` of een datum) | invoer |
| L | dagen verlenging (0–2) | invoer |
| M | planten per m² | invoer |
| N, O | bloei week, dag | formule |
| P | teeltduur | formule |
| R | teeltverlenging | invoer |
| T | gewichtsprognose (g) | invoer |
| V | datum geoogst (`34-3`) | invoer |
| X | gemiddeld gewicht | invoer |
| Y | uitval (fractie) | invoer |
| Z | geplant (= M × 726) | formule |
| AB | oogstaantal | formule |
| AD, AF | maand, cumulatief | formule |

- Lees invoercellen als waarde; lees formulecellen zowel als formule als gecachte uitkomst en bewaar beide als
  bewijs. Voer geen formules uit.
- Het kalenderjaar staat niet in het werkboek. Vraag per ronde het ISO-jaar van de eerste planting (zoals
  Gewichtsverwachting doet) en leid de rest af.
- Geïmporteerde invoerwaarden worden **handmatige** waarden in het geïmporteerde scenario "Werkboek".

### 7.2 `Belichting winterronde.xlsx`

| Tabblad | Inhoud | Gebruik |
|---|---|---|
| `belichting` rij 2–84 | branduren per week (kolommen = weken 36…10) per module; rij 3–42 LED (1–40), rij 44–83 SON-T (101–140); waarden zijn Excel-duur (`3 days, 10:35` = 82,6 uur) | weekuren winter 2023/24 (jaar bevestigen) |
| `belichting` rij 87–127 | per kap: plantdatum, LD/KD-dagen en uren, uren LED/SON-T, µmol, gewicht prognose/realisatie, LBE prognose/realisatie, kosten | ijkpunten voor §6.5 |
| `belichting` kolom AK–AM | kostenvergelijking 150/200 µmol | startwaarden scenariovergelijking |
| `teeltschema` | circa 860 regels historie: planten, KD, licht uit, pluizen, netten, leeg (week/dag), gewicht, tussentijden, teeltduur, reactietijd, uitval, alar, lbe | kalibratie reactietijd, LD-dagen, dag-offsets pluizen/netten |
| `alar`, `alar tutu` | remstofdoseringen | later (niet in fase 1–3) |
| `data` | bijna 210.000 regels: tijdstempel per 5 minuten + dimstand in % voor modules 101–140, vanaf 1 juli 2024 | dimgewogen uren per dag |

Aandachtspunten:

- Het bestand is groot. Lees het **streamend** (openpyxl `read_only=True`, of de eigen XML-lezer uit
  `legacy_lighting.py`); volledig inladen kost meer geheugen dan de container mag gebruiken. Draai de import als
  achtergrondtaak met voortgang.
- Normaliseer duurcellen op celtype (timedelta, time, datetime, getal in dagen), niet op weergegeven tekst.
- De regels in `data` staan niet strikt op tijdvolgorde; er ontbreken perioden (april–mei 2025, juni–juli 2026).
  Meld ontbrekende intervallen, dubbele tijdstempels en dagen met zomer-/wintertijdovergang.
- Controleer weekuren op 0–168.

### 7.3 Realisatie uit Gewichtsverwachting

Zie §11.2. Dit vervangt op termijn het handmatig importeren van het `teeltschema`.

---

## 8. Automatisch plannen

### 8.1 Afzetmomenten (`kern/afzet.py`, `bronnen/feestdagen.py`)

- Officiële feestdagen per land met het pakket `holidays` (lokaal, geen netwerk): NL, BE, DE, GB, FR, PL, IT, ES.
- Bloemendagen komen uit een eigen regeltabel. Startinhoud (elke regel met een test tegen minstens drie bekende
  jaren; markeer in de app als "nog niet door de teler bevestigd"): Valentijnsdag (14 feb), Vrouwendag (8 mrt),
  Britse moederdag (3 weken vóór Pasen), Pasen, moederdag (2e zondag mei), Franse moederdag, Allerheiligen
  (1 nov), Totensonntag, Kerst.
- Per moment instelbaar: `venster_dagen_voor` (hoeveel dagen vóór de dag er geoogst moet zijn; startwaarde
  `null`, vraag aan de teler), `gewicht` (belang, 0–10) en of het actief is.
- Terugrekenen: `plantdatum = gewenste_oogstdatum − reactietijd − verlenging − ld_dagen`.
- Weekoverzicht: oogstaantal per week tegenover de afzetmomenten, met de weken waarin veel aanvoer en lage prijs
  te verwachten is als aparte instelbare markering.

### 8.2 Plan genereren en zoeken

1. **Genereren**: voor een nieuw jaar of een nieuwe ronde vult de app per kap de ketting vooruit met voorstellen
   (ras volgens een instelbare rasverdeling, LD-dagen, reactietijd, dichtheid, gewicht).
2. **Zoeken**: een lokale zoekactie probeert kleine wijzigingen (plantdag ±1, LD-dagen ±1, verlenging 0–2, ras
   wisselen tussen kappen) en scoort op: dekking van afzetmomenten × gewicht, afwijking van een gelijkmatige
   weekoogst, afwijking van het doelgewicht, en energiekosten. De gewichten van die vier onderdelen zijn
   instelbaar.
3. De zoekfunctie levert **voorstellen als lijst** ("schuif kap 14 in ronde 9 één dag: +3.200 takken in de
   moederdagweek"). De gebruiker past ze één voor één of allemaal toe. Niets wordt automatisch toegepast en
   handmatige waarden zijn vaste randvoorwaarden.
4. Gebruik geen externe solver in deze versie. Leg in `docs/ARCHITECTUUR.md` vast dat OR-Tools CP-SAT de
   kandidaat is als er harde eisen bijkomen (bijvoorbeeld een arbeidsplafond per dag).

### 8.3 Model (`voorstel/model.py`)

Een ladder; een hogere trede wordt pas gebruikt als hij de lagere verslaat in de backtest.

1. Seizoensbasis: mediaan van hetzelfde ras binnen ±2 plantweken in eerdere jaren.
2. LBE-identiteit met de eigen LBE-tabel (§6.5).
3. Ridge-regressie op het residu (scikit-learn): plantweek cyclisch (sin/cos), lichtsom, dichtheid, LD-dagen, ras.
4. Kleine boomensemble met monotone beperkingen (meer licht nooit lichter, hogere dichtheid nooit zwaarder).

- Toets met een voortschrijdende backtest (train t/m jaar N, toets op N+1). Geen willekeurige kruisvalidatie.
- Een model krijgt de status "gevalideerd" pas als het trede 1 verslaat; anders blijft het "indicatief" en
  gebruikt de app de lagere trede.
- Elk voorstel bewaart modelversie, invoer, puntschatting, bandbreedte en de drie meest vergelijkbare eerdere
  teelten. Toon die uitleg op het scherm in vaste zinnen.
- **Geen GPU, geen taalmodel, geen TabPFN** (licentie sluit productiegebruik uit). Training draait op de CPU als
  achtergrondtaak en mag de webapp niet blokkeren.
- De tussentijdse gewichtsprognose op basis van KD10/KD30-metingen bouw je niet opnieuw: lees `prognoses` uit
  Gewichtsverwachting en toon die naast de eigen planprognose zodra hij "gevalideerd" is.

---

## 9. Scherm en afdruk

### 9.1 Schermen

| Scherm | Inhoud |
|---|---|
| Ronde | raster met 40 regels en dezelfde kolommen en volgorde als het rondeblad; bewerkbare cellen; toetsenbordnavigatie (pijlen, Enter, Tab, Esc); markering van handmatige waarden; rijmeldingen |
| Kap | de ketting van teelten van één kap over alle rondes, met tijdbalk |
| Jaaroverzicht | kappen × weken (wie staat waar, in welke fase), oogst per week, afzetmomenten, kWh per week |
| Teelt | alle drie de lagen per veld, uitleg bij elk voorstel, lichtsom zon/lamp, energie en kosten |
| Scenario's | kopiëren, naast elkaar vergelijken, vaststellen |
| Teeltkaart | kies kap en ronde; voorbeeld; afdrukken; alle 40 van een ronde in één keer |
| Import | de drie importers met controlevoorbeeld |
| Beheer | kappen, lampgroepen, rassen, parameters, afzetmomenten, prijzen, bronnen, koppelingen, gebruikers, beveiliging, back-up, audit |

Het raster moet werkbaar zijn op een telefoon (horizontaal scrollen met vaste eerste kolommen), maar het
kantoorscherm is leidend.

### 9.2 Teeltkaart: hoe hij nu werkt

Afdrukbereik `A1:AG31`, A4 liggend, **twee kaarten naast elkaar** (kap N in kolom A–P, kap N+1 in R–AG). Invoer:
kap (`N1`) en ronde (`N2`); de rest komt uit het rondeblad.

- Kop: plantweek/dag, sortiment, planten per m², licht uit (week-dag) met LD-dagen, licht aan (= licht uit + 12)
  met tussenlichtdagen, tweede licht uit, slak, remstof.
- Tabel rij 11–26 (16 momenten): datum (week-dag), LD-dag, streeflengte, en invulkolommen soort, cm, opmerking.
  - Het eerste moment valt op de eerstvolgende maandag, woensdag of vrijdag ten opzichte van licht uit. Volgens
    het werkboek, met weekdag van licht uit 1…7: moment op weekdag 1, 3, 3, 5, 5, 1, 1 en LD-dag 0, 1, 0, 1, 0,
    2, 1.
  - Daarna steeds het volgende moment: ma → wo (+2), wo → vr (+2), vr → ma (+3).
  - Streeflengte = opzoeken op LD-dag in de tabel hieronder, kolom volgens **plantweek**: winter = week 43–52,
    voorjaar = week 1–6, zomer = week 7–36, najaar = week 37–42.
- Onderaan: begin oogst (week-dag), RT (reactietijd), drie regels opmerkingen.

Streeflengte in cm (uit het werkboek; laad als begingegevens in `streeflengte`, aanpasbaar in Beheer):

| LD-dag | winter | voorjaar | zomer | najaar |
|---|---|---|---|---|
| 4 | – | – | 30 | – |
| 7 | 45 | 39 | 35 | 39 |
| 8 | 48 | 42 | 40 | 42 |
| 9 | 51 | 45 | 42 | 45 |
| 10 | 54 | 48 | 44 | 48 |
| 11 | 56 | 51 | 46 | 51 |
| 12 | 58 | 56 | 48 | 56 |
| 13 | 60 | 58 | 50 | 58 |
| 14 | 62 | 60 | 52 | 60 |
| 15 | 64 | 62 | 54 | 62 |
| 16 | 66 | 64 | 56 | 64 |
| 17 | 68 | 66 | 58 | 66 |
| 18 | 69 | 67 | 60 | 67 |
| 19 | 70 | 68 | 62 | 68 |
| 20 | 71 | 69 | 63 | 69 |
| 21 | 72 | 70 | 65 | 70 |
| 22 | 73 | 71 | 67 | 71 |
| 23 | 74 | 72 | 69 | 72 |
| 24 | 75 | 73 | 71 | 73 |
| 25 | 76 | 74 | 73 | 74 |
| 26 | 77 | 75 | 75 | 75 |
| 27 | 77 | 76 | 77 | 76 |
| 28 | 78 | 77 | 78 | 77 |
| 29 | 78 | 78 | 79 | 78 |
| 30 | 79 | 78 | 80 | 78 |
| 31 | 79 | 79 | 80 | 79 |
| 32 | 80 | 79 | 80 | 79 |
| 33–35 | 80 | 80 | 80 | 80 |

Lees de volledige tabel (`teeltkaart!AI1:AM41`) opnieuw uit het echte werkboek bij de eerste import en gebruik
die als bron; bovenstaande is de controle. LD-dagen zonder waarde blijven leeg op de kaart.

### 9.3 Afdrukken

**Route A (verplicht): gevuld sjabloon.** De gebruiker uploadt in Beheer één keer zijn eigen werkboek als
sjabloon (opgeslagen onder `/data/sjabloon/`, nooit in de image). De app opent een kopie, schrijft **waarden**
(geen formules) in de bestaande cellen van het rondeblad (`B4:R51`, A4 staand) of de teeltkaart, laat opmaak,
kolombreedtes, samengevoegde cellen en afdrukinstellingen ongemoeid, en biedt het resultaat aan als download.
Voor "alle 40 kaarten" maak je één werkboek met 20 tabbladen (twee kaarten per blad).

**Route B (verplicht): pdf rechtstreeks uit de app via LibreOffice.** LibreOffice mag in de image (akkoord van
Leon). Het gevulde sjabloon uit route A wordt in de container door LibreOffice zonder scherm omgezet naar pdf,
zodat de gebruiker met één klik een afdrukbare pdf krijgt, ook op de telefoon.

- Installeer alleen wat nodig is (`libreoffice-calc-nogui` of het kleinste pakket dat xlsx → pdf kan) plus de
  lettertypen die het werkboek gebruikt. Lees de gebruikte lettertypen uit het sjabloon; ontbreekt er een, meld
  dat in Beheer en bied aan om een lettertypebestand te uploaden naar `/data/lettertypen/`. Vervangende
  lettertypen zijn de voornaamste oorzaak van verschoven regels.
- Draai de conversie als afzonderlijk proces met een tijdslimiet, een eigen tijdelijke profielmap per aanroep
  onder `/data/tmp/`, als gebruiker 568 en zonder netwerk. Eén conversie tegelijk (wachtrij); ruim tijdelijke
  bestanden altijd op.
- Zet vóór de conversie de afdrukinstellingen expliciet in het werkboek (afdrukbereik, richting, passend op één
  pagina breed en hoog), zodat een blad niet over meerdere pagina's wordt gesplitst.
- Controleer na de conversie het aantal pagina's (rondeblad: 1; teeltkaarten: 1 per blad). Wijkt dat af, toon
  dan een melding en bied het Excel-bestand aan.
- Test in CI met het synthetische sjabloon: pdf wordt gemaakt, heeft het verwachte aantal pagina's en bevat de
  verwachte tekst. Vergelijk in fase 2 één echte kaart uit Excel en uit de app door beide naar een afbeelding om
  te zetten en over elkaar te leggen; leg het resultaat vast in `docs/STATUS.md`.
- Mislukt de conversie, dan blijft de download van het gevulde Excel-bestand altijd werken.

Een afdrukweergave in HTML is hiermee niet nodig.

---

## 10. Externe bronnen (`bronnen/`)

Elke bron: standaard uit, eigen schakelaar, tijdslimiet, nette foutafhandeling, laatste succesvolle ophaalmoment
zichtbaar, en de app blijft werken als de bron wegvalt.

| Bron | Gebruik | Let op |
|---|---|---|
| KNMI Data Platform | gemeten globale straling en temperatuur per dag, dichtstbijzijnde station (instelbaar) | het oude script `daggegevens` stopt eind 2026: bouw direct op de dataset `daily-in-situ-meteorological-observations-validated`. Variabelenaam en eenheid voor straling zijn **niet geverifieerd**: controleer in de datasetdocumentatie en leg vast. API-sleutel via Beheer. Gratis, ook commercieel. |
| Open-Meteo | historie (ERA5) en verwachting, `shortwave_radiation_sum` in MJ/m² (× 100 = J/cm²) | gratis API is voor niet-commercieel gebruik; toon dat bij de schakelaar en maak de basis-URL instelbaar (eigen server of betaald abonnement) |
| Weeknormalen | P10/P50/P90 van de stralingssom per ISO-week uit 20–30 jaar daghistorie | plan op P50, toon de band; de eerste 1–2 weken vervangen door de verwachting. Geen seizoensverwachting gebruiken. |
| `holidays` (pakket) | officiële feestdagen | lokaal, geen netwerk |
| `energyzero` (pakket) | dagvooruitprijzen stroom | alleen voor kosten van de komende dag en achteraf; voor planning het prijsscenario gebruiken |
| Handmatige import | veilingafrekeningen (later), Priva-export | Priva- en LetsGrow-API's zijn betaald of nog niet bevestigd: niet bouwen, wel de interface zo opzetten dat een bron later kan worden toegevoegd |

Delphy QMS: er is geen openbare API of export gevonden. Bouw geen koppeling en geen schermlezer voor de
QMS-website. Als Leon later een export van Delphy krijgt, komt daar een gewone bestandsimporter voor.

---

## 11. Bedrijfskoppeling API v1

Het contract staat in `lvos04/bedrijfsdashboard/docs/koppeling-api.md`. Volg het letterlijk en kopieer het
document naar `docs/koppeling-api.md` in de nieuwe repo, aangevuld met het app-specifieke deel.

### 11.1 Aanbieden (slug `teeltplanning`)

- `GET /api/koppeling/v1/info`, `/samenvatting`, `/datasets/{naam}`; alleen GET; `Authorization: Bearer bk_…`;
  alleen de SHA-256-hash van de sleutel opslaan; instelling "Bedrijfskoppeling ingeschakeld" standaard **uit**.
- Volgorde van controles: uitgeschakeld → `403 {"fout":"koppeling_uitgeschakeld"}`; sleutel fout → `401`;
  te veel verzoeken (120/min per sleutel) → `429`; onbekende dataset → `404`; ongeldige parameter → `422`.
- Alleen het **vastgestelde** scenario wordt gedeeld; concepten nooit.
- Ontbrekende waarde = `null`. Geen vrije tekst, geen gebruikersnamen.

KPI's in `/samenvatting`:

| sleutel | betekenis | status |
|---|---|---|
| `plantingen_komende_14_dagen` | aantal geplande plantingen vandaag t/m +14 dagen | `ok` |
| `oogsten_komende_14_dagen` | aantal teelten met geplande oogst vandaag t/m +14 dagen | `ok` |
| `oogstprognose_deze_week` | verwacht aantal takken deze week | `onbekend` zonder plan |
| `planconflicten` | teelten met overlap of ontbrekende verplichte waarde | `actie` bij > 0 |
| `voorstellen_indicatief` | aandeel teelten waarvan een voorstel op een niet-gevalideerd model rust | `let_op` boven een instelbare grens |
| `kwh_komende_week` | verwacht stroomverbruik belichting | `onbekend` zonder lichtdata |

Meldingen: planconflicten, verlopen weerdata, ontbrekende belichtingsdata, verouderde back-up.

Datasets:

| naam | velden per item |
|---|---|
| `planning` | `id, ronde, kap, vakgroep, volgnummer, soort, bedden, plantdatum, ld_dagen, licht_uit_datum, reactietijd, verlenging, oogstdatum, teeltduur, dichtheid, gepland_aantal, uitvalfractie, oogstaantal, gewicht_g, gewicht_ondergrens_g, gewicht_bovengrens_g, handmatig_velden[], bijgewerkt_op` |
| `oogstprognose` | `id (iso_jaar-week), iso_jaar, week, aantal_takken, gemiddeld_gewicht_g, ondergrens_g, bovengrens_g, aantal_teelten, bijgewerkt_op` |
| `energieprognose` | `id (iso_jaar-week-kap), iso_jaar, week, kap, dimgewogen_uren, kwh, kosten_eur, soort (gerealiseerd/gepland), bijgewerkt_op` |
| `afzetmomenten` | `id, naam, datum, venster_begin, venster_eind, gewicht, geplande_takken_in_venster` |

### 11.2 Afnemen

Koppelbronnen in Beheer (URL + koppelsleutel, versleuteld opgeslagen; kijk hoe het dashboard dat doet in
`app/versleuteling.py` en `app/ophalen.py`). Ophalen op een instelbaar interval en met een knop "nu ophalen".

Uit `gewichtsverwachting`: `teelten`, `oogsten`, `metingen`, `prognoses`. Gebruik:

- `oogsten` + `teelten` → gerealiseerd gewicht, uitval, plant-, licht-uit- en oogstdatum per kap → kalibratie
  van LBE, reactietijd en LD-dagen (§6.5, §6.7);
- `prognoses` → tussentijdse gewichtsverwachting tonen naast de planprognose;
- koppel op kap + plantdatum (± een instelbaar aantal dagen); toon niet-gekoppelde teelten in een lijst in
  plaats van te raden.

Behandel `403 koppeling_uitgeschakeld` als "staat uit bij de bron" met de uitleg waar de schakelaar zit
(Gewichtsverwachting: Beheer → Bedrijfskoppeling), niet als storing.

### 11.3 Wijzigingen in andere repo's (aparte, kleine pull requests in fase 6)

Maak deze pas na akkoord van Leon, elk als eigen PR met tests:

1. **`lvos04/gewichtsverwachting`**: nieuwe dataset `belichting` in de koppeling (per dag per module:
   `datum, module, branduren, dimgewogen_uren, bron (priva/excel), kwaliteit`), zodat Teeltplanning de
   Priva-branduren niet zelf hoeft te importeren. Documenteer in `docs/koppeling-api.md`.
2. **`lvos04/gewichtsverwachting`** (optioneel, na overleg): het plan uit Teeltplanning kunnen inlezen via de
   dataset `planning`, zodat geplande teelten daar niet meer handmatig worden ingevoerd.
3. **`lvos04/bedrijfsdashboard`**: slug `teeltplanning` toevoegen aan de bekende apps (de namenlijst in
   `app/main.py` en de schakelaarplekken in `app/ophalen.py`), met de plek van de
   schakelaar ("Beheer → Koppelingen → Bedrijfskoppeling") in de tabel en in de uitlegtekst; test erbij.

---

## 12. Beveiliging en beheer

Neem over uit Gewichtsverwachting (zelfde gedrag, zelfde instellingen): eerste beheerder aanmaken bij eerste
start (geen standaardaccounts), wachtwoord minimaal twaalf tekens, gehashte wachtwoorden, HTTP-only sessies,
strikte oorsprongcontrole op schrijfverzoeken, aanmeldbeperking per account én per IP met vertrouwde proxy's,
optionele MFA (en "MFA verplicht voor beheerders", standaard uit), opnieuw bevestigen bij back-up terugzetten,
volledige export en koppelsleutel aanmaken, auditlog, dagelijkse opschoning van verlopen sessies, back-up en
herstel met status en schijfruimte, maximale verzoekgrootte 1 MB behalve bij imports.

Rollen: beheerder (alles), medewerker (plan bewerken, importeren), lezer (alleen bekijken en afdrukken).

---

## 13. Bouwen, uitbrengen en installeren

- CI zoals `gewichtsverwachting/.github/workflows/`: tests en lint bij elke push; image naar
  `ghcr.io/lvos04/teeltplanning`; tags `v*` → versie-image; testbuilds van een `ccr-*`-branch → `<versie>-rc`,
  `branch-<naam>`, `sha-<commit>`; een testbuild zet nooit `latest`. Versie uit één bron
  (`teeltplanning/__init__.py`).
- Dockerfile zoals Gewichtsverwachting (twee stappen, gebruiker 568, healthcheck), zonder tesseract/zbar maar
  met LibreOffice Calc zonder schermonderdelen en de benodigde lettertypen (§9.3). Vermeld de imagegrootte in
  `docs/STATUS.md`.
- `docs/TRUENAS.md`: installatie via de wizard **Custom App** zonder YAML: image en vaste tag, containerpoort
  8000 → een vrije hostpoort (voorstel 30096; laat Leon bevestigen dat die vrij is), Host Path-dataset op
  `/data` met schrijfrecht voor 568:568, omgevingsvariabelen `DATA_DIR`, `TZ`, `SECURE_COOKIES`,
  `TRUSTED_PROXIES`. Vermeld dat het GHCR-pakket eenmalig op publiek moet staan, of dat er inloggegevens voor de
  registry nodig zijn.
- Documentatie in `docs/`: `GEBRUIK.md`, `TRUENAS.md`, `HERSTEL.md`, `REKENKERN.md` (alle formules met
  voorbeelden), `EXCEL.md`, `koppeling-api.md`, `ARCHITECTUUR.md`, `OPEN_VRAGEN.md`, `WIJZIGINGEN.md`,
  `STATUS.md`.

---

## 14. Fasen en acceptatiecriteria

### Fase 0 — Proeven en vragen (geen productcode)

- Repo aanmaken, CI-skelet, lege app met `/health`, image bouwt en start als gebruiker 568.
- Synthetische testwerkboeken genereren met exact de structuur uit §7.
- Proef 1: duurcellen uit het echte belichtingsbestand correct als uren lezen (tien regels handmatig nagerekend).
- Proef 2: sjabloon vullen en in Excel openen; opmaak en afdrukbereik ongewijzigd.
- `docs/OPEN_VRAGEN.md` gevuld met §15.

**Klaar als:** image staat op GHCR, beide proeven zijn beschreven met uitkomst, open vragen staan klaar voor Leon.

### Fase 1 — Rekenkern, import en raster

- `kern/kalender.py`, `kern/keten.py`, aantallen uit §6.2; importer §7.1; scherm Ronde en Kap; drie lagen;
  auditlog; inloggen en rollen.

**Klaar als:**
- voor alle 1.360 kapregels van het echte werkboek zijn licht uit, bloei en teeltduur gelijk aan de gecachte
  uitkomsten in het werkboek, behalve de gevallen die door de 52-wekenfout afwijken; die staan in een rapport;
- een teelt over de jaarwisseling 2026/2027 klopt met de kalender (week 53 bestaat);
- een wijziging in ronde N schuift door naar N+1 in dezelfde kap, tenzij daar de plantdatum is vastgezet;
- handmatige waarden overleven een herimport en "terug naar voorstel" werkt;
- herberekening van het hele plan blijft onder 200 ms (test);
- `kern/` importeert niets van buiten zichzelf (test);
- het instellingenregister uit §4.3 bestaat, het scherm Beheer → Instellingen wordt eruit gegenereerd, en elke
  waarde die fase 1 gebruikt komt uit het register (test op harde constanten);
- een gewijzigde instelling rekent het plan direct opnieuw door en staat in het auditlog.

Voor elke latere fase geldt hetzelfde: nieuwe getallen, lijsten en regels komen in het register of in een
beheertabel, nooit in de code.

### Fase 2 — Afdrukken en teeltkaart

- Sjabloonbeheer, gevuld rondeblad, teeltkaart volgens §9.2, alle 40 kaarten in één bestand, pdf via
  LibreOffice (route B).

**Klaar als:**
- rondeblad en teeltkaart zijn met één klik als pdf te openen, met het juiste aantal pagina's;
- een mislukte conversie geeft een duidelijke melding en het Excel-bestand blijft beschikbaar;
- een afdruk van het gevulde rondeblad en van een teeltkaart is naast het origineel niet te onderscheiden;
- voor tien steekproefkappen zijn alle velden en de 16 momenten met streeflengte gelijk aan wat het werkboek
  toont;
- de kaart is op te vragen met alleen kap en ronde.

### Fase 3 — Licht, energie en dichtheid

- Lampgroepen (§6.4), importer §7.2, weerbronnen en weeknormalen (§10), lichtsom, LBE-kalibratie,
  dichtheidsvoorstel, energie en kosten, scenariovergelijking (§6.6).

**Klaar als:**
- de eenheidstests met proefcijfers uit §6.5 slagen;
- de berekende LBE van de 22 kappen uit het belichtingsblad ligt binnen een met Leon afgesproken marge van de
  LBE-realisatie in dat blad, of het verschil is verklaard;
- dimgewogen uren per dag uit `data` kloppen met een handmatig nagerekende steekproef van vijf dagen;
- ontbrekende perioden in de belichtingsdata worden gemeld en leveren geen stilzwijgend ingevulde lichtsom;
- kWh per week sluit aan op een door Leon aangeleverde meterstand of factuur, of het verschil staat in
  `OPEN_VRAGEN.md`.

### Fase 4 — Voorstellen met model

- §6.7 en §8.3; uitleg bij voorstellen; backtest; status indicatief/gevalideerd.

**Klaar als:**
- een model wordt alleen "gevalideerd" als het de seizoensbasis verslaat in de voortschrijdende backtest, en de
  backtestuitkomsten zijn in de app te zien;
- elk voorstel toont bron, bandbreedte, modelversie en drie vergelijkbare teelten;
- alles draait zonder GPU en training blokkeert de webapp niet.

### Fase 5 — Afzetpieken, genereren en zoeken

- §8.1 en §8.2; jaaroverzicht; scenario's.

**Klaar als:**
- elke datumregel is getest tegen minstens drie bekende jaren;
- genereren levert voor een leeg jaar een volledig, conflictvrij plan op;
- de zoekfunctie past niets automatisch toe en raakt geen handmatige waarden (test);
- twee scenario's zijn naast elkaar te vergelijken op oogst per week, afzetdekking en kWh.

### Fase 6 — Koppelingen

- §11.1, §11.2 en, na akkoord, de pull requests uit §11.3.

**Klaar als:**
- de contracttests slagen (401 zonder/ongeldige/ingetrokken sleutel, sleutel eenmalig getoond en alleen als hash
  opgeslagen, 403 bij uitgeschakelde koppeling vóór alle andere controles, paginering, `sinds`, 404, 422, 429,
  niet-beheerders kunnen geen sleutels beheren);
- het Bedrijfsdashboard toont de nieuwe app als bron met de juiste uitleg bij een uitgeschakelde koppeling;
- gerealiseerde teelten uit Gewichtsverwachting worden gekoppeld en voeden de kalibratie;
- externe bronnen staan na een schone installatie allemaal uit.

---

## 15. Open vragen voor Leon (niet zelf invullen)

| # | Vraag | Nodig voor |
|---|---|---|
| 1 | Precieze maand van de overgang SON-T → LED fase 1 en van SON-T → LED fase 2 | fase 3 |
| 2 | Is het tabblad `belichting` (week 36 t/m 10) winter 2023/24? | fase 3 |
| 3 | Waar komen de branduren van LED fase 1 (modules 1–40) vandaan sinds juli 2024? | fase 3 |
| 4 | Oppervlak per kap: 726 of 750 m², en is dat voor alle kappen gelijk? | fase 1 |
| 5 | ISO-jaar per ronde in het werkboek | fase 1 |
| 6 | Kastransmissie, of gemeten PAR in de kas om hem te ijken | fase 3 |
| 7 | Doelgewicht per ras | fase 3 |
| 8 | Toegestane plantdagen per week en wisseltijd tussen teelten (ook bij stomen) | fase 1 |
| 9 | Verkoopvenster en belang per afzetmoment | fase 5 |
| 10 | Stroomprijs voor de planning (het werkboek rekent met 80 en met 300 €/MWh) | fase 3 |
| 11 | Antwoord van Delphy Digital op de vraag naar een export of API | geen blokkade |
| 12 | Moet de nieuwe repo publiek zijn zoals de andere, en mag de hostpoort 30096 worden? | fase 0 |
| 13 | Welke lettertypen gebruikt het werkboek, en mogen die in de image of worden ze geüpload? | fase 2 |

De antwoorden op deze vragen worden in de app ingevuld als instelling (§4.3), niet in de code. Tot een antwoord
er is, staat de instelling op "onbekend" en toont de app wat daardoor niet berekend kan worden.

---

## 16. Startopdracht (plak dit in Claude Code)

```
Je gaat de app "Teeltplanning" bouwen volgens docs/BOUWPLAN.md (dit document). Lees dat eerst volledig, en lees
daarna in lvos04/gewichtsverwachting en lvos04/bedrijfsdashboard de bestanden die in §2 worden genoemd.

Begin met fase 0 en stop daarna. Houd je aan §0: één fase per branch en pull request, niets verzinnen, geen
bedrijfsgegevens in de repo, alles in het Nederlands, externe verbindingen standaard uit.

Lever aan het eind van fase 0 op:
1. de repo lvos04/teeltplanning met CI en een image die start;
2. synthetische testwerkboeken met de structuur uit §7;
3. de uitkomst van de twee proeven;
4. docs/OPEN_VRAGEN.md met de vragen uit §15.

Meld daarna wat werkt, wat je getest hebt en wat je van mij nodig hebt voor fase 1.
```
