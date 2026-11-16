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
