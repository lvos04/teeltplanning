"""Het instellingenregister (BOUWPLAN §4.3): één plek voor alle rekenwaarden, drempels, lijsten en regels.

Elke instelling heeft een sleutel, groep, label, uitleg in gewone taal, type, eenheid, startwaarde, toegestane
grenzen of keuzes en of `null` ("onbekend") is toegestaan. De waarden hieronder zijn uitsluitend
**startwaarden** bij een schone installatie; de beheerder wijzigt ze in Beheer → Instellingen. Een startwaarde
is alleen ingevuld als het bouwplan hem noemt; anders `None` met een verwijzing naar docs/OPEN_VRAGEN.md.

Opslag: tabel `instelling` (één regel per wijziging, met optionele ingangsdatum). De rekenkern krijgt de
waarden als parameter (`teeltplanning.planning`); `kern/` leest zelf nooit uit de database.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

TYPEN = ("geheel", "getal", "tekst", "bool", "keuze", "weekdagen", "weekbereik", "lijst", "tabel", "toewijzing")
WEEKDAGEN = {1: "ma", 2: "di", 3: "wo", 4: "do", 5: "vr", 6: "za", 7: "zo"}


@dataclass(frozen=True)
class Kolom:
    naam: str
    type: str  # geheel | getal | tekst | datum
    label: str
    null: bool = False
    min: float | None = None
    max: float | None = None


@dataclass(frozen=True)
class Instelling:
    sleutel: str
    groep: str
    label: str
    uitleg: str
    type: str
    startwaarde: Any = None
    eenheid: str | None = None
    min: float | None = None
    max: float | None = None
    keuzes: tuple = ()
    null: bool = False
    met_ingangsdatum: bool = False
    geheim: bool = False
    kolommen: tuple[Kolom, ...] = ()  # voor type tabel
    vraag: str | None = None  # verwijzing naar docs/OPEN_VRAGEN.md
    fase: int = 1  # vanaf welke fase de app hem gebruikt
    patroon: str | None = None  # tekst / waarden van een toewijzing moeten hieraan voldoen (regex)
    sleutels: tuple[str, ...] = ()  # toewijzing: toegestane sleutels (leeg = vrij)

    def __post_init__(self):
        assert self.type in TYPEN, self.type


GROEPEN = (
    "Bedrijf", "Kalender", "Datumketen", "Licht", "Lampen", "Gewas", "Voorstellen", "Model", "Energie", "Afzet",
    "Zoeken", "Genereren", "Teeltkaart", "Afdrukken", "Import", "Bronnen", "Koppeling", "Beveiliging", "Systeem",
)  # fmt: skip

_STREEFLENGTE = [
    (4, None, None, 30, None), (7, 45, 39, 35, 39), (8, 48, 42, 40, 42), (9, 51, 45, 42, 45), (10, 54, 48, 44, 48),
    (11, 56, 51, 46, 51), (12, 58, 56, 48, 56), (13, 60, 58, 50, 58), (14, 62, 60, 52, 60), (15, 64, 62, 54, 62),
    (16, 66, 64, 56, 64), (17, 68, 66, 58, 66), (18, 69, 67, 60, 67), (19, 70, 68, 62, 68), (20, 71, 69, 63, 69),
    (21, 72, 70, 65, 70), (22, 73, 71, 67, 71), (23, 74, 72, 69, 72), (24, 75, 73, 71, 73), (25, 76, 74, 73, 74),
    (26, 77, 75, 75, 75), (27, 77, 76, 77, 76), (28, 78, 77, 78, 77), (29, 78, 78, 79, 78), (30, 79, 78, 80, 78),
    (31, 79, 79, 80, 79), (32, 80, 79, 80, 79), (33, 80, 80, 80, 80), (34, 80, 80, 80, 80), (35, 80, 80, 80, 80),
]  # fmt: skip
STREEFLENGTE_START = [
    dict(zip(("ld_dag", "winter", "voorjaar", "zomer", "najaar"), r, strict=True)) for r in _STREEFLENGTE
]

KOLOMMEN_RONDEBLAD = {
    "vakgroep": "B", "volgnummer": "C", "schema": "D", "plant_week": "E", "plant_dag": "F", "ld_dagen": "G",
    "licht_uit_week": "H", "licht_uit_dag": "I", "reactietijd": "J", "tussenlicht": "K", "verlenging": "L",
    "dichtheid": "M", "bloei_week": "N", "bloei_dag": "O", "teeltduur": "P", "teeltverlenging": "R",
    "gewicht": "T", "geoogst": "V", "gemiddeld_gewicht": "X", "uitval": "Y", "geplant": "Z", "oogstaantal": "AB",
    "maand": "AD", "cumulatief": "AF",
}  # fmt: skip
# Velden van de teeltkaart (§9.2) die een cel kunnen krijgen. Datums: als één cel (tekst w-d of datum) of als
# week + dag in twee cellen.
_KAART_DATUMS = ("plantdatum", "licht_uit", "licht_aan", "licht_uit_2", "slak", "remstof", "pluizen", "netten", "oogst")
KAART_VELDEN = (
    ("kap", "ronde", "soort", "dichtheid", "ld_dagen", "tussenlicht_dagen", "reactietijd", "verlenging", "opmerking")
    + _KAART_DATUMS
    + tuple(f"{d}_{deel}" for d in _KAART_DATUMS for deel in ("week", "dag"))
)
# Indeling van het synthetische testsjabloon (tests/synthetisch.py). Alleen N1 (kap) en N2 (ronde) en de
# tabel op rij 11–26 komen uit het bouwplan; de rest is een aanname tot het echte sjabloon is gecontroleerd (B15).
KAART_CELLEN_START = {
    "kap": "N1", "ronde": "N2", "plantdatum_week": "C4", "plantdatum_dag": "D4", "soort": "C5", "dichtheid": "C6",
    "licht_uit_week": "H3", "licht_uit_dag": "I3", "ld_dagen": "K3", "licht_aan_week": "H4", "licht_aan_dag": "I4",
    "tussenlicht_dagen": "K4", "licht_uit_2_week": "H5", "licht_uit_2_dag": "I5", "slak_week": "H6", "slak_dag": "I6",
    "remstof_week": "H7", "remstof_dag": "I7", "oogst_week": "C28", "oogst_dag": "D28", "reactietijd": "H28",
}  # fmt: skip
KAART_MOMENTEN_START = {"datum": "A", "ld_dag": "C", "streeflengte": "E"}
KOPPEN_RONDEBLAD = {"D10": "schema", "E10": "pl datum", "H10": "licht uit", "N10": "bloei", "C11": "vak", "E11": "w/d"}

I = Instelling  # noqa: E741 - korte naam voor de lijst hieronder
REGISTER_LIJST: list[Instelling] = [
    # --- Bedrijf -----------------------------------------------------------------------------------------
    I("bedrijf.naam", "Bedrijf", "Bedrijfsnaam", "Naam van het bedrijf, voor afdrukken en de koppeling.", "tekst", null=True, max=200),
    I("bedrijf.aantal_kappen", "Bedrijf", "Aantal kappen", "Aantal kappen in de kas.", "geheel", 40, min=1, max=500),
    I("bedrijf.oppervlak_per_kap_m2", "Bedrijf", "Oppervlak per kap",
      "Teeltoppervlak van één kap. Nodig voor geplant aantal (dichtheid × oppervlak) en oogstaantal. Het werkboek "
      "rekent met 726 m² (rondeblad) én 750 m² (kostenblok); welke klopt is nog niet bevestigd. Per kap kan in "
      "Beheer → Kappen een eigen oppervlak worden ingevuld.", "getal", None, "m²", min=1, max=100000, null=True,
      met_ingangsdatum=True, vraag="4"),
    I("bedrijf.bedden_per_kap", "Bedrijf", "Bedden per kap", "Aantal bedden per kap (voor gemengde kappen zoals 3a3p).", "geheel", 6, min=1, max=50),
    I("bedrijf.breedtegraad", "Bedrijf", "Breedtegraad", "Breedtegraad van de kas (daglengte, fase 3).", "getal", None, "°", min=-66, max=66, null=True, fase=3),
    I("bedrijf.knmi_station", "Bedrijf", "KNMI-station", "Dichtstbijzijnde KNMI-station (fase 3).", "tekst", None, null=True, max=50, fase=3),
    I("bedrijf.lengtegraad", "Bedrijf", "Lengtegraad", "Lengtegraad van de kas (Open-Meteo vraagt breedte- en lengtegraad).", "getal", None, "°", min=-180, max=180, null=True, fase=3),
    # --- Kalender ----------------------------------------------------------------------------------------
    I("kalender.plantdagen", "Kalender", "Toegestane plantdagen",
      "Weekdagen waarop geplant mag worden. Het plantdatumvoorstel wordt afgerond naar de eerstvolgende toegestane "
      "dag.", "weekdagen", [1, 2, 3, 4, 5, 6], vraag="8"),
    I("kalender.oogstdagen", "Kalender", "Toegestane oogstdagen", "Weekdagen waarop geoogst mag worden (fase 5). Leeg = onbekend.", "weekdagen", None, null=True, fase=5),
    I("kalender.wisseltijd_dagen", "Kalender", "Wisseltijd per kap",
      "Dagen tussen oogst en de volgende planting in dezelfde kap. Per kap kan een eigen waarde worden ingevuld "
      "in Beheer → Kappen.", "geheel", 0, "dagen", min=0, max=120, vraag="8"),
    I("kalender.blokkadeperioden", "Kalender", "Blokkadeperioden",
      "Perioden waarin niet geplant mag worden, bijvoorbeeld stomen. Kap leeg = alle kappen.", "tabel", [],
      kolommen=(Kolom("van", "datum", "van"), Kolom("tot", "datum", "tot en met"), Kolom("kap", "geheel", "kap", null=True, min=1, max=500),
                Kolom("reden", "tekst", "reden", null=True))),
    I("kalender.weergave", "Kalender", "Weergave week-dag", "Hoe datums op het scherm staan: 44-6 of 44-6 (2026).", "keuze", "week-dag", keuzes=("week-dag", "week-dag-jaar")),
    # --- Datumketen --------------------------------------------------------------------------------------
    I("keten.licht_aan_na_licht_uit_dagen", "Datumketen", "Dagen van licht uit tot licht aan",
      "Licht aan (tussenlicht) = licht uit + dit aantal dagen (uit de teeltkaart).", "geheel", 12, "dagen", min=0, max=120),
    I("keten.plantdatum_volgt_keten", "Datumketen", "Plantdatum volgt de keten",
      "Aan: het voorstel voor de plantdatum is de oogst van de vorige teelt in dezelfde kap + wisseltijd, "
      "afgerond naar een toegestane plantdag.", "bool", True),
    I("keten.ld_dagen_min", "Datumketen", "LD-dagen minimaal", "Ondergrens voor dagen vegetatief; daarbuiten een fout op de rij.", "geheel", 0, "dagen", min=0, max=365),
    I("keten.ld_dagen_max", "Datumketen", "LD-dagen maximaal", "Bovengrens voor dagen vegetatief.", "geheel", 365, "dagen", min=0, max=365),
    I("keten.reactietijd_min", "Datumketen", "Reactietijd minimaal", "Ondergrens voor de reactietijd.", "geheel", 0, "dagen", min=0, max=365),
    I("keten.reactietijd_max", "Datumketen", "Reactietijd maximaal", "Bovengrens voor de reactietijd.", "geheel", 365, "dagen", min=0, max=365),
    I("keten.verlenging_min", "Datumketen", "Verlenging minimaal", "Ondergrens voor de verlenging (werkboek: 0–2).", "geheel", 0, "dagen", min=0, max=60),
    I("keten.verlenging_max", "Datumketen", "Verlenging maximaal", "Bovengrens voor de verlenging (werkboek: 0–2).", "geheel", 2, "dagen", min=0, max=60),
    I("keten.overlap", "Datumketen", "Gedrag bij overlap",
      "Plantdatum vóór de oogst van de vorige teelt in dezelfde kap: alleen waarschuwen, of het opslaan blokkeren.",
      "keuze", "waarschuwen", keuzes=("waarschuwen", "blokkeren")),
    # --- Licht (fase 3) ----------------------------------------------------------------------------------
    I("licht.straling_naar_par", "Licht", "Omrekenfactor straling → PAR", "par [mol/m²/dag] = straling [J/cm²/dag] × deze factor × transmissie.", "getal", 0.023, min=0, max=1, fase=3),
    I("licht.transmissie", "Licht", "Kastransmissie", "Fractie van het buitenlicht die binnenkomt.", "getal", None, min=0, max=1, null=True, met_ingangsdatum=True, vraag="6", fase=3),
    I("licht.minimale_daglichtsom", "Licht", "Minimale daglichtsom", "Ondergrens voor de daglichtsom.", "getal", None, "mol/m²/dag", min=0, max=100, null=True, fase=3),
    I("licht.bron_toekomstige_branduren", "Licht", "Bron voor toekomstige branduren", "Waarmee branduren in de toekomst worden geschat.", "keuze", "historisch_gemiddelde",
      keuzes=("historisch_gemiddelde", "regel", "handmatig"), fase=3),
    I("licht.verwachting_dagen", "Licht", "Dagen weersverwachting", "Zoveel dagen vooruit wordt de weersverwachting gebruikt in plaats van de weeknormaal (§10: de eerste 1–2 weken).", "geheel", 14, "dagen", min=0, max=16, fase=3),
    I("licht.onvolledige_belichtingsdag", "Licht", "Onvolledige belichtingsdag",
      "Een dag waarop 5-minutenwaarden ontbreken: als ontbrekend behandelen (de lichtsom wordt onbekend) of de aanwezige waarden gebruiken (met melding).",
      "keuze", "ontbrekend", keuzes=("ontbrekend", "gebruiken_met_melding"), fase=3),
    I("licht.weekuren_verdelen", "Licht", "Weekuren verdelen over de dagen",
      "Branduren uit het tabblad belichting staan per week. Aan: elke dag van de week krijgt een zevende (melding op de teelt). Uit: alleen dagdata telt.",
      "bool", True, fase=3),
    # --- Gewas -------------------------------------------------------------------------------------------
    I("gewas.uitval_per_oogstweek", "Gewas", "Uitval per oogstweek",
      "Uitvalfractie per ISO-oogstweek; levert het uitvalvoorstel. Komt uit het blad 'productie cijfers' van het "
      "werkboek; tot die is ingevuld is het uitvalvoorstel onbekend.", "tabel", [],
      kolommen=(Kolom("week_van", "geheel", "week van", min=1, max=53), Kolom("week_tot", "geheel", "week t/m", min=1, max=53),
                Kolom("fractie", "getal", "fractie", min=0, max=1)), vraag="B5"),
    I("gewas.dichtheid_min", "Gewas", "Dichtheid minimaal", "Ondergrens planten per m²; daarbuiten een fout op de rij. Leeg = geen grens.", "getal", None, "planten/m²", min=0, max=1000, null=True),
    I("gewas.dichtheid_max", "Gewas", "Dichtheid maximaal", "Bovengrens planten per m². Leeg = geen grens.", "getal", None, "planten/m²", min=0, max=1000, null=True),
    I("gewas.reactietijd_aftrek_1", "Gewas", "Reactietijd: eerste aftrek", "Reactietijd = teeltduur korte dag − deze aftrek − tweede aftrek (teeltschema).", "geheel", 2, "dagen", min=0, max=30, fase=4),
    I("gewas.reactietijd_aftrek_2", "Gewas", "Reactietijd: tweede aftrek", "Zie eerste aftrek.", "geheel", 3, "dagen", min=0, max=30, fase=4),
    I("gewas.min_waarnemingen_lbe", "Gewas", "Minimum waarnemingen per LBE-cel", "Minder waarnemingen = terugval op het seizoensgemiddelde (fase 3).", "geheel", None, min=1, max=1000, null=True, fase=3),
    I("gewas.dichtheid_reeks", "Gewas", "Toegestane dichtheden",
      "Dichtheden die in de praktijk voorkomen (planten/m²); het dichtheidsvoorstel wordt afgerond op de dichtstbijzijnde. "
      "Af te leiden uit de historie (Beheer → LBE en dichtheid). Leeg = niet afronden.", "lijst", [], fase=3),
    I("gewas.lichtregimes", "Gewas", "Lichtregimes voor de LBE-tabel",
      "Indeling op geïnstalleerd lamplicht (µmol/m²/s) op de plantdatum. Leeg = één regime voor alles.", "tabel", [],
      kolommen=(Kolom("naam", "tekst", "naam"), Kolom("umol_van", "getal", "µmol van", min=0, max=10000), Kolom("umol_tot", "getal", "µmol tot", min=0, max=10000)), fase=3),
    I("gewas.lbe_ijk_marge", "Gewas", "Marge LBE tegenover het belichtingsblad",
      "Toegestaan verschil tussen de berekende LBE en de LBE-realisatie in het belichtingsblad (g/mol). Af te spreken met de teler.",
      "getal", None, "g/mol", min=0, max=100, null=True, vraag="B24", fase=3),
    # --- Voorstellen / Model -----------------------------------------------------------------------------
    I("voorstel.seizoensvenster_weken", "Voorstellen", "Venster seizoensbasis", "Plantweken rond de eigen plantweek voor de seizoensbasis (±).", "geheel", 2, "weken", min=0, max=26, fase=4),
    I("voorstel.terugkijk_jaren", "Voorstellen", "Terugkijkperiode", "Aantal jaren historie voor voorstellen. Leeg = alle eerdere jaren.", "geheel", None, "jaar", min=1, max=50, null=True, fase=4),
    I("voorstel.temperatuurcorrectie", "Voorstellen", "Temperatuurcorrectie reactietijd", "Standaard uit tot er temperatuurdata en een gekalibreerde gevoeligheid zijn.", "bool", False, fase=4),
    I("voorstel.referentietemperatuur", "Voorstellen", "Referentietemperatuur", "Voor de temperatuurcorrectie.", "getal", None, "°C", min=-10, max=40, null=True, fase=4),
    I("voorstel.ld_dagen_aan", "Voorstellen", "Voorstel LD-dagen", "Mediaan van hetzelfde ras rond dezelfde plantweek in eerdere jaren (§6.7). Handmatig wint altijd.", "bool", True, fase=4),
    I("voorstel.reactietijd_aan", "Voorstellen", "Voorstel reactietijd", "Seizoensbasis, eventueel met temperatuurcorrectie (§6.7). Handmatig wint altijd.", "bool", True, fase=4),
    I("voorstel.verlenging_aan", "Voorstellen", "Voorstel verlenging", "Seizoensbasis van de dagen verlenging. Handmatig wint altijd.", "bool", True, fase=4),
    I("voorstel.gewicht_aan", "Voorstellen", "Voorstel gewicht uit het model",
      "Gewichtsprognose uit de voorstelladder (§8.3). Uit: de LBE-berekening van fase 3 (LBE × lichtsom / dichtheid).", "bool", True, fase=4),
    I("voorstel.min_waarnemingen", "Voorstellen", "Minimum teelten voor een seizoensbasis",
      "Minder eerdere teelten binnen het venster = geen voorstel (onbekend).", "geheel", 1, min=1, max=1000, fase=4),
    I("voorstel.band_percentielen", "Voorstellen", "Breedte van de bandbreedte",
      "Onder- en bovenpercentiel (in procenten) van de seizoensbasis en van de backtestfouten.", "lijst", ["10", "90"], fase=4),
    I("voorstel.aantal_vergelijkbaar", "Voorstellen", "Vergelijkbare teelten in de uitleg", "§8.3: de drie meest vergelijkbare eerdere teelten.", "geheel", 3, min=1, max=20, fase=4),
    I("model.treden", "Model", "Actieve treden",
      "Treden van de voorstelladder die worden getraind en getoetst. De seizoensbasis doet altijd mee.", "lijst",
      ["lbe", "ridge", "bomen", "temperatuur"], keuzes=("lbe", "ridge", "bomen", "temperatuur"), fase=4),
    I("model.kenmerken_ridge", "Model", "Kenmerken ridge", "Kenmerken voor de ridge-regressie op het residu van de LBE-identiteit (§8.3).",
      "lijst", ["plantweek", "lichtsom", "dichtheid", "ld_dagen", "ras"], keuzes=("plantweek", "lichtsom", "dichtheid", "ld_dagen", "ras"), fase=4),
    I("model.kenmerken_bomen", "Model", "Kenmerken bomen", "Kenmerken voor de bomen met monotone beperkingen (§8.3).",
      "lijst", ["plantweek", "lichtsom", "dichtheid", "ld_dagen", "ras"], keuzes=("plantweek", "lichtsom", "dichtheid", "ld_dagen", "ras"), fase=4),
    I("model.monotoon", "Model", "Monotone beperkingen",
      "Per kenmerk: stijgend (meer licht nooit lichter) of dalend (hogere dichtheid nooit zwaarder).", "toewijzing",
      {"lichtsom": "stijgend", "dichtheid": "dalend"}, patroon=r"stijgend|dalend", sleutels=("lichtsom", "dichtheid", "ld_dagen"), fase=4),
    I("model.drempel_gevalideerd", "Model", "Drempel voor 'gevalideerd'",
      "Hoeveel procent lager de gemiddelde absolute fout dan die van de seizoensbasis moet zijn in de backtest. Leeg = elke "
      "verbetering telt (strikt kleiner).", "getal", None, "%", min=0, max=100, null=True, fase=4),
    I("model.backtest_jaren", "Model", "Backtestperiode", "Aantal meest recente toetsjaren in de voortschrijdende backtest. Leeg = alle jaren met een eerder trainingsjaar.",
      "geheel", None, "jaar", min=1, max=50, null=True, fase=4),
    I("model.min_toetsteelten", "Model", "Minimum toetsteelten",
      "Minder toetsteelten in de backtest = nooit 'gevalideerd'. Technische startwaarde; leeg = geen minimum.", "geheel", 20, min=1, max=100000, null=True, vraag="B32", fase=4),
    I("model.ridge_alpha", "Model", "Ridge: regularisatie", "Strafterm op de gestandaardiseerde kenmerken. Technische startwaarde.", "getal", 1.0, min=0, max=1000000, fase=4),
    I("model.bomen_aantal", "Model", "Bomen: aantal stappen", "Aantal stompjes in de boosting. Technische startwaarde.", "geheel", 100, min=1, max=2000, fase=4),
    I("model.bomen_leersnelheid", "Model", "Bomen: leersnelheid", "Krimpfactor per stap. Technische startwaarde.", "getal", 0.1, min=0.001, max=1, fase=4),
    I("model.bomen_min_blad", "Model", "Bomen: minimum per blad", "Minimum aantal teelten aan elke kant van een splitsing. Technische startwaarde.", "geheel", 5, min=1, max=1000, fase=4),
    I("model.hertrainen_uren", "Model", "Automatisch hertrainen", "Interval voor automatisch trainen en toetsen. Leeg = alleen handmatig (Beheer → Model).",
      "geheel", None, "uur", min=1, max=8760, null=True, fase=4),
    I("model.max_rekentijd_s", "Model", "Maximale rekentijd training", "Daarna wordt de training afgebroken. Technische startwaarde.", "geheel", 900, "s", min=10, max=86400, fase=4),
    # --- Energie / Afzet / Zoeken ------------------------------------------------------------------------
    I("energie.stroomprijs_eur_per_mwh", "Energie", "Stroomprijs voor de planning", "Het werkboek rekent met 80 en met 300 €/MWh.", "getal", None, "€/MWh", min=0, max=10000, null=True,
      met_ingangsdatum=True, vraag="10", fase=3),
    I("energie.opslagen_eur_per_mwh", "Energie", "Opslagen en belasting", "Bovenop de stroomprijs (€/MWh).", "getal", None, "€/MWh", min=0, max=10000, null=True,
      met_ingangsdatum=True, vraag="10", fase=3),
    I("energie.opbrengst_per_tak_eur", "Energie", "Opbrengst per tak", "Voor de scenariovergelijking (§6.6).", "getal", None, "€", min=0, max=100, null=True, fase=3),
    I("energie.overige_kosten_per_tak", "Energie", "Overige kosten per tak",
      "Kostenverdeling per tak naast stroom (bijv. stek, pluizen en netten, overig), voor de scenariovergelijking.", "tabel", [],
      kolommen=(Kolom("post", "tekst", "post"), Kolom("eur_per_tak", "getal", "€ per tak", null=True, min=0, max=100)), fase=3),
    I("afzet.landen", "Afzet", "Landen voor officiële feestdagen", "Landcodes voor het pakket holidays.", "lijst", ["NL", "BE", "DE", "GB", "FR", "PL", "IT", "ES"], fase=5),
    I("afzet.lage_prijs_weken", "Afzet", "Weken met veel aanvoer en lage prijs",
      "Markering in het jaaroverzicht en de vergelijking (§8.1). Leeg = onbekend.", "tabel", [],
      kolommen=(Kolom("week_van", "geheel", "week van", min=1, max=53), Kolom("week_tot", "geheel", "week t/m", min=1, max=53),
                Kolom("reden", "tekst", "reden", null=True)), vraag="B35", fase=5),
    I("zoeken.gewicht_afzet", "Zoeken", "Gewicht dekking afzetmomenten", "Belang van de aanvoer in de vensters van de afzetmomenten (× hun belang). Leeg = telt niet mee.",
      "getal", None, min=0, max=1000, null=True, vraag="B36", fase=5),
    I("zoeken.gewicht_gelijkmatig", "Zoeken", "Gewicht gelijkmatige weekoogst", "Belang van een gelijkmatige oogst per week. Leeg = telt niet mee.",
      "getal", None, min=0, max=1000, null=True, vraag="B36", fase=5),
    I("zoeken.gewicht_doelgewicht", "Zoeken", "Gewicht doelgewicht", "Belang van de afwijking van het doelgewicht. Leeg = telt niet mee.",
      "getal", None, min=0, max=1000, null=True, vraag="B36", fase=5),
    I("zoeken.gewicht_energie", "Zoeken", "Gewicht energiekosten", "Belang van de stroomkosten (of kWh als de prijs onbekend is). Leeg = telt niet mee.",
      "getal", None, min=0, max=1000, null=True, vraag="B36", fase=5),
    I("zoeken.zetten", "Zoeken", "Wijzigingen die de zoekfunctie probeert", "Handmatige waarden worden nooit gewijzigd.", "lijst",
      ["plantdag", "ld_dagen", "verlenging", "ras_wisselen"], keuzes=("plantdag", "ld_dagen", "verlenging", "ras_wisselen"), fase=5),
    I("zoeken.stap_plantdag", "Zoeken", "Stapgrootte plantdag", "Plantdatum ± dit aantal dagen.", "geheel", 1, "dagen", min=1, max=14, fase=5),
    I("zoeken.stap_ld_dagen", "Zoeken", "Stapgrootte LD-dagen", "LD-dagen ± dit aantal.", "geheel", 1, "dagen", min=1, max=14, fase=5),
    I("zoeken.max_voorstellen", "Zoeken", "Maximum aantal voorstellen", "Lengte van de lijst met voorstellen. Technische startwaarde.", "geheel", 20, min=1, max=500, fase=5),
    I("zoeken.max_rekentijd_s", "Zoeken", "Maximale rekentijd zoekfunctie", "Daarna stopt het zoeken met de voorstellen tot dan toe. Technische startwaarde.",
      "getal", 30, "s", min=1, max=3600, fase=5),
    I("genereren.rasverdeling", "Genereren", "Rasverdeling", "Aandeel per ras bij genereren (§8.2). Leeg = het ras van de vorige teelt in de kap.",
      "tabel", [], kolommen=(Kolom("ras", "tekst", "ras"), Kolom("aandeel", "getal", "aandeel", min=0, max=1000)), vraag="B37", fase=5),
    I("genereren.ld_dagen_terugval", "Genereren", "LD-dagen zonder voorstel", "Als het model geen voorstel heeft. Leeg = onbekend (de ketting stopt).",
      "geheel", None, "dagen", min=0, max=365, null=True, vraag="B38", fase=5),
    I("genereren.reactietijd_terugval", "Genereren", "Reactietijd zonder voorstel", "Als het model geen voorstel heeft. Leeg = onbekend (de ketting stopt).",
      "geheel", None, "dagen", min=0, max=365, null=True, vraag="B38", fase=5),
    I("genereren.verlenging_terugval", "Genereren", "Verlenging zonder voorstel", "Als het model geen voorstel heeft. Leeg = onbekend (de ketting stopt).",
      "geheel", None, "dagen", min=0, max=60, null=True, vraag="B38", fase=5),
    I("genereren.kappen_per_plantdag", "Genereren", "Kappen per plantdag",
      "Voor kappen zonder eerdere teelt: zoveel kappen per toegestane plantdag vanaf de startdatum. Leeg = alle op de eerste plantdag.",
      "geheel", None, min=1, max=500, null=True, vraag="B38", fase=5),
    # --- Teeltkaart (fase 2) -----------------------------------------------------------------------------
    I("teeltkaart.streeflengte", "Teeltkaart", "Streeflengtetabel", "Streeflengte in cm per LD-dag en seizoen (uit het werkboek, §9.2). Leeg = geen waarde op de kaart.", "tabel",
      STREEFLENGTE_START, kolommen=(Kolom("ld_dag", "geheel", "LD-dag", min=0, max=100),) + tuple(Kolom(s, "geheel", s, null=True, min=0, max=500)
                                     for s in ("winter", "voorjaar", "zomer", "najaar")), fase=2),
    I("teeltkaart.seizoen_winter", "Teeltkaart", "Seizoen winter (plantweken)", "Plantweken die bij winter horen.", "weekbereik", "43-52", fase=2),
    I("teeltkaart.seizoen_voorjaar", "Teeltkaart", "Seizoen voorjaar (plantweken)", "Plantweken die bij voorjaar horen.", "weekbereik", "1-6", fase=2),
    I("teeltkaart.seizoen_zomer", "Teeltkaart", "Seizoen zomer (plantweken)", "Plantweken die bij zomer horen.", "weekbereik", "7-36", fase=2),
    I("teeltkaart.seizoen_najaar", "Teeltkaart", "Seizoen najaar (plantweken)", "Plantweken die bij najaar horen.", "weekbereik", "37-42", fase=2),
    I("teeltkaart.meetdagen", "Teeltkaart", "Weekdagen van de meetmomenten", "Weekdagen van de meetmomenten op de kaart.", "weekdagen", [1, 3, 5], fase=2),
    I("teeltkaart.aantal_momenten", "Teeltkaart", "Aantal meetmomenten", "Aantal regels in de tabel op de kaart.", "geheel", 16, min=1, max=60, fase=2),
    I("teeltkaart.offset_remstof", "Teeltkaart", "Dag-offset remstof", "Dagen na licht uit.", "geheel", None, "dagen", min=-60, max=120, null=True, fase=2),
    I("teeltkaart.offset_slak", "Teeltkaart", "Dag-offset slak", "Dagen na licht uit.", "geheel", None, "dagen", min=-60, max=120, null=True, fase=2),
    I("teeltkaart.offset_pluizen", "Teeltkaart", "Dag-offset pluizen", "Dagen na licht uit.", "geheel", None, "dagen", min=-60, max=120, null=True, fase=2),
    I("teeltkaart.offset_netten", "Teeltkaart", "Dag-offset netten", "Dagen na licht uit.", "geheel", None, "dagen", min=-60, max=120, null=True, fase=2),
    # --- Afdrukken (fase 2) ------------------------------------------------------------------------------
    I("afdrukken.standaard", "Afdrukken", "Standaard afdrukvorm", "Pdf of Excel als standaard (de knop die bovenaan staat). Leeg = beide gelijk.", "keuze", None, keuzes=("pdf", "excel"), null=True, fase=2),
    I("afdrukken.tijdslimiet_s", "Afdrukken", "Tijdslimiet pdf-conversie",
      "Maximale duur van één LibreOffice-conversie; daarna wordt hij afgebroken en blijft het Excel-bestand beschikbaar. "
      "Technische startwaarde (zoals proef 2).", "geheel", 120, "s", min=5, max=600, fase=2),
    I("afdrukken.papierformaat", "Afdrukken", "Papierformaat", "Wordt vóór de pdf-conversie expliciet in het werkblad gezet.", "keuze", "A4", keuzes=("A4", "A3"), fase=2),
    I("afdrukken.passend_op_een_pagina", "Afdrukken", "Passend op één pagina", "Afdruk passend op één pagina breed en hoog, zodat een blad niet wordt gesplitst.", "bool", True, fase=2),
    I("afdrukken.rondeblad_sjabloonblad", "Afdrukken", "Rondeblad: blad in het sjabloon",
      "Welk rondeblad uit het sjabloon als opmaak dient. De celtoewijzing is dezelfde als bij het importeren "
      "(Import → Rondeblad: kolomtoewijzing, eerste en laatste regel, titelcel).", "tekst", "Ronde 1", max=100, fase=2),
    I("afdrukken.rondeblad_bereik", "Afdrukken", "Rondeblad: afdrukbereik", "Afdrukbereik van het rondeblad (§9.3: B4:R51).", "tekst", "B4:R51", max=20,
      patroon=r"\$?[A-Z]{1,3}\$?\d+:\$?[A-Z]{1,3}\$?\d+", fase=2),
    I("afdrukken.rondeblad_richting", "Afdrukken", "Rondeblad: richting", "§9.3: A4 staand.", "keuze", "staand", keuzes=("staand", "liggend"), fase=2),
    I("afdrukken.teeltkaart_blad", "Afdrukken", "Teeltkaart: blad in het sjabloon", "Naam van het tabblad met de teeltkaart.", "tekst", "teeltkaart", max=100, fase=2),
    I("afdrukken.teeltkaart_bereik", "Afdrukken", "Teeltkaart: afdrukbereik", "Afdrukbereik van één blad met kaarten (§9.2: A1:AG31).", "tekst", "A1:AG31", max=20,
      patroon=r"\$?[A-Z]{1,3}\$?\d+:\$?[A-Z]{1,3}\$?\d+", fase=2),
    I("afdrukken.teeltkaart_richting", "Afdrukken", "Teeltkaart: richting", "§9.2: A4 liggend.", "keuze", "liggend", keuzes=("staand", "liggend"), fase=2),
    I("afdrukken.teeltkaart_kaarten_per_blad", "Afdrukken", "Teeltkaart: kaarten per blad", "§9.2: twee kaarten naast elkaar (kap N en kap N+1).", "geheel", 2, min=1, max=2, fase=2),
    I("afdrukken.teeltkaart_tweede_kaart_kolommen", "Afdrukken", "Teeltkaart: verschuiving tweede kaart",
      "Aantal kolommen tussen de eerste en de tweede kaart op een blad (§9.2: A–P en R–AG = 17).", "geheel", 17, "kolommen", min=1, max=200, fase=2),
    I("afdrukken.teeltkaart_cellen", "Afdrukken", "Teeltkaart: celtoewijzing kop en onderregel",
      "Cel per veld voor de eerste (linker) kaart. Een datum kan als één cel (bijv. licht_uit → tekst 44-6, of een "
      "datum als de cel een datumnotatie heeft) of als week en dag in twee cellen (licht_uit_week, licht_uit_dag). "
      "Alleen kap (N1) en ronde (N2) staan in het bouwplan; de rest is de indeling van het synthetische testsjabloon "
      "en moet bij het echte sjabloon worden gecontroleerd (Beheer → Sjabloon → controleafdruk met veldnamen).",
      "toewijzing", KAART_CELLEN_START, fase=2, vraag="B15", patroon=r"[A-Z]{1,3}[1-9]\d{0,5}", sleutels=KAART_VELDEN),
    I("afdrukken.teeltkaart_momentkolommen", "Afdrukken", "Teeltkaart: kolommen van de meetmomenten",
      "Kolom per onderdeel van een meetmoment (datum, datum_week, datum_dag, ld_dag, streeflengte) voor de linker kaart.",
      "toewijzing", KAART_MOMENTEN_START, fase=2, vraag="B15", patroon=r"[A-Z]{1,3}", sleutels=("datum", "datum_week", "datum_dag", "ld_dag", "streeflengte")),
    I("afdrukken.teeltkaart_eerste_momentrij", "Afdrukken", "Teeltkaart: eerste regel meetmomenten", "§9.2: tabel op rij 11–26.", "geheel", 11, min=1, max=1000, fase=2),
    I("afdrukken.niet_gevulde_formules", "Afdrukken", "Formules die de app niet vult",
      "Wat er in de afdruk komt in een formulecel van het sjabloon die niet in de celtoewijzing staat: leegmaken "
      "(veilig: nooit een waarde van een andere kap) of de laatst door Excel berekende waarde uit het sjabloon.",
      "keuze", "leegmaken", keuzes=("leegmaken", "sjabloonwaarde"), fase=2),
    I("afdrukken.max_sjabloon_mb", "Afdrukken", "Maximale grootte sjabloon", "Grootste toegestane sjabloonwerkboek.", "geheel", 32, "MiB", min=1, max=256, fase=2),
    I("afdrukken.max_lettertype_mb", "Afdrukken", "Maximale grootte lettertypebestand", "Voor uploads naar /data/lettertypen/.", "geheel", 20, "MiB", min=1, max=100, fase=2),
    I("afdrukken.metrisch_gelijk", "Afdrukken", "Metrisch gelijke lettertypen",
      "Lettertype uit het sjabloon → lettertype in de image met dezelfde tekenbreedtes (dan verschuift er niets). "
      "Een lettertype dat hier niet staat en niet is geïnstalleerd of geüpload, wordt in Beheer → Sjabloon gemeld.",
      "toewijzing", {"Calibri": "Carlito", "Cambria": "Caladea", "Arial": "Liberation Sans", "Times New Roman": "Liberation Serif",
                     "Courier New": "Liberation Mono", "Helvetica": "Liberation Sans"}, vraag="13", fase=2),
    # --- Import ------------------------------------------------------------------------------------------
    I("import.max_upload_mb", "Import", "Maximale uploadgrootte", "Grootste toegestane werkboek.", "geheel", 32, "MiB", min=1, max=256),
    I("import.max_uitgepakt_mb", "Import", "Maximale uitgepakte grootte", "Grootste toegestane uitgepakte inhoud van een werkboek.", "geheel", 512, "MiB", min=1, max=2048),
    I("import.rondeblad_eerste_rij", "Import", "Rondeblad: eerste regel", "Eerste regel met een kap.", "geheel", 12, min=1, max=1000),
    I("import.rondeblad_laatste_rij", "Import", "Rondeblad: laatste regel", "Laatste regel met een kap.", "geheel", 51, min=1, max=1000),
    I("import.rondeblad_titelcel", "Import", "Rondeblad: titelcel", "Cel met de rondenaam.", "tekst", "C8", max=10),
    I("import.rondeblad_kolommen", "Import", "Rondeblad: kolomtoewijzing", "Kolomletter per veld. Verplaatst Leon een kolom, pas hem hier aan.", "toewijzing", KOLOMMEN_RONDEBLAD),
    I("import.rondeblad_koppen", "Import", "Rondeblad: herkenningskoppen", "Cel → verwachte tekst (hoofdletters en spaties tellen niet).", "toewijzing", KOPPEN_RONDEBLAD, vraag="B7"),
    I("import.plantdatum_bij_verwijzing", "Import", "Plantdatum bij verwijzing naar vorige ronde",
      "Als de plantweek een formule is die naar de vorige ronde verwijst: 'keten' = de plantdatum volgt de keten "
      "(voorstel), 'vastzetten' = de waarde uit het werkboek wordt als handmatige waarde vastgezet.", "keuze", "keten",
      keuzes=("keten", "vastzetten"), vraag="B4"),
    I("import.weekuren_min", "Import", "Weekuren minimaal", "Controlegrens bij het importeren van weekuren.", "getal", 0, "uur", min=0, max=168, fase=3),
    I("import.weekuren_max", "Import", "Weekuren maximaal", "Controlegrens bij het importeren van weekuren.", "getal", 168, "uur", min=0, max=168, fase=3),
    I("import.belichting_weekuren_rijen", "Import", "Belichting: rijen weekuren", "Eerste en laatste rij van de weekurentabel (rij 2 = weeknummers).", "tekst", "2-84", max=20, patroon=r"\d+-\d+", fase=3),
    I("import.belichting_kaptabel_rijen", "Import", "Belichting: rijen kaptabel", "Kopregel en laatste rij van de kaptabel vanaf rij 87.", "tekst", "87-127", max=20, patroon=r"\d+-\d+", fase=3),
    I("import.belichting_kaptabel_kolommen", "Import", "Belichting: kolommen kaptabel",
      "Kolomletter per veld van de kaptabel (rij 87–127). De startwaarde volgt het synthetische testwerkboek; controleren bij het echte bestand.",
      "toewijzing", {"kap": "A", "plantdatum": "B", "ld_dagen": "C", "kd_dagen": "D", "uren_ld": "E", "uren_kd": "F", "uren_led": "G",
                     "uren_son_t": "H", "umol": "I", "gewicht_prognose": "J", "gewicht_realisatie": "K", "lbe_prognose": "L",
                     "lbe_realisatie": "M", "kosten": "N"}, patroon=r"[A-Z]{1,3}", vraag="B8", fase=3),
    I("import.belichting_kostenvergelijking", "Import", "Belichting: kostenvergelijking",
      "Cellen van de vergelijking 150/200 µmol: kolom met omschrijving, de twee kolommen met waarden en de rijen.",
      "toewijzing", {"omschrijving": "AK", "a": "AL", "b": "AM", "rijen": "2-12"}, vraag="B8", fase=3),
    I("import.teeltschema_kolommen", "Import", "Teeltschema: kolommen",
      "Kolomnummer per veld (1 = A). Week en dag staan in twee kolommen naast elkaar. De startwaarde volgt het synthetische testwerkboek.",
      "toewijzing", {"kap": "1", "plant_week": "2", "plant_dag": "3", "licht_uit_week": "7", "licht_uit_dag": "8", "pluizen_week": "10",
                     "pluizen_dag": "11", "netten_week": "13", "netten_dag": "14", "leeg_week": "16", "leeg_dag": "17", "gewicht": "19",
                     "teeltduur": "23", "teeltduur_kd": "24", "lbe": "38", "uitval": "39", "reactietijd": "42"}, patroon=r"\d{1,3}", vraag="B8", fase=3),
    I("import.data_interval_minuten", "Import", "Data: interval", "Tijd tussen twee regels in het tabblad data (§7.2: 5 minuten).", "geheel", 5, "minuten", min=1, max=60, fase=3),
    I("import.data_tijdstempel", "Import", "Data: tijdstempel is begin of eind van het interval",
      "Bepaalt bij welke dag de waarde rond middernacht hoort.", "keuze", None, keuzes=("begin", "eind"), null=True, vraag="B21", fase=3),
    I("import.koppel_tolerantie_dagen", "Import", "Tolerantie koppelen op plantdatum", "± dagen bij koppelen van realisatie op kap + plantdatum.", "geheel", None, "dagen", min=0, max=30, null=True, fase=6),
    # --- Bronnen (alle standaard uit) --------------------------------------------------------------------
    I("bronnen.knmi_aan", "Bronnen", "KNMI Data Platform", "Gemeten straling en temperatuur ophalen.", "bool", False, fase=3),
    I("bronnen.open_meteo_aan", "Bronnen", "Open-Meteo", "Let op: de gratis API is voor niet-commercieel gebruik.", "bool", False, fase=3),
    I("bronnen.energyzero_aan", "Bronnen", "EnergyZero", "Dagvooruitprijzen stroom.", "bool", False, fase=3),
    I("bronnen.open_meteo_historie_url", "Bronnen", "Open-Meteo: adres historie", "Basis-URL voor historie (ERA5). Instelbaar voor een eigen server of een betaald abonnement.", "tekst",
      "https://archive-api.open-meteo.com/v1/archive", max=300, patroon=r"https?://\S+", fase=3),
    I("bronnen.open_meteo_verwachting_url", "Bronnen", "Open-Meteo: adres verwachting", "Basis-URL voor de verwachting.", "tekst",
      "https://api.open-meteo.com/v1/forecast", max=300, patroon=r"https?://\S+", fase=3),
    I("bronnen.knmi_url", "Bronnen", "KNMI: adres Open Data API", "Basis-URL van het KNMI Data Platform.", "tekst",
      "https://api.dataplatform.knmi.nl/open-data/v1", max=300, patroon=r"https?://\S+", fase=3),
    I("bronnen.knmi_dataset", "Bronnen", "KNMI: dataset", "§10: niet het oude script daggegevens (stopt eind 2026).", "tekst",
      "daily-in-situ-meteorological-observations-validated", max=200, fase=3),
    I("bronnen.knmi_versie", "Bronnen", "KNMI: datasetversie", "Versie van de dataset.", "tekst", None, null=True, max=20, vraag="B23", fase=3),
    I("bronnen.knmi_variabele_straling", "Bronnen", "KNMI: variabele globale straling",
      "Naam van de variabele voor de dagsom van de globale straling en de eenheid zijn niet geverifieerd (§10). Leeg = KNMI ophalen kan niet.",
      "tekst", None, null=True, max=100, vraag="B23", fase=3),
    I("bronnen.energyzero_url", "Bronnen", "EnergyZero: adres", "Basis-URL van de prijzen-API.", "tekst", "https://api.energyzero.nl/v1/energyprices", max=300,
      patroon=r"https?://\S+", fase=3),
    I("bronnen.ophaalinterval_uren", "Bronnen", "Ophaalinterval", "Hoe vaak een ingeschakelde bron wordt bijgewerkt.", "geheel", 6, "uur", min=1, max=168, fase=3),
    I("bronnen.tijdslimiet_s", "Bronnen", "Tijdslimiet per verzoek", "Na deze tijd geeft een bron op; de app werkt door.", "geheel", 30, "s", min=1, max=300, fase=3),
    I("bronnen.normalen_jaren", "Bronnen", "Jaren historie voor weeknormalen", "§10: 20–30 jaar daghistorie.", "geheel", 30, "jaar", min=1, max=80, fase=3),
    I("bronnen.normalen_min_jaren", "Bronnen", "Minimum jaren voor een weeknormaal", "Minder volledige jaren voor een week = geen normaal (onbekend).", "geheel", 20, "jaar", min=1, max=80, fase=3),
    I("bronnen.percentielen", "Bronnen", "Percentielen weeknormaal", "Onder, midden (plannen) en boven, in procenten.", "lijst", ["10", "50", "90"], fase=3),
    # --- Koppeling (fase 6) ------------------------------------------------------------------------------
    I("koppeling.ingeschakeld", "Koppeling", "Bedrijfskoppeling ingeschakeld",
      "Bedrijfskoppeling API v1 aanbieden. Uit = alle koppelroutes geven 403 koppeling_uitgeschakeld (vanaf fase 6).", "bool", False),
    I("koppeling.verzoeken_per_minuut", "Koppeling", "Verzoeken per minuut per sleutel", "Limiet van het contract.", "geheel", 120, min=1, max=10000, fase=6),
    # --- Beveiliging (zoals Gewichtsverwachting) ---------------------------------------------------------
    I("beveiliging.sessie_uren", "Beveiliging", "Sessieduur", "Hoe lang een aanmelding geldig blijft.", "geheel", 12, "uur", min=1, max=168),
    I("beveiliging.inlogpogingen_account", "Beveiliging", "Maximale inlogpogingen per account", "Binnen het venster.", "geheel", 5, min=3, max=50),
    I("beveiliging.inlogpogingen_ip", "Beveiliging", "Maximale inlogpogingen per IP-adres",
      "Ruimer dan per account, zodat één persoon achter een gedeeld adres niet iedereen blokkeert. 0 = zelfde als per account.", "geheel", 25, min=0, max=1000),
    I("beveiliging.venster_minuten", "Beveiliging", "Venster aanmeldpogingen", "Periode waarover mislukte pogingen tellen.", "geheel", 15, "minuten", min=1, max=1440),
    I("beveiliging.vertraging_max_s", "Beveiliging", "Maximale vertraging na fout", "Oplopende vertraging (0,25 s per eerdere fout).", "getal", 1.0, "s", min=0, max=10),
    I("beveiliging.vertrouwde_proxys", "Beveiliging", "Vertrouwde proxy's",
      "IP-adressen of netwerken (CIDR), komma-gescheiden, waarvan X-Forwarded-For wordt vertrouwd. Leeg = "
      "omgevingsvariabele TRUSTED_PROXIES. Nooit 0.0.0.0/0.", "tekst", "", max=1000),
    I("beveiliging.strikte_oorsprong", "Beveiliging", "Strikte oorsprongcontrole", "Schrijfverzoeken zonder Origin of Referer van dezelfde host weigeren.", "bool", True),
    I("beveiliging.mfa_verplicht_beheerders", "Beveiliging", "MFA verplicht voor beheerders", "Beheerders zonder tweestapsverificatie krijgen geen beheerrechten.", "bool", False),
    I("beveiliging.audit_bewaardagen", "Beveiliging", "Bewaartermijn auditlog", "0 = onbeperkt bewaren.", "geheel", 0, "dagen", min=0, max=36500),
    # --- Systeem -----------------------------------------------------------------------------------------
    I("systeem.backup_interval_uren", "Systeem", "Back-upinterval", "Automatische lokale back-up.", "geheel", 24, "uur", min=1, max=168),
    I("systeem.backup_bewaardagen", "Systeem", "Bewaartermijn back-ups", "0 = onbeperkt (advies: 30).", "geheel", 0, "dagen", min=0, max=3650),
    I("systeem.tijdzone", "Systeem", "Tijdzone", "Tijdzone voor weergave.", "keuze", "Europe/Amsterdam", keuzes=("Europe/Amsterdam",)),
    I("systeem.getalnotatie", "Systeem", "Getalnotatie", "Decimaalteken op het scherm.", "keuze", "nl", keuzes=("nl", "en")),
    I("systeem.hostnaam", "Systeem", "Hostnaam", "Hostnaam voor veilige cookies (informatief; SECURE_COOKIES staat in TrueNAS).", "tekst", None, null=True, max=200),
]  # fmt: skip
del I
REGISTER: dict[str, Instelling] = {i.sleutel: i for i in REGISTER_LIJST}
assert len(REGISTER) == len(REGISTER_LIJST), "dubbele sleutel in het register"


class OngeldigeWaarde(ValueError):
    pass


def _getal(waarde: Any, inst_naam: str, geheel: bool, minimum, maximum) -> float | int:
    if isinstance(waarde, bool):
        raise OngeldigeWaarde(f"{inst_naam}: verwacht een getal.")
    if isinstance(waarde, str):
        tekst = waarde.strip().replace(",", ".")
        try:
            waarde = float(tekst)
        except ValueError as exc:
            raise OngeldigeWaarde(f"{inst_naam}: '{waarde}' is geen getal.") from exc
    if not isinstance(waarde, (int, float)) or waarde != waarde or waarde in (float("inf"), float("-inf")):
        raise OngeldigeWaarde(f"{inst_naam}: verwacht een getal.")
    if geheel:
        if float(waarde) != int(waarde):
            raise OngeldigeWaarde(f"{inst_naam}: verwacht een geheel getal.")
        waarde = int(waarde)
    if minimum is not None and waarde < minimum:
        raise OngeldigeWaarde(f"{inst_naam}: minimaal {minimum}.")
    if maximum is not None and waarde > maximum:
        raise OngeldigeWaarde(f"{inst_naam}: maximaal {maximum}.")
    return waarde


def _weekbereik(tekst: str) -> tuple[int, int]:
    try:
        van, tot = (int(x) for x in str(tekst).split("-"))
    except ValueError as exc:
        raise OngeldigeWaarde("Gebruik week van-tot, bijvoorbeeld 43-52.") from exc
    if not (1 <= van <= 53 and 1 <= tot <= 53):
        raise OngeldigeWaarde("Weken lopen van 1 t/m 53.")
    return van, tot


def _kolomwaarde(kolom: Kolom, waarde: Any) -> Any:
    if waarde is None or (isinstance(waarde, str) and not waarde.strip()):
        if not kolom.null:
            raise OngeldigeWaarde(f"Kolom '{kolom.label}' is verplicht.")
        return None
    if kolom.type in ("geheel", "getal"):
        return _getal(waarde, kolom.label, kolom.type == "geheel", kolom.min, kolom.max)
    if kolom.type == "datum":
        try:
            return date.fromisoformat(str(waarde).strip()).isoformat()
        except ValueError as exc:
            raise OngeldigeWaarde(f"Kolom '{kolom.label}': gebruik JJJJ-MM-DD.") from exc
    return str(waarde).strip()[:500]


def valideer(inst: Instelling, waarde: Any) -> Any:
    """Controleer en normaliseer een waarde. `OngeldigeWaarde` met een begrijpelijke melding bij fouten."""
    leeg = waarde is None or (isinstance(waarde, str) and not waarde.strip() and inst.type not in ("tekst",))
    if leeg or (inst.type == "tekst" and waarde is None):
        if not inst.null:
            raise OngeldigeWaarde(f"{inst.label}: een waarde is verplicht.")
        return None
    t = inst.type
    if t in ("geheel", "getal"):
        return _getal(waarde, inst.label, t == "geheel", inst.min, inst.max)
    if t == "tekst":
        tekst = str(waarde).strip()
        if inst.max is not None and len(tekst) > inst.max:
            raise OngeldigeWaarde(f"{inst.label}: maximaal {int(inst.max)} tekens.")
        if not tekst and inst.null:
            return None if inst.startwaarde is None else ""
        if inst.patroon and not re.fullmatch(inst.patroon, tekst):
            raise OngeldigeWaarde(f"{inst.label}: '{tekst}' heeft niet de juiste vorm.")
        return tekst
    if t == "bool":
        if isinstance(waarde, bool):
            return waarde
        if str(waarde).lower() in ("1", "true", "aan", "ja", "on"):
            return True
        if str(waarde).lower() in ("0", "false", "uit", "nee", "off"):
            return False
        raise OngeldigeWaarde(f"{inst.label}: kies aan of uit.")
    if t == "keuze":
        if waarde not in inst.keuzes:
            raise OngeldigeWaarde(f"{inst.label}: kies uit {', '.join(map(str, inst.keuzes))}.")
        return waarde
    if t == "weekdagen":
        if isinstance(waarde, str):
            waarde = [x for x in waarde.replace(";", ",").split(",") if x.strip()]
        dagen = sorted({int(_getal(x, inst.label, True, 1, 7)) for x in waarde})
        if not dagen and not inst.null:
            raise OngeldigeWaarde(f"{inst.label}: kies minstens één dag.")
        return dagen
    if t == "weekbereik":
        van, tot = _weekbereik(waarde)
        return f"{van}-{tot}"
    if t == "lijst":
        if isinstance(waarde, str):
            waarde = [x.strip() for x in waarde.replace(";", ",").split(",")]
        lijst = [str(x).strip()[:100] for x in waarde if str(x).strip()]
        if inst.keuzes:
            fout = [x for x in lijst if x not in inst.keuzes]
            if fout:
                raise OngeldigeWaarde(f"{inst.label}: onbekend {', '.join(fout)}; kies uit {', '.join(inst.keuzes)}.")
        return lijst
    if t == "toewijzing":
        if isinstance(waarde, str):
            try:
                waarde = json.loads(waarde)
            except json.JSONDecodeError as exc:
                raise OngeldigeWaarde(f"{inst.label}: ongeldige JSON.") from exc
        if not isinstance(waarde, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in waarde.items()
        ):
            raise OngeldigeWaarde(f"{inst.label}: verwacht paren tekst → tekst.")
        if (
            isinstance(inst.startwaarde, dict)
            and set(waarde) != set(inst.startwaarde)
            and inst.sleutel.endswith("kolommen")
        ):
            raise OngeldigeWaarde(f"{inst.label}: alle velden moeten een kolom hebben ({', '.join(inst.startwaarde)}).")
        uit = {k.strip(): v.strip() for k, v in waarde.items()}
        if inst.sleutels and set(uit) - set(inst.sleutels):
            raise OngeldigeWaarde(f"{inst.label}: onbekende velden {', '.join(sorted(set(uit) - set(inst.sleutels)))}; "
                                  f"kies uit {', '.join(inst.sleutels)}.")  # fmt: skip
        if inst.patroon:
            fout = [f"{k}={v}" for k, v in uit.items() if not re.fullmatch(inst.patroon, v)]
            if fout:
                raise OngeldigeWaarde(f"{inst.label}: ongeldige verwijzing(en) {', '.join(fout)}.")
        return uit
    if t == "tabel":
        if isinstance(waarde, str):
            waarde = csv_naar_tabel(inst, waarde)
        if not isinstance(waarde, list):
            raise OngeldigeWaarde(f"{inst.label}: verwacht een tabel.")
        rijen = []
        for nummer, rij in enumerate(waarde, 1):
            if not isinstance(rij, dict):
                raise OngeldigeWaarde(f"{inst.label}: regel {nummer} is geen rij.")
            try:
                rijen.append({k.naam: _kolomwaarde(k, rij.get(k.naam)) for k in inst.kolommen})
            except OngeldigeWaarde as exc:
                raise OngeldigeWaarde(f"{inst.label}, regel {nummer}: {exc}") from exc
        return rijen
    raise OngeldigeWaarde(f"Onbekend type {t}")  # pragma: no cover


def csv_naar_tabel(inst: Instelling, tekst: str) -> list[dict]:
    """Puntkomma-gescheiden tekst met een kopregel (kolomnamen) → lijst van rijen."""
    regels = [r for r in tekst.strip().splitlines() if r.strip()]
    if not regels:
        return []
    kop = [k.strip() for k in regels[0].split(";")]
    namen = {k.naam for k in inst.kolommen}
    if set(kop) != namen:
        raise OngeldigeWaarde(f"{inst.label}: kopregel moet zijn: {';'.join(k.naam for k in inst.kolommen)}")
    return [dict(zip(kop, (v.strip() for v in r.split(";")), strict=False)) for r in regels[1:]]


def tabel_naar_csv(inst: Instelling, rijen: list[dict] | None) -> str:
    namen = [k.naam for k in inst.kolommen]
    uit = [";".join(namen)]
    for rij in rijen or []:
        uit.append(";".join("" if rij.get(n) is None else str(rij.get(n)) for n in namen))
    return "\n".join(uit)


def weergave(inst: Instelling, waarde: Any) -> str:
    if waarde is None:
        return "onbekend"
    if inst.type == "bool":
        return "aan" if waarde else "uit"
    if inst.type == "weekdagen":
        return ", ".join(WEEKDAGEN[d] for d in waarde) or "geen"
    if inst.type == "lijst":
        return ", ".join(waarde)
    if inst.type == "tabel":
        return f"{len(waarde)} regel(s)"
    if inst.type == "toewijzing":
        return ", ".join(f"{k}={v}" for k, v in waarde.items())
    if isinstance(waarde, float):
        return f"{waarde:g}".replace(".", ",")
    return str(waarde)


# --- Opslag en opvragen ------------------------------------------------------------------------------------
def _nu() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class Snapshot:
    """Alle instellingen zoals ze nu gelden, plus de reeksen met ingangsdatum."""

    waarden: dict[str, Any] = field(default_factory=dict)
    reeksen: dict[str, list[tuple[date | None, Any]]] = field(default_factory=dict)

    def __getitem__(self, sleutel: str) -> Any:
        if sleutel not in REGISTER:
            raise KeyError(f"Onbekende instelling {sleutel}")
        return self.waarden[sleutel]

    def reeks(self, sleutel: str) -> list[tuple[date | None, Any]]:
        return self.reeksen.get(sleutel, [(None, REGISTER[sleutel].startwaarde)])

    def op(self, sleutel: str, datum: date | None) -> Any:
        gekozen = REGISTER[sleutel].startwaarde
        for vanaf, waarde in self.reeks(sleutel):
            if vanaf is None or (datum is not None and vanaf <= datum):
                gekozen = waarde
        return gekozen


def laad(db: Session, vandaag: date | None = None) -> Snapshot:
    from .db.modellen import Instelling as Rij

    vandaag = vandaag or date.today()
    snap = Snapshot()
    rijen = db.scalars(select(Rij).order_by(Rij.id)).all()
    per_sleutel: dict[str, list] = {}
    for rij in rijen:
        if rij.sleutel in REGISTER:
            per_sleutel.setdefault(rij.sleutel, []).append(rij)
    for sleutel, inst in REGISTER.items():
        rijen_s = per_sleutel.get(sleutel, [])
        if inst.met_ingangsdatum:
            # Per ingangsdatum geldt de laatst opgeslagen waarde.
            per_datum: dict[date | None, Any] = {None: inst.startwaarde}
            for rij in rijen_s:
                per_datum[rij.geldig_vanaf] = inst.startwaarde if rij.is_startwaarde else rij.waarde
            reeks = sorted(per_datum.items(), key=lambda x: (x[0] is not None, x[0] or date.min))
            snap.reeksen[sleutel] = reeks
            snap.waarden[sleutel] = None
            snap.waarden[sleutel] = snap.op(sleutel, vandaag)
        else:
            laatste = rijen_s[-1] if rijen_s else None
            snap.waarden[sleutel] = inst.startwaarde if laatste is None or laatste.is_startwaarde else laatste.waarde
    return snap


def wijzig(db: Session, gebruiker, sleutel: str, waarde: Any, reden: str | None = None,
           geldig_vanaf: date | None = None, naar_start: bool = False) -> Any:  # fmt: skip
    """Sla een nieuwe waarde op (gevalideerd), schrijf het auditlog en markeer het plan als gewijzigd."""
    from .auth import audit
    from .db.modellen import Instelling as Rij

    if sleutel not in REGISTER:
        raise OngeldigeWaarde("Onbekende instelling.")
    inst = REGISTER[sleutel]
    if geldig_vanaf is not None and not inst.met_ingangsdatum:
        raise OngeldigeWaarde(f"{inst.label} heeft geen ingangsdatum.")
    if inst.geheim:
        raise OngeldigeWaarde("Geheime waarden worden niet via het register gewijzigd.")
    nieuw = inst.startwaarde if naar_start else valideer(inst, waarde)
    oud = laad(db).op(sleutel, geldig_vanaf) if inst.met_ingangsdatum else laad(db)[sleutel]
    db.add(Rij(sleutel=sleutel, waarde=nieuw, is_startwaarde=naar_start, geldig_vanaf=geldig_vanaf,
               gewijzigd_door=gebruiker.id if gebruiker else None, reden=(reden or "").strip()[:2000] or None))  # fmt: skip
    audit(db, gebruiker, "instelling_gewijzigd", f"instelling:{sleutel}",
          {"oud": oud, "nieuw": nieuw, "geldig_vanaf": geldig_vanaf.isoformat() if geldig_vanaf else None,
           "naar_startwaarde": naar_start, "reden": reden})  # fmt: skip
    return nieuw


def exporteer(db: Session) -> dict:
    """Alle instellingen als JSON-document (zonder geheimen)."""
    snap = laad(db)
    uit: dict[str, Any] = {"formaat": "teeltplanning-instellingen", "versie": 1, "instellingen": {}}
    for sleutel, inst in REGISTER.items():
        if inst.geheim:
            continue
        if inst.met_ingangsdatum:
            uit["instellingen"][sleutel] = {
                "reeks": [{"geldig_vanaf": d.isoformat() if d else None, "waarde": w} for d, w in snap.reeks(sleutel)]
            }
        else:
            uit["instellingen"][sleutel] = {"waarde": snap[sleutel]}
    return uit


def importeer(db: Session, gebruiker, document: dict, reden: str) -> int:
    """Lees een exportdocument in. Alles wordt eerst gevalideerd; bij één fout verandert er niets."""
    if not isinstance(document, dict) or document.get("formaat") != "teeltplanning-instellingen":
        raise OngeldigeWaarde("Dit is geen instellingenbestand van Teeltplanning.")
    items = document.get("instellingen") or {}
    onbekend = [k for k in items if k not in REGISTER]
    if onbekend:
        raise OngeldigeWaarde(f"Onbekende instellingen: {', '.join(sorted(onbekend))}")
    snap = laad(db)
    wijzigingen: list[tuple[str, Any, date | None]] = []
    for sleutel, item in items.items():
        inst = REGISTER[sleutel]
        if inst.met_ingangsdatum:
            for deel in item.get("reeks", []):
                vanaf = date.fromisoformat(deel["geldig_vanaf"]) if deel.get("geldig_vanaf") else None
                wijzigingen.append((sleutel, valideer(inst, deel.get("waarde")), vanaf))
        else:
            wijzigingen.append((sleutel, valideer(inst, item.get("waarde")), None))
    aantal = 0
    for sleutel, waarde, vanaf in wijzigingen:
        huidig = snap.op(sleutel, vanaf) if REGISTER[sleutel].met_ingangsdatum else snap[sleutel]
        if huidig != waarde or (REGISTER[sleutel].met_ingangsdatum and vanaf not in dict(snap.reeks(sleutel))):
            wijzig(db, gebruiker, sleutel, waarde, reden, vanaf)
            aantal += 1
    return aantal
