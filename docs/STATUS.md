# Status

Per fase: wat werkt, wat getest is en wat nog niet kan. Echte bedrijfsbestanden zijn tijdens de bouw **niet**
beschikbaar geweest; alles is gedaan op synthetische werkboeken met dezelfde structuur (`tests/synthetisch.py`).

## Fase 0 — Proeven en vragen (versie 0.1.0)

| Klaar als | Uitkomst |
|---|---|
| Repo, CI-skelet, lege app met `/health` | Gedaan. `.github/workflows/check.yml` (ruff lint + format, pytest, docker build + rooktest), `image.yml` (ghcr.io/lvos04/teeltplanning; `ccr-*`/`fase-*` → `<versie>-rc`, `branch-…`, `sha-…`, nooit `latest`; tags `v*` → versie + `latest`), `auto-release.yml` (gelijk aan Gewichtsverwachting, versie uit `teeltplanning/__init__.py`). |
| Image bouwt en start als gebruiker 568 | Gedaan (lokaal): `docker run` → `/health` geeft `{"status":"ok","versie":"0.1.0"}`, `id -u` = 568, Docker-healthcheck `healthy`. **Imagegrootte: 806 MB** (waarvan het grootste deel LibreOffice Calc zonder scherm + lettertypen; zonder LibreOffice ±200 MB). |
| Image staat op GHCR | Wacht op de eerste push van een `fase-*`-branch naar GitHub (de workflow publiceert dan `0.1.0-rc`, `branch-fase-0`, `sha-…`). Het GHCR-pakket staat standaard privé: eenmalig op publiek zetten of TrueNAS registry-inloggegevens geven (TRUENAS.md). |
| Synthetische testwerkboeken met de structuur uit §7 | Gedaan: `python -m tests.synthetisch <map>` maakt `Teeltprogramma maken synthetisch.xlsx` (39 tabbladen, `Ronde 1`…`Ronde 34`, rij 10–11 kop, `C8` titel, rij 12–51 met formules **én** gecachte uitkomst, verwijzingen `='Ronde N'!N12`, 52-weken-rekenwerk, teeltkaart met afdrukbereik `A1:AG31` en streeflengtetabel `AI1:AM41`) en `Belichting winterronde synthetisch.xlsx` (`belichting` rij 2–84 weekuren in vijf celtypen, kaptabel vanaf rij 87, kostenvergelijking AK–AM, `teeltschema` 860 regels, `alar`, `alar tutu`, `data` met 5-minutenreeks over de wintertijdovergang, een gat, een dubbele tijdstempel en regels buiten tijdvolgorde). Aannames over de delen die het bouwplan niet beschrijft: EXCEL.md. |
| Proef 1 beschreven met uitkomst | **Op synthetisch: geslaagd. Op het echte bestand: wacht op het bestand.** Zie hieronder. |
| Proef 2 beschreven met uitkomst | **Op synthetisch: geslaagd (LibreOffice). Echt sjabloon in Excel: wacht op het bestand.** Zie hieronder. |
| Open vragen klaar voor Leon | Gedaan: OPEN_VRAGEN.md (§15, plus ontbrekende documenten en nieuwe vragen). |

### Proef 1 — duurcellen als uren

Code: `proeven/proef1_duurcellen.py`. Normaliseert op **celtype**, niet op getoonde tekst:

| Celtype (openpyxl) | Komt voor bij getalnotatie | Omrekening |
|---|---|---|
| `timedelta` | `[h]:mm`, `[h]:mm:ss` | `total_seconds / 3600` |
| `datetime` | datumnotatie zoals `d "days," hh:mm` | terug naar Excel-getal in het datumstelsel van het werkboek (1900/1904) × 24 — `1900-01-03 10:35` → 3,4410 dagen → 82,583 uur |
| `time` | `hh:mm` met waarde < 1 dag | dagdeel × 24 (melding: kan geen duur ≥ 24 uur zijn) |
| getal | `General`, `0.000` | dagen × 24 |
| tekst | `3 days, 10:35`, `82:35` | alleen gelezen **met melding** "controleer de bron" |

Uitkomst op het synthetische werkboek: alle 2.160 weekcellen (80 modules × 27 weken) komen exact overeen met de
bij het genereren vastgelegde uren (`tests/test_proef1_duurcellen.py`); `3 days, 10:35` = 82,583 uur; de cel met
180 uur wordt gemeld als buiten 0–168; de lege cel blijft "onbekend". Steekproef van tien cellen (handmatig
nagerekend, uitvoer van `python -m proeven.proef1_duurcellen … --steekproef 10`):

```
cel    module week celtype    getalnotatie          Excel-dagen   uren      uu:mm
C12    10     37   datetime   d "days," hh:mm           2.045139    49.083   49:05   (2,045139 × 24 = 49,083)
C61    119    37   time       hh:mm                     0.614583    14.750   14:45
T71    129    2    timedelta  [h]:mm                    4.104167    98.500   98:30
E75    133    39   timedelta  [h]:mm                    2.201389    52.833   52:50
S78    136    1    timedelta  [h]:mm                    2.274306    54.583   54:35
U16    14     3    timedelta  [h]:mm                    1.173611    28.167   28:10
K20    18     45   timedelta  [h]:mm                    0.864583    20.750   20:45
Y22    20     7    getal      0.000                     1.100694    26.417   26:25
J34    32     44   timedelta  [h]:mm                    4.218750   101.250  101:15
F41    39     40   getal      0.000                     5.812500   139.500  139:30
```

