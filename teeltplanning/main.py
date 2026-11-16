"""Webapp-ingang: migraties, startgegevens, beveiligingskoppen, verzoekgrenzen en routes."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, select, text

from . import __version__, instellingen, onderhoud, plannen
from .auth import NietAangemeld
from .backups import maak_backup, pas_herstel_toe
from .bronnen import dienst as bronnen_dienst
from .db.basis import SessionLocal, engine
from .db.modellen import Kap, Lampgroep, Ras
from .voorstel import dienst as model_dienst
from .web import (
    routes_afdruk,
    routes_auth,
    routes_beheer,
    routes_import,
    routes_licht,
    routes_model,
    routes_plan,
    routes_scenario,
)
from .web.sjablonen import MAP, templates

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("teeltplanning")
MAX_VERZOEK = 1024 * 1024  # 1 MB, behalve bij imports en het terugzetten van een back-up (§12)
UPLOADPADEN = (
    "/import/upload",
    "/beheer/backup/herstel",
    "/beheer/instellingen/import",
    "/beheer/sjabloon/upload",
    "/beheer/sjabloon/lettertype",
)

# Startgegevens die het bouwplan noemt: rassen a/p/t/b = Antonov/Pjotr/Tutu/Baltazar (§5), zonder doelgewicht.
START_RASSEN = (("a", "Antonov"), ("p", "Pjotr"), ("t", "Tutu"), ("b", "Baltazar"))
# Lampgroepen uit §6.4 (door de teler bevestigd op 16 november 2026, tenzij anders vermeld). De precieze maanden zijn
# niet bevestigd (open vraag 1): geldig_vanaf/geldig_tot blijven leeg ("onbekend") tot Leon ze invult.
START_LAMPGROEPEN = (
    dict(naam="SON-T volledig", type="son_t", intensiteit_umol=120.0, kw_per_kap=50.0, dimbaar=False, status="indicatief",
         opmerking="Tot circa zomer 2023. Intensiteit en vermogen afgeleid uit het kostenblok (resterende helft = 1 MW "
                   "over 40 kappen en 60 µmol). Modulenummers onbekend."),
    dict(naam="LED fase 1 (2023–2024)", type="led", intensiteit_umol=80.0, kw_per_kap=20.0, dimbaar=False, module_van=1,
         module_tot=40, opmerking="Circa zomer 2023 – zomer 2024; 0,8 MW totaal."),
    dict(naam="SON-T resterend (50%)", type="son_t", intensiteit_umol=60.0, kw_per_kap=25.0, dimbaar=False, module_van=101,
         module_tot=140, opmerking="Circa zomer 2023 – zomer 2024; 1 MW totaal. Het tabblad belichting (week 36 t/m 10) is "
                                   "waarschijnlijk winter 2023/24 (vraag 2)."),
    dict(naam="LED fase 1 (vanaf 2024)", type="led", intensiteit_umol=80.0, kw_per_kap=20.0, dimbaar=False, module_van=1,
         module_tot=40, opmerking="Vanaf uiterlijk juli 2024. Staat niet in het tabblad data; bron voor branduren navragen (vraag 3)."),
    dict(naam="LED fase 2", type="led", intensiteit_umol=120.0, kw_per_kap=27.5, dimbaar=True, module_van=101, module_tot=140,
         status="indicatief",
         opmerking="Vanaf uiterlijk juli 2024; dimbaar. Bevestigd: samen met fase 1 47,5 kW per kap (1,9 MW totaal); "
                   "27,5 kW is afgeleid (47,5 − 20). 5-minutendata met dimstand in het tabblad data vanaf 1 juli 2024."),
)  # fmt: skip


def migreer() -> None:
    """Vóór een schema-upgrade van een bestaande database altijd eerst een consistente back-up."""
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    config = Config(str(ROOT / "alembic.ini"))
    gewenst = ScriptDirectory.from_config(config).get_current_head()
    with engine.connect() as verbinding:
        huidig = MigrationContext.configure(verbinding).get_current_revision()
        bestaand = inspect(verbinding).get_table_names()
    if huidig != gewenst:
        if bestaand:
            maak_backup()
        command.upgrade(config, "head")


def startgegevens() -> None:
    with SessionLocal() as db:
        snap = instellingen.laad(db)
        if db.scalar(select(Kap.nummer).limit(1)) is None:
            for nummer in range(1, snap["bedrijf.aantal_kappen"] + 1):
                db.add(Kap(nummer=nummer, plantvolgorde=nummer))
        if db.scalar(select(Lampgroep.id).limit(1)) is None:
            for groep in START_LAMPGROEPEN:
                db.add(Lampgroep(**groep))
        bestaand = {r.code for r in db.scalars(select(Ras))}
        for code, naam in START_RASSEN:
            if code not in bestaand:
                db.add(Ras(code=code, naam=naam))
        plannen.startgegevens(db)
        db.commit()


async def achtergrond() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.to_thread(onderhoud.dagelijks_opschonen)
        except Exception:
            log.exception("Dagelijkse opschoning mislukt")
        try:
            await asyncio.to_thread(onderhoud.backup_als_nodig)
        except Exception:
            log.exception("Automatische lokale back-up mislukt")
        try:
            await asyncio.to_thread(bronnen_dienst.ververs_als_nodig)
        except Exception:
            log.exception("Bijwerken van externe bronnen mislukt")
        try:
            await asyncio.to_thread(model_dienst.hertrain_als_nodig)
        except Exception:
            log.exception("Automatisch hertrainen mislukt")


@asynccontextmanager
async def levensloop(app):
    hersteld = pas_herstel_toe()
    if hersteld:
        log.warning("Back-up teruggezet; vorige bestanden staan in %s", hersteld)
    migreer()
    startgegevens()
    taak = asyncio.create_task(achtergrond())
    yield
    taak.cancel()
    try:
        await taak
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Teeltplanning", version=__version__, lifespan=levensloop, docs_url=None, redoc_url=None, openapi_url=None
)


class VerzoekGrens:
    """Begrens de verzoekgrootte vóórdat Starlette de body inleest (1 MB; uploads: instelling Import)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") in ("GET", "HEAD", "OPTIONS"):
            return await self.app(scope, receive, send)
        pad = scope.get("path", "")
        grens = MAX_VERZOEK
        if pad.startswith(UPLOADPADEN):
            with SessionLocal() as db:
                snap = instellingen.laad(db)
            grens = snap["import.max_upload_mb"] * 1024 * 1024 + 64 * 1024
            if pad.startswith("/beheer/backup/herstel"):
                grens = snap["import.max_uitgepakt_mb"] * 1024 * 1024
        koppen = dict(scope.get("headers", []))
        try:
            lengte = int(koppen.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "Ongeldige Content-Length"}, status_code=400)(scope, receive, send)
        if lengte > grens:
            return await JSONResponse({"detail": "Verzoek te groot"}, status_code=413)(scope, receive, send)
        ontvangen = 0

        async def begrensd():
            nonlocal ontvangen
            bericht = await receive()
            ontvangen += len(bericht.get("body", b""))
            if ontvangen > grens:
                raise HTTPException(413, "Verzoek te groot")
            return bericht

        return await self.app(scope, begrensd, send)


