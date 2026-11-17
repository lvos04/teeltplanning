"""Fase 6: Beheer → Koppelingen: Bedrijfskoppeling (aanbieden: schakelaar en koppelsleutels) en koppelbronnen
(afnemen: Gewichtsverwachting)."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import auth, instellingen, koppeling
from ..bronnen import koppelbronnen
from ..db.basis import get_db
from ..db.modellen import Gebruiker, Koppelbron, KoppelItem, Koppelsleutel
from .sjablonen import templates

router = APIRouter(prefix="/beheer/koppelingen")


def _terug(melding: str) -> RedirectResponse:
    return RedirectResponse("/beheer/koppelingen?melding=" + quote(melding), status_code=303)


def _pagina(
    request: Request, db: Session, gebruiker: Gebruiker, melding: str | None = None, nieuwe_sleutel: dict | None = None
):
    snap = instellingen.laad(db)
    sleutels = [
        koppeling.sleutel_dict(db, r) for r in db.scalars(select(Koppelsleutel).order_by(Koppelsleutel.id.desc()))
    ]
    bronnen = db.scalars(select(Koppelbron).order_by(Koppelbron.id)).all()
    aantallen = {(b, d): n for b, d, n in db.execute(select(KoppelItem.bron_id, KoppelItem.dataset, func.count())
                                                     .group_by(KoppelItem.bron_id, KoppelItem.dataset))}  # fmt: skip
    koppelingen = koppelbronnen.koppel(db) if bronnen else {"gekoppeld": [], "niet_gekoppeld": [], "tolerantie": None}
    return templates.TemplateResponse(request, "beheer/koppelingen.html", {
        "gebruiker": gebruiker, "melding": melding, "nieuwe_sleutel": nieuwe_sleutel, "sleutels": sleutels,
        "ingeschakeld": snap["koppeling.ingeschakeld"], "bronnen_aan": snap["koppeling.bronnen_aan"], "bronnen": bronnen,
        "aantallen": aantallen, "datasets": koppelbronnen.DATASETS, "koppelingen": koppelingen,
        "gedeeld": koppeling.gedeelde_datasets(db), "apps": koppelbronnen.SCHAKELAAR,
        "uitleg_uit": koppelbronnen.uitleg_uit, "per_minuut": snap["koppeling.verzoeken_per_minuut"],
    })  # fmt: skip


@router.get("")
def koppelingen(request: Request, melding: str | None = None, gebruiker: Gebruiker = Depends(auth.vereis_beheer),
                db: Session = Depends(get_db)):  # fmt: skip
    return _pagina(request, db, gebruiker, melding)


@router.post("/schakelaar")
def schakelaar(welke: str = Form(...), aan: str = Form(...), gebruiker: Gebruiker = Depends(auth.vereis_beheer),
               db: Session = Depends(get_db)):  # fmt: skip
    sleutel = {"aanbieden": "koppeling.ingeschakeld", "afnemen": "koppeling.bronnen_aan"}.get(welke)
    if sleutel is None:
        raise HTTPException(404)
    instellingen.wijzig(db, gebruiker, sleutel, aan == "1", "via Beheer → Koppelingen")
    db.commit()
    tekst = {"aanbieden": "Bedrijfskoppeling", "afnemen": "Koppelbronnen ophalen"}[welke]
    return _terug(f"{tekst} staat nu {'aan' if aan == '1' else 'uit'}.")


@router.post("/sleutels")
def sleutel_aanmaken(request: Request, naam: str = Form(..., max_length=100), gebruiker: Gebruiker = Depends(auth.vereis_beheer),
                     db: Session = Depends(get_db)):  # fmt: skip
    rij, sleutel = koppeling.maak_sleutel(db, naam, gebruiker)
    # De sleutel staat alleen in dit ene antwoord (niet in een adres, niet in het log) en is daarna niet meer op te vragen.
    return _pagina(request, db, gebruiker, nieuwe_sleutel={"naam": rij.naam, "sleutel": sleutel})


@router.post("/sleutels/{sleutel_id}/intrekken")
def sleutel_intrekken(
    sleutel_id: int, gebruiker: Gebruiker = Depends(auth.vereis_beheer), db: Session = Depends(get_db)
):
    rij = koppeling.trek_in(db, sleutel_id, gebruiker)
    return _terug(f"Sleutel {rij.voorvoegsel}… ({rij.naam}) is ingetrokken.")


@router.post("/bronnen")
def bron_toevoegen(naam: str = Form(..., max_length=100), url: str = Form(..., max_length=300), sleutel: str = Form(..., max_length=300),
                   app: str = Form("gewichtsverwachting"), gebruiker: Gebruiker = Depends(auth.vereis_beheer),
                   db: Session = Depends(get_db)):  # fmt: skip
    try:
        bron = koppelbronnen.maak(db, gebruiker, naam, url, sleutel, app)
        db.commit()
    except koppelbronnen.Fout as fout:
        db.rollback()
        return _terug(f"Niet toegevoegd: {fout}")
    return _terug(f"Koppelbron {bron.naam} toegevoegd (sleutel versleuteld opgeslagen).")


@router.post("/bronnen/{bron_id}")
def bron_wijzigen(bron_id: int, url: str = Form("", max_length=300), sleutel: str = Form("", max_length=300), actief: str = Form(""),
                  gebruiker: Gebruiker = Depends(auth.vereis_beheer), db: Session = Depends(get_db)):  # fmt: skip
    bron = db.get(Koppelbron, bron_id)
    if bron is None:
        raise HTTPException(404)
    try:
        koppelbronnen.wijzig(db, gebruiker, bron, url, sleutel, {"1": True, "0": False}.get(actief))
        db.commit()
    except koppelbronnen.Fout as fout:
        db.rollback()
        return _terug(f"Niet gewijzigd: {fout}")
    return _terug(f"Koppelbron {bron.naam} bijgewerkt.")


@router.post("/bronnen/{bron_id}/ophalen")
def bron_ophalen(bron_id: int, gebruiker: Gebruiker = Depends(auth.vereis_beheer), db: Session = Depends(get_db)):
    bron = db.get(Koppelbron, bron_id)
    if bron is None:
        raise HTTPException(404)
    gelukt, melding = koppelbronnen.haal_op(db, bron)
    auth.audit(db, gebruiker, "koppelbron_opgehaald", f"koppelbron:{bron_id}", {"gelukt": gelukt})
    db.commit()
    return _terug(f"{bron.naam}: {melding}")
