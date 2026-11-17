"""Afdrukken (§9.1 Teeltkaart, §9.3): rondeblad en teeltkaart als pdf of Excel, en sjabloonbeheer."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth, instellingen, planning
from ..afdruk import dienst, pdf, sjabloon
from ..afdruk.kaart import kaartvelden
from ..db.basis import get_db
from ..db.modellen import Gebruiker, Scenario, Sjabloon
from ..xlsxpakket import NieuwBlad, Pakket, PakketFout, bouw_kopie, kolom_verschuif
from .sjablonen import templates

router = APIRouter()
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _scenario(db: Session, scenario_id: int | None) -> Scenario:
    scenario = db.get(Scenario, scenario_id) if scenario_id else planning.vastgesteld_of_eerste(db)
    if scenario is None:
        raise HTTPException(404, "Er is nog geen plan. Importeer eerst een werkboek.")
    return scenario


def _bestand(inhoud: bytes, naam: str, soort: str, inline: bool) -> Response:
    media = "application/pdf" if soort == "pdf" else XLSX
    wijze = "inline" if inline else "attachment"
    veilig = naam.encode("ascii", "replace").decode().replace('"', "")
    return Response(inhoud, media_type=media, headers={
        "Content-Disposition": f"{wijze}; filename=\"{veilig}.{soort}\"; filename*=UTF-8''{quote(naam)}.{soort}"})  # fmt: skip


@router.get("/afdruk")
def afdruk_pagina(request: Request, scenario: int | None = None, ronde: int | None = None, kap: int | None = None,
                  gebruiker: Gebruiker = Depends(auth.vereis_lezen), db: Session = Depends(get_db)):  # fmt: skip
    scenarios = db.scalars(select(Scenario).order_by(Scenario.id)).all()
    ctx = {"gebruiker": gebruiker, "scenarios": scenarios, "sjabloon": sjabloon.actief(db), "kaart": None,
           "standaard": instellingen.laad(db)["afdrukken.standaard"]}  # fmt: skip
    if not scenarios:
        return templates.TemplateResponse(request, "afdruk.html", ctx | {"scenario": None, "rondes": [], "kappen": []})
    scenario_obj = _scenario(db, scenario)
    geladen, plan = planning.bereken(db, scenario_obj.id)
    rondes = sorted(r.nummer for r in geladen.rondes.values())
    ronde = ronde if ronde in rondes else (rondes[0] if rondes else None)
    kappen = []
    if ronde is not None:
        geladen, kaarten, teelten = dienst.kaarten_van_ronde(db, scenario_obj.id, ronde)
        kappen = sorted(teelten)
        if kap in kaarten:
            ctx["kaart"] = kaarten[kap]
            ctx["velden"] = kaartvelden(kaarten[kap], kap, ronde, teelten[kap].opmerking)
    return templates.TemplateResponse(request, "afdruk.html", ctx | {
        "scenario": scenario_obj, "rondes": rondes, "ronde": ronde, "kappen": kappen, "kap": kap,
    })  # fmt: skip


def _lever(db: Session, request: Request, gebruiker: Gebruiker, afdruk: dienst.Afdruk, soort: str):
    if soort == "xlsx":
        return _bestand(afdruk.xlsx, afdruk.naam, "xlsx", inline=False)
    try:
        inhoud = dienst.naar_pdf(db, afdruk)
    except pdf.ConversieFout as fout:
        return templates.TemplateResponse(request, "afdruk_fout.html", {
            "gebruiker": gebruiker, "melding": str(fout), "excel": str(request.url).replace(".pdf?", ".xlsx?", 1)
            if ".pdf?" in str(request.url) else str(request.url).replace(".pdf", ".xlsx"), "naam": afdruk.naam,
        }, status_code=200)  # fmt: skip
    return _bestand(inhoud, afdruk.naam, "pdf", inline=True)


@router.get("/afdruk/rondeblad.{soort}")
def rondeblad(request: Request, soort: str, ronde: int, scenario: int | None = None,
              gebruiker: Gebruiker = Depends(auth.vereis_lezen), db: Session = Depends(get_db)):  # fmt: skip
    if soort not in ("pdf", "xlsx"):
        raise HTTPException(404)
    try:
        afdruk = dienst.maak_rondeblad(db, _scenario(db, scenario).id, ronde)
    except (dienst.AfdrukFout, PakketFout) as fout:
        raise HTTPException(422, str(fout)) from fout
    return _lever(db, request, gebruiker, afdruk, soort)


@router.get("/afdruk/teeltkaart.{soort}")
def teeltkaart(request: Request, soort: str, ronde: int, kap: int | None = None, scenario: int | None = None,
               gebruiker: Gebruiker = Depends(auth.vereis_lezen), db: Session = Depends(get_db)):  # fmt: skip
    """De kaart is op te vragen met alleen kap en ronde; zonder kap: alle kappen van de ronde in één bestand."""
    if soort not in ("pdf", "xlsx"):
        raise HTTPException(404)
    try:
        afdruk = dienst.maak_teeltkaarten(db, _scenario(db, scenario).id, ronde, kap)
    except (dienst.AfdrukFout, PakketFout) as fout:
        raise HTTPException(422, str(fout)) from fout
    return _lever(db, request, gebruiker, afdruk, soort)


# --- Beheer → Sjabloon ---------------------------------------------------------------------------------------
@router.get("/beheer/sjabloon")
def sjabloon_pagina(request: Request, melding: str | None = None, gebruiker: Gebruiker = Depends(auth.vereis_beheer),
                    db: Session = Depends(get_db)):  # fmt: skip
    rij = sjabloon.actief(db)
    snap = instellingen.laad(db)
    lettertypen = sjabloon.lettertype_status((rij.controle or {}).get("lettertypen", []), snap) if rij else []
    eerdere = db.scalars(select(Sjabloon).order_by(Sjabloon.id.desc()).limit(10)).all()
    return templates.TemplateResponse(request, "beheer/sjabloon.html", {
        "gebruiker": gebruiker, "sjabloon": rij, "controle": (rij.controle if rij else None) or {},
        "lettertypen": lettertypen, "bestanden": sjabloon.lettertypebestanden(), "eerdere": eerdere,
        "melding": melding, "soffice": pdf.soffice() is not None,
    })  # fmt: skip


def _terug(melding: str) -> RedirectResponse:
    return RedirectResponse(f"/beheer/sjabloon?melding={quote(melding)}", status_code=303)


@router.post("/beheer/sjabloon/upload")
async def sjabloon_upload(bestand: UploadFile, gebruiker: Gebruiker = Depends(auth.vereis_beheer),
                          db: Session = Depends(get_db)):  # fmt: skip
    try:
        rij = sjabloon.sla_op(db, gebruiker, bestand.filename or "sjabloon.xlsx", await bestand.read())
    except ValueError as fout:
        db.rollback()
        return _terug(str(fout))
    db.commit()
    return _terug(f"Sjabloon {rij.bestandsnaam} opgeslagen en gecontroleerd.")


@router.post("/beheer/sjabloon/controle")
def sjabloon_controle(gebruiker: Gebruiker = Depends(auth.vereis_beheer), db: Session = Depends(get_db)):
    rij = sjabloon.actief(db)
    if rij is None:
        return _terug("Er is nog geen sjabloon.")
    sjabloon.ververs_controle(db, rij)
    db.commit()
    return _terug("Controle opnieuw uitgevoerd met de huidige instellingen.")


@router.post("/beheer/sjabloon/lettertype")
async def lettertype_upload(bestand: UploadFile, gebruiker: Gebruiker = Depends(auth.vereis_beheer),
                            db: Session = Depends(get_db)):  # fmt: skip
    try:
        doel = sjabloon.sla_lettertype_op(db, gebruiker, bestand.filename or "", await bestand.read())
    except ValueError as fout:
        db.rollback()
        return _terug(str(fout))
    db.commit()
    return _terug(f"Lettertype {doel.name} opgeslagen in /data/lettertypen/.")


@router.get("/beheer/sjabloon/proefkaart.{soort}")
def proefkaart(request: Request, soort: str, gebruiker: Gebruiker = Depends(auth.vereis_beheer),
               db: Session = Depends(get_db)):  # fmt: skip
    """Controleafdruk: elke toegewezen cel krijgt de naam van zijn veld, zodat te zien is waar wat landt."""
    if soort not in ("pdf", "xlsx"):
        raise HTTPException(404)
    rij = sjabloon.actief(db)
    if rij is None:
        raise HTTPException(422, "Er is nog geen sjabloon.")
    snap = instellingen.laad(db)
    waarden: dict[str, object] = {}
    for positie in range(snap["afdrukken.teeltkaart_kaarten_per_blad"]):
        verschuiving = positie * snap["afdrukken.teeltkaart_tweede_kaart_kolommen"]
        for veld, cel in snap["afdrukken.teeltkaart_cellen"].items():
            waarden[kolom_verschuif(cel, verschuiving)] = f"«{veld}»"
        eerste = snap["afdrukken.teeltkaart_eerste_momentrij"]
        for onderdeel, kolom in snap["afdrukken.teeltkaart_momentkolommen"].items():
            for i in range(snap["teeltkaart.aantal_momenten"]):
                waarden[kolom_verschuif(f"{kolom}{eerste + i}", verschuiving)] = f"«{onderdeel} {i + 1}»"
    blad = NieuwBlad(snap["afdrukken.teeltkaart_blad"], "Proefkaart", waarden, snap["afdrukken.teeltkaart_bereik"],
                     snap["afdrukken.teeltkaart_richting"])  # fmt: skip
    try:
        xlsx, meldingen = bouw_kopie(Pakket.lees(sjabloon.pad(rij)), [blad], behoud_overige=False,
                                     papier=snap["afdrukken.papierformaat"], passend=snap["afdrukken.passend_op_een_pagina"],
                                     formules_bevriezen=True, volledig_herberekenen=False)  # fmt: skip
    except PakketFout as fout:
        raise HTTPException(422, str(fout)) from fout
    return _lever(db, request, gebruiker, dienst.Afdruk(xlsx, "Proefkaart veldnamen", 1, meldingen), soort)