**Wat met het echte bestand nog moet:** in de container op de NAS (het bestand blijft op de dataset):
`python -m proeven.proef1_duurcellen "/data/bron/Belichting winterronde.xlsx" --steekproef 10` en de tien regels
naast Excel leggen (de cel in Excel op notatie `[u]:mm` zetten en vergelijken). Daarnaast
`TEELT_ECHT_BELICHTING=<pad> pytest -s tests/test_proef1_duurcellen.py`: faalt als er onbekende celtypen zijn.
Let op de verdeling over celtypen en de meldingen in de uitvoer.

### Proef 2 — sjabloon vullen

Code: `proeven/proef2_sjabloon.py`. Aanpak: **waarden rechtstreeks in de XML van één werkblad** schrijven
(bestaande cel: stijlnummer blijft; ontbrekende cel/rij: op volgorde invoegen; tekst als inline-string, zodat de
gedeelde-tekstentabel niet verandert). Alle andere onderdelen van het pakket blijven byte-voor-byte gelijk;
alleen `xl/workbook.xml` krijgt `fullCalcOnLoad="1"` en een eventuele `xl/calcChain.xml` wordt verwijderd.

Uitkomst op het synthetische werkboek (rondeblad `B4:R51` en teeltkaart `A1:AG31`):

* pakketvergelijking: alleen `xl/workbook.xml` en het gevulde blad verschillen; niets verdwenen of nieuw;
* opmaak via openpyxl vergeleken (kolombreedtes, samengevoegde cellen, afdrukbereik, richting, papierformaat,
  passend op 1×1, marges, vastgezette deelvensters, en per cel lettertype, vulling, rand, uitlijning,
  getalnotatie, bescherming): **geen verschillen**; gegevensvalidatie en opmerking blijven staan;
* ter vergelijking: openpyxl laden + opslaan herschrijft `styles.xml`, `docProps/core.xml` en **alle**
  werkbladen — daarom niet die route;
* LibreOffice 7.4 (zonder scherm, eigen tijdelijk profiel, tijdslimiet) zet het gevulde blad om naar pdf:
  rondeblad **1 pagina**, teeltkaart **1 pagina**, ook in de container als gebruiker 568. Bevinding: LibreOffice
  drukt **verborgen** bladen wél af; voor de pdf wordt daarom een afdrukkopie gemaakt met alleen het af te drukken
  blad in `workbook.xml` (fase 2 neemt dit over).

**Wat met het echte bestand nog moet:** `TEELT_ECHT_PROGRAMMA=<pad> pytest -s tests/test_proef2_sjabloon.py`
(vult `Ronde 1`, controleert pakket en opmaak, print het pad van het gevulde bestand) en dat gevulde bestand
**in Excel openen**: geen herstelmelding, opmaak en afdrukvoorbeeld gelijk aan het origineel. Dit kon hier niet
(geen Excel, geen echt bestand). Mogelijke verrassingen in het echte werkboek die het synthetische niet heeft:
gedeelde formules (`<f t="shared">`), matrixformules, afbeeldingen/vormen en externe koppelingen; de vuller laat
die buiten de gevulde cellen ongemoeid, maar alleen de echte proef bewijst het.

### Tests fase 0

`pytest`: 25 geslaagd, 2 overgeslagen (de twee tests met het echte bestand). `ruff check` en
`ruff format --check` schoon.

## Fase 1 — Rekenkern, import en raster (versie 0.2.0)

| Klaar als | Uitkomst |
|---|---|
| Voor alle 1.360 kapregels van het echte werkboek zijn licht uit, bloei en teeltduur gelijk aan de gecachte uitkomsten, behalve de 52-wekenfout; die staan in een rapport | **Gebouwd en getest op synthetisch; wacht op het echte werkboek.** De vergelijking (`importers/vergelijking.py`) staat in het importvoorbeeld en als CSV-rapport. Synthetisch (1.360 regels, ISO-jaren 2020–2026): 1.289 `gelijk`, 70 `52_weken`, 1 `onvolledig` (bewuste regel zonder plantdatum), **0 `afwijking`** (`tests/test_import_programma.py`, `tests/test_web_plan.py`; ook in de container nagelopen). Met het echte bestand: `TEELT_ECHT_PROGRAMMA=<pad> TEELT_ECHT_JAREN='1:2018,2:2018,…' pytest -s tests/test_import_programma.py`, of gewoon importeren in de app en de jaren invullen. Hoe het echte werkboek precies rekent, is open vraag B1. |
| Een teelt over de jaarwisseling 2026/2027 klopt met de kalender (week 53 bestaat) | Gedaan: `test_week_53_van_2026_bestaat`, `test_teelt_over_jaarwisseling_2026_2027_kent_week_53` (licht uit 30-12-2026 = 53-3, oogst 2027 week 1). Het synthetische werkboek toont de 52-wekenfout in 2020 en 2026. |
| Een wijziging in ronde N schuift door naar N+1 in dezelfde kap, tenzij daar de plantdatum is vastgezet | Gedaan: kern (`test_wijziging_schuift_door_tot_vastgezette_plantdatum`) en via de webapp (`test_import_raster_keten_herimport_en_instellingen`: LD + 7 in ronde 2 → ronde 3 schuift ≥ 7 dagen; na vastzetten in ronde 3 schuift niets meer). |
| Handmatige waarden overleven een herimport en "terug naar voorstel" werkt | Gedaan: een in de app ingevulde dichtheid (met reden) blijft staan na een tweede import van hetzelfde werkboek; daarna "terug naar voorstel" (lege cel / Ctrl+Backspace / knop) → auditregels `veld_gewijzigd`, `veld_terug_naar_voorstel`. Een tweede identieke bevestiging maakt niets dubbel (1.360 teelten). |
| Herberekening van het hele plan onder 200 ms (test) | Gedaan: `tests/test_prestatie.py`, circa 25 ms voor 34 × 40. |
| `kern/` importeert niets van buiten zichzelf (test) | Gedaan: `tests/test_kern_zuiver.py` (AST-controle: alleen standaardbibliotheek en relatieve imports binnen `kern/`). |
| Instellingenregister, scherm Beheer → Instellingen eruit gegenereerd, en elke waarde die fase 1 gebruikt komt uit het register (test op harde constanten) | Gedaan: `teeltplanning/instellingen.py` (alle 18 groepen van §4.3; Lampen wordt in fase 3 een beheertabel), scherm met groepen, zoeken, startwaarde, terug naar startwaarde, ingangsdatum en historie; export/import JSON. `test_geen_harde_getallen_in_kern_voorstel_afdruk` (alleen 0, 1, 7, 24, 60, 100, 3600 toegestaan). Kolomtoewijzing en herkenningskoppen van de importer zijn ook instellingen. |
| Een gewijzigde instelling rekent het plan direct door en staat in het auditlog | Gedaan: *Dagen van licht uit tot licht aan* 12 → 14 verschuift licht aan met 2 dagen; auditregel met oud/nieuw. Oppervlak met ingangsdatum invullen → aantallen verschijnen. |

