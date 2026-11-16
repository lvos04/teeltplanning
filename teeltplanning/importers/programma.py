"""Importer voor `Teeltprogramma maken …xlsx` (BOUWPLAN §7.1): alleen lezen, geen databasecode.

Rondeblad: titel in de titelcel (start `C8`), kop op rij 10–11, kapregels op rij 12–51. Kolommen, koppen en
rijgrenzen komen uit het instellingenregister (groep Import), zodat een verplaatste kolom in Beheer kan worden
aangepast. Invoercellen worden als waarde gelezen; formulecellen zowel als formule als gecachte uitkomst, en
beide worden als bewijs bewaard. Er worden geen formules uitgevoerd. Het kalenderjaar staat niet in het
werkboek: het ISO-jaar van de eerste planting wordt per ronde gevraagd.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string

from ..kern.kalender import lees_week_dag, week_dag
from .kalenderhulp import iso_date, plantjaren
from .werkboek import _int_literal, _json_value, _source, _validate_archive, parse_schema

MAX_BLADEN = 200
WEEK_DAG = re.compile(r"^\s*(\d{1,2})\s*-\s*([1-7])\s*$")

# Velden die als handmatige waarde worden overgenomen, met hun type.
INVOERVELDEN = {
    "ld_dagen": "geheel",
    "reactietijd": "geheel",
    "verlenging": "geheel",
    "dichtheid": "getal",
    "gewicht": "getal",
    "uitval": "getal",
}
CACHEVELDEN = ("licht_uit_week", "licht_uit_dag", "bloei_week", "bloei_dag", "teeltduur", "geplant", "oogstaantal")


def _norm(waarde) -> str:
    return re.sub(r"\s+", " ", str(waarde or "")).strip().lower()


def _getal(bron: dict) -> float | None:
    waarde = bron["cached"] if bron["formula"] is not None else bron["value"]
    if isinstance(waarde, bool) or not isinstance(waarde, (int, float)):
        return None
    return float(waarde)


def _geheel(bron: dict) -> tuple[int | None, str | None]:
    """Geheel getal uit een invoercel; (None, reden) als het geen geheel getal is."""
    if bron["value"] is None and bron["formula"] is None:
        return None, None
    if bron["formula"] is not None:
        waarde = bron["cached"]
        if isinstance(waarde, (int, float)) and not isinstance(waarde, bool) and float(waarde).is_integer():
            return int(waarde), "formule_als_invoer"
        return None, "formule_zonder_bruikbare_uitkomst"
    waarde = _int_literal(bron, 0, 100000)
    return (waarde, None) if waarde is not None else (None, "geen_geheel_getal")


def lees_programma(pad: Path, instelling: dict) -> dict:
    """Lees alle rondebladen. `instelling` bevat de importinstellingen uit het register.

    Uitkomst: {"rondes": [...], "meldingen": [...]}; elke ronde heeft `nummer`, `blad`, `titel`,
    `titel_conflict` en `regels` (één per kap, met broncellen).
    """
    max_file = instelling["import.max_upload_mb"] * 1024 * 1024
    max_uitgepakt = instelling["import.max_uitgepakt_mb"] * 1024 * 1024
    _validate_archive(pad, max_file, max_uitgepakt)
    kolommen = {
        veld: column_index_from_string(letter) for veld, letter in instelling["import.rondeblad_kolommen"].items()
    }
    koppen = instelling["import.rondeblad_koppen"]
    titelcel = instelling["import.rondeblad_titelcel"]
    eerste, laatste = instelling["import.rondeblad_eerste_rij"], instelling["import.rondeblad_laatste_rij"]
    max_kolom = max(
        [*kolommen.values(), *(column_index_from_string(re.sub(r"\d", "", c)) for c in [*koppen, titelcel])]
    )

    formules = load_workbook(pad, read_only=True, data_only=False, keep_links=False)
    gecacht = load_workbook(pad, read_only=True, data_only=True, keep_links=False)
    try:
        if len(formules.sheetnames) > MAX_BLADEN:
            raise ValueError("Te veel werkbladen")
        rondes, meldingen = [], []
        for ws in formules.worksheets:
            m = re.fullmatch(r"Ronde\s+(\d+)", ws.title.strip(), re.I)
            if not m:
                continue
            cs = gecacht[ws.title]
            rijen = zip(
                ws.iter_rows(min_row=1, max_row=laatste, min_col=1, max_col=max_kolom),
                cs.iter_rows(min_row=1, max_row=laatste, min_col=1, max_col=max_kolom),
                strict=False,
            )
            cellen: dict[str, object] = {}
            regels_ruw = []
            for rijnummer, (ruw, waarden) in enumerate(rijen, 1):
                if rijnummer < eerste:
                    for kolom, cel in enumerate(waarden, 1):
                        cellen[f"{_kolomletter(kolom)}{rijnummer}"] = cel.value
                else:
                    regels_ruw.append((rijnummer, ruw, waarden))
            afwijkend = {cel: tekst for cel, tekst in koppen.items() if _norm(cellen.get(cel)) != _norm(tekst)}
            if afwijkend:
                meldingen.append(
                    {"blad": ws.title, "code": "rondeblad_niet_herkend",
                     "tekst": f"{ws.title}: kopteksten wijken af ({', '.join(f'{c} is {cellen.get(c)!r}, verwacht {t!r}' for c, t in afwijkend.items())}); blad overgeslagen."}
                )  # fmt: skip
                continue
            nummer = int(m.group(1))
            titel = _json_value(cellen.get(titelcel))
            conflict = bool(titel) and _norm(titel) != f"ronde {nummer}"
            regels = _lees_regels(ws.title, regels_ruw, kolommen)
            rondes.append(
                {"nummer": nummer, "blad": ws.title, "titel": titel, "titel_conflict": conflict, "regels": regels}
            )
            if conflict:
                meldingen.append({"blad": ws.title, "code": "rondelabel_wijkt_af",
                                  "tekst": f"{ws.title}: titel in {titelcel} is {titel!r}."})  # fmt: skip
        rondes.sort(key=lambda r: r["nummer"])
        return {"rondes": rondes, "meldingen": meldingen}
    finally:
        formules.close()
        gecacht.close()


def _kolomletter(index: int) -> str:
    from openpyxl.utils import get_column_letter

    return get_column_letter(index)


def _lees_regels(blad: str, regels_ruw, kolommen: dict[str, int]) -> list[dict]:
    regels = []
    vakgroep = None
    for rijnummer, ruw, waarden in regels_ruw:
        bron = {}
        for veld, kolom in kolommen.items():
            if kolom - 1 < len(ruw):
                bron[veld] = _source(ruw[kolom - 1], waarden[kolom - 1], rijnummer, kolom)
            else:
                bron[veld] = {"cell": None, "value": None, "formula": None, "cached": None, "confidence": "missing"}
        volgnummer = _int_literal(bron["volgnummer"], 1, 1000)
        if volgnummer is None:
            continue
        meldingen: list[str] = []
        eigen_groep = bron["vakgroep"]["value"]
        if eigen_groep not in (None, ""):
            vakgroep = str(eigen_groep).strip()
        plant_week = _getal(bron["plant_week"])
        plant_dag = _getal(bron["plant_dag"])
        plant_week = (
            int(plant_week) if plant_week is not None and plant_week.is_integer() and 1 <= plant_week <= 53 else None
        )
        plant_dag = int(plant_dag) if plant_dag is not None and plant_dag.is_integer() and 1 <= plant_dag <= 7 else None
        if plant_week is None or plant_dag is None:
            meldingen.append("plantdatum_onbekend")
        formule_plantweek = bron["plant_week"]["formula"]
        verwijzing = None
        if formule_plantweek:
            mv = re.fullmatch(r"='?Ronde\s+(\d+)'?!\$?([A-Z]+)\$?(\d+)", str(formule_plantweek).strip(), re.I)
            verwijzing = (
                {"ronde": int(mv.group(1)), "cel": mv.group(2) + mv.group(3)} if mv else {"ronde": None, "cel": None}
            )
        invoer = {}
        for veld, soort in INVOERVELDEN.items():
            if soort == "geheel":
                waarde, reden = _geheel(bron[veld])
                if reden and reden != "formule_als_invoer":
                    meldingen.append(f"{veld}_{reden}")
            else:
                waarde = _getal(bron[veld]) if bron[veld]["formula"] is None else None
                if bron[veld]["formula"] is not None:
                    meldingen.append(f"{veld}_formule_genegeerd")
            invoer[veld] = waarde
        if invoer["uitval"] is not None and not 0 <= invoer["uitval"] <= 1:
            meldingen.append("uitval_geen_fractie")
            invoer["uitval"] = None
        schema = bron["schema"]["value"]
        rassen, schema_redenen = parse_schema(schema)
        meldingen.extend(schema_redenen)
        cache = {}
        for veld in CACHEVELDEN:
            waarde = _getal(bron[veld])
            cache[veld] = int(waarde) if waarde is not None and waarde.is_integer() else waarde
        regels.append(
            {
                "blad": blad,
                "rij": rijnummer,
                "volgnummer": volgnummer,
                "vakgroep": vakgroep,
                "vakgroep_doorgevuld": eigen_groep in (None, ""),
                "schema": None if schema is None else str(schema),
                "rassen": rassen,
                "plant_week": plant_week,
                "plant_dag": plant_dag,
                "plant_verwijzing": verwijzing,
                **invoer,
                "tussenlicht_ruw": bron["tussenlicht"]["value"],
                "teeltverlenging_ruw": bron["teeltverlenging"]["value"],
                "geoogst_ruw": bron["geoogst"]["value"],
                "gemiddeld_gewicht": _getal(bron["gemiddeld_gewicht"]),
                "cache": cache,
                "meldingen": meldingen,
                "broncellen": bron,
            }
        )
    return regels


def datums(regels: list[dict], iso_jaar: int) -> dict[int, dict]:
    """Echte datums per rij bij een opgegeven ISO-jaar van de eerste planting (week 53 telt mee)."""
    jaren = plantjaren(regels, iso_jaar)
    uit = {}
    for r in regels:
        jaar = jaren[r["rij"]]
        plant = iso_date(jaar, r["plant_week"], r["plant_dag"])
        meldingen = []
        if r["plant_week"] is not None and r["plant_dag"] is not None and plant is None:
            meldingen.append("week_bestaat_niet_in_jaar")
        tussenlicht = _tussenlicht(r["tussenlicht_ruw"], plant, r.get("ld_dagen"), meldingen)
        geoogst = None
        if isinstance(r["geoogst_ruw"], str) and WEEK_DAG.match(r["geoogst_ruw"]) and plant is not None:
            anker = plant + timedelta(days=(r["cache"].get("teeltduur") or 0))
            geoogst = lees_week_dag(r["geoogst_ruw"], anker)
        elif r["geoogst_ruw"] not in (None, ""):
            meldingen.append("datum_geoogst_onleesbaar")
        uit[r["rij"]] = {
            "jaar": jaar,
            "plantdatum": plant,
            "tussenlicht": tussenlicht,
            "geoogst": geoogst,
            "meldingen": meldingen,
        }
    return uit


def _tussenlicht(ruw, plant: date | None, ld: int | None, meldingen: list[str]) -> date | None:
    if ruw in (None, ""):
        return None
    anker = plant + timedelta(days=ld or 0) if plant else None
    if isinstance(ruw, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}(T[\d:.]+)?", ruw.strip()):
        ruw = datetime.fromisoformat(ruw.strip())  # datumcel, als bewijs bewaard in ISO-notatie
    if isinstance(ruw, (datetime, date)):
        d = ruw.date() if isinstance(ruw, datetime) else ruw
        if anker is None or abs((d - anker).days) <= 183:
            return d
        meldingen.append("tussenlicht_datum_jaar_aangepast")
        _, week, dag = week_dag(d)
        return lees_week_dag(f"{week}-{dag}", anker)
    if isinstance(ruw, str) and WEEK_DAG.match(ruw) and anker is not None:
        return lees_week_dag(ruw, anker)
    meldingen.append("tussenlicht_onleesbaar")
    return None
