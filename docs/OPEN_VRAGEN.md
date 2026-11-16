# Open vragen voor Leon

Niet zelf invullen (BOUWPLAN §0.2 en §15). Elk antwoord wordt in de app ingevuld als **instelling** of in een
**beheertabel**, niet in de code. Tot er een antwoord is, staat de waarde op "onbekend" (`null`) en toont de app
wat daardoor niet berekend kan worden.

Status: **open** · **deels** (gedeeltelijk beantwoord) · **beantwoord**.

## Vragen uit het bouwplan (§15)

| # | Vraag | Nodig voor | Status | Waar het antwoord terechtkomt / wat er nu niet werkt |
|---|---|---|---|---|
| 1 | Precieze maand van de overgang SON-T → LED fase 1 en van SON-T → LED fase 2 | fase 3 | open | `lampgroep.geldig_vanaf/geldig_tot` (fase 3) |
| 2 | Is het tabblad `belichting` (week 36 t/m 10) winter 2023/24? | fase 3 | open | jaar van de weekuren bij import §7.2 |
| 3 | Waar komen de branduren van LED fase 1 (modules 1–40) vandaan sinds juli 2024? | fase 3 | open | bron voor branduren |
| 4 | Oppervlak per kap: 726 of 750 m², en is dat voor alle kappen gelijk? | fase 1 | open | instelling *Oppervlak per kap* (`bedrijf.oppervlak_per_kap_m2`, met ingangsdatum) en per kap in Beheer → Kappen. Nu `null`: **geplant aantal en oogstaantal worden niet berekend** (scherm toont "onbekend: oppervlak per kap") |
| 5 | ISO-jaar per ronde in het werkboek | fase 1 | open | wordt per ronde gevraagd in het importvoorbeeld; een ronde zonder jaar wordt niet geïmporteerd |
| 6 | Kastransmissie, of gemeten PAR in de kas om hem te ijken | fase 3 | open | instelling transmissie (startwaarde `null`) |
| 7 | Doelgewicht per ras | fase 3 | open | `ras.doelgewicht_g` (nu `null` voor a/p/t/b) |
| 8 | Toegestane plantdagen per week en wisseltijd tussen teelten (ook bij stomen) | fase 1 | deels | startwaarden uit het bouwplan: plantdagen **ma t/m za**, wisseltijd **0 dagen**; graag bevestigen en de wisseltijd bij stomen opgeven (instellingen *Toegestane plantdagen*, *Wisseltijd per kap*, *Blokkadeperioden*) |
| 9 | Verkoopvenster en belang per afzetmoment | fase 5 | open | `afzetmoment.venster_dagen_voor` en `gewicht` |
| 10 | Stroomprijs voor de planning (het werkboek rekent met 80 en met 300 €/MWh) | fase 3 | open | `prijsscenario` |
| 11 | Antwoord van Delphy Digital op de vraag naar een export of API | geen blokkade | open | eventueel een bestandsimporter |
| 12 | Moet de nieuwe repo publiek zijn zoals de andere, en mag de hostpoort 30096 worden? | fase 0 | deels | **repo**: blijft publiek tijdens de bouw en gaat daarna privé (beantwoord). **Poort 30096**: nog bevestigen dat die vrij is op de TrueNAS. Let op: na privé zetten moet het GHCR-pakket publiek blijven of moet TrueNAS met registry-inloggegevens werken (zie TRUENAS.md) |
| 13 | Welke lettertypen gebruikt het werkboek, en mogen die in de image of worden ze geüpload? | fase 2 | open | de image bevat nu Liberation (Arial/Times/Courier-metrisch) en Carlito/Caladea (Calibri/Cambria-metrisch) |

## Ontbrekende documenten en bestanden

| # | Wat | Gevolg |
|---|---|---|
| D1 | Het onderzoeksdocument `Teeltplanning app chrysant onderzoek.md` (§0 noemt het; het hoort in `docs/`) is niet aangeleverd | Het bouwplan is leidend en volledig genoeg voor fase 0–1; bronnen en achtergrond ontbreken in `docs/`. Graag aanleveren. |
| D2 | Het echte werkboek `Teeltprogramma maken …xlsx` | Proef 2 en de fase 1-vergelijking van 1.360 kapregels zijn alleen op een synthetisch werkboek gedaan (zie STATUS.md) |
| D3 | Het echte werkboek `Belichting winterronde.xlsx` | Proef 1 is alleen op een synthetisch werkboek gedaan (zie STATUS.md) |