Verder in fase 1:

* **Schermen**: Ronde (40 regels, kolommen in de volgorde van het rondeblad, bewerkbare cellen,
  toetsenbordnavigatie, markering handmatig, rijmeldingen, vaste eerste kolommen op de telefoon), Kap (ketting met
  tijdbalk), Teelt (drie lagen, uitleg, werkboekbewijs). Browser-rooktest (`tests/browser_rooktest.py`, Chrome):
  typen + Enter slaat op en markeert handmatig, focus gaat een rij omlaag, Esc annuleert, Ctrl+Backspace zet terug,
  geen scriptfouten.
* **Inloggen en rollen**, MFA, strikte oorsprong, aanmeldbeperking per account en IP met vertrouwde proxy's,
  auditlog, back-up/herstel/export met herbevestiging, 1 MB verzoekgrens: `tests/test_beveiliging.py`.
* **Oppervlak per kap is `null`** (vraag 4): geplant aantal, oogstaantal en cumulatief zijn "onbekend"; het
  rondescherm toont dat bovenaan. Ook onbekend tot Leon antwoordt: uitvalvoorstel (tabel leeg), aantal dagen
  tussenlicht (dus tweede licht uit), doelgewicht per ras.
* Niet in fase 1 (volgens plan later): licht/energie/LBE (fase 3), modelvoorstellen (fase 4), scenario's
  vergelijken en jaaroverzicht (fase 5), afdrukken (fase 2), koppeling (fase 6; alleen de instelling
  *Bedrijfskoppeling ingeschakeld* = uit bestaat).

### Tests en container fase 1

* `pytest`: 80 geslaagd, 3 overgeslagen (echte bestanden); `ruff check` en `ruff format --check` schoon;
  browser-rooktest geslaagd.
* Docker (lokaal): image **806 MB**; start als 568, `/health` = `{"status":"ok","versie":"0.2.0"}`, healthcheck
  `healthy`; met curl in de container: eerste beheerder, POST zonder Origin → 403, upload + jaren + bevestigen
  van het synthetische werkboek → vergelijking 1.289/70/1, raster 40 regels, back-up gemaakt in `/data/backups/`.

## Fase 2 — Afdrukken en teeltkaart (versie 0.3.0)

