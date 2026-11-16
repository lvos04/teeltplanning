"""Importer voor `Belichting winterronde.xlsx` (BOUWPLAN §7.2): alleen lezen, geen databasecode.

* `belichting` rij 2–84: branduren per week per module (duurcellen, op celtype gelezen; `importers/duur.py`).
* `belichting` vanaf rij 87: kaptabel met LBE-prognose en -realisatie (ijkpunten §6.5).
* `belichting` AK–AM: kostenvergelijking 150/200 µmol (startwaarden scenariovergelijking).
* `teeltschema`: historie per teelt (week/dag-kolommen; het jaar wordt gevraagd).
* `data`: 5-minutenregels met dimstand per module → dimgewogen uren per dag. Het blad is groot en wordt
  **streamend** gelezen (XML, één regel tegelijk), naar het voorbeeld van `legacy_lighting.py` in
  Gewichtsverwachting. Ontbrekende intervallen, dubbele tijdstempels, regels buiten tijdvolgorde en dagen met
  zomer-/wintertijdovergang worden gemeld.

    dimgewogen_uren = Σ (dimstand / 100 × interval)        branduren = Σ (interval waarin dimstand > 0)
"""

from __future__ import annotations

import math
import posixpath
import re
import zipfile
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from defusedxml.ElementTree import fromstring, iterparse
from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string

from .duur import duur_naar_uren, lees_weekuren
from .kalenderhulp import iso_date

X = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NL = ZoneInfo("Europe/Amsterdam")
MAX_LIJST = 200  # lengte van meldingslijsten in het voorbeeld
MAX_REGELS = 2_000_000


def _bereik(tekst: str) -> tuple[int, int]:
    van, tot = (int(x) for x in str(tekst).split("-"))
    return van, tot


# --- belichting: weekuren, kaptabel, kostenvergelijking ----------------------------------------------------
def weekuren(pad: Path, inst: dict) -> dict:
    eerste, laatste = _bereik(inst["import.belichting_weekuren_rijen"])
    regels = lees_weekuren(pad, "belichting", eerste, laatste, inst["import.weekuren_min"], inst["import.weekuren_max"])
    soorten: dict[str, int] = {}
    for r in regels:
        soorten[r.duur.soort] = soorten.get(r.duur.soort, 0) + 1
    weken = []
    for r in regels:
        if r.week not in weken:
            weken.append(r.week)
    return {
        "regels": [
            {
                "cel": r.cel,
                "module": int(r.module),
                "week": int(r.week),
                "uren": r.duur.uren,
                "soort": r.duur.soort,
                "melding": r.duur.melding,
                "buiten_grenzen": r.buiten_grenzen,
            }
            for r in regels
        ],  # fmt: skip
        "weken": weken,
        "soorten": soorten,
        "buiten_grenzen": [r.cel for r in regels if r.buiten_grenzen],
        "meldingen": [(r.cel, r.duur.melding) for r in regels if r.duur.melding and r.duur.soort not in ("time",)],
    }


def jaren_van_weken(weken: list[int], eerste_iso_jaar: int) -> dict[int, int]:
    """ISO-jaar per weekkolom: het opgegeven jaar voor de eerste week, +1 na de jaarwisseling (week springt terug)."""
    jaar, vorige, uit = eerste_iso_jaar, None, {}
    for week in weken:
        if vorige is not None and week < vorige:
            jaar += 1
        uit[week] = jaar
        vorige = week
    return uit


def _celwaarde(waarde, epoch):
    if isinstance(waarde, datetime):
        return waarde.date().isoformat()
    if isinstance(waarde, date):
        return waarde.isoformat()
    return waarde


def kaptabel(pad: Path, inst: dict) -> list[dict]:
    kop, laatste = _bereik(inst["import.belichting_kaptabel_rijen"])
    kolommen = {veld: column_index_from_string(k) for veld, k in inst["import.belichting_kaptabel_kolommen"].items()}
    wb = load_workbook(pad, read_only=True, data_only=True, keep_links=False)
    try:
        ws = wb["belichting"]
        uit = []
        for rijnummer, rij in enumerate(ws.iter_rows(min_row=kop + 1, max_row=laatste, max_col=max(kolommen.values()),
                                                     values_only=True), kop + 1):  # fmt: skip
            waarden = {veld: rij[k - 1] if k - 1 < len(rij) else None for veld, k in kolommen.items()}
            kap = waarden.get("kap")
            if not isinstance(kap, (int, float)) or isinstance(kap, bool):
                continue
            if all(v is None for veld, v in waarden.items() if veld != "kap"):
                continue
            for veld in [v for v in waarden if v.startswith("uren_")]:
                duur = duur_naar_uren(waarden[veld], wb.epoch)
                waarden[veld] = duur.uren
            plant = waarden.get("plantdatum")
            uit.append({"rij": rijnummer, "kap": int(kap),
                        "plantdatum": plant.date().isoformat() if isinstance(plant, datetime) else None,
                        "waarden": {k: _celwaarde(v, wb.epoch) for k, v in waarden.items()}})  # fmt: skip
        return uit
    finally:
        wb.close()


