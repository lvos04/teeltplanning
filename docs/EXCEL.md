# Excel-werkboeken

De echte werkboeken blijven op de dataset van de NAS en komen nooit in de repo (BOUWPLAN §0.4). Tests gebruiken
synthetische werkboeken uit `tests/synthetisch.py` (`python -m tests.synthetisch <map>`); alle getallen daarin
zijn verzonnen testwaarden.

## Synthetisch `Teeltprogramma maken`

Volgt §7.1. Tabbladen: `productie cijfers`, `Ronde 1` … `Ronde 34`, `productie`, `teeltkaart`,
`weekplanning dekker` (leeg), `Blad2` (leeg).

Rondeblad: titel in `C8` (in `Ronde 34` bewust afwijkend: "Ronde 35"), kop op rij 10–11, regels op rij 12–51.

| Kolom | Inhoud | Synthetisch |
|---|---|---|
| B | vakgroep | alleen op de eerste regel van elke groep van 5 (11, 12, 21, 22, 23, 31, 32, 33 — aanname) |
| C | volgnummer 1–40 | |
| D | schemacode | `a`, `p`, `t`, `3a3p`, één keer vrije tekst `proef` |
| E, F | plantdatum week, dag | ronde 1 letterlijk; daarna om de regel `='Ronde N-1'!N<rij>` (met cache) of letterlijk |
| G | LD-dagen | |
| H, I | licht uit week, dag | formule, 52 weken per jaar: `=MOD(E-1+INT((F-1+G)/7),52)+1`, `=MOD(F-1+G,7)+1` |
| J | reactietijd | |
| K | tussenlicht | tekst `44-6`, een datumcel, of leeg |
| L | dagen verlenging 0–2 | met gegevensvalidatie 0–2 |
| M | planten per m² | 41,1–58,6 |
| N, O | bloei week, dag | formule, zoals H/I met J + L |
| P | teeltduur | `=G+J+L` |
| R | teeltverlenging | soms 1 (betekenis: open vraag B2) |
| T | gewichtsprognose | |
| V | datum geoogst | tekst `34-3` (rondes 1–30) |
| X | gemiddeld gewicht | (rondes 1–30) |
| Y | uitval | 0,05 of 0,08 |
| Z, AB, AD, AF | geplant `=M*726`, oogstaantal, maand, cumulatief | formules met cache |

De formules in H/I/N/O van het **echte** werkboek zijn niet bekend (open vraag B1); de synthetische versie
reproduceert alleen de beschreven 52-wekenfout. Één regel (ronde 5, kap 8) heeft geen plantdatum.

Teeltkaart: afdrukbereik `A1:AG31`, A4 liggend, kap in `N1`, ronde in `N2`, streeflengtetabel `AI1:AM41`
(LD-dag 0–39 met de controlewaarden uit §9.2). De bladen `productie cijfers` en `productie` bevatten alleen een
notitie: hun echte indeling is onbekend (open vraag B5).

## Synthetisch `Belichting winterronde`

Volgt §7.2. `belichting`: rij 2 = `module` + weken 36…52, 1…10 (kolom B–AB); rij 3–42 LED-modules 1–40;
rij 43 leeg; rij 44–83 SON-T-modules 101–140; rij 84 totalen (formule). Weekuren in vijf celtypen
(`[h]:mm` → timedelta, `d "days," hh:mm` → datetime, `hh:mm` → time, getal, één tekstcel), één lege cel en één
cel met 180 uur (> 168). Rij 87 kop + rij 88–127 kaptabel (kolomvolgorde aangenomen: kap, plantdatum, LD dagen,
KD dagen, uren LD, uren KD, uren LED, uren SON-T, µmol, gewicht prognose, gewicht realisatie, LBE prognose,
LBE realisatie, kosten; 22 kappen gevuld). AK–AM: kostenvergelijking 150/200 µmol.

`teeltschema`: 860 regels; kop zoals Gewichtsverwachting hem herkent (kolom 1 `kap`, 2 `planten`, 7 `licht uit`,
16 `leeg`, 19 `gewicht`, 39 uitval, 40 opmerking, 41 alar, 42 reactietijd = teeltduur KD − 2 − 3).
`data`: rij 1 `Gem. licht%`, rij 2 `PCU 1:MOD. LICHT 101` … `140`, rij 3 `%`, daarna 5-minutenregels van
26 t/m 28 oktober 2024 (wintertijdovergang), met een gat van een uur, een dubbele tijdstempel en tien regels
buiten tijdvolgorde.

## Groottegrenzen

Zoals Gewichtsverwachting (docs/EXCEL.md daar): upload maximaal 32 MiB, uitgepakt maximaal 512 MiB, geen
macro's, geen versleutelde werkboeken, geen XML-entiteiten. Vanaf fase 1 instelbaar in Beheer → Instellingen →
Import.

## Importer `Teeltprogramma maken` (fase 1)

Code: `teeltplanning/importers/programma.py` (alleen lezen) en `dienst.py` (importstroom). De veilige
werkboekcontrole (`_validate_archive`: geen macro's, geen versleutelde onderdelen, geen XML-entiteiten,
groottegrenzen), `_source`, `_int_literal`, `parse_schema` en de week/dag-hulpen (`iso_date`, `next_week_date`,
`group_plant_years`) zijn overgenomen uit Gewichtsverwachting, met bronvermelding.

* Een blad heet `Ronde N` en wordt herkend aan de kopteksten (instelling *Import → Rondeblad:
  herkenningskoppen*). Kolommen, eerste/laatste regel en titelcel zijn instellingen.
* Per kapregel wordt van elke kolom bewaard: cel, waarde, formule en gecachte uitkomst (tabel `werkboekregel`,
  zichtbaar op de teeltpagina).