| Klaar als | Uitkomst |
|---|---|
| Rondeblad en teeltkaart zijn met één klik als pdf te openen, met het juiste aantal pagina's | **Gedaan.** *Afdrukken* → *Pdf* opent de pdf direct (inline) in de browser, ook op de telefoon. Rondeblad 1 pagina, teeltkaart (kap N + N+1) 1 pagina, alle 40 kaarten van een ronde 20 pagina's: `test_rondeblad_en_teeltkaart_met_een_klik_als_pdf`, en in de container (`tests/container_rooktest.py`, zie hieronder). Het aantal pagina's wordt na elke conversie gecontroleerd. |
| Een mislukte conversie geeft een duidelijke melding en het Excel-bestand blijft beschikbaar | **Gedaan.** LibreOffice met een fout (`test_mislukte_conversie_…`: melding met de foutregel), een hangende conversie (afgebroken na de *tijdslimiet*, start 120 s; tijdelijke bestanden opgeruimd) en een afwijkend aantal pagina's (`test_verkeerd_aantal_paginas_…`) geven een pagina met de melding en een knop naar hetzelfde bestand als Excel. Zonder sjabloon: melding "Upload je werkboek in Beheer → Sjabloon". |
| Een afdruk van het gevulde rondeblad en van een teeltkaart is naast het origineel niet te onderscheiden | **Synthetisch: gedaan (0 pixels verschil). Echt: wacht op het echte werkboek, en op Leon voor de vergelijking met een afdruk uit Excel.** `test_afdruk_niet_te_onderscheiden_van_origineel`: de kaart uit het werkboek zelf (in LibreOffice herberekend met kap en ronde in N1/N2) en de kaart uit de app worden allebei naar pdf en naar een grijswaardenafbeelding (60 dpi) omgezet en over elkaar gelegd: **0,000 % van de pixels verschilt** (teeltkaart en rondeblad). Beide afdrukken komen uit LibreOffice; een afdruk uit **Excel** kan er anders uitzien (regelafstand, lettertypevervanging). Dat kan hier niet (geen Excel); zie de stappen hieronder. |
| Voor tien steekproefkappen zijn alle velden en de 16 momenten met streeflengte gelijk aan wat het werkboek toont | **Synthetisch: gedaan (10 kaarten × 69 cellen, 0 verschillen). Echt: wacht op het echte werkboek.** `test_tien_steekproefkappen_gelijk_aan_werkboek`: het werkboek wordt met tien kopieën van de teeltkaart (elk met een andere kap/ronde in N1/N2) in één keer door LibreOffice herberekend, en alle toegewezen cellen (kop, onderregel, 16 × datum/LD-dag/streeflengte) worden vergeleken met de app. De synthetische teeltkaart heeft echte formules (`INDEX(INDIRECT("'Ronde "&N2&"'!…"),N1)`, de weekdagregel `CHOOSE(…,0,1,0,1,0,2,1)` en de streeflengtetabel `AI1:AM41`), dus de vergelijking toetst de app tegen een onafhankelijke rekenweg. Steekproef zonder jaarwisseling, want daar rekent het werkboek met 52 weken (B1). |
| De kaart is op te vragen met alleen kap en ronde | **Gedaan.** `/afdruk/teeltkaart.pdf?ronde=3&kap=7` (of het scherm *Afdrukken*: ronde en kap kiezen, met voorbeeld op het scherm); zonder kap: alle kaarten van de ronde in één bestand. |

### Wat met het echte werkboek nog moet (fase 2)

1. In de app: **Beheer → Sjabloon** → het echte `Teeltprogramma maken …xlsx` uploaden. De controle toont de bladen,
   de afdrukbereiken, de gebruikte lettertypen (en of ze in de container staan of metrisch gelijk zijn) en de
   formulecellen die de app niet vult.
2. **Controleafdruk met veldnamen** (Beheer → Sjabloon): laat zien in welke cel elk veld landt. De celtoewijzing
   van de teeltkaart is een aanname (alleen N1/N2 en rij 11–26 staan in het bouwplan; open vraag B15); aanpassen
   in *Beheer → Instellingen → Afdrukken*.
3. Tien steekproefkappen: in de container `python -m teeltplanning.afdruk.controle "/data/sjabloon/sjabloon-….xlsx"
   --kaarten 10` (na het importeren van hetzelfde werkboek), of buiten de NAS
   `TEELT_ECHT_PROGRAMMA=<pad> TEELT_ECHT_JAREN='1:2018,…' pytest -s tests/test_afdruk.py -k echt`.
4. Overleggen met Excel: één kaart in Excel afdrukken als pdf (kap en ronde in N1/N2), dezelfde kaart uit de app,
   en beide met `teeltplanning.beeld.pdf_naar_beeld`/`beeldverschil` (of gewoon op elkaar gelegd op het scherm)
   vergelijken. Leg de uitkomst hier vast.

### Tests en container fase 2

* `pytest`: 102 geslaagd, 4 overgeslagen (echte bestanden); `ruff check` en `ruff format --check` schoon; browser-rooktest geslaagd.
* Docker (lokaal): image **806 MB** (geen nieuwe pakketten; LibreOffice zat er al in). Start als 568,
  `/health` = `{"status":"ok","versie":"0.3.0"}`, healthcheck `healthy`. `tests/container_rooktest.py` tegen de
  container: eerste beheerder, synthetisch werkboek importeren, sjabloon uploaden, dan **in de container** naar pdf:
  rondeblad 1 pagina (3,3 s, eerste conversie), teeltkaart 1 pagina (0,9 s), alle 40 kaarten 20 pagina's (1,4 s);
  Excel-download werkt; `/data/tmp` is daarna leeg. Deze rooktest draait ook in CI.
* Netwerk tijdens de conversie: in de container is `unshare -rn` niet toegestaan (Docker-standaardprofiel), dus
  de conversie draait zonder eigen netwerknaamruimte; externe koppelingen zitten niet in de afdrukkopie en het
  proces krijgt een proxy die nergens heen gaat (`127.0.0.1:9`). Waar `unshare -rn` wel kan, gebruikt de app het.

## Fase 3 — Licht, energie en dichtheid (versie 0.4.0)

