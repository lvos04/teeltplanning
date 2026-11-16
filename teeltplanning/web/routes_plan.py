"""Schermen Ronde (raster), Kap (ketting met tijdbalk) en Teelt (drie lagen per veld)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth, instellingen
from ..db.basis import get_db
from ..db.modellen import Gebruiker, Importbatch, Kap, Ronde, Scenario, Teelt, Werkboekregel
from ..kern.kalender import week_dag, weergave
from ..kern.keten import LABELS
from ..kern.typen import DATUMVELDEN, VELDEN
from ..planning import Ongeldig, bereken, controleer_overlap, lees_invoer, vastgesteld_of_eerste, zet_handmatig
from .sjablonen import getal, templates

router = APIRouter()

# Kolommen van het rondeblad, in dezelfde volgorde (BOUWPLAN §7.1).
KOLOMMEN = [
    ("B", "vakgroep", "groep"), ("C", "volgnummer", "kap"), ("D", "soort", "soort"), ("E", "plant_week", "pl wk"),
    ("F", "plant_dag", "dag"), ("G", "ld_dagen", "LD"), ("H", "licht_uit_week", "l.uit wk"), ("I", "licht_uit_dag", "dag"),
    ("J", "reactietijd", "RT"), ("K", "tussenlicht", "tussenl."), ("L", "verlenging", "verl."), ("M", "dichtheid", "pl/m²"),
    ("N", "bloei_week", "bloei wk"), ("O", "bloei_dag", "dag"), ("P", "teeltduur", "duur"), ("R", "teeltverlenging", "teeltverl."),
    ("T", "gewicht", "gew. progn."), ("V", "geoogst", "geoogst"), ("X", "gemiddeld_gewicht", "gem. gew."),
    ("Y", "uitval", "uitval"), ("Z", "geplant", "geplant"), ("AB", "oogstaantal", "oogstaantal"), ("AD", "maand", "maand"),
    ("AF", "cumulatief", "cumulatief"),
]  # fmt: skip
BEWERKBAAR = {"plant_week": "plantdatum", "plant_dag": "plantdatum", "ld_dagen": "ld_dagen", "reactietijd": "reactietijd",
              "tussenlicht": "tussenlicht", "verlenging": "verlenging", "dichtheid": "dichtheid", "gewicht": "gewicht",
              "uitval": "uitval"}  # fmt: skip


def _formaat(geladen) -> str:
    return geladen.snap["kalender.weergave"]


def _veldtekst(veld: str, waarde) -> str:
    if waarde is None:
        return ""
    if veld in DATUMVELDEN:
        return weergave(waarde)
    if veld == "uitval":
        return getal(waarde * 100, 1) + "%"
    if veld == "dichtheid":
        return getal(waarde, 1)
    return getal(waarde)


def rijen(geladen, plan, ronde: Ronde, kan_schrijven: bool, ruw: dict | None = None) -> list[dict]:
    teelten = sorted(
        (t for t in geladen.teelten.values() if t.ronde_id == ronde.id), key=lambda t: (t.volgnummer or 0, t.kap)
    )
    uit = []
    cumulatief = 0.0
    cumulatief_bekend = True
    for teelt in teelten:
        u = plan.teelten[teelt.id]
        kap = geladen.kappen.get(teelt.kap)
        plant = u.effectief["plantdatum"]
        lu, oogst = u.licht_uit, u.oogst
        if u.oogstaantal is None:
            cumulatief_bekend = False
        else:
            cumulatief += u.oogstaantal
        regel = geladen.veldwaarden
        cellen = {
            "vakgroep": {"tekst": kap.vakgroep if kap and kap.vakgroep else ""},
            "volgnummer": {"tekst": str(teelt.kap)},
            "soort": {"tekst": teelt.schemacode or ""},
            "plant_week": {"tekst": str(week_dag(plant)[1]) if plant else ""},
            "plant_dag": {"tekst": str(week_dag(plant)[2]) if plant else ""},
            "licht_uit_week": {"tekst": str(week_dag(lu)[1]) if lu else ""},
            "licht_uit_dag": {"tekst": str(week_dag(lu)[2]) if lu else ""},
            "bloei_week": {"tekst": str(week_dag(oogst)[1]) if oogst else ""},
            "bloei_dag": {"tekst": str(week_dag(oogst)[2]) if oogst else ""},
            "teeltduur": {"tekst": getal(u.teeltduur)},
            "teeltverlenging": {"tekst": _tekst((ruw or {}).get(teelt.id, {}).get("teeltverlenging"))},
            "geoogst": {"tekst": weergave(teelt.geoogst_op) if teelt.geoogst_op else ""},
            "gemiddeld_gewicht": {"tekst": getal(teelt.gemiddeld_gewicht_g, 1)},
            "geplant": {
                "tekst": getal(u.geplant)
                if u.geplant is not None
                else ("onbekend" if u.effectief["dichtheid"] is not None else "")
            },
            "oogstaantal": {"tekst": getal(u.oogstaantal) if u.oogstaantal is not None else ""},
            "maand": {"tekst": str(oogst.month) if oogst else ""},
            "cumulatief": {"tekst": getal(cumulatief) if cumulatief_bekend and u.oogstaantal is not None else ""},
        }
        for kolom, veld in BEWERKBAAR.items():
            if kolom in ("plant_week", "plant_dag"):
                cel = cellen[kolom]
            else:
                cel = cellen.setdefault(kolom, {})
                cel["tekst"] = _veldtekst(veld, u.effectief[veld])
            cel["veld"] = kolom if kan_schrijven else None
            cel["handmatig"] = u.bron[veld] == "handmatig"
            cel["voorstel"] = _veldtekst(veld, u.voorstel[veld]) or "onbekend"
            v = regel.get((teelt.id, veld))
            cel["bron"] = (v.handmatig_bron or "") if v else ""
        uit.append({"teelt": teelt, "uitkomst": u, "cellen": cellen, "meldingen": u.meldingen,
                    "fout": u.heeft_fout, "plant_volgt_keten": u.bron["plantdatum"] == "voorstel"})  # fmt: skip
    return uit


def _tekst(waarde) -> str:
    """Ruwe werkboekwaarde die de app bewaart maar niet doorrekent (zoals kolom R, open vraag B2)."""
    if waarde is None:
        return ""
    return (
        getal(waarde, 1)
        if isinstance(waarde, float) and not waarde.is_integer()
        else str(int(waarde) if isinstance(waarde, float) else waarde)
    )


def _context(request, db, gebruiker, scenario_id: int | None, ronde_nummer: int | None):
    scenario = db.get(Scenario, scenario_id) if scenario_id else vastgesteld_of_eerste(db)
    scenarios = db.scalars(select(Scenario).order_by(Scenario.id)).all()
    if scenario is None:
        return None
    geladen, plan = bereken(db, scenario.id)
    rondes = sorted(geladen.rondes.values(), key=lambda r: r.nummer)
    return scenario, scenarios, geladen, plan, rondes


@router.get("/ronde")
def ronde_pagina(request: Request, scenario: int | None = None, nummer: int | None = None,
                 gebruiker: Gebruiker = Depends(auth.vereis_lezen), db: Session = Depends(get_db)):  # fmt: skip
    ctx = _context(request, db, gebruiker, scenario, nummer)
    if ctx is None or not ctx[4]:
        return templates.TemplateResponse(request, "leeg.html", {"gebruiker": gebruiker})
    scenario_obj, scenarios, geladen, plan, rondes = ctx
    ronde = next((r for r in rondes if r.nummer == nummer), rondes[0])
    kan_schrijven = gebruiker.rol in ("beheerder", "medewerker")
    ruw = _werkboek_ruw(db, geladen, ronde)
    return templates.TemplateResponse(request, "ronde.html", {
        "gebruiker": gebruiker, "scenario": scenario_obj, "scenarios": scenarios, "rondes": rondes, "ronde": ronde,
        "kolommen": KOLOMMEN, "rijen": rijen(geladen, plan, ronde, kan_schrijven, ruw), "kan_schrijven": kan_schrijven,
        "oppervlak_onbekend": all(v is None for _, v in geladen.snap.reeks("bedrijf.oppervlak_per_kap_m2"))
        and all(k.oppervlak_m2 is None for k in geladen.kappen.values()),
    })  # fmt: skip


def _werkboek_ruw(db: Session, geladen, ronde: Ronde) -> dict[int, dict]:
    """Ruwe werkboekwaarden die niet worden doorgerekend (zoals kolom R), per teelt."""
    ids = [t.id for t in geladen.teelten.values() if t.ronde_id == ronde.id]
    if not ids:
        return {}
    uit = {}
    for regel in db.scalars(select(Werkboekregel).where(Werkboekregel.teelt_id.in_(ids))):
        r = (regel.broncellen or {}).get("teeltverlenging") or {}
        uit[regel.teelt_id] = {"teeltverlenging": r.get("value")}
    return uit


@router.post("/teelt/{teelt_id}/cel", response_class=HTMLResponse)
def cel_wijzigen(request: Request, teelt_id: int, veld: str = Form(...), waarde: str = Form("", max_length=50),
                 reden: str = Form("", max_length=2000), gebruiker: Gebruiker = Depends(auth.vereis_schrijven),
                 db: Session = Depends(get_db)):  # fmt: skip
    """Eén cel opslaan, het hele plan herberekenen en de gewijzigde rijen van deze ronde terugsturen."""
    teelt = db.get(Teelt, teelt_id)
    if teelt is None or veld not in BEWERKBAAR:
        raise HTTPException(404, "Onbekende teelt of veld.")
    ronde = db.get(Ronde, teelt.ronde_id)
    geladen_voor, plan_voor = bereken(db, ronde.scenario_id)
    ruw = _werkboek_ruw(db, geladen_voor, ronde)
    voor = rijen(geladen_voor, plan_voor, ronde, True, ruw)
    uitkomst = plan_voor.teelten[teelt.id]
    doelveld = BEWERKBAAR[veld]
    try:
        waarde_obj = _lees_cel(veld, waarde, uitkomst, ronde)
        zet_handmatig(db, gebruiker, teelt, doelveld, waarde_obj, reden)
        db.flush()
        if geladen_voor.snap["keten.overlap"] == "blokkeren":
            melding = controleer_overlap(db, ronde.scenario_id, teelt.id)
            if melding:
                db.rollback()
                raise Ongeldig(melding + " Opslaan is geblokkeerd (instelling Gedrag bij overlap).")
        db.commit()
    except (Ongeldig, ValueError) as fout:
        db.rollback()
        return HTMLResponse(f'<div class="melding fout">{_html(str(fout))}</div>', status_code=422)
    geladen, plan = bereken(db, ronde.scenario_id)
    na = rijen(geladen, plan, ronde, True, ruw)
    oud = {r["teelt"].id: (r["cellen"], r["meldingen"]) for r in voor}
    gewijzigd = [r for r in na if oud.get(r["teelt"].id) != (r["cellen"], r["meldingen"])]
    elders = sum(1 for tid, u in plan.teelten.items()
                 if geladen.teelten[tid].ronde_id != ronde.id and plan_voor.teelten.get(tid) != u)  # fmt: skip
    return templates.TemplateResponse(request, "_rijen.html", {
        "rijen": gewijzigd, "kolommen": KOLOMMEN, "kan_schrijven": True, "elders": elders,
    })  # fmt: skip


def _html(tekst: str) -> str:
    from html import escape

    return escape(tekst)


def _lees_cel(veld: str, tekst: str, uitkomst, ronde: Ronde):
    huidig = uitkomst.effectief["plantdatum"] or uitkomst.voorstel["plantdatum"]
    anker = huidig or (
        date.fromisocalendar(ronde.iso_jaar_eerste_planting, 1, 1) if ronde.iso_jaar_eerste_planting else date.today()
    )
    tekst = (tekst or "").strip()
    if veld in ("plant_week", "plant_dag"):
        if tekst == "":
            return None
        if not tekst.isdigit():
            raise Ongeldig("Vul een getal in.")
        _, week, dag = week_dag(anker)
        if veld == "plant_week":
            week = int(tekst)
        else:
            dag = int(tekst)
        return lees_invoer("plantdatum", f"{week}-{dag}", anker)
    doel = BEWERKBAAR[veld]
    if doel == "tussenlicht":
        return lees_invoer(doel, tekst, uitkomst.licht_uit or anker)
    return lees_invoer(doel, tekst, anker)


@router.get("/kap/{nummer}")
def kap_pagina(request: Request, nummer: int, scenario: int | None = None, gebruiker: Gebruiker = Depends(auth.vereis_lezen),
               db: Session = Depends(get_db)):  # fmt: skip
    kap = db.get(Kap, nummer)
    if kap is None:
        raise HTTPException(404, "Onbekende kap.")
    ctx = _context(request, db, gebruiker, scenario, None)
    if ctx is None:
        return templates.TemplateResponse(request, "leeg.html", {"gebruiker": gebruiker})
    scenario_obj, scenarios, geladen, plan, rondes = ctx
    ketting = plan.per_kap(nummer)
    datums = [d for u in ketting for d in (u.effectief["plantdatum"], u.oogst) if d]
    begin, eind = (min(datums), max(datums)) if datums else (None, None)
    span = max((eind - begin).days, 1) if begin else 1
    balken = []
    for u in ketting:
        teelt = geladen.teelten[u.id]
        plant = u.effectief["plantdatum"]
        b = {"uitkomst": u, "teelt": teelt, "ronde": geladen.rondes[teelt.ronde_id]}
        if plant and u.licht_uit and u.oogst and begin:
            b["ld"] = ((plant - begin).days / span * 100, (u.licht_uit - plant).days / span * 100)
            b["kd"] = ((u.licht_uit - begin).days / span * 100, (u.oogst - u.licht_uit).days / span * 100)
        balken.append(b)
    return templates.TemplateResponse(request, "kap.html", {
        "gebruiker": gebruiker, "kap": kap, "scenario": scenario_obj, "scenarios": scenarios, "balken": balken,
        "begin": begin, "eind": eind, "kappen": sorted(geladen.kappen),
    })  # fmt: skip


@router.get("/teelt/{teelt_id}")
def teelt_pagina(
    request: Request, teelt_id: int, gebruiker: Gebruiker = Depends(auth.vereis_lezen), db: Session = Depends(get_db)
):
    teelt = db.get(Teelt, teelt_id)
    if teelt is None:
        raise HTTPException(404, "Onbekende teelt.")
    ronde = db.get(Ronde, teelt.ronde_id)
    geladen, plan = bereken(db, ronde.scenario_id)
    u = plan.teelten[teelt.id]
    velden = []
    for naam in VELDEN:
        v = geladen.veldwaarden.get((teelt.id, naam))
        door = db.get(Gebruiker, v.gewijzigd_door) if v and v.gewijzigd_door else None
        velden.append({
            "naam": naam, "label": LABELS[naam], "voorstel": _veldtekst(naam, u.voorstel[naam]),
            "voorstel_bron": ("keten (oogst vorige teelt + wisseltijd)" if naam == "plantdatum" and u.voorstel[naam] and not (v and v.voorstel)
                              else "tabel uitval per oogstweek" if naam == "uitval" and u.voorstel[naam] and not (v and v.voorstel)
                              else f"LBE × lichtsom / doelgewicht, afgerond (onafgerond {getal(u.dichtheid_onafgerond, 1)}); LBE {(u.lbe.status if u.lbe else '') or 'onbekend'}"
                              if naam == "dichtheid" and u.voorstel[naam] is not None and not (v and v.voorstel)
                              else f"LBE × lichtsom / dichtheid (band {getal(u.gewicht_band[0], 1)}–{getal(u.gewicht_band[1], 1)} g); LBE {(u.lbe.status if u.lbe else '') or 'onbekend'}"
                              if naam == "gewicht" and u.voorstel[naam] is not None and not (v and v.voorstel)
                              else (v.bron if v and v.voorstel is not None else "")),
            "handmatig": _veldtekst(naam, u.effectief[naam]) if u.bron[naam] == "handmatig" else "",
            "effectief": _veldtekst(naam, u.effectief[naam]), "bron": u.bron[naam],
            "wijziging": v, "door": door.gebruikersnaam if door else None,
        })  # fmt: skip
    bewijs = db.scalar(
        select(Werkboekregel).where(Werkboekregel.teelt_id == teelt.id).order_by(Werkboekregel.id.desc())
    )
    batch = db.get(Importbatch, bewijs.importbatch_id) if bewijs else None
    return templates.TemplateResponse(request, "teelt.html", {
        "gebruiker": gebruiker, "teelt": teelt, "ronde": ronde, "u": u, "velden": velden, "bewijs": bewijs, "batch": batch,
        "kan_schrijven": gebruiker.rol in ("beheerder", "medewerker"),
    })  # fmt: skip


@router.post("/teelt/{teelt_id}/veld/{veld}")
def teelt_veld(request: Request, teelt_id: int, veld: str, waarde: str = Form("", max_length=50), reden: str = Form("", max_length=2000),
               actie: str = Form("opslaan"), gebruiker: Gebruiker = Depends(auth.vereis_schrijven), db: Session = Depends(get_db)):  # fmt: skip
    teelt = db.get(Teelt, teelt_id)
    if teelt is None or veld not in VELDEN:
        raise HTTPException(404, "Onbekende teelt of veld.")
    ronde = db.get(Ronde, teelt.ronde_id)
    _, plan = bereken(db, ronde.scenario_id)
    u = plan.teelten[teelt.id]
    try:
        nieuw = (
            None
            if actie == "voorstel"
            else lees_invoer(veld, waarde, u.effectief["plantdatum"] or u.voorstel["plantdatum"] or date.today())
        )
        zet_handmatig(db, gebruiker, teelt, veld, nieuw, reden)
        db.flush()
        if instellingen.laad(db)["keten.overlap"] == "blokkeren":
            melding = controleer_overlap(db, ronde.scenario_id, teelt.id)
            if melding:
                raise Ongeldig(melding + " Opslaan is geblokkeerd (instelling Gedrag bij overlap).")
        db.commit()
    except (Ongeldig, ValueError) as fout:
        db.rollback()
        raise HTTPException(422, str(fout)) from fout
    return RedirectResponse(f"/teelt/{teelt.id}", status_code=303)