* De vakgroep staat alleen op de eerste regel van een groep en wordt doorgevuld; een lege vakgroep in Beheer →
  Kappen wordt ermee gevuld.
* Het ISO-jaar van de eerste planting wordt per ronde gevraagd; binnen de ronde gaat het jaar één omhoog als de
  plantweek zichtbaar terugspringt (meer dan 20 weken).
* `tussenlicht` (K): week-dagtekst of datumcel → datum rond licht uit. `V` (`34-3`) → datum geoogst rond de
  verwachte oogst. `R` wordt alleen bewaard (open vraag B2).
* Invoerwaarden (G, J, L, M, T, Y en K) worden **handmatige waarden** in het scenario Werkboek. De plantdatum
  ook, behalve als de plantweek een verwijzing naar de vorige ronde is en die ronde ook wordt geïmporteerd: dan
  volgt de plantdatum de keten (instelling, open vraag B4).
* Bevestigen schrijft alles in één transactie; nogmaals bevestigen of herimporteren maakt niets dubbel, en
  waarden die een gebruiker in de app heeft ingevuld blijven staan.

Uitkomst op het synthetische werkboek (1.360 kapregels, ISO-jaren 2020–2026): **1.289 gelijk, 70 52_weken,
1 onvolledig** (bewust een regel zonder plantdatum), **0 afwijking**.

## Teeltkaart in het synthetische werkboek (fase 2)

Het tabblad `teeltkaart` heeft twee kaarten naast elkaar (kap in N1 / AE1 = N1+1, ronde in N2 / AE2 = N2) met
**echte formules** die alles uit het rondeblad halen: `INDEX(INDIRECT("'Ronde "&N2&"'!E12:E51"),N1)` voor plantweek
(C4), plantdag (D4), sortiment (C5), dichtheid (C6), licht uit (H3/I3), LD (K3), bloei (C28/D28) en RT (H28);
licht aan = licht uit + 12 (H4/I4), slak + 7 en remstof + 14 (H6/I6, H7/I7; synthetische offsets); meetmomenten op
rij 11–26 met datum `w-d` (A), LD-dag (C: eerste moment `CHOOSE(weekdag,0,1,0,1,0,2,1)`, daarna +2/+2/+3) en
streeflengte (E, uit `AI1:AM41` met het seizoen van de plantweek). Week-dag rekent met 52 weken per jaar. Deze
indeling is een **aanname** (open vraag B15) en is de startwaarde van de celtoewijzing in Beheer → Instellingen →
Afdrukken.

Tussenlicht als datumcel (kolom K) is in het synthetische werkboek licht uit + 12 als echte datum (vanaf fase 2;
daarvoor een willekeurige datum uit 2001).

## Afdrukken (fase 2)

Code: `teeltplanning/xlsxpakket.py` (techniek: waarden in de werkblad-XML, afdrukkopie, bladen kopiëren) en
`teeltplanning/afdruk/` (wat er in welke cel komt). Een afdrukkopie bevat alleen de af te drukken bladen
(kopieën van het rondeblad of de teeltkaart uit het sjabloon, voor "alle 40" twintig kopieën); andere bladen,
externe koppelingen, draaitabelcaches, de rekenketen en gedefinieerde namen gaan eruit (alleen de afdrukbereiken
worden opnieuw gezet). Formules in de af te drukken bladen worden vervangen door hun uitkomst of leeggemaakt (instelling
*Formules die de app niet vult*). Vóór de conversie worden richting, papierformaat en "passend op één pagina" in het
blad gezet en pagina-einden verwijderd. Een datum gaat als Excel-datum naar een cel met datumnotatie, anders als
`w-d`-tekst. Het rondeblad gebruikt dezelfde kolomtoewijzing als de importer.

## Importer `Belichting winterronde` (fase 3)

Code: `teeltplanning/importers/belichting.py` (alleen lezen), `duur.py` (duurcellen, uit proef 1),
`belichting_dienst.py` (importstroom als achtergrondtaak).

| Deel | Gelezen | Instellingen |
|---|---|---|
| `belichting` rij 2–84 | weekuren per module (rij 2 = weeknummers, kolom A = module), op celtype naar uren; buiten 0–168 = afgekeurd | *Belichting: rijen weekuren*, *Weekuren min/max* |
| `belichting` rij 87–127 | kaptabel (ijkpunten), uren-kolommen als duur | *Belichting: rijen kaptabel*, *kolommen kaptabel* (aanname, B8) |
| `belichting` AK–AM | kostenvergelijking (omschrijving, waarde A, waarde B) | *Belichting: kostenvergelijking* |
| `teeltschema` | per regel kap, week/dag van planten, licht uit, pluizen, netten, leeg; gewicht, teeltduur, LBE, uitval, reactietijd | *Teeltschema: kolommen* (aanname, B8); ISO-jaar van de eerste regel gevraagd |
| `data` | streamend (XML): kopregels `Gem. licht%` / `PCU 1:MOD. LICHT 101…` / `%`, daarna tijd + dimstand per module | *Data: interval* (5 min), *Data: tijdstempel* (begin/eind, B21) |

Uitkomst per module per dag: dimgewogen uren (Σ dimstand/100 × interval), branduren (intervallen met dimstand > 0),
volledig (alle 288 intervallen) en kwaliteit (onvolledig, zomer_wintertijd). Elke tijdstempel telt één keer (een
tweede regel met dezelfde tijd = dubbel, gemeld).

Synthetisch werkboek: het tabblad `data` heeft vanaf fase 3 ook vier volledige dagen vanaf 1 juli 2024 (onderaan,
zodat het blad niet op tijdvolgorde staat en er een lange ontbrekende periode juli–oktober is).
