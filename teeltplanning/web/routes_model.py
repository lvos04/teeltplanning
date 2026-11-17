"""Fase 4: Beheer → Model (trainen als achtergrondtaak, backtestuitkomsten)."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth
from ..db.basis import get_db
from ..db.modellen import Gebruiker, Modelversie
from ..voorstel import dienst
from ..voorstel.model import DOELEN, EENHEID, TREDE_LABEL, TREDEN
from .sjablonen import templates

router = APIRouter()
DOEL_LABEL = {"ld_dagen": "LD-dagen", "verlenging": "verlenging", "reactietijd": "reactietijd", "gewicht": "gewicht"}
STATUS_LABEL = {"basis": "referentie", "gevalideerd": "gevalideerd", "indicatief": "indicatief",
                "onvoldoende": "onvoldoende gegevens", "uit": "staat uit"}  # fmt: skip


@router.get("/beheer/model")
def model_pagina(request: Request, versie: int | None = None, melding: str | None = None,
                 gebruiker: Gebruiker = Depends(auth.vereis_lezen), db: Session = Depends(get_db)):  # fmt: skip
    versies = db.scalars(select(Modelversie).order_by(Modelversie.id.desc()).limit(50)).all()
    actief = dienst.actief(db)
    gekozen = db.get(Modelversie, versie) if versie else (actief or (versies[0] if versies else None))
    tabellen = []
    if gekozen is not None and gekozen.artefact:
        backtest = gekozen.artefact["backtest"]
        for doel in DOELEN:
            r = backtest.get(doel, {})
            jaren = r.get("toetsjaren", [])
            regels = []
            for trede in TREDEN[doel]:
                t = r.get("treden", {}).get(trede)
                if t is None:
                    regels.append({"trede": TREDE_LABEL[trede], "status": "uit", "jaren": {}, "n": 0})
                    continue
                regels.append({"trede": TREDE_LABEL[trede], "status": t["status"], "n": t["n"], "mae": t["mae"],
                               "mae_basis": t["mae_basis"], "verbetering": t.get("verbetering_pct"),
                               "jaren": t["jaren"], "reden": t.get("reden"), "residu": t.get("residu"),
                               "gekozen": trede == r.get("gekozen")})  # fmt: skip
            tabellen.append({"doel": DOEL_LABEL[doel], "eenheid": EENHEID[doel], "jaren": [str(j) for j in jaren],
                             "regels": regels, "gekozen": TREDE_LABEL.get(r.get("gekozen"), "")})  # fmt: skip
    return templates.TemplateResponse(request, "beheer/model.html", {
        "gebruiker": gebruiker, "versies": versies, "gekozen": gekozen, "actief": actief, "tabellen": tabellen,
        "status_label": STATUS_LABEL, "melding": melding, "bezig": dienst.bezig(db),
    })  # fmt: skip


@router.post("/beheer/model/trainen")
def model_trainen(gebruiker: Gebruiker = Depends(auth.vereis_beheer), db: Session = Depends(get_db)):
    """Start trainen en toetsen op de achtergrond en keer meteen terug (de webapp blijft bruikbaar)."""
    versie = dienst.start_training(db, gebruiker)
    return RedirectResponse(f"/beheer/model?versie={versie.id}&melding=" + quote(
        f"Training {versie.versie} loopt op de achtergrond; ververs deze pagina voor de uitkomst."), status_code=303)  # fmt: skip


@router.get("/beheer/model/{versie_id}/status")
def model_status(versie_id: int, gebruiker: Gebruiker = Depends(auth.vereis_lezen), db: Session = Depends(get_db)):
    versie = db.get(Modelversie, versie_id)
    if versie is None:
        raise HTTPException(404, "Onbekende modelversie.")
    return {"versie": versie.versie, "status": versie.status, "melding": versie.melding}
