"""Eerste start, aanmelden (met optionele MFA) en afmelden."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import auth, instellingen
from ..db.basis import get_db
from ..db.modellen import Gebruiker
from ..mfa import controleer_code
from .sjablonen import templates

router = APIRouter()


def _geen_gebruikers(db: Session) -> bool:
    return db.scalar(select(func.count()).select_from(Gebruiker)) == 0


@router.get("/")
def start(request: Request, db: Session = Depends(get_db)):
    if _geen_gebruikers(db):
        return RedirectResponse("/eerste-start", status_code=303)
    if not auth.sessie_gebruiker(request, db):
        return RedirectResponse("/aanmelden", status_code=303)
    return RedirectResponse("/ronde", status_code=303)


@router.get("/eerste-start")
def eerste_start_pagina(request: Request, db: Session = Depends(get_db)):
    if not _geen_gebruikers(db):
        return RedirectResponse("/aanmelden", status_code=303)
    return templates.TemplateResponse(request, "eerste_start.html", {})


@router.post("/eerste-start")
def eerste_start(request: Request, gebruikersnaam: str = Form(..., max_length=80), wachtwoord: str = Form(..., max_length=256),
                 db: Session = Depends(get_db)):  # fmt: skip
    auth.controleer_oorsprong(request, instellingen.laad(db)["beveiliging.strikte_oorsprong"])
    if db.bind.dialect.name == "sqlite":
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    if not _geen_gebruikers(db):
        raise HTTPException(409, "De installatie is al voltooid.")
    if not gebruikersnaam.strip():
        raise HTTPException(422, "Kies een gebruikersnaam.")
    auth.controleer_nieuw_wachtwoord(wachtwoord)
    gebruiker = Gebruiker(
        gebruikersnaam=gebruikersnaam.strip(), wachtwoord_hash=auth.hash_wachtwoord(wachtwoord), rol="beheerder"
    )
    db.add(gebruiker)
    db.flush()
    antwoord = RedirectResponse("/ronde", status_code=303)
    auth.maak_sessie(db, antwoord, gebruiker, request)
    auth.audit(db, gebruiker, "eerste_beheerder", f"gebruiker:{gebruiker.id}")
    db.commit()
    return antwoord


@router.get("/aanmelden")
def aanmelden_pagina(request: Request, db: Session = Depends(get_db)):
    if _geen_gebruikers(db):
        return RedirectResponse("/eerste-start", status_code=303)
    return templates.TemplateResponse(request, "aanmelden.html", {})


@router.post("/aanmelden")
def aanmelden(request: Request, gebruikersnaam: str = Form(..., max_length=80), wachtwoord: str = Form(..., max_length=256),
              db: Session = Depends(get_db)):  # fmt: skip
    auth.controleer_oorsprong(request, instellingen.laad(db)["beveiliging.strikte_oorsprong"])
    if db.bind.dialect.name == "sqlite":
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    snap, ip_sleutel, gebruiker_sleutel = auth.limietsleutels(request, db, gebruikersnaam)
    eerdere = auth.controleer_limieten(db, snap, ip_sleutel, gebruiker_sleutel)
    gebruiker = db.scalar(
        select(Gebruiker).where(func.lower(Gebruiker.gebruikersnaam) == gebruikersnaam.strip().lower())
    )
    if not gebruiker or not gebruiker.actief or not auth.controleer_wachtwoord(wachtwoord, gebruiker.wachtwoord_hash):
        auth.registreer_fout(db, snap, [ip_sleutel, gebruiker_sleutel], eerdere, gebruikersnaam)
        return templates.TemplateResponse(
            request, "aanmelden.html", {"fout": "Ongeldige gebruikersnaam of wachtwoord."}, status_code=401
        )
    if gebruiker.mfa_aan:
        token = auth.begin_mfa_stap(db, gebruiker)
        auth.audit(db, gebruiker, "aanmelden_wachtwoord_ok_mfa_volgt", "aanmelden")
        db.commit()
        return templates.TemplateResponse(request, "aanmelden_mfa.html", {"stap": token})
    auth.wis_pogingen(db, [ip_sleutel, gebruiker_sleutel])
    antwoord = RedirectResponse("/ronde", status_code=303)
    auth.maak_sessie(db, antwoord, gebruiker, request)
    auth.audit(db, gebruiker, "aanmelden", "aanmelden")
    db.commit()
    return antwoord


@router.post("/aanmelden/mfa")
def aanmelden_mfa(request: Request, stap: str = Form(..., max_length=200), code: str = Form(..., max_length=10),
                  db: Session = Depends(get_db)):  # fmt: skip
    auth.controleer_oorsprong(request, instellingen.laad(db)["beveiliging.strikte_oorsprong"])
    if db.bind.dialect.name == "sqlite":
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    mfa_stap = auth.mfa_stap(db, stap)
    if mfa_stap is None:
        return templates.TemplateResponse(
            request, "aanmelden.html", {"fout": "Aanmeldcode verlopen; meld opnieuw aan."}, status_code=401
        )
    gebruiker = db.get(Gebruiker, mfa_stap.gebruiker_id)
    snap, ip_sleutel, gebruiker_sleutel = auth.limietsleutels(
        request, db, gebruiker.gebruikersnaam if gebruiker else ""
    )
    eerdere = auth.controleer_limieten(db, snap, ip_sleutel, gebruiker_sleutel)
    if not gebruiker or not gebruiker.actief or not gebruiker.mfa_aan or not controleer_code(gebruiker, code):
        mfa_stap.pogingen += 1
        auth.registreer_fout(
            db,
            snap,
            [ip_sleutel, gebruiker_sleutel],
            eerdere,
            gebruiker.gebruikersnaam if gebruiker else "",
            "aanmelden_mfa_mislukt",
        )
        return templates.TemplateResponse(
            request, "aanmelden_mfa.html", {"stap": stap, "fout": "Ongeldige verificatiecode."}, status_code=401
        )
    db.delete(mfa_stap)
    auth.wis_pogingen(db, [ip_sleutel, gebruiker_sleutel])
    antwoord = RedirectResponse("/ronde", status_code=303)
    auth.maak_sessie(db, antwoord, gebruiker, request)
    auth.audit(db, gebruiker, "aanmelden", "aanmelden", {"mfa": True})
    db.commit()
    return antwoord


@router.post("/afmelden")
def afmelden(request: Request, gebruiker: Gebruiker = Depends(auth.vereis_lezen), db: Session = Depends(get_db)):
    auth.beeindig_sessie(db, request)
    auth.audit(db, gebruiker, "afmelden", "aanmelden")
    db.commit()
    antwoord = RedirectResponse("/aanmelden", status_code=303)
    antwoord.delete_cookie(auth.COOKIE, path="/")
    return antwoord