def kostenvergelijking(pad: Path, inst: dict) -> list[dict]:
    cellen = inst["import.belichting_kostenvergelijking"]
    van, tot = _bereik(cellen.get("rijen", "2-12"))
    wb = load_workbook(pad, read_only=True, data_only=True, keep_links=False)
    try:
        ws = wb["belichting"]
        uit = []
        for rij in range(van, tot + 1):
            regel = {s: ws[f"{cellen[s]}{rij}"].value for s in ("omschrijving", "a", "b")}
            if any(v is not None for v in regel.values()):
                uit.append(
                    {"rij": rij, **{k: v if isinstance(v, (int, float, str)) else None for k, v in regel.items()}}
                )
        return uit
    finally:
        wb.close()


# --- teeltschema ---------------------------------------------------------------------------------------------
def teeltschema(pad: Path, inst: dict) -> list[dict]:
    kolommen = {veld: int(k) for veld, k in inst["import.teeltschema_kolommen"].items()}
    wb = load_workbook(pad, read_only=True, data_only=True, keep_links=False)
    try:
        ws = wb["teeltschema"]
        uit = []
        for rijnummer, rij in enumerate(ws.iter_rows(min_row=2, max_col=max(kolommen.values()), values_only=True), 2):
            waarden = {veld: rij[k - 1] if k - 1 < len(rij) else None for veld, k in kolommen.items()}
            if not isinstance(waarden.get("kap"), (int, float)) or isinstance(waarden.get("kap"), bool):
                continue
            schoon = {k: (v if isinstance(v, (int, float, str)) and not isinstance(v, bool) else None)
                      for k, v in waarden.items()}  # fmt: skip
            uit.append({"rij": rijnummer, "kap": int(waarden["kap"]), "waarden": schoon})
        return uit
    finally:
        wb.close()


def teeltschema_datums(regels: list[dict], eerste_iso_jaar: int) -> list[dict]:
    """Echte datums: het jaar van de eerste regel, +1 als de plantweek zichtbaar terugspringt (> 20 weken)."""
    from .kalenderhulp import group_plant_years

    groep = {"rows": [{"plant_week": r["waarden"].get("plant_week"), "row_number": r["rij"]} for r in regels]}
    jaren = group_plant_years(groep, eerste_iso_jaar)
    uit = []
    for r in regels:
        w = r["waarden"]
        week, dag = w.get("plant_week"), w.get("plant_dag")
        plant = (
            iso_date(jaren[r["rij"]], int(week), int(dag))
            if isinstance(week, (int, float)) and isinstance(dag, (int, float))
            else None
        )
        datums = {"plantdatum": plant}
        for soort in ("licht_uit", "pluizen", "netten", "leeg"):
            wk, dg = w.get(f"{soort}_week"), w.get(f"{soort}_dag")
            datums[soort] = None
            if plant and isinstance(wk, (int, float)) and isinstance(dg, (int, float)):
                for jaar in (plant.isocalendar().year, plant.isocalendar().year + 1):
                    d = iso_date(jaar, int(wk), int(dg))
                    if d and d >= plant:
                        datums[soort] = d
                        break
        uit.append(r | {"datums": datums})
    return uit


# --- data: 5-minutenwaarden ----------------------------------------------------------------------------------
def _werkblad(archief: zipfile.ZipFile, naam: str) -> tuple[str, list[str], str]:
    werkboek = fromstring(archief.read("xl/workbook.xml"))
    eigenschappen = werkboek.find(X + "workbookPr")
    stelsel = "1904" if eigenschappen is not None and eigenschappen.get("date1904") in ("1", "true") else "1900"
    blad = next((s for s in werkboek.find(X + "sheets") if s.get("name", "").casefold() == naam), None)
    if blad is None:
        raise ValueError(f"Werkblad {naam} ontbreekt")
    relaties = fromstring(archief.read("xl/_rels/workbook.xml.rels"))
    rel = next((r for r in relaties if r.get("Id") == blad.get(R + "id")), None)
    if rel is None or rel.get("TargetMode") == "External":
        raise ValueError("Ongeldige werkbladverwijzing")
    doel = rel.get("Target", "")
    onderdeel = posixpath.normpath(doel.lstrip("/") if doel.startswith("/") else "xl/" + doel)
    if not onderdeel.startswith("xl/worksheets/"):
        raise ValueError("Onveilig werkbladpad")
    teksten = []
    if "xl/sharedStrings.xml" in archief.namelist():
        for si in fromstring(archief.read("xl/sharedStrings.xml")):
            teksten.append("".join(si.itertext()))
    return onderdeel, teksten, stelsel