| Klaar als | Uitkomst |
|---|---|
| De eenheidstests met proefcijfers uit §6.5 slagen | **Gedaan.** `tests/test_kern_licht.py`: `7,3 × 938 / 63,8 = 107,3 g`, `7,5 × 640 / 48,8 = 98,4 g` en `6,8 × 834 / 98 = 57,9 planten/m²` (binnen 0,2), plus de omkering `lbe_gerealiseerd`, afronden op de dichtheidsreeks, terugval op het seizoensgemiddelde (indicatief) en gemengde kappen (gewogen naar bedden). |
| De berekende LBE van de 22 kappen uit het belichtingsblad ligt binnen een met Leon afgesproken marge van de LBE-realisatie, of het verschil is verklaard | **Gebouwd; wacht op het echte bestand en op antwoorden.** De kaptabel (rij 87–127) wordt geïmporteerd als ijkpunten; *Beheer → LBE en dichtheid* zet per kap de LBE-prognose en -realisatie uit het blad naast de eigen LBE (gewicht realisatie × dichtheid uit het plan / lichtsom van de app), met verschil en oordeel. Synthetisch: 22 ijkpunten ingelezen en getoond (`test_ijkpunten_…`); de getallen zijn verzonnen, dus een vergelijking zegt niets. Nodig: het echte werkboek, de **marge** (B24), de **kastransmissie** (vraag 6), de **maanden van de lampgroepen** (vraag 1), het jaar van het blad (vraag 2), en de bijbehorende teelten in het geïmporteerde programma (kap + plantdatum). Tot dan staat bij elke regel waarom de LBE onbekend is. |
| Dimgewogen uren per dag uit `data` kloppen met een handmatig nagerekende steekproef van vijf dagen | **Synthetisch: gedaan. Echt: wacht op het bestand (test klaar).** `test_dimgewogen_uren_steekproef_vijf_dagen` rekent onafhankelijk na (het hele blad met openpyxl, Σ dimstand/100 × 5/60, elke tijdstempel één keer) voor vijf volledige dagen en drie modules; alle 15 waarden gelijk (zie hieronder). Met het echte bestand: `TEELT_ECHT_BELICHTING=<pad> TEELT_ECHT_BELICHTING_JAAR=2023 pytest -s tests/test_belichting.py -k echt` kiest vijf willekeurige volledige dagen en rekent ze op dezelfde manier na. |
| Ontbrekende perioden in de belichtingsdata worden gemeld en leveren geen stilzwijgend ingevulde lichtsom | **Gedaan.** Het importvoorbeeld meldt ontbrekende intervallen als perioden (synthetisch: 5 juli – 26 oktober 2024 en 28 oktober 03:00–04:00), onvolledige dagen, dubbele tijdstempels, regels buiten tijdvolgorde en dagen met zomer-/wintertijdovergang (27 oktober 2024). Een teelt waarvoor één dag licht ontbreekt, krijgt lichtsom **onbekend** met de ontbrekende dagen en de reden (`test_lichtsom_in_het_plan_en_ontbrekende_perioden`, `test_ontbrekende_dag_geeft_geen_stilzwijgende_lichtsom`); een onvolledige dag telt standaard als ontbrekend (instelling). Ook onbekende transmissie, lampgroepperiode, intensiteit of straling geven een reden in plaats van een getal. |
| kWh per week sluit aan op een door Leon aangeleverde meterstand of factuur, of het verschil staat in `OPEN_VRAGEN.md` | **Niet gedaan: wacht op een meterstand of factuur (B25)** en op de maanden van de lampgroepen (vraag 1) en de bron van de branduren van LED fase 1 sinds juli 2024 (vraag 3). Het scherm *Energie* toont kWh en kosten per week voor het bedrijf en per kap per week; zolang de lampgroepperiodes onbekend zijn staat daar "onbekend" met de reden. |

Steekproef dimgewogen uren (synthetisch werkboek, uren per dag):

| dag | module 101 | module 120 | module 140 |
|---|---|---|---|
| 1-7-2024 | 8,9250 | 8,6167 | 8,7792 |
| 2-7-2024 | 9,3500 | 8,1792 | 8,7750 |
| 3-7-2024 | 8,7667 | 9,0917 | 8,5708 |
| 4-7-2024 | 9,1083 | 8,0292 | 9,2083 |
| 26-10-2024 | 10,7917 | 10,9792 | 10,1042 |

App en handmatige berekening zijn gelijk (verschil < 10⁻⁹).

Verder in fase 3:

* **Lampgroepen** (§6.4) als beheertabel met de vijf bevestigde groepen als startgegevens; `geldig_vanaf`/`geldig_tot`
  leeg (vraag 1). SON-T volledig (120 µmol, 50 kW) staat op "indicatief" (afgeleid); LED fase 2 heeft 27,5 kW per kap,
  afgeleid uit "samen 47,5 kW" − 20 kW (status indicatief, in de opmerking uitgelegd).
* **Importer §7.2** als achtergrondtaak met voortgang: weekuren (op celtype, 0–168 gecontroleerd, jaar gevraagd),
  kaptabel, kostenvergelijking AK–AM, teeltschema (860 regels, jaar gevraagd) en het tabblad data streamend
  (XML-regel voor regel). Idempotent (tweede bevestiging en herimport maken niets dubbel).
* **Weerbronnen** (§10), alle standaard uit, alleen met nep-HTTP getest (een fixture laat elk echt verzoek falen):
  Open-Meteo historie + verwachting (MJ/m² × 100 = J/cm², incrementeel bijwerken, adres instelbaar, melding over
  niet-commercieel gebruik); weeknormalen P10/P50/P90 per ISO-week (alleen volledige weken, laatste 30 jaar,
  minimaal 20); KNMI: sleutel versleuteld (Fernet, `/data/geheim.key`), verbinding testbaar, **waarden inlezen staat
  uit** tot variabele, eenheid en bestandsformaat zijn geverifieerd (B23); EnergyZero: dagprijzen (€/kWh → €/MWh).
* **Lichtsom** in de rekenkern, met prefixsommen per kap: het hele plan (34 × 40) met acht jaar dagdata in
  ±140–165 ms (test, grens 200 ms; zonder lichtdata ±30 ms).
