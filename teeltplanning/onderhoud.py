"""Achtergrondonderhoud (zoals Gewichtsverwachting `app/onderhoud.py`): dagelijkse opschoning en back-ups."""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from . import instellingen
from .auth import audit
from .db.basis import DATA_DIR, SessionLocal
from .db.modellen import Aanmeldpoging, Audit, MfaStap, Sessie, utcnu

log = logging.getLogger("teeltplanning")
ADVIES_BEWAARDAGEN = 30


def opschonen(db: Session, nu: datetime | None = None) -> dict:
    """Verlopen sessies, MFA-stappen en oude aanmeldpogingen weg; audit alleen inkorten als ingesteld."""
    nu = nu or utcnu()
    snap = instellingen.laad(db)
    grens_pogingen = nu - max(timedelta(days=2), timedelta(minutes=2 * snap["beveiliging.venster_minuten"]))
    uitkomst = {
        "sessies": db.execute(delete(Sessie).where(Sessie.verloopt_op <= nu)).rowcount or 0,
        "mfa_stappen": db.execute(delete(MfaStap).where(MfaStap.verloopt_op <= nu)).rowcount or 0,
        "aanmeldpogingen": db.execute(delete(Aanmeldpoging).where(Aanmeldpoging.op < grens_pogingen)).rowcount or 0,
        "auditregels": 0,
    }
    bewaardagen = snap["beveiliging.audit_bewaardagen"]
    if bewaardagen > 0:
        grens = nu - timedelta(days=bewaardagen)
        uitkomst["auditregels"] = db.execute(delete(Audit).where(Audit.op < grens)).rowcount or 0
        if uitkomst["auditregels"]:
            audit(
                db, None, "audit_ingekort", "audit", {"verwijderd": uitkomst["auditregels"], "bewaardagen": bewaardagen}
            )
    db.commit()
    return uitkomst


_laatste_opschoning: dict[str, date | None] = {"datum": None}


def dagelijks_opschonen() -> dict | None:
    vandaag = datetime.now(UTC).date()
    if _laatste_opschoning["datum"] == vandaag:
        return None
    with SessionLocal() as db:
        uitkomst = opschonen(db)
    _laatste_opschoning["datum"] = vandaag
    return uitkomst


def backupmap():
    return DATA_DIR / "backups"


def ruim_backups_op(bewaardagen: int, nu_ts: float | None = None) -> list[str]:
    """Archieven ouder dan `bewaardagen` weg (0 = onbeperkt); de nieuwste blijft altijd."""
    if not bewaardagen:
        return []
    nu_ts = datetime.now(UTC).timestamp() if nu_ts is None else nu_ts
    grens = nu_ts - bewaardagen * 86400
    archieven = sorted(backupmap().glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    weg = []
    for oud in archieven[1:]:
        if oud.stat().st_mtime < grens:
            oud.unlink()
            weg.append(oud.name)
    return weg


def backupstatus(bewaardagen: int) -> dict:
    map_ = backupmap()
    archieven = sorted(map_.glob("*.zip"), reverse=True) if map_.exists() else []
    gebruik = shutil.disk_usage(map_ if map_.exists() else DATA_DIR)
    advies = []
    if bewaardagen == 0:
        advies.append(
            f"Back-ups worden onbeperkt bewaard. Advies: kies een bewaartermijn van bijvoorbeeld {ADVIES_BEWAARDAGEN} dagen."
        )
    if gebruik.total and gebruik.free / gebruik.total < 0.10:
        advies.append("Minder dan 10% schijfruimte vrij op de dataset. Ruim back-ups op of verkort de bewaartermijn.")
    nieuwste = max((p.stat().st_mtime for p in archieven), default=None)
    return {
        "archieven": [
            {"naam": p.name, "grootte": p.stat().st_size, "op": datetime.fromtimestamp(p.stat().st_mtime)}
            for p in archieven
        ],
        "aantal": len(archieven),
        "totaal_bytes": sum(p.stat().st_size for p in archieven),
        "nieuwste": datetime.fromtimestamp(nieuwste) if nieuwste else None,
        "schijf": {"totaal": gebruik.total, "vrij": gebruik.free},
        "advies": advies,
        "herstel_klaar": (DATA_DIR / "herstel" / "klaar" / "app.db").is_file(),
        "offsite": "Back-ups staan op dezelfde dataset als de app. Laat TrueNAS (Cloud Sync/Replication) een kopie op een andere locatie maken.",
    }


def backup_als_nodig() -> None:
    from .backups import maak_backup

    with SessionLocal() as db:
        snap = instellingen.laad(db)
    map_ = backupmap()
    laatste = max((p.stat().st_mtime for p in map_.glob("*.zip")), default=0) if map_.exists() else 0
    if datetime.now(UTC).timestamp() - laatste > snap["systeem.backup_interval_uren"] * 3600:
        maak_backup()
        ruim_backups_op(snap["systeem.backup_bewaardagen"])