## Vragen die tijdens de bouw zijn opgekomen

| # | Vraag | Nodig voor | Waarom |
|---|---|---|---|
| B1 | Welke formules staan in het rondeblad in H/I (licht uit) en N/O (bloei)? | fase 1 | De app vergelijkt alleen met de **gecachte uitkomsten**; de 52-wekenfout wordt herkend door het werkboekrekenwerk "week-dag plus dagen, 52 weken per jaar" na te bootsen. Of het echte werkboek precies zo rekent, blijkt pas bij de echte import. |
| B2 | Wat staat er in kolom R van het rondeblad: "teeltverlenging" (bouwplan §7.1) of opmerkingen (zo leest Gewichtsverwachting kolom R)? En wat is dan het verschil met kolom L "dagen verlenging"? | fase 1 | De keten rekent met L als verlenging. R wordt bewaard als bewijs, maar nergens in meegerekend. |
| B3 | Wat betekent de datum/week-dag in kolom K (tussenlicht): de dag dat het tussenlicht begint (licht aan) of eindigt (tweede licht uit)? En waar komt het aantal tussenlichtdagen vandaan? | fase 1/2 | K wordt geïmporteerd als datum (veld *tussenlicht*); licht aan = licht uit + 12 is een instelling. Het aantal tussenlichtdagen (voor licht uit 2) is nu onbekend. |
| B4 | Als de plantweek in een ronde verwijst naar de bloeiweek van de vorige ronde (`='Ronde 2'!N12`) maar de plantdag los is ingevuld: moet de app die plantdatum laten **volgen** (keten) of **vastzetten** zoals het werkboek hem nu toont? | fase 1 | Instelling *Plantdatum bij verwijzing naar vorige ronde* (startwaarde: volgen = keten, zoals §6.1 het beschrijft). Verschillen staan in het importrapport. |
| B5 | Indeling van de bladen `productie cijfers` (uitval per oogstweek) en `productie` | fase 1/3 | Het bouwplan noemt alleen "8% in week 1–11, daarna 5%". De tabel *Uitval per oogstweek* is in Beheer leeg tot hij is ingevuld of geïmporteerd; tot dan is het uitvalvoorstel onbekend. |
| B6 | Welke vakgroepen bestaan er en welke kappen horen erbij? | fase 1 | Het werkboek zet de vakgroep alleen op de eerste regel van een groep; de import vult die door en zet hem in Beheer → Kappen als daar nog niets staat. |
| B7 | Kopteksten van het echte rondeblad (rij 10–11) | fase 1 | De import herkent het rondeblad aan kopteksten (instelbaar in Beheer → Instellingen → Import). De startwaarden zijn overgenomen van Gewichtsverwachting (`schema`, `pl datum`, `licht uit`, `bloei`, `vak`, `w/d`). |
| B8 | Kolommen van de kaptabel op `belichting` vanaf rij 87 en van `teeltschema` | fase 3 | Het synthetische werkboek gebruikt een aangenomen volgorde (EXCEL.md); kolomtoewijzing wordt een instelling. |
| B9 | Rekent het echte belichtingswerkboek met datumstelsel 1900 of 1904? | fase 3 | Proef 1 leest het stelsel uit het werkboek; ter controle. |
| B10 | Wie controleert proef 2 in **Excel** (openen van het gevulde sjabloon)? | fase 0/2 | Hier is alleen LibreOffice beschikbaar; de opmaakvergelijking is automatisch, het openen in Excel zelf moet Leon doen (zie STATUS.md). |
| B11 | Moet `geplant = dichtheid × oppervlak` met het bruto kapoppervlak, of met netto teeltoppervlak (paden eraf)? | fase 1 | Hangt samen met vraag 4 (726 of 750). |
