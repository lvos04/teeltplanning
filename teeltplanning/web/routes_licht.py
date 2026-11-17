"""Fase 3: import belichting, bronnen, LBE en dichtheid, energie en de scenariovergelijking."""

from __future__ import annotations

from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import auth, instellingen, planning
from ..bronnen import dienst as bronnen
from ..db.basis import get_db
from ..db.modellen import (
    BelichtingDag,
    BelichtingWeek,
    Gebruiker,
    Importbatch,
    Kap,
    Lampgroep,
    LbeIjkpunt,
    ParameterLbe,
    Scenario,
    TeeltschemaRegel,
    Weeknormaal,
    Weerdag,
)
from ..importers import belichting_dienst
from ..kern import energie as kern_energie
from ..kern.licht import Rekenaar
from ..voorstel import lbe as lbe_dienst
from .sjablonen import templates

router = APIRouter()


def _terug(pad: str, melding: str) -> RedirectResponse:
    return RedirectResponse(pad + ("&" if "?" in pad else "?") + "melding=" + quote(melding), status_code=303)


# --- Import belichting ---------------------------------------------------------------------------------------
@router.post("/import/belichting/upload")
async def belichting_upload(bestand: UploadFile, gebruiker: Gebruiker = Depends(auth.vereis_schrijven),
                            db: Session = Depends(get_db)):  # fmt: skip
    try:
        batch = belichting_dienst.sla_upload_op(
            db, gebruiker, bestand.filename or "belichting.xlsx", await bestand.read()
        )
    except ValueError as fout:
        db.rollback()
        raise HTTPException(422, f"Werkboek niet gelezen: {fout}") from fout
    db.commit()
    belichting_dienst.start_lezen(batch.id)
    return RedirectResponse(f"/import/belichting/{batch.id}", status_code=303)


def _batch(db: Session, batch_id: int) -> Importbatch:
    batch = db.get(Importbatch, batch_id)
    if batch is None or batch.soort != "belichting":
        raise HTTPException(404, "Onbekende import.")
    return batch


@router.get("/import/belichting/{batch_id}")
def belichting_pagina(request: Request, batch_id: int, melding: str | None = None,
                      gebruiker: Gebruiker = Depends(auth.vereis_lezen), db: Session = Depends(get_db)):  # fmt: skip
    batch = _batch(db, batch_id)
    snap = instellingen.laad(db)
    return templates.TemplateResponse(request, "import_belichting.html", {
        "gebruiker": gebruiker, "batch": batch, "v": batch.voorbeeld or {}, "keuzes": batch.keuzes or {},
        "kan_schrijven": gebruiker.rol in ("beheerder", "medewerker"), "melding": melding,
        "tijdstempel": snap["import.data_tijdstempel"],
    })  # fmt: skip


@router.post("/import/belichting/{batch_id}/keuzes")
async def belichting_keuzes(request: Request, batch_id: int, gebruiker: Gebruiker = Depends(auth.vereis_schrijven),
                            db: Session = Depends(get_db)):  # fmt: skip
    batch = _batch(db, batch_id)
    formulier = await request.form()
    keuzes: dict[str, int | None] = {}
    for sleutel in ("jaar_weekuren", "jaar_teeltschema"):
        tekst = str(formulier.get(sleutel, "")).strip()
        if tekst and not tekst.isdigit():
            raise HTTPException(422, f"Ongeldig jaar: {tekst}")
        keuzes[sleutel] = int(tekst) if tekst else None
    try:
        belichting_dienst.zet_keuzes(batch, keuzes)
    except ValueError as fout:
        raise HTTPException(422, str(fout)) from fout
    db.commit()
    return RedirectResponse(f"/import/belichting/{batch.id}", status_code=303)


