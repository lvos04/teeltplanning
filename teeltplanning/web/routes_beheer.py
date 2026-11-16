"""Beheer: instellingen (gegenereerd uit het register), kappen, rassen, gebruikers, MFA, audit, back-up, export."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import auth, instellingen, mfa, onderhoud
from ..backups import VOORVOEGSEL, maak_backup, zet_herstel_klaar
from ..db.basis import DATA_DIR, Base, get_db
from ..db.modellen import Audit, Gebruiker, Instelling, Kap, Ras
from .sjablonen import templates

router = APIRouter(prefix="/beheer")


def _terug(pad: str, melding: str | None = None) -> RedirectResponse:
    from urllib.parse import quote

    return RedirectResponse(
        pad + (("&" if "?" in pad else "?") + "melding=" + quote(melding) if melding else ""), status_code=303
    )


@router.get("")
def index(request: Request, gebruiker: Gebruiker = Depends(auth.vereis_lezen)):
    return templates.TemplateResponse(request, "beheer/index.html", {"gebruiker": gebruiker})


# --- Instellingen ---------------------------------------------------------------------------------------------
@router.get("/instellingen")
def instellingen_pagina(request: Request, groep: str = "Bedrijf", zoek: str = "", melding: str | None = None,
                        gebruiker: Gebruiker = Depends(auth.vereis_beheer), db: Session = Depends(get_db)):  # fmt: skip
    snap = instellingen.laad(db)
    zoekterm = zoek.strip().lower()
    if zoekterm:
        lijst = [i for i in instellingen.REGISTER_LIJST
                 if zoekterm in f"{i.sleutel} {i.label} {i.uitleg} {i.groep}".lower()]  # fmt: skip
    else:
        lijst = [i for i in instellingen.REGISTER_LIJST if i.groep == groep]
    historie = {}
    for inst in lijst:
        rijen = db.scalars(
            select(Instelling).where(Instelling.sleutel == inst.sleutel).order_by(Instelling.id.desc()).limit(5)
        ).all()
        historie[inst.sleutel] = rijen
    regels = [{
        "inst": i, "waarde": snap[i.sleutel], "weergave": instellingen.weergave(i, snap[i.sleutel]),
        "start": instellingen.weergave(i, i.startwaarde), "is_start": snap[i.sleutel] == i.startwaarde,
        "reeks": snap.reeks(i.sleutel) if i.met_ingangsdatum else None,
        "csv": instellingen.tabel_naar_csv(i, snap[i.sleutel]) if i.type == "tabel" else None,
        "json": json.dumps(snap[i.sleutel], ensure_ascii=False) if i.type == "toewijzing" else None,
        "historie": historie[i.sleutel],
    } for i in lijst]  # fmt: skip
    return templates.TemplateResponse(request, "beheer/instellingen.html", {
        "gebruiker": gebruiker, "groepen": instellingen.GROEPEN, "groep": groep, "zoek": zoek, "regels": regels,
        "melding": melding, "weekdagen": instellingen.WEEKDAGEN,
    })  # fmt: skip


@router.post("/instellingen/{sleutel}")
async def instelling_opslaan(request: Request, sleutel: str, gebruiker: Gebruiker = Depends(auth.vereis_beheer),
                             db: Session = Depends(get_db)):  # fmt: skip
    if sleutel not in instellingen.REGISTER:
        raise HTTPException(404, "Onbekende instelling.")
    inst = instellingen.REGISTER[sleutel]
    formulier = await request.form()
    actie = formulier.get("actie", "opslaan")
    reden = str(formulier.get("reden", ""))[:2000]
    vanaf_tekst = str(formulier.get("geldig_vanaf", "")).strip()
    try:
        vanaf = date.fromisoformat(vanaf_tekst) if vanaf_tekst else None
        if inst.type == "weekdagen":
            waarde = [int(x) for x in formulier.getlist("waarde")]
            if not waarde and inst.null:
                waarde = None
        elif inst.type == "bool":
            waarde = "aan" if formulier.get("waarde") == "aan" else "uit"
        else:
            waarde = formulier.get("waarde")
        if (
            sleutel == "beveiliging.mfa_verplicht_beheerders"
            and actie != "start"
            and waarde == "aan"
            and not gebruiker.mfa_aan
        ):
            raise instellingen.OngeldigeWaarde(
                "Zet eerst voor je eigen account tweestapsverificatie aan, anders sluit je jezelf buiten het beheer."
            )
        if sleutel == "beveiliging.vertrouwde_proxys" and waarde:
            delen = [d for d in str(waarde).replace(";", ",").split(",") if d.strip()]
            if len(auth.lees_proxys(str(waarde))) != len(delen) or any(
                d.strip() in ("0.0.0.0/0", "::/0") for d in delen
            ):
                raise instellingen.OngeldigeWaarde(
                    "Vertrouwde proxy: gebruik IP-adressen of netwerken (CIDR), niet 0.0.0.0/0."
                )
        instellingen.wijzig(db, gebruiker, sleutel, waarde, reden, vanaf, naar_start=actie == "start")
        db.commit()
    except (instellingen.OngeldigeWaarde, ValueError) as fout:
        db.rollback()
        return _terug(f"/beheer/instellingen?groep={inst.groep}", f"Niet opgeslagen: {fout}")
    return _terug(
        f"/beheer/instellingen?groep={inst.groep}", f"{inst.label} opgeslagen; het plan is opnieuw doorgerekend."
    )


@router.get("/instellingen/export.json")
def instellingen_export(gebruiker: Gebruiker = Depends(auth.vereis_beheer), db: Session = Depends(get_db)):
    inhoud = json.dumps(instellingen.exporteer(db), ensure_ascii=False, indent=1)
    auth.audit(db, gebruiker, "instellingen_geexporteerd", "instellingen")
    db.commit()
    return Response(inhoud, media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="teeltplanning-instellingen.json"'})  # fmt: skip


@router.post("/instellingen/import")
async def instellingen_import(bestand: UploadFile, reden: str = Form("import instellingenbestand"),
                              gebruiker: Gebruiker = Depends(auth.vereis_beheer), db: Session = Depends(get_db)):  # fmt: skip
    try:
        document = json.loads(await bestand.read())
        aantal = instellingen.importeer(db, gebruiker, document, reden)
        db.commit()
    except (ValueError, instellingen.OngeldigeWaarde) as fout:
        db.rollback()
        return _terug("/beheer/instellingen", f"Niet ingelezen: {fout}")
    return _terug("/beheer/instellingen", f"{aantal} instelling(en) gewijzigd.")


# --- Tabellen: kappen en rassen ------------------------------------------------------------------------------
TABELLEN = {
    "kappen": (Kap, ["nummer", "vakgroep", "oppervlak_m2", "plantvolgorde", "wisseltijd_dagen", "actief"], "nummer"),
    "rassen": (Ras, ["code", "naam", "doelgewicht_g", "temperatuurgevoeligheid_dagen_per_graad", "actief"], "code"),
}
TYPEN = {"nummer": int, "plantvolgorde": int, "wisseltijd_dagen": int, "oppervlak_m2": float, "doelgewicht_g": float,
         "temperatuurgevoeligheid_dagen_per_graad": float}  # fmt: skip


def _cel(kolom: str, tekst: str):
    tekst = (tekst or "").strip()
    if kolom == "actief":
        return tekst.lower() in ("1", "ja", "aan", "true", "waar", "on")
    if tekst == "":
        return None
    if kolom in TYPEN:
        try:
            waarde = TYPEN[kolom](tekst.replace(",", "."))
        except ValueError as exc:
            raise ValueError(f"{kolom}: '{tekst}' is geen getal") from exc
        if waarde < 0:
            raise ValueError(f"{kolom}: mag niet negatief zijn")
        return waarde
    return tekst[:100]


@router.get("/tabel/{tabel}")
def tabel_pagina(request: Request, tabel: str, melding: str | None = None, gebruiker: Gebruiker = Depends(auth.vereis_lezen),
                 db: Session = Depends(get_db)):  # fmt: skip
    if tabel not in TABELLEN:
        raise HTTPException(404)
    model, kolommen, sleutel = TABELLEN[tabel]
    rijen = db.scalars(select(model).order_by(getattr(model, sleutel))).all()
    return templates.TemplateResponse(request, "beheer/tabel.html", {
        "gebruiker": gebruiker, "tabel": tabel, "kolommen": kolommen, "rijen": rijen, "melding": melding,
        "csv": _tabel_csv(rijen, kolommen),
    })  # fmt: skip


def _tabel_csv(rijen, kolommen) -> str:
    uit = io.StringIO()
    schrijver = csv.writer(uit, delimiter=";", lineterminator="\n")
    schrijver.writerow(kolommen)
    for rij in rijen:
        schrijver.writerow(["ja" if getattr(rij, k) is True else "nee" if getattr(rij, k) is False else
                            ("" if getattr(rij, k) is None else getattr(rij, k)) for k in kolommen])  # fmt: skip
    return uit.getvalue()


@router.get("/tabel/{tabel}/export.csv")
def tabel_export(tabel: str, gebruiker: Gebruiker = Depends(auth.vereis_lezen), db: Session = Depends(get_db)):
    if tabel not in TABELLEN:
        raise HTTPException(404)
    model, kolommen, sleutel = TABELLEN[tabel]
    rijen = db.scalars(select(model).order_by(getattr(model, sleutel))).all()
    return Response("﻿" + _tabel_csv(rijen, kolommen), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{tabel}.csv"'})  # fmt: skip


@router.post("/tabel/{tabel}/csv")
def tabel_import(tabel: str, csv_tekst: str = Form(..., max_length=500_000), reden: str = Form("", max_length=2000),
                 gebruiker: Gebruiker = Depends(auth.vereis_beheer), db: Session = Depends(get_db)):  # fmt: skip
    """Toevoegen, wijzigen en uitzetten (actief = nee) via CSV; rijen worden nooit verwijderd."""
    if tabel not in TABELLEN:
        raise HTTPException(404)
    model, kolommen, sleutel = TABELLEN[tabel]
    try:
        lezer = csv.reader(io.StringIO(csv_tekst.lstrip("﻿")), delimiter=";")
        kop = [k.strip() for k in next(lezer)]
        if kop != kolommen:
            raise ValueError(f"kopregel moet zijn: {';'.join(kolommen)}")
        bestaand = {getattr(r, sleutel): r for r in db.scalars(select(model))}
        wijzigingen = []
        for nummer, velden in enumerate(lezer, 2):
            if not any(v.strip() for v in velden):
                continue
            waarden = {k: _cel(k, v) for k, v in zip(kolommen, velden, strict=False)}
            if waarden.get(sleutel) in (None, ""):
                raise ValueError(f"regel {nummer}: {sleutel} ontbreekt")
            rij = bestaand.get(waarden[sleutel])
            if rij is None:
                rij = model(**waarden)
                db.add(rij)
                wijzigingen.append({"nieuw": {k: v for k, v in waarden.items()}})
            else:
                oud = {k: getattr(rij, k) for k in kolommen}
                if oud != waarden:
                    for k, v in waarden.items():
                        setattr(rij, k, v)
                    wijzigingen.append({"oud": oud, "nieuw": waarden})
        if wijzigingen:
            auth.audit(db, gebruiker, f"{tabel}_gewijzigd", tabel, {"wijzigingen": wijzigingen, "reden": reden or None})
        db.commit()
    except (ValueError, StopIteration) as fout:
        db.rollback()
        return _terug(f"/beheer/tabel/{tabel}", f"Niet opgeslagen: {fout}")
    return _terug(f"/beheer/tabel/{tabel}", f"{len(wijzigingen)} regel(s) gewijzigd; het plan is opnieuw doorgerekend.")


# --- Gebruikers ---------------------------------------------------------------------------------------------
@router.get("/gebruikers/lijst")
def gebruikers(request: Request, melding: str | None = None, gebruiker: Gebruiker = Depends(auth.vereis_beheer),
               db: Session = Depends(get_db)):  # fmt: skip
    lijst = db.scalars(select(Gebruiker).order_by(Gebruiker.gebruikersnaam)).all()
    return templates.TemplateResponse(request, "beheer/gebruikers.html", {"gebruiker": gebruiker, "lijst": lijst, "rollen": auth.ROLLEN,
                                                                         "melding": melding})  # fmt: skip


@router.post("/gebruikers/nieuw")
def gebruiker_nieuw(gebruikersnaam: str = Form(..., max_length=80), wachtwoord: str = Form(..., max_length=256), rol: str = Form(...),
                    gebruiker: Gebruiker = Depends(auth.vereis_beheer), db: Session = Depends(get_db)):  # fmt: skip
    if rol not in auth.ROLLEN or not gebruikersnaam.strip():
        raise HTTPException(422, "Kies een naam en een geldige rol.")
    auth.controleer_nieuw_wachtwoord(wachtwoord)
    if db.scalar(select(Gebruiker).where(func.lower(Gebruiker.gebruikersnaam) == gebruikersnaam.strip().lower())):
        return _terug("/beheer/gebruikers/lijst", "Die gebruikersnaam bestaat al.")
    nieuw = Gebruiker(gebruikersnaam=gebruikersnaam.strip(), wachtwoord_hash=auth.hash_wachtwoord(wachtwoord), rol=rol)
    db.add(nieuw)
    db.flush()
    auth.audit(
        db,
        gebruiker,
        "gebruiker_aangemaakt",
        f"gebruiker:{nieuw.id}",
        {"gebruikersnaam": nieuw.gebruikersnaam, "rol": rol},
    )
    db.commit()
    return _terug("/beheer/gebruikers/lijst", f"Gebruiker {nieuw.gebruikersnaam} aangemaakt.")


@router.post("/gebruikers/{gebruiker_id}")
def gebruiker_wijzigen(gebruiker_id: int, rol: str = Form(...), actief: str = Form("nee"), mfa_reset: str = Form("nee"),
                       nieuw_wachtwoord: str = Form("", max_length=256), gebruiker: Gebruiker = Depends(auth.vereis_beheer),
                       db: Session = Depends(get_db)):  # fmt: skip
    doel = db.get(Gebruiker, gebruiker_id)
    if doel is None or rol not in auth.ROLLEN:
        raise HTTPException(404)
    if doel.id == gebruiker.id and (rol != "beheerder" or actief != "ja"):
        return _terug("/beheer/gebruikers/lijst", "Je kunt je eigen beheerrechten niet intrekken.")
    oud = {"rol": doel.rol, "actief": doel.actief}
    doel.rol, doel.actief = rol, actief == "ja"
    if nieuw_wachtwoord:
        auth.controleer_nieuw_wachtwoord(nieuw_wachtwoord)
        doel.wachtwoord_hash = auth.hash_wachtwoord(nieuw_wachtwoord)
    if mfa_reset == "ja":
        mfa.reset(db, doel, gebruiker)
    auth.audit(db, gebruiker, "gebruiker_gewijzigd", f"gebruiker:{doel.id}",
               {"oud": oud, "nieuw": {"rol": doel.rol, "actief": doel.actief}, "wachtwoord": bool(nieuw_wachtwoord)})  # fmt: skip
    db.commit()
    return _terug("/beheer/gebruikers/lijst", f"{doel.gebruikersnaam} bijgewerkt.")


# --- Eigen tweestapsverificatie ------------------------------------------------------------------------------
@router.get("/mfa")
def mfa_pagina(request: Request, melding: str | None = None, gebruiker: Gebruiker = Depends(auth.vereis_lezen)):
    qr = uri = None
    if gebruiker.mfa_geheim and not gebruiker.mfa_aan:
        uri = mfa.otpauth_uri(gebruiker, gebruiker.mfa_geheim)
        qr = mfa.qr_svg(uri)
    return templates.TemplateResponse(
        request, "beheer/mfa.html", {"gebruiker": gebruiker, "qr": qr, "uri": uri, "melding": melding}
    )


@router.post("/mfa/start")
def mfa_start(gebruiker: Gebruiker = Depends(auth.vereis_lezen), db: Session = Depends(get_db)):
    gebruiker = db.get(Gebruiker, gebruiker.id)
    if gebruiker.mfa_aan:
        return _terug("/beheer/mfa", "Tweestapsverificatie staat al aan.")
    gebruiker.mfa_geheim, gebruiker.mfa_laatste_stap = mfa.nieuw_geheim(), None
    auth.audit(db, gebruiker, "mfa_instellen_gestart", f"gebruiker:{gebruiker.id}")
    db.commit()
    return _terug("/beheer/mfa")


@router.post("/mfa/aan")
def mfa_aan(
    code: str = Form(..., max_length=10),
    gebruiker: Gebruiker = Depends(auth.vereis_lezen),
    db: Session = Depends(get_db),
):
    gebruiker = db.get(Gebruiker, gebruiker.id)
    if not gebruiker.mfa_geheim or gebruiker.mfa_aan or not mfa.controleer_code(gebruiker, code):
        db.commit()
        return _terug("/beheer/mfa", "Code klopt niet. Controleer de tijd op je telefoon en probeer opnieuw.")
    gebruiker.mfa_aan = True
    auth.audit(db, gebruiker, "mfa_aangezet", f"gebruiker:{gebruiker.id}")
    db.commit()
    return _terug("/beheer/mfa", "Tweestapsverificatie staat aan.")


@router.post("/mfa/uit")
def mfa_uit(wachtwoord: str = Form(..., max_length=256), code: str = Form(..., max_length=10),
            gebruiker: Gebruiker = Depends(auth.vereis_lezen), db: Session = Depends(get_db)):  # fmt: skip
    gebruiker = db.get(Gebruiker, gebruiker.id)
    if gebruiker.rol == "beheerder" and instellingen.laad(db)["beveiliging.mfa_verplicht_beheerders"]:
        return _terug("/beheer/mfa", "Tweestapsverificatie is verplicht voor beheerders.")
    if (
        not gebruiker.mfa_aan
        or not auth.controleer_wachtwoord(wachtwoord, gebruiker.wachtwoord_hash)
        or not mfa.controleer_code(gebruiker, code)
    ):
        db.commit()
        return _terug("/beheer/mfa", "Wachtwoord of code klopt niet.")
    gebruiker.mfa_aan, gebruiker.mfa_geheim, gebruiker.mfa_laatste_stap = False, None, None
    auth.audit(db, gebruiker, "mfa_uitgezet", f"gebruiker:{gebruiker.id}")
    db.commit()
    return _terug("/beheer/mfa", "Tweestapsverificatie staat uit.")


# --- Auditlog ------------------------------------------------------------------------------------------------
@router.get("/audit/log")
def audit_pagina(request: Request, pagina: int = 1, actie: str = "", gebruiker: Gebruiker = Depends(auth.vereis_beheer),
                 db: Session = Depends(get_db)):  # fmt: skip
    vraag = select(Audit).order_by(Audit.id.desc())
    if actie:
        vraag = vraag.where(Audit.actie == actie)
    regels = db.scalars(vraag.offset((max(pagina, 1) - 1) * 100).limit(100)).all()
    namen = {g.id: g.gebruikersnaam for g in db.scalars(select(Gebruiker))}
    acties = db.scalars(select(Audit.actie).distinct().order_by(Audit.actie)).all()
    return templates.TemplateResponse(request, "beheer/audit.html", {"gebruiker": gebruiker, "regels": regels, "namen": namen,
                                                                     "pagina": pagina, "actie": actie, "acties": acties})  # fmt: skip


# --- Back-up en export ----------------------------------------------------------------------------------------
@router.get("/backup/status")
def backup_pagina(request: Request, melding: str | None = None, gebruiker: Gebruiker = Depends(auth.vereis_beheer),
                  db: Session = Depends(get_db)):  # fmt: skip
    status = onderhoud.backupstatus(instellingen.laad(db)["systeem.backup_bewaardagen"])
    return templates.TemplateResponse(
        request, "beheer/backup.html", {"gebruiker": gebruiker, "status": status, "melding": melding}
    )


@router.post("/backup/maken")
def backup_maken(gebruiker: Gebruiker = Depends(auth.vereis_beheer), db: Session = Depends(get_db)):
    auth.audit(db, gebruiker, "backup_gemaakt", "backup")
    db.commit()
    doel = maak_backup()
    return _terug("/beheer/backup/status", f"Back-up {doel.name} gemaakt.")


@router.post("/backup/download")
def backup_download(naam: str = Form(...), wachtwoord: str = Form("", max_length=256), code: str = Form("", max_length=10),
                    gebruiker: Gebruiker = Depends(auth.vereis_beheer), db: Session = Depends(get_db)):  # fmt: skip
    if Path(naam).name != naam or not naam.startswith(VOORVOEGSEL) or not naam.endswith(".zip"):
        raise HTTPException(404)
    pad = DATA_DIR / "backups" / naam
    if not pad.is_file():
        raise HTTPException(404)
    auth.herbevestig(db, db.get(Gebruiker, gebruiker.id), wachtwoord, code)
    auth.audit(db, gebruiker, "backup_gedownload", f"backup:{naam}")
    db.commit()
    return FileResponse(pad, filename=naam, media_type="application/zip")


@router.post("/backup/herstel")
async def backup_herstel(bestand: UploadFile, wachtwoord: str = Form("", max_length=256), code: str = Form("", max_length=10),
                         gebruiker: Gebruiker = Depends(auth.vereis_beheer), db: Session = Depends(get_db)):  # fmt: skip
    """Back-up terugzetten: opnieuw bevestigen, controleren en klaarzetten; actief na een herstart van de app."""
    auth.herbevestig(db, db.get(Gebruiker, gebruiker.id), wachtwoord, code)
    tmp = DATA_DIR / "tmp"
    tmp.mkdir(exist_ok=True)
    doel = tmp / f"herstel-{datetime.now().strftime('%Y%m%d%H%M%S')}.zip"
    with doel.open("wb") as uit:
        while blok := await bestand.read(1024 * 1024):
            uit.write(blok)
    try:
        manifest = zet_herstel_klaar(doel)
    except (ValueError, zipfile.BadZipFile, KeyError) as fout:
        return _terug("/beheer/backup/status", f"Back-up afgekeurd: {fout}")
    finally:
        doel.unlink(missing_ok=True)
    auth.audit(
        db,
        gebruiker,
        "backup_herstel_klaargezet",
        "backup",
        {"bestanden": len(manifest["files"]), "gemaakt": manifest.get("created_at")},
    )
    db.commit()
    return _terug(
        "/beheer/backup/status",
        "Back-up gecontroleerd en klaargezet. Herstart de app in TrueNAS om hem terug te zetten.",
    )


@router.post("/export")
def volledige_export(wachtwoord: str = Form("", max_length=256), code: str = Form("", max_length=10),
                     gebruiker: Gebruiker = Depends(auth.vereis_beheer), db: Session = Depends(get_db)):  # fmt: skip
    """Alle bedrijfstabellen als CSV (Excel-leesbaar), zonder wachtwoordhashes, sessies of MFA-geheimen."""
    auth.herbevestig(db, db.get(Gebruiker, gebruiker.id), wachtwoord, code)
    uitgesloten = {"gebruiker", "sessie", "aanmeldpoging", "mfa_stap", "alembic_version"}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archief:
        for tabel in Base.metadata.sorted_tables:
            if tabel.name in uitgesloten:
                continue
            uit = io.StringIO()
            schrijver = csv.writer(uit, delimiter=";")
            schrijver.writerow([c.name for c in tabel.columns])
            for rij in db.execute(select(tabel)).mappings():
                waarden = []
                for waarde in rij.values():
                    if isinstance(waarde, (dict, list)):
                        waarde = json.dumps(waarde, ensure_ascii=False)
                    elif isinstance(waarde, (datetime, date)):
                        waarde = waarde.isoformat()
                    if isinstance(waarde, str) and waarde.startswith(("=", "+", "-", "@", "\t", "\r")):
                        waarde = "'" + waarde  # geen formules in Excel
                    waarden.append(waarde)
                schrijver.writerow(waarden)
            archief.writestr(tabel.name + ".csv", "﻿" + uit.getvalue())
        archief.writestr("instellingen.json", json.dumps(instellingen.exporteer(db), ensure_ascii=False, indent=1))
    auth.audit(db, gebruiker, "volledige_export", "alle_tabellen")
    db.commit()
    return Response(buffer.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="teeltplanning-export.zip"'})  # fmt: skip