app.add_middleware(VerzoekGrens)


@app.middleware("http")
async def koppen(request: Request, call_next):
    antwoord = await call_next(request)
    antwoord.headers["X-Content-Type-Options"] = "nosniff"
    antwoord.headers["X-Frame-Options"] = "DENY"
    antwoord.headers["Referrer-Policy"] = "same-origin"
    antwoord.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    antwoord.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    )
    antwoord.headers.setdefault("Cache-Control", "no-store")
    if os.environ.get("SECURE_COOKIES", "").lower() in ("1", "true"):
        antwoord.headers["Strict-Transport-Security"] = "max-age=31536000"
    return antwoord


def _wil_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "") and not request.headers.get("hx-request")


@app.exception_handler(NietAangemeld)
async def niet_aangemeld(request: Request, _fout: NietAangemeld):
    if request.method == "GET" and _wil_html(request):
        return RedirectResponse("/aanmelden", status_code=303)
    return JSONResponse({"detail": "Aanmelden vereist"}, status_code=401)


@app.exception_handler(HTTPException)
async def http_fout(request: Request, fout: HTTPException):
    if _wil_html(request):
        return templates.TemplateResponse(request, "fout.html", {"status": fout.status_code, "melding": fout.detail},
                                          status_code=fout.status_code)  # fmt: skip
    return JSONResponse({"detail": fout.detail}, status_code=fout.status_code, headers=getattr(fout, "headers", None))


@app.exception_handler(RequestValidationError)
async def ongeldig_verzoek(request: Request, fout: RequestValidationError):
    # Geen ingevoerde waarden (zoals wachtwoorden) terugsturen.
    return JSONResponse(
        status_code=422, content={"detail": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in fout.errors()]}
    )


@app.get("/health", include_in_schema=False)
def health() -> dict:
    with engine.connect() as verbinding:
        verbinding.execute(text("SELECT 1"))
    return {"status": "ok", "versie": __version__}


app.include_router(routes_auth.router)
app.include_router(routes_plan.router)
app.include_router(routes_import.router)
app.include_router(routes_afdruk.router)
app.include_router(routes_licht.router)
app.include_router(routes_model.router)
app.include_router(routes_scenario.router)
app.include_router(routes_beheer.router)
app.mount("/static", StaticFiles(directory=str(MAP / "static")), name="static")