@router.post("/import/belichting/{batch_id}/tijdstempel")
def belichting_tijdstempel(batch_id: int, waarde: str = Form(...), gebruiker: Gebruiker = Depends(auth.vereis_beheer),
                           db: Session = Depends(get_db)):  # fmt: skip
    batch = _batch(db, batch_id)
    try:
        instellingen.wijzig(db, gebruiker, "import.data_tijdstempel", waarde, f"bij import {batch.id}")
    except instellingen.OngeldigeWaarde as fout:
        raise HTTPException(422, str(fout)) from fout
    batch.status, batch.voorbeeld = "lezen", {"voortgang": 0}
    db.commit()
    belichting_dienst.start_lezen(batch.id)
    return RedirectResponse(f"/import/belichting/{batch.id}", status_code=303)


@router.post("/import/belichting/{batch_id}/opnieuw")
def belichting_opnieuw(
    batch_id: int, gebruiker: Gebruiker = Depends(auth.vereis_schrijven), db: Session = Depends(get_db)
):
    batch = _batch(db, batch_id)
    if batch.status == "bevestigd":
        raise HTTPException(409, "Deze import is al bevestigd.")
    batch.status, batch.voorbeeld = "lezen", {"voortgang": 0}
    db.commit()
    belichting_dienst.start_lezen(batch.id)
    return RedirectResponse(f"/import/belichting/{batch.id}", status_code=303)


@router.post("/import/belichting/{batch_id}/bevestig")
def belichting_bevestig(
    batch_id: int, gebruiker: Gebruiker = Depends(auth.vereis_schrijven), db: Session = Depends(get_db)
):
    batch = _batch(db, batch_id)
    if db.bind.dialect.name == "sqlite":
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    try:
        tellingen = belichting_dienst.bevestig(db, gebruiker, batch)
    except ValueError as fout:
        db.rollback()
        raise HTTPException(422, str(fout)) from fout
    db.commit()
    melding = "Al eerder bevestigd." if tellingen.get("al_bevestigd") else (
        f"Opgeslagen: {tellingen['weekuren']} weekuren, {tellingen['ijkpunten']} ijkpunten, {tellingen['teeltschema']} "
        f"teeltschemaregels, {tellingen['belichtingsdagen']} belichtingsdagen."
        + (f" Overgeslagen: {'; '.join(tellingen['overgeslagen'])}." if tellingen["overgeslagen"] else ""))  # fmt: skip
    return _terug(f"/import/belichting/{batch.id}", melding)


# --- Beheer → Bronnen ----------------------------------------------------------------------------------------
@router.get("/beheer/bronnen")
def bronnen_pagina(request: Request, melding: str | None = None, gebruiker: Gebruiker = Depends(auth.vereis_beheer),
                   db: Session = Depends(get_db)):  # fmt: skip
    weer = db.execute(select(Weerdag.soort, Weerdag.bron, func.count(), func.min(Weerdag.datum), func.max(Weerdag.datum))
                      .group_by(Weerdag.soort, Weerdag.bron)).all()  # fmt: skip
    normalen = db.scalars(select(Weeknormaal).order_by(Weeknormaal.week)).all()
    return templates.TemplateResponse(request, "beheer/bronnen.html", {
        "gebruiker": gebruiker, "bronnen": bronnen.statussen(db), "weer": weer, "normalen": normalen, "melding": melding,
    })  # fmt: skip


@router.post("/beheer/bronnen/{bron}/ophalen")
def bron_ophalen(bron: str, gebruiker: Gebruiker = Depends(auth.vereis_beheer), db: Session = Depends(get_db)):
    if bron not in bronnen.BRONNEN:
        raise HTTPException(404)
    gelukt, melding = bronnen.ververs(db, bron)
    auth.audit(db, gebruiker, "bron_opgehaald", f"bron:{bron}", {"gelukt": gelukt})
    db.commit()
    return _terug("/beheer/bronnen", f"{bronnen.BRONNEN[bron][1]}: {melding}")


@router.post("/beheer/bronnen/{bron}/sleutel")
def bron_sleutel(bron: str, sleutel: str = Form("", max_length=2000), gebruiker: Gebruiker = Depends(auth.vereis_beheer),
                 db: Session = Depends(get_db)):  # fmt: skip
    if bron not in bronnen.BRONNEN:
        raise HTTPException(404)
    bronnen.zet_geheim(db, gebruiker, bron, sleutel)
    db.commit()
    return _terug("/beheer/bronnen", "Sleutel opgeslagen (versleuteld)." if sleutel.strip() else "Sleutel verwijderd.")


