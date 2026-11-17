"""Fase 5: scenario's (kopiëren, vaststellen, vergelijken), genereren, zoeken, jaaroverzicht en afzetmomenten."""

from __future__ import annotations

from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import auth, instellingen, plannen, planning
from ..bronnen import feestdagen
from ..db.basis import get_db
from ..db.modellen import Gebruiker, Ronde, Scenario, Teelt, Zoekrun
from ..kern import afzet as kern_afzet
from ..kern.kalender import weergave
from .sjablonen import templates

router = APIRouter()


def _terug(pad: str, melding: str) -> RedirectResponse:
    return RedirectResponse(pad + ("&" if "?" in pad else "?") + "melding=" + quote(melding), status_code=303)


def _jaar(jaar: int | None) -> int:
    return jaar or date.today().isocalendar().year


# --- Scenario's ----------------------------------------------------------------------------------------------------
@router.get("/scenarios")
def scenario_pagina(request: Request, melding: str | None = None, gebruiker: Gebruiker = Depends(auth.vereis_lezen),
                    db: Session = Depends(get_db)):  # fmt: skip
    scenarios = db.scalars(select(Scenario).order_by(Scenario.id)).all()
    tellingen = dict(db.execute(select(Ronde.scenario_id, func.count(Teelt.id)).join(Teelt, Teelt.ronde_id == Ronde.id)
                                .group_by(Ronde.scenario_id)).all())  # fmt: skip
    runs = db.scalars(select(Zoekrun).order_by(Zoekrun.id.desc()).limit(10)).all()
    snap = instellingen.laad(db)
    return templates.TemplateResponse(request, "scenarios.html", {
        "gebruiker": gebruiker, "scenarios": scenarios, "tellingen": tellingen, "melding": melding, "runs": runs,
        "jaar": _jaar(None), "kan_schrijven": gebruiker.rol in ("beheerder", "medewerker"),
        "rasverdeling": snap["genereren.rasverdeling"], "gewichten": {n: snap[f"zoeken.gewicht_{n}"] for n in ("afzet", "gelijkmatig", "doelgewicht", "energie")},
    })  # fmt: skip


@router.post("/scenarios/{scenario_id}/kopie")
def scenario_kopie(scenario_id: int, naam: str = Form(..., max_length=100), gebruiker: Gebruiker = Depends(auth.vereis_schrijven),
                   db: Session = Depends(get_db)):  # fmt: skip
    try:
        kopie = plannen.kopieer(db, gebruiker, scenario_id, naam)
        db.commit()
    except plannen.Fout as fout:
        db.rollback()
        return _terug("/scenarios", f"Niet gekopieerd: {fout}")
    return _terug("/scenarios", f"Kopie '{kopie.naam}' gemaakt (concept).")


@router.post("/scenarios/{scenario_id}/vaststellen")
def scenario_vaststellen(
    scenario_id: int, gebruiker: Gebruiker = Depends(auth.vereis_beheer), db: Session = Depends(get_db)
):
    try:
        scenario = plannen.stel_vast(db, gebruiker, scenario_id)
        db.commit()
    except plannen.Fout as fout:
        db.rollback()
        return _terug("/scenarios", f"Niet vastgesteld: {fout}")
    return _terug("/scenarios", f"'{scenario.naam}' is nu het vastgestelde plan.")


@router.post("/scenarios/{scenario_id}/genereren")
def scenario_genereren(scenario_id: int, jaar: str = Form(""), rondes: str = Form(""), start: str = Form(""),
                       gebruiker: Gebruiker = Depends(auth.vereis_schrijven), db: Session = Depends(get_db)):  # fmt: skip
    try:
        j = int(jaar) if jaar.strip() else None
        r = int(rondes) if rondes.strip() else None
        s = date.fromisoformat(start) if start.strip() else None
    except ValueError as fout:
        raise HTTPException(422, "Ongeldig jaar, aantal rondes of startdatum (JJJJ-MM-DD).") from fout
    try:
        uitkomst = plannen.genereer(db, gebruiker, scenario_id, j, r, s)
        db.commit()
    except plannen.Fout as fout:
        db.rollback()
        return _terug("/scenarios", f"Niet gegenereerd: {fout}")
    tekst = f"{uitkomst['teelten']} teelt(en) in {uitkomst['rondes']} ronde(s) gegenereerd als voorstel."
    if uitkomst["meldingen"]:
        tekst += " " + " ".join(uitkomst["meldingen"][:5]) + (" …" if len(uitkomst["meldingen"]) > 5 else "")
    return _terug(f"/jaar?scenario={scenario_id}&jaar={j or _jaar(None)}", tekst)


@router.get("/scenarios/vergelijk")
def scenario_vergelijk(request: Request, a: int | None = None, b: int | None = None, jaar: int | None = None,
                       gebruiker: Gebruiker = Depends(auth.vereis_lezen), db: Session = Depends(get_db)):  # fmt: skip
    scenarios = db.scalars(select(Scenario).order_by(Scenario.id)).all()
    uitkomst = None
    if a and b:
        if db.get(Scenario, a) is None or db.get(Scenario, b) is None:
            raise HTTPException(404, "Onbekend scenario.")
        uitkomst = plannen.vergelijk(db, a, b, _jaar(jaar))
    return templates.TemplateResponse(request, "scenario_vergelijk.html", {
        "gebruiker": gebruiker, "scenarios": scenarios, "a": a, "b": b, "jaar": _jaar(jaar), "u": uitkomst,
    })  # fmt: skip


