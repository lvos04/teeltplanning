"""LBE-kalibratie, dichtheidsreeks en ijkpunten (BOUWPLAN §6.5).

* **Kalibratie**: per afgesloten teelt `lbe_gerealiseerd = gemiddeld_takgewicht × dichtheid / lichtsom`, gemiddeld
  per (ras, plantweek, lichtregime) in de tabel `parameter_lbe`. Alleen teelten met één ras; regels met bron
  "handmatig" worden niet overschreven.
* **Dichtheidsreeks**: de dichtheden die in de historie voorkomen (handmatige waarden in het plan).
* **IJkpunten**: de kaptabel op het tabblad belichting (22 kappen) naast de eigen berekening.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import instellingen, planning
from ..auth import audit
from ..db.modellen import LbeIjkpunt, ParameterLbe, Teelt
from ..kern import gewas


def gerealiseerde_lbe(db: Session, scenario_id: int) -> list[dict]:
    """Per afgesloten teelt (geoogst, gemiddeld gewicht, dichtheid en lichtsom bekend) de gerealiseerde LBE."""
    geladen, plan = planning.bereken(db, scenario_id)
    uit = []
    for teelt in geladen.teelten.values():
        u = plan.teelten[teelt.id]
        lichtsom = u.lichtsom.totaal if u.lichtsom else None
        lbe = gewas.lbe_gerealiseerd(teelt.gemiddeld_gewicht_g, u.effectief["dichtheid"], lichtsom)
        if teelt.geoogst_op is None or lbe is None or u.effectief["plantdatum"] is None:
            continue
        rassen = [r.code for r in teelt.rassen]
        uit.append({"teelt": teelt.id, "kap": teelt.kap, "ras": rassen[0] if len(rassen) == 1 else None,
                    "plantweek": u.effectief["plantdatum"].isocalendar().week, "lbe": lbe, "lichtsom": lichtsom})  # fmt: skip
    return uit


def kalibreer(db: Session, gebruiker, scenario_id: int) -> dict:
    """LBE per (ras, plantweek) uit de historie: afgesloten teelten uit het plan, het teeltschema (via kap +
    plantdatum) en, vanaf fase 6, de gekoppelde teelten uit Gewichtsverwachting (`voorstel/historie.py`)."""
    from ..bronnen import koppelbronnen
    from . import historie

    snap = instellingen.laad(db)
    minimum = snap["gewas.min_waarnemingen_lbe"]
    gekoppeld = koppelbronnen.waarnemingen(db)
    waarnemingen, _ = historie.verzamel(db, extra=gekoppeld)
    per_cel: dict[tuple[str, int, str], list[float]] = {}
    gemengd = uit_koppeling = 0
    for w in waarnemingen:
        lbe = gewas.lbe_gerealiseerd(w.gewicht, w.dichtheid, w.lichtsom)
        if lbe is None:
            continue
        if w.ras is None:
            gemengd += 1
            continue
        uit_koppeling += w.sleutel.startswith("koppeling:")
        per_cel.setdefault((w.ras, w.week, ""), []).append(lbe)
    bestaand = {(p.ras, p.plantweek, p.lichtregime or ""): p for p in db.scalars(select(ParameterLbe))}
    gewijzigd = overgeslagen = 0
    for (ras, week, regime), waarden in per_cel.items():
        rij = bestaand.get((ras, week, regime))
        if rij is not None and rij.bron == "handmatig":
            overgeslagen += 1
            continue
        if rij is None:
            rij = ParameterLbe(ras=ras, plantweek=week, lichtregime=regime)
            db.add(rij)
        rij.lbe_g_per_mol = sum(waarden) / len(waarden)
        rij.aantal_waarnemingen = len(waarden)
        rij.status = "indicatief" if minimum is None else ("gevalideerd" if len(waarden) >= minimum else "onvoldoende")
        rij.bron = "kalibratie"
        gewijzigd += 1
    uitkomst = {"cellen": gewijzigd, "teelten": sum(len(w) for w in per_cel.values()), "gemengd_overgeslagen": gemengd,
                "handmatig_behouden": overgeslagen, "uit_koppeling": uit_koppeling}  # fmt: skip
    audit(db, gebruiker, "lbe_gekalibreerd", "parameter_lbe", uitkomst)
    return uitkomst


def dichtheden_uit_historie(db: Session, scenario_id: int) -> list[float]:
    geladen, _ = planning.bereken(db, scenario_id)
    waarden = set()
    for (teelt_id, veld), v in geladen.veldwaarden.items():
        if veld == "dichtheid" and v.handmatig is not None and teelt_id in geladen.teelten:
            waarden.add(float(v.handmatig))
    return sorted(waarden)


def ijkpunten(db: Session, scenario_id: int | None) -> dict:
    """De kaptabel uit het belichtingsblad naast de eigen LBE (gewicht realisatie × dichtheid / eigen lichtsom)."""
    snap = instellingen.laad(db)
    marge = snap["gewas.lbe_ijk_marge"]
    tolerantie = snap["import.koppel_tolerantie_dagen"] or 0
    per_kap: dict[int, list[tuple[date, Teelt]]] = {}
    geladen, plan = planning.bereken(db, scenario_id) if scenario_id else (None, None)
    for teelt in geladen.teelten.values() if geladen else []:
        plant = plan.teelten[teelt.id].effectief["plantdatum"]
        if plant is not None:
            per_kap.setdefault(teelt.kap, []).append((plant, teelt))
    regels = []
    for ijk in db.scalars(select(LbeIjkpunt).order_by(LbeIjkpunt.rij)):
        w = ijk.waarden or {}
        regel = {"rij": ijk.rij, "kap": ijk.kap, "plantdatum": ijk.plantdatum, "werkboek": w, "teelt": None,
                 "lichtsom": None, "dichtheid": None, "lbe": None, "verschil": None, "oordeel": None, "reden": None}  # fmt: skip
        kandidaten = [t for d, t in per_kap.get(ijk.kap, [])
                      if ijk.plantdatum and abs((d - ijk.plantdatum).days) <= tolerantie]  # fmt: skip
        if not kandidaten:
            regel["reden"] = "geen teelt in het plan op deze kap en plantdatum"
            regels.append(regel)
            continue
        teelt = kandidaten[0]
        u = plan.teelten[teelt.id]
        regel["teelt"] = teelt.id
        regel["lichtsom"] = u.lichtsom.totaal if u.lichtsom else None
        regel["dichtheid"] = u.effectief["dichtheid"]
        realisatie = w.get("gewicht_realisatie")
        realisatie = realisatie if isinstance(realisatie, (int, float)) else None
        regel["lbe"] = gewas.lbe_gerealiseerd(realisatie, regel["dichtheid"], regel["lichtsom"])
        lbe_werkboek = w.get("lbe_realisatie") if isinstance(w.get("lbe_realisatie"), (int, float)) else None
        if regel["lbe"] is None:
            if u.lichtsom and u.lichtsom.ontbrekend:
                _, _, reden = u.lichtsom.ontbrekend[0]
                regel["reden"] = f"lichtsom onbekend: {reden}"
            else:
                regel["reden"] = "dichtheid of gewicht onbekend"
        elif lbe_werkboek is not None:
            regel["verschil"] = regel["lbe"] - lbe_werkboek
            if marge is None:
                regel["oordeel"] = "marge niet afgesproken"
            else:
                regel["oordeel"] = "binnen marge" if abs(regel["verschil"]) <= marge else "buiten marge"
        regels.append(regel)
    return {"regels": regels, "marge": marge, "tolerantie": tolerantie,
            "binnen": sum(1 for r in regels if r["oordeel"] == "binnen marge"),
            "buiten": sum(1 for r in regels if r["oordeel"] == "buiten marge"),
            "onbekend": sum(1 for r in regels if r["lbe"] is None)}  # fmt: skip
