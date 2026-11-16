# Rekenkern

De rekenkern (`teeltplanning/kern/`) is een zuiver Python-pakket: geen web, geen database, geen bestanden, en
geen imports van buiten `kern/` (test `tests/test_kern_zuiver.py`). Eén functie rekent het hele plan door:

```python
from teeltplanning.kern import bereken_plan
plan = bereken_plan(invoer: PlanInvoer, parameters: Parameters)  # deterministisch, zonder neveneffecten
```

* `PlanInvoer` bevat per teelt (kap, ronde) de velden met hun **voorstel** en **handmatige** waarde.
* `Parameters` bevat de kappen (eigen oppervlak, eigen wisseltijd) en `KernInstellingen`, die de
  planningsdienst (`teeltplanning/planning.py`) opbouwt uit het instellingenregister. De kern leest zelf nooit
  instellingen of de database.
* Elke getal in `kern/`, `voorstel/` en `afdruk/` dat geen wiskundige of kalenderconstante is (0, 1, 7, 24, 60,
  100, 3600), laat de test op harde constanten falen.

Rekentijd voor het hele plan (34 rondes × 40 kappen = 1.360 teelten): **circa 25 ms** (test
`tests/test_prestatie.py`, grens 200 ms).

## Drie lagen per veld

Velden: plantdatum, LD-dagen, reactietijd, verlenging, tussenlicht (datum), dagen tussenlicht, dichtheid,
gewicht, uitval.

| Laag | Betekenis |
|---|---|
| voorstel | wat de app berekent (fase 1: de plantdatum uit de keten en de uitval uit de tabel "uitval per oogstweek") |
| handmatig | wat de gebruiker (of de import) invult; **wint altijd** |
| effectief | handmatig als die er is, anders voorstel; hiermee rekent de kern |

Ontbreekt een effectieve waarde, dan is alles wat ervan afhangt `None` ("onbekend") en krijgt de regel een
melding. Er wordt niets aangevuld.

## Datumketen (`kern/keten.py`, BOUWPLAN §6.1)

```
licht_uit   = plantdatum + ld_dagen
oogst       = licht_uit + reactietijd + verlenging
teeltduur   = oogst − plantdatum                        (dagen)
licht_aan   = licht_uit + [Datumketen → Dagen van licht uit tot licht aan, start 12]
licht_uit_2 = licht_aan + dagen_tussenlicht
voorstel plantdatum volgende teelt in de kap
            = eerstvolgende toegestane plantdag ≥ oogst vorige teelt + wisseltijd
              [Kalender → Toegestane plantdagen, start ma–za; Wisseltijd, start 0; per kap te overschrijven;
               Blokkadeperioden worden overgeslagen]
```

Voorbeeld: plantdatum maandag 2 maart 2026 (week 10-1), LD 14, reactietijd 50, verlenging 2 →
licht uit 16 maart (12-1), oogst 7 mei (19-4), teeltduur 66 dagen, licht aan 28 maart (13-6). Volgt de
volgende teelt in de kap de keten, dan is haar voorstel 7 mei (19-4, een donderdag; toegestaan).

* Een wijziging schuift alle latere teelten in dezelfde kap mee, tot aan de eerstvolgende teelt met een
  handmatig vastgezette plantdatum (test `test_wijziging_schuift_door_tot_vastgezette_plantdatum`).
* Overlap (plantdatum vóór de oogst van de vorige teelt in de kap) is een **fout** op de rij; opslaan wordt
  alleen geblokkeerd als *Datumketen → Gedrag bij overlap* op "blokkeren" staat (start: waarschuwen).
* Grenzen (LD-dagen, reactietijd, verlenging, dichtheid) zijn instellingen; daarbuiten een fout op de rij.

## Kalender (`kern/kalender.py`, §4.4)

Intern altijd `date`. Weergave `week-dag` (bijv. `44-6`), met het ISO-jaar apart (of als `44-6 (2026)`, instelling
*Weergave week-dag*). ISO-jaren met 53 weken worden goed gerekend: week 53 van 2026 = 28 december 2026 t/m
3 januari 2027. Invoer `44-6` zonder jaar wordt gelezen als de datum met die week-dag die het dichtst bij de
huidige waarde ligt; `44-6-2026` legt het jaar vast.

Voorbeeld jaarwisseling: plant 2026 week 51 dag 3 (16 december), LD 14 → licht uit 30 december 2026 =
**53-3**. Het werkboek, dat met 52 weken per jaar rekent, zou hier `1-3` tonen.

## Aantallen (`kern/gewas.py`, §6.2)

```
geplant     = dichtheid × oppervlak_kap
oogstaantal = geplant × (1 − uitval)
```

* Oppervlak: de eigen waarde van de kap (Beheer → Kappen), anders *Bedrijf → Oppervlak per kap* met de
  **ingangsdatum** die geldt op de plantdatum. Startwaarde onbekend (open vraag 4): dan zijn geplant,
  oogstaantal en cumulatief onbekend. Voorbeeld: dichtheid 50 × 726 m² = 36.300 geplant; uitval 8% →
  33.396 oogstaantal.
* Uitvalvoorstel: tabel *Gewas → Uitval per oogstweek* (ISO-week van de oogst); start leeg = onbekend.

## Vergelijking met het werkboek (`importers/vergelijking.py`)

Per kapregel rekent de app licht uit, bloei en teeltduur met echte datums uit de invoer zoals het werkboek die
toont, en vergelijkt met de gecachte uitkomst. Klassen: `gelijk`, `52_weken` (de afwijking is precies de
52-wekenfout van het werkboek: week-dag + dagen met 52 weken per jaar geeft de cache), `afwijking`
(onverklaard) en `onvolledig`.

## Teeltkaart (`kern/teeltkaart.py`, §9.2)

```
eerste meetmoment = eerstvolgende meetdag ≥ licht uit       [Teeltkaart → Weekdagen van de meetmomenten, start ma/wo/vr]
volgende momenten = steeds de volgende meetdag (ma→wo +2, wo→vr +2, vr→ma +3), [Aantal meetmomenten, start 16]
LD-dag            = moment − licht uit (dagen)
seizoen           = seizoen van de plantweek                 [start winter 43–52, voorjaar 1–6, zomer 7–36, najaar 37–42]
streeflengte      = tabel[LD-dag][seizoen]                   [Streeflengtetabel; geen waarde = leeg]
slak/remstof/pluizen/netten = licht uit + offset             [start onbekend → leeg]
```

Dit is precies de werkboekregel "weekdag licht uit 1…7 → moment op weekdag 1, 3, 3, 5, 5, 1, 1 en LD-dag 0, 1, 0,
1, 0, 2, 1" (test `test_eerste_moment_zoals_werkboek`). Voorbeeld: plant 2 maart 2026 (10-1), LD 9 → licht uit
woensdag 11 maart (11-3); momenten 11-3 (LD 0), 11-5 (2), 12-1 (5), 12-3 (7) … tot LD 35; plantweek 10 = zomer,
dus LD 7 → 35 cm. Een plantweek 53 valt in geen seizoen: streeflengte leeg met melding (open vraag B16).