# --- Beheer → LBE en dichtheid -------------------------------------------------------------------------------
@router.get("/beheer/lbe")
def lbe_pagina(request: Request, scenario: int | None = None, melding: str | None = None,
               gebruiker: Gebruiker = Depends(auth.vereis_lezen), db: Session = Depends(get_db)):  # fmt: skip
    scenario_obj = db.get(Scenario, scenario) if scenario else planning.vastgesteld_of_eerste(db)
    ijk = lbe_dienst.ijkpunten(db, scenario_obj.id if scenario_obj else None)
    gerealiseerd = lbe_dienst.gerealiseerde_lbe(db, scenario_obj.id) if scenario_obj else []
    return templates.TemplateResponse(request, "beheer/lbe.html", {
        "gebruiker": gebruiker, "scenario": scenario_obj, "ijk": ijk, "gerealiseerd": gerealiseerd,
        "cellen": db.scalar(select(func.count()).select_from(ParameterLbe)), "melding": melding,
        "reeks": instellingen.laad(db)["gewas.dichtheid_reeks"],
        "teeltschema": db.scalar(select(func.count()).select_from(TeeltschemaRegel)),
        "ijkpunten": db.scalar(select(func.count()).select_from(LbeIjkpunt)),
    })  # fmt: skip


@router.post("/beheer/lbe/kalibreren")
def lbe_kalibreren(gebruiker: Gebruiker = Depends(auth.vereis_beheer), db: Session = Depends(get_db)):
    scenario = planning.vastgesteld_of_eerste(db)
    if scenario is None:
        return _terug("/beheer/lbe", "Er is nog geen plan.")
    uitkomst = lbe_dienst.kalibreer(db, gebruiker, scenario.id)
    db.commit()
    return _terug("/beheer/lbe", f"{uitkomst['cellen']} LBE-cel(len) bijgewerkt uit {uitkomst['teelten']} teelt(en); "
                                 f"{uitkomst['gemengd_overgeslagen']} gemengde teelt(en) overgeslagen.")  # fmt: skip


@router.post("/beheer/lbe/dichtheden")
def lbe_dichtheden(gebruiker: Gebruiker = Depends(auth.vereis_beheer), db: Session = Depends(get_db)):
    scenario = planning.vastgesteld_of_eerste(db)
    reeks = lbe_dienst.dichtheden_uit_historie(db, scenario.id) if scenario else []
    if not reeks:
        return _terug("/beheer/lbe", "Geen dichtheden in de historie gevonden.")
    instellingen.wijzig(db, gebruiker, "gewas.dichtheid_reeks", [f"{w:g}" for w in reeks], "afgeleid uit de historie")
    db.commit()
    return _terug("/beheer/lbe", f"{len(reeks)} dichtheden overgenomen ({min(reeks):g}–{max(reeks):g}).")


# --- Energie -------------------------------------------------------------------------------------------------
@router.get("/energie")
def energie_pagina(request: Request, jaar: int | None = None, scenario: int | None = None,
                   gebruiker: Gebruiker = Depends(auth.vereis_lezen), db: Session = Depends(get_db)):  # fmt: skip
    vandaag = date.today()
    jaar = jaar or vandaag.isocalendar().year
    van, tot = date.fromisocalendar(jaar, 1, 1), date.fromisocalendar(jaar + 1, 1, 1)
    snap = instellingen.laad(db)
    rekenaar = Rekenaar(planning.licht_invoer(db, snap, vandaag))
    kappen = sorted(db.scalars(select(Kap.nummer).where(Kap.actief.is_(True))))
    regels = kern_energie.per_kap_per_week(rekenaar, kappen, van, tot)
    bedrijf = kern_energie.bedrijf_per_week(regels)
    per_kap: dict[int, dict[int, float | None]] = {}
    for r in regels:
        per_kap.setdefault(r.kap, {})[r.week] = r.kwh
    onbekend = rekenaar.lampen_altijd_onbekend()
    groepen = db.scalars(select(Lampgroep).order_by(Lampgroep.id)).all()
    return templates.TemplateResponse(request, "energie.html", {
        "gebruiker": gebruiker, "jaar": jaar, "bedrijf": bedrijf, "per_kap": per_kap, "weken": [b["week"] for b in bedrijf],
        "onbekend": onbekend, "groepen": groepen, "prijs_onbekend": all(w is None for _, w in snap.reeks("energie.stroomprijs_eur_per_mwh")),
        "dagdata": db.scalar(select(func.count()).select_from(BelichtingDag)),
        "weekdata": db.scalar(select(func.count()).select_from(BelichtingWeek)),
    })  # fmt: skip