def _cel(cel, teksten):
    soort = cel.get("t", "n")
    v = cel.find(X + "v")
    ruw = v.text if v is not None else None
    if soort == "s" and ruw is not None:
        return teksten[int(ruw)]
    if soort == "inlineStr":
        inline = cel.find(X + "is")
        return "".join(inline.itertext()) if inline is not None else None
    if soort == "n" and ruw is not None:
        try:
            return float(ruw)
        except ValueError:
            return ruw
    return ruw


def _excel_tijd(waarde, stelsel: str) -> datetime | None:
    if not isinstance(waarde, float) or not math.isfinite(waarde):
        return None
    basis = datetime(1904, 1, 1) if stelsel == "1904" else datetime(1899, 12, 30)
    tijd = basis + timedelta(seconds=round(waarde * 86400))
    return tijd if 2000 <= tijd.year <= 2099 else None


def regels_data(pad: Path):
    """Levert ('kop', {kolom: module}) en daarna per regel ('regel', (rijnummer, tijd, {module: waarde}))."""
    with zipfile.ZipFile(pad) as archief:
        onderdeel, teksten, stelsel = _werkblad(archief, "data")
        koppen: dict[int, dict[str, object]] = {}
        modules: dict[str, int] = {}
        aantal = 0
        with archief.open(onderdeel) as stroom:
            ouder = None
            for gebeurtenis, element in iterparse(stroom, events=("start", "end")):
                if gebeurtenis == "start" and element.tag == X + "sheetData":
                    ouder = element
                if gebeurtenis != "end" or element.tag != X + "row":
                    continue
                rij = int(element.get("r", "0"))
                cellen = {}
                for cel in element:
                    kolom = re.sub(r"\d+$", "", cel.get("r", ""))
                    cellen[kolom] = _cel(cel, teksten)
                if rij <= 3:
                    koppen[rij] = cellen
                    if rij == 3:
                        for kolom, kop in koppen.get(2, {}).items():
                            m = re.fullmatch(r"(.+):MOD\.\s*LICHT\s+(\d+)", str(kop or ""), re.I)
                            if m and str(koppen.get(1, {}).get(kolom, "")).strip().lower() == "gem. licht%":
                                modules[kolom] = int(m.group(2))
                        if not modules:
                            raise ValueError("Kopregels van het tabblad data (module, Gem. licht%) niet herkend")
                        yield "kop", {"modules": modules, "stelsel": stelsel}
                else:
                    aantal += 1
                    if aantal > MAX_REGELS:
                        raise ValueError("Te veel regels in het tabblad data")
                    tijd = _excel_tijd(cellen.get("A"), stelsel)
                    waarden = {modules[k]: cellen.get(k) for k in modules}
                    if tijd is not None or any(v is not None for v in waarden.values()):
                        yield "regel", (rij, tijd, waarden)
                element.clear()
                if ouder is not None:
                    ouder.clear()


def _dst_dag(d: date) -> bool:
    begin = datetime.combine(d, datetime.min.time(), NL)
    eind = datetime.combine(d + timedelta(days=1), datetime.min.time(), NL)
    return (eind - begin) != timedelta(days=1) or begin.utcoffset() != eind.utcoffset()


