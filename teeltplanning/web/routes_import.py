"""Import van het programmawerkboek: uploaden, controlevoorbeeld met ISO-jaar per ronde, bevestigen, rapport."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth
from ..db.basis import get_db
from ..db.modellen import Gebruiker, Importbatch
from ..importers import dienst, vergelijking
from .sjablonen import templates

router = APIRouter()


@router.get("/import")
def import_pagina(request: Request, gebruiker: Gebruiker = Depends(auth.vereis_lezen), db: Session = Depends(get_db)):
    batches = db.scalars(select(Importbatch).order_by(Importbatch.id.desc()).limit(50)).all()
    return templates.TemplateResponse(request, "import.html", {"gebruiker": gebruiker, "batches": batches,
                                                               "kan_schrijven": gebruiker.rol in ("beheerder", "medewerker")})  # fmt: skip


@router.post("/import/upload")
async def upload(request: Request, bestand: UploadFile, gebruiker: Gebruiker = Depends(auth.vereis_schrijven),
                 db: Session = Depends(get_db)):  # fmt: skip
    inhoud = await bestand.read()
    try:
        batch = dienst.sla_upload_op(db, gebruiker, bestand.filename or "werkboek.xlsx", inhoud)
        dienst.maak_voorbeeld(db, batch)
    except ValueError as fout:
        db.rollback()
        raise HTTPException(422, f"Werkboek niet gelezen: {fout}") from fout
    db.commit()
    return RedirectResponse(f"/import/{batch.id}", status_code=303)


def _batch(db: Session, batch_id: int) -> Importbatch:
    batch = db.get(Importbatch, batch_id)
    if batch is None:
        raise HTTPException(404, "Onbekende import.")
    return batch


@router.get("/import/{batch_id}")
def batch_pagina(
    request: Request, batch_id: int, gebruiker: Gebruiker = Depends(auth.vereis_lezen), db: Session = Depends(get_db)
):
    batch = _batch(db, batch_id)
    return templates.TemplateResponse(request, "import_batch.html", {
        "gebruiker": gebruiker, "batch": batch, "voorbeeld": batch.voorbeeld or {},
        "kan_schrijven": gebruiker.rol in ("beheerder", "medewerker"),
    })  # fmt: skip


@router.post("/import/{batch_id}/jaren")
async def jaren(
    request: Request,
    batch_id: int,
    gebruiker: Gebruiker = Depends(auth.vereis_schrijven),
    db: Session = Depends(get_db),
):
    batch = _batch(db, batch_id)
    if batch.status != "voorbeeld":
        raise HTTPException(409, "Deze import is al bevestigd.")
    formulier = await request.form()
    keuze: dict[int, int | None] = {}
    for sleutel, waarde in formulier.items():
        if sleutel.startswith("jaar_"):
            tekst = str(waarde).strip()
            if tekst and not tekst.isdigit():
                raise HTTPException(422, f"Ongeldig jaar: {tekst}")
            keuze[int(sleutel[5:])] = int(tekst) if tekst else None
    try:
        dienst.zet_jaren(batch, keuze)
        dienst.maak_voorbeeld(db, batch)
    except ValueError as fout:
        db.rollback()
        raise HTTPException(422, str(fout)) from fout
    db.commit()
    return RedirectResponse(f"/import/{batch.id}", status_code=303)


@router.post("/import/{batch_id}/bevestig")
def bevestig(
    request: Request,
    batch_id: int,
    gebruiker: Gebruiker = Depends(auth.vereis_schrijven),
    db: Session = Depends(get_db),
):
    batch = _batch(db, batch_id)
    if db.bind.dialect.name == "sqlite":
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    try:
        dienst.bevestig(db, gebruiker, batch)
    except ValueError as fout:
        db.rollback()
        raise HTTPException(422, str(fout)) from fout
    db.commit()
    return RedirectResponse(f"/import/{batch.id}", status_code=303)


@router.get("/import/{batch_id}/vergelijking.csv")
def rapport_csv(batch_id: int, gebruiker: Gebruiker = Depends(auth.vereis_lezen), db: Session = Depends(get_db)):
    batch = _batch(db, batch_id)
    regels = ((batch.voorbeeld or {}).get("vergelijking") or {}).get("regels", [])
    return Response(vergelijking.als_csv(regels), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="vergelijking-import-{batch.id}.csv"'})  # fmt: skip