def _getal(tekst: str | None) -> float | None:
    tekst = (tekst or "").strip().replace(",", ".")
    if not tekst:
        return None
    try:
        return float(tekst)
    except ValueError as fout:
        raise HTTPException(422, f"'{tekst}' is geen getal") from fout


@router.get("/energie/vergelijking")
def vergelijking(request: Request, gebruiker: Gebruiker = Depends(auth.vereis_lezen), db: Session = Depends(get_db)):
    """Scenariovergelijking (§6.6): twee lichtniveaus naast elkaar. Startwaarden uit de laatste belichtingsimport
    (kostenvergelijking AK–AM) en uit de instellingen; alles is op het scherm aan te passen."""
    p = request.query_params
    snap = instellingen.laad(db)
    laatste = db.scalar(select(Importbatch).where(Importbatch.soort == "belichting", Importbatch.status == "bevestigd")
                        .order_by(Importbatch.id.desc()))  # fmt: skip
    werkboek = ((laatste.voorbeeld or {}).get("kostenvergelijking") if laatste else None) or []
    oppervlak = snap["bedrijf.oppervlak_per_kap_m2"]
    overig = sum(r["eur_per_tak"] or 0 for r in snap["energie.overige_kosten_per_tak"] or []) or None
    velden = {
        "a_naam": p.get("a_naam", "A"), "b_naam": p.get("b_naam", "B"),
        "a_umol": p.get("a_umol", ""), "b_umol": p.get("b_umol", ""), "a_uren": p.get("a_uren", ""), "b_uren": p.get("b_uren", ""),
        "a_kw": p.get("a_kw", ""), "b_kw": p.get("b_kw", ""),
        "oppervlak": p.get("oppervlak", "" if oppervlak is None else f"{oppervlak:g}"),
        "zon": p.get("zon", ""), "lbe": p.get("lbe", ""), "doelgewicht": p.get("doelgewicht", ""),
        "prijs": p.get("prijs", "" if snap["energie.stroomprijs_eur_per_mwh"] is None else f"{snap['energie.stroomprijs_eur_per_mwh']:g}"),
        "opbrengst": p.get("opbrengst", "" if snap["energie.opbrengst_per_tak_eur"] is None else f"{snap['energie.opbrengst_per_tak_eur']:g}"),
        "overig": p.get("overig", "" if overig is None else f"{overig:g}"),
    }  # fmt: skip
    uitkomst = None
    if p.get("rekenen"):
        g = kern_energie.Gemeenschappelijk(_getal(velden["oppervlak"]), _getal(velden["zon"]), _getal(velden["lbe"]),
                                           _getal(velden["doelgewicht"]), _getal(velden["prijs"]), _getal(velden["opbrengst"]),
                                           _getal(velden["overig"]))  # fmt: skip
        a = kern_energie.Lichtscenario(
            velden["a_naam"], _getal(velden["a_umol"]), _getal(velden["a_uren"]), _getal(velden["a_kw"])
        )
        b = kern_energie.Lichtscenario(
            velden["b_naam"], _getal(velden["b_umol"]), _getal(velden["b_uren"]), _getal(velden["b_kw"])
        )
        uitkomst = kern_energie.vergelijk(a, b, g)
    return templates.TemplateResponse(request, "vergelijking.html", {
        "gebruiker": gebruiker, "v": velden, "uitkomst": uitkomst, "werkboek": werkboek,
    })  # fmt: skip