# --- Zoeken --------------------------------------------------------------------------------------------------------
@router.post("/scenarios/{scenario_id}/zoeken")
def zoeken_starten(scenario_id: int, jaar: int = Form(...), gebruiker: Gebruiker = Depends(auth.vereis_schrijven),
                   db: Session = Depends(get_db)):  # fmt: skip
    try:
        run = plannen.start_zoeken(db, gebruiker, scenario_id, jaar)
    except plannen.Fout as fout:
        raise HTTPException(404, str(fout)) from fout
    return _terug(f"/zoeken/{run.id}", "Zoeken loopt op de achtergrond; ververs deze pagina. Er wordt niets toegepast.")


def _run(db: Session, run_id: int) -> Zoekrun:
    run = db.get(Zoekrun, run_id)
    if run is None:
        raise HTTPException(404, "Onbekende zoekactie.")
    return run


@router.get("/zoeken/{run_id}")
def zoeken_pagina(request: Request, run_id: int, melding: str | None = None,
                  gebruiker: Gebruiker = Depends(auth.vereis_lezen), db: Session = Depends(get_db)):  # fmt: skip
    run = _run(db, run_id)
    return templates.TemplateResponse(request, "zoeken.html", {
        "gebruiker": gebruiker, "run": run, "u": run.uitkomst or {}, "scenario": db.get(Scenario, run.scenario_id),
        "melding": melding, "kan_schrijven": gebruiker.rol in ("beheerder", "medewerker"), "toegepast": set(run.toegepast or []),
    })  # fmt: skip


@router.post("/zoeken/{run_id}/toepassen/{nummer}")
def zoeken_toepassen(run_id: int, nummer: int, gebruiker: Gebruiker = Depends(auth.vereis_schrijven),
                     db: Session = Depends(get_db)):  # fmt: skip
    run = _run(db, run_id)
    try:
        melding = plannen.pas_toe(db, gebruiker, run, nummer - 1)
        db.commit()
    except plannen.Fout as fout:
        db.rollback()
        melding = f"Niet toegepast: {fout}"
    return _terug(f"/zoeken/{run_id}", melding)


@router.post("/zoeken/{run_id}/alles")
def zoeken_alles(run_id: int, gebruiker: Gebruiker = Depends(auth.vereis_schrijven), db: Session = Depends(get_db)):
    run = _run(db, run_id)
    gelukt, niet = 0, []
    for nummer in range(len((run.uitkomst or {}).get("voorstellen") or [])):
        if nummer in (run.toegepast or []):
            continue
        try:
            plannen.pas_toe(db, gebruiker, run, nummer)
            db.commit()
            gelukt += 1
        except plannen.Fout as fout:
            db.rollback()
            run = _run(db, run_id)
            niet.append(f"{nummer + 1}: {fout}")
    return _terug(
        f"/zoeken/{run_id}", f"{gelukt} voorstel(len) toegepast." + (f" Niet: {'; '.join(niet)}" if niet else "")
    )


# --- Jaaroverzicht en afzet ----------------------------------------------------------------------------------------
@router.get("/jaar")
def jaar_pagina(request: Request, scenario: int | None = None, jaar: int | None = None, melding: str | None = None,
                gebruiker: Gebruiker = Depends(auth.vereis_lezen), db: Session = Depends(get_db)):  # fmt: skip
    scenario_obj = db.get(Scenario, scenario) if scenario else planning.vastgesteld_of_eerste(db)
    if scenario_obj is None:
        return templates.TemplateResponse(request, "leeg.html", {"gebruiker": gebruiker})
    overzicht = plannen.jaaroverzicht(db, scenario_obj.id, _jaar(jaar))
    return templates.TemplateResponse(request, "jaar.html", {
        "gebruiker": gebruiker, "o": overzicht, "jaar": _jaar(jaar), "scenario": scenario_obj, "melding": melding,
        "scenarios": db.scalars(select(Scenario).order_by(Scenario.id)).all(),
    })  # fmt: skip


@router.get("/afzet")
def afzet_pagina(request: Request, jaar: int | None = None, oogst: str | None = None, ld: str | None = None,
                 rt: str | None = None, verl: str | None = None, gebruiker: Gebruiker = Depends(auth.vereis_lezen),
                 db: Session = Depends(get_db)):  # fmt: skip
    j = _jaar(jaar)
    jaren = [j - 1, j, j + 1]
    md, meldingen = plannen.momenten(db, jaren)
    snap = instellingen.laad(db)
    terug = None
    if oogst:
        try:
            terug = kern_afzet.terugrekenen(date.fromisoformat(oogst), int(rt), int(verl or 0), int(ld))
        except (TypeError, ValueError) as fout:
            raise HTTPException(422, "Vul een oogstdatum (JJJJ-MM-DD) en hele dagen in.") from fout
    return templates.TemplateResponse(request, "afzet.html", {
        "gebruiker": gebruiker, "jaar": j, "momenten": md, "meldingen": meldingen, "terug": terug,
        "feestdagen": feestdagen.officiele_feestdagen(snap["afzet.landen"] or [], [j]),
        "onbekende_landen": feestdagen.onbekende_landen(snap["afzet.landen"] or []),
        "v": {"oogst": oogst or "", "ld": ld or "", "rt": rt or "", "verl": verl or ""}, "weergave": weergave,
    })  # fmt: skip
