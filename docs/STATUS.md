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