def dimgewogen_per_dag(pad: Path, interval_minuten: int, tijdstempel: str | None,
                       voortgang: Callable[[int], None] | None = None) -> dict:  # fmt: skip
    """Dimgewogen uren en branduren per module per dag, met een kwaliteitsrapport. `tijdstempel`: 'begin' of
    'eind' van het interval (bij 'eind' telt de waarde mee voor het interval ervóór)."""
    slots_per_dag = 24 * 60 // interval_minuten
    uur_per_slot = interval_minuten / 60
    dagen: dict[date, dict] = {}
    rapport = {"regels": 0, "zonder_tijd": 0, "buiten_tijdvolgorde": 0, "dubbel": 0, "naast_raster": 0,
               "ongeldige_waarden": 0, "dubbele_voorbeelden": [], "modules": []}  # fmt: skip
    vorige = None
    for soort, inhoud in regels_data(pad):
        if soort == "kop":
            rapport["modules"] = sorted(inhoud["modules"].values())
            continue
        rij, tijd, waarden = inhoud
        rapport["regels"] += 1
        if voortgang and rapport["regels"] % 5000 == 0:
            voortgang(rapport["regels"])
        if tijd is None:
            rapport["zonder_tijd"] += 1
            continue
        if vorige is not None and tijd < vorige:
            rapport["buiten_tijdvolgorde"] += 1
        vorige = tijd
        moment = tijd - timedelta(minutes=interval_minuten) if tijdstempel == "eind" else tijd
        seconden = moment.hour * 3600 + moment.minute * 60 + moment.second
        slot, rest = divmod(seconden, interval_minuten * 60)
        if rest:
            rapport["naast_raster"] += 1
            continue
        dag = dagen.setdefault(moment.date(), {"masker": 0, "modules": {}})
        bit = 1 << slot
        if dag["masker"] & bit:
            rapport["dubbel"] += 1
            if len(rapport["dubbele_voorbeelden"]) < MAX_LIJST:
                rapport["dubbele_voorbeelden"].append({"rij": rij, "tijd": tijd.isoformat(sep=" ")})
            continue
        dag["masker"] |= bit
        for module, waarde in waarden.items():
            m = dag["modules"].setdefault(module, {"som": 0.0, "aan": 0, "geldig": 0, "ongeldig": 0})
            if not isinstance(waarde, float) or not 0 <= waarde <= 100:
                m["ongeldig"] += 1
                rapport["ongeldige_waarden"] += 1
                continue
            m["geldig"] += 1
            m["som"] += waarde / 100 * uur_per_slot
            m["aan"] += waarde > 0
    resultaat = []
    onvolledig, dst = [], []
    for d in sorted(dagen):
        info = dagen[d]
        volledig_tijd = info["masker"].bit_count() == slots_per_dag
        if not volledig_tijd:
            onvolledig.append(
                {"datum": d.isoformat(), "intervallen": info["masker"].bit_count(), "verwacht": slots_per_dag}
            )
        if _dst_dag(d):
            dst.append(d.isoformat())
        for module, m in sorted(info["modules"].items()):
            volledig = volledig_tijd and m["geldig"] == slots_per_dag
            kwaliteit = [] if volledig else ["onvolledig"]
            if _dst_dag(d):
                kwaliteit.append("zomer_wintertijd")
            resultaat.append([module, d.isoformat(), m["som"], m["aan"] * uur_per_slot, volledig, kwaliteit])
    rapport["dagen"] = len(dagen)
    rapport["eerste"] = min(dagen).isoformat() if dagen else None
    rapport["laatste"] = max(dagen).isoformat() if dagen else None
    rapport["onvolledige_dagen"] = onvolledig[:MAX_LIJST]
    rapport["aantal_onvolledige_dagen"] = len(onvolledig)
    rapport["zomer_wintertijd"] = dst
    rapport["ontbrekend"] = _ontbrekende_perioden(dagen, slots_per_dag, interval_minuten)
    return {"dagen": resultaat, "rapport": rapport}


def _ontbrekende_perioden(dagen: dict[date, dict], slots_per_dag: int, interval_minuten: int) -> dict:
    """Aaneengesloten ontbrekende intervallen tussen de eerste en de laatste dag (ook hele dagen)."""
    if not dagen:
        return {"perioden": [], "aantal": 0, "intervallen": 0}
    perioden: list[list] = []
    totaal = 0
    d, laatste = min(dagen), max(dagen)
    stap = timedelta(minutes=interval_minuten)
    while d <= laatste:
        masker = dagen.get(d, {"masker": 0})["masker"]
        if masker.bit_count() != slots_per_dag:
            for slot in range(slots_per_dag):
                if not masker >> slot & 1:
                    totaal += 1
                    begin = datetime.combine(d, datetime.min.time()) + slot * stap
                    if perioden and perioden[-1][1] == begin:
                        perioden[-1][1] = begin + stap
                    else:
                        perioden.append([begin, begin + stap])
        d += timedelta(days=1)
    return {"perioden": [{"van": a.isoformat(sep=" "), "tot": b.isoformat(sep=" ")} for a, b in perioden[:MAX_LIJST]],
            "aantal": len(perioden), "intervallen": totaal}  # fmt: skip