* **Dichtheidsvoorstel** (LBE × lichtsom / doelgewicht, afgerond op de reeks, niet-afgeronde waarde zichtbaar) en
  **gewichtsprognose** (LBE × lichtsom / dichtheid, met band uit P10/P90) als voorstel; handmatig wint altijd.
  Doelgewicht per ras is onbekend (vraag 7), dus er is nog geen dichtheidsvoorstel.
* **Energie en kosten** per teelt (teeltpagina), per kap per week en per week voor het bedrijf (scherm *Energie*);
  **scenariovergelijking** (§6.6) op *Energie → Scenariovergelijking* met de startwaarden uit AK–AM zichtbaar.

### Tests en container fase 3

* `pytest`: 131 geslaagd, 5 overgeslagen (echte bestanden); `ruff check` en `ruff format --check` schoon;
  browser-rooktest geslaagd. Geen echte netwerkverbindingen in tests (fixture `geen_netwerk`).
* Docker (lokaal): image **830 MB** (+24 MB door `cryptography`). Start als 568, `/health` =
  `{"status":"ok","versie":"0.4.0"}`, healthcheck `healthy`. `tests/container_rooktest.py`: naast de pdf's van fase 2
  (rondeblad 1 pagina, kaart 1 pagina, alle kaarten 20 pagina's) nu ook het synthetische belichtingswerkboek
  importeren in de container (achtergrondtaak): 2.158 weekuren, 22 ijkpunten, 860 teeltschemaregels, 280
  belichtingsdagen; schermen Energie, LBE, Bronnen en Scenariovergelijking geven 200; `/data/tmp` leeg.

## Fase 4 — Voorstellen met model (versie 0.5.0)

| Klaar als | Uitkomst |
|---|---|
| Een model wordt alleen "gevalideerd" als het de seizoensbasis verslaat in de voortschrijdende backtest, en de backtestuitkomsten zijn in de app te zien | **Synthetisch: gedaan. Echt: wacht op historie met lichtsom.** `voorstel/model.py`: per toetsjaar N+1 trainen t/m N (`test_backtest_voortschrijdend_trainen_tm_jaar_n_toetsen_op_n_plus_1`). Synthetische historie met een echt lichtsignaal: LBE-identiteit MAE 1,6 g tegenover seizoensbasis 21,1 g → *gevalideerd* en gekozen; dezelfde historie met ruis als gewicht: geen enkele trede gevalideerd, gekozen = seizoensbasis (`test_model_alleen_gevalideerd_als_het_de_seizoensbasis_verslaat`); ook drempel en minimum toetsteelten (`test_drempel_en_minimum_toetsteelten`) en de temperatuurtrede (`test_temperatuurcorrectie_alleen_als_ze_wint`). *Beheer → Model* toont per doel en trede status, toetsteelten, fout tegenover de seizoensbasis en de fout per toetsjaar (`test_trainen_backtest_en_uitleg_bij_voorstel`). Met het echte werkboek: zolang transmissie (vraag 6) en lampgroepmaanden (vraag 1) onbekend zijn, is de lichtsom onbekend, kunnen LBE, ridge en bomen niet rekenen ("onvoldoende gegevens") en blijven alle voorstellen **indicatief** op de seizoensbasis. |
| Elk voorstel toont bron, bandbreedte, modelversie en drie vergelijkbare teelten | **Gedaan (synthetisch).** Teeltpagina: per veld met een voorstel vaste zinnen voor bron, bandbreedte, modelversie en status, en de drie meest vergelijkbare eerdere teelten (kap, plantdatum, waarde, bron). Voor rekenregels (keten, uitvaltabel) staat er dat het een rekenregel is; voor dichtheid en uitval komen de vergelijkbare teelten uit dezelfde modelversie. Getest in `test_trainen_backtest_en_uitleg_bij_voorstel` en `test_train_en_voorspeller_leveren_bron_band_modelversie_en_drie_vergelijkbare`. |
| Alles draait zonder GPU en training blokkeert de webapp niet | **Gedaan.** Zuivere Python, geen nieuwe pakketten (ARCHITECTUUR.md). *Nu trainen* komt binnen 5 s terug (in de test < 0,1 s), `/health` antwoordt tijdens de training en de backtest draait in een **ander proces** (pid in het artefact ≠ pid van de webapp, getest). Het hele plan met modelvoorstellen: ±45 ms (`test_hele_plan_met_modelvoorstellen_onder_200_ms`). |

Verder in fase 4:

* **Historie** (`voorstel/historie.py`): afgesloten teelten uit het vastgestelde plan (alleen handmatige of
  geïmporteerde waarden; gewicht = kolom X), aangevuld met het teeltschema via kap + plantdatum. Op het synthetische
  werkboek: ruim 500 teelten uit het plan (test). Hertrainen verandert geen enkele handmatige waarde (test).
* **Ladder**: LD-dagen en verlenging alleen seizoensbasis; reactietijd + temperatuurcorrectie (uit, B34); gewicht
  seizoensbasis → LBE → ridge → bomen met monotone beperkingen (lichtsom stijgend, dichtheid dalend).
* Zonder getraind model verandert er niets ten opzichte van fase 3 (geen voorstellen voor LD/reactietijd; gewicht
  uit de LBE-berekening).

### Tests fase 4

* `pytest`: 145 geslaagd, 5 overgeslagen (echte bestanden); `ruff check` en `ruff format --check` schoon.
* Image: geen nieuwe pakketten (zie fase 6 voor de containerbuild).

## Fase 5 — Afzetpieken, genereren en zoeken (versie 0.6.0)

| Klaar als | Uitkomst |
|---|---|
| Elke datumregel is getest tegen minstens drie bekende jaren | **Gedaan.** `tests/test_afzet.py`: alle negen startregels tegen 2024, 2025, 2026 en 2027 (Valentijnsdag, Vrouwendag, Britse moederdag 10-3/30-3/15-3/7-3, Pasen 31-3/20-4/5-4/28-3, moederdag 12-5/11-5/10-5/9-5, Franse moederdag 26-5/25-5/31-5/30-5 plus de Pinksteruitzondering in 2004 (6 juni), Allerheiligen, Totensonntag 24-11/23-11/22-11/21-11, Kerst); Pasen ook tegen het pakket `holidays`; foute regels geven een uitleg en worden in Beheer geweigerd. De regels zelf zijn **nog niet door de teler bevestigd** (zo gemarkeerd in de app); venster en belang zijn onbekend (vraag 9, B39). |
| Genereren levert voor een leeg jaar een volledig, conflictvrij plan op | **Synthetisch: gedaan.** Kern (`test_genereren_leeg_jaar_volledig_en_conflictvrij`): 40 kappen, leeg jaar 2025, rasverdeling 5:3:2, vier kappen per plantdag, oogst alleen op werkdagen → elke teelt heeft plantdatum, LD-dagen, reactietijd, verlenging en oogst, geen fout, geen overlap, geen verkeerde plant- of oogstdag, en elke kap is gevuld tot het eind van het jaar. Webapp (`test_web_genereren_zoeken_vergelijken`): synthetisch werkboek importeren, model trainen, kopie maken, het eerste lege jaar genereren → alle actieve kappen gevuld, volledig en conflictvrij, rassen toegekend, **geen enkele handmatige waarde** aangemaakt; een tweede keer voor hetzelfde jaar wordt geweigerd; in het vastgestelde plan wordt genereren geweigerd. Met echte gegevens: rasverdeling en eventueel terugvalwaarden ontbreken nog (B37, B38). |
| De zoekfunctie past niets automatisch toe en raakt geen handmatige waarden; dat staat in een test | **Gedaan.** `test_zoeken_past_niets_toe_en_raakt_geen_handmatige_waarden` (kern: invoer en plan na het zoeken identiek, geen voorstel voor een handmatig veld, het eerste voorstel verschuift een oogst in het moederdagvenster) en `test_web_genereren_zoeken_vergelijken` (webapp: alle `veldwaarde`-regels vóór en na het zoeken gelijk; een voorstel voor een veld dat intussen handmatig is, wordt geweigerd en de handmatige waarde blijft; toepassen zet een handmatige waarde met reden "zoekvoorstel …"). |
| Twee scenario's zijn naast elkaar te vergelijken op oogst per week, afzetdekking en kWh | **Synthetisch: gedaan.** *Scenario's → Twee scenario's vergelijken*: per week oogst A/B/verschil en kWh A/B/verschil, per afzetmoment venster, belang en dekking A/B/verschil, met totalen (test). kWh telt alleen dagen met een teelt in de kap; met echte gegevens is kWh onbekend tot de lampgroepmaanden bekend zijn (vraag 1). |

Verder in fase 5:

* **Jaaroverzicht** (*Jaar*): kappen × weken (P, LD, KD, O), oogst en kWh per week, afzetmomenten, feestdagen
  (aantal, met namen als tooltip) en de markering "lage prijs" (B35).
* **Scenario's**: kopiëren, vaststellen (beheerder; het vorige wordt concept), genereren en zoeken per scenario.
* **Zoeken** draait op de achtergrond in een apart proces met de ingestelde maximale rekentijd (start 30 s): op het
  synthetische plan 3 voorstellen uit ±4.900 geprobeerde wijzigingen in ±20 s. Zonder ingestelde gewichten (B36)
  geeft de zoekfunctie geen voorstellen, met die uitleg.
* **Toegestane oogstdagen** (*Kalender*, start onbekend): als ze zijn ingevuld, krijgt een oogst op een andere dag
  een waarschuwing en kiest genereren een verlenging die past.

### Tests fase 5

* `pytest`: 171 geslaagd, 5 overgeslagen (echte bestanden); `ruff check` en `ruff format --check` schoon.
* Nieuwe afhankelijkheid `holidays` 0.107 (+ `python-dateutil`, `six`; ±10 MB).

## Fase 6 — Koppelingen (versie 0.7.0)

Alleen §11.1 en §11.2. De pull requests uit §11.3 (Gewichtsverwachting, Bedrijfsdashboard) zijn **niet** gemaakt:
die wachten op akkoord van Leon (B42).

| Klaar als | Uitkomst |
|---|---|
| Contracttests: 401 zonder/ongeldige/ingetrokken sleutel | **Gedaan.** `test_401_zonder_ongeldige_en_ingetrokken_sleutel`: geen kop, `Basic`, onbekende sleutel, één teken anders, sleutel zonder `Bearer`, ingetrokken sleutel → `401 {"fout": "ongeldige_koppelsleutel"}`. |
| Sleutel eenmalig getoond en alleen als SHA-256-hash opgeslagen | **Gedaan.** `test_sleutel_eenmalig_getoond_en_alleen_als_hash_opgeslagen`: JSON en scherm tonen de sleutel één keer; de tabel bevat alleen hash + voorvoegsel (geen kolom bevat de sleutel); de lijst en het auditlog tonen hem nooit. |
| 403 `koppeling_uitgeschakeld` vóór alle andere controles | **Gedaan.** `test_403_uitgeschakeld_komt_voor_alle_andere_controles` (zonder sleutel, ongeldige sleutel, geldige sleutel × onbekende dataset en ongeldige parameters). |
| Paginering met cursor, `sinds`, 404, 422 en 429 (120/min per sleutel) | **Gedaan.** `test_paginering_sinds_404_en_422` (1.360 planningregels in pagina's van 97 = dezelfde, stabiele volgorde; standaard 100; `sinds` morgen/gisteren; een wijziging in één kap geeft met `sinds` alleen die kap terug; 404 vóór 422; 422 voor limiet 0/501/abc, `sinds` zonder tijdzone, foute cursor, onbekende parameter); `test_429_per_sleutel` (limiet per sleutel, `Retry-After`). |
| Niet-beheerders kunnen geen sleutels beheren | **Gedaan.** `test_niet_beheerders_kunnen_geen_sleutels_beheren`: medewerker en lezer krijgen 403 op lijst, aanmaken, intrekken en de beheerpagina; niet aangemeld 401. |
| Alleen het vastgestelde scenario wordt gedeeld; KPI's, meldingen en datasets volgen §11.1 | **Gedaan (synthetisch).** `test_info_samenvatting_en_alleen_het_vastgestelde_plan`: de zes KPI's in de volgorde van §11.1 met geldige status (`null` ⇒ `onbekend`), meldingen, de vier datasets met precies de velden uit §11.1; een kopie (concept) wordt nooit gedeeld; zonder vastgesteld plan lege datasets en onbekende KPI's; geen vrije tekst of gebruikersnamen. `energieprognose.dimgewogen_uren` is `null` (B41). |
| Afnemen uit Gewichtsverwachting: versleutelde bronnen, instelbaar interval, "nu ophalen" | **Gedaan (nep-server).** `test_koppelbron_versleuteld_ophalen_koppelen_en_kalibratie`: sleutel versleuteld opgeslagen (niet leesbaar in de database, wel te ontsleutelen), standaard uit (geen verzoek), na aanzetten *Nu ophalen* → teelten, oogsten, metingen en prognoses met paginering (limiet 500). Interval: `koppeling.ophaalinterval_uren` in de achtergrondtaak. Met de echte Gewichtsverwachting: **wacht op** adres en koppelsleutel van de NAS. |
| Koppeling op kap + plantdatum ± dagen; niet-gekoppelde teelten in een lijst; niet raden | **Gedaan.** Zelfde test: exact en ± 1 dag gekoppeld; te ver weg, kap zonder planteelt en twee bronteelten op één planteelt staan met reden in de lijst op *Beheer → Koppelingen*. De tolerantie zelf is onbekend (B33). |
| Gekoppelde teelten voeden de kalibratie | **Gedaan (synthetisch).** Zelfde test: *Kalibreren* maakt LBE-cellen uit `92 g × 50 / lichtsom` van de gekoppelde teelten; de modeltraining telt ze als bron `gewichtsverwachting`. Reactietijd wordt alleen overgenomen als de gerealiseerde oogstdatum is gekozen (B40). |
| 403 `koppeling_uitgeschakeld` getoond als "staat uit bij de bron (…)", niet als storing | **Gedaan.** `test_403_bij_de_bron_is_staat_uit_geen_storing`: status `uit_bij_bron`, tekst "Staat uit bij de bron (Gewichtsverwachting: Beheer → Bedrijfskoppeling)"; een 401 is wel een fout. |
| Schakelaar in deze app: "Beheer → Koppelingen → Bedrijfskoppeling"; dashboard niet aangepast | **Gedaan.** Het dashboard is niet aangepast; de plek staat in `docs/koppeling-api.md` (met de opmerking dat de dashboardtabel pas met §11.3 volgt). |
| Na een schone installatie staan alle externe bronnen en de koppeling uit (test) | **Gedaan.** `test_schone_installatie_alle_externe_bronnen_en_koppeling_uit`: geen enkele bool in *Bronnen* of *Koppeling* staat aan, geen koppelbronnen of sleutels, alle API-routes 403, de achtergrondtaken maken geen verbinding. |
| `docs/koppeling-api.md` met het app-specifieke deel | **Gedaan.** |

Verder in fase 6: prognose uit Gewichtsverwachting op de teeltpagina naast de planprognose (alleen als die daar
*gevalideerd* is); CLI `beheer_cli koppelsleutel <naam>`.

### Tests en container fase 6

* `pytest`: 182 geslaagd, 5 overgeslagen (alleen de tests met echte bestanden); `ruff check` en
  `ruff format --check` schoon. Geen echte netwerkverbindingen (fixture `geen_netwerk`; de koppelbron draait tegen
  een nep-server).
* Docker (lokaal, met de proxy-CA alleen als build-secret): image **848 MB** (fase 3: 830 MB; +18 MB vooral
  `holidays`; het model voegt niets toe). `tests/container_rooktest.py` tegen de container (gebruiker 568,
  `/health` = 0.7.0): pdf's (1/1/20 pagina's), belichtingsimport, **training in een apart proces in de container**
  (2.030 teelten, 2,1 s, `/health` bleef antwoorden), schermen Model/Jaar/Scenario's/Afzet/Vergelijken/Koppelingen
  200, `/api/koppeling/v1/info` → 403 `koppeling_uitgeschakeld`; `/data/tmp` leeg.
