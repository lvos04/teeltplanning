"""Consistente SQLite-back-ups en herstel (overgenomen uit Gewichtsverwachting `app/backups.py`).

Herstellen gebeurt nooit over een draaiende database heen:

* offline via de opdrachtregel naar een **nieuwe lege map** (`python -m teeltplanning.backups restore …`), of
* via Beheer → Back-up (na opnieuw bevestigen met wachtwoord en eventueel MFA-code): de back-up wordt
  gecontroleerd en klaargezet in `DATA_DIR/herstel/klaar/`; bij de **volgende start** zet de app hem op zijn
  plaats en bewaart de oude bestanden in `DATA_DIR/herstel/oud-<tijd>/`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from .db.basis import DATA_DIR, engine

VOORVOEGSEL = "teeltplanning-"
UITGESLOTEN = {"backups", "tmp", "__pycache__", "herstel"}


def _digest(pad: Path) -> str:
    h = hashlib.sha256()
    with pad.open("rb") as f:
        for blok in iter(lambda: f.read(1024 * 1024), b""):
            h.update(blok)
    return h.hexdigest()


def maak_backup() -> Path:
    if engine.dialect.name != "sqlite" or not engine.url.database:
        raise ValueError("Deze back-upfunctie ondersteunt alleen SQLite-bestandsdatabases.")
    map_ = DATA_DIR / "backups"
    map_.mkdir(mode=0o700, exist_ok=True)
    stempel = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    doel = map_ / f"{VOORVOEGSEL}{stempel}.zip"
    deels = doel.with_suffix(".partial")
    with tempfile.TemporaryDirectory(dir=map_) as tmp:
        kopie = Path(tmp) / "app.db"
        bron_pad = Path(engine.url.database).resolve()
        with sqlite3.connect(str(bron_pad)) as bron, sqlite3.connect(str(kopie)) as dest:
            bron.backup(dest)
            if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Databasecontrole mislukt.")
        onderdelen = [("app.db", kopie)]
        for pad in sorted(DATA_DIR.rglob("*")):
            relatief = pad.relative_to(DATA_DIR)
            if any(deel in UITGESLOTEN for deel in relatief.parts) or pad.is_symlink() or not pad.is_file():
                continue
            if pad.resolve() == bron_pad or pad.name in {bron_pad.name + "-wal", bron_pad.name + "-shm"}:
                continue
            if relatief.as_posix() == "app.db":
                continue
            onderdelen.append((relatief.as_posix(), pad))
        with zipfile.ZipFile(deels, "w", compression=zipfile.ZIP_DEFLATED) as archief:
            manifest = {"format": 1, "created_at": stempel, "database": "app.db", "files": {}}
            for naam, pad in onderdelen:
                h = hashlib.sha256()
                with pad.open("rb") as src, archief.open(naam, "w") as dst:
                    for blok in iter(lambda: src.read(1024 * 1024), b""):
                        dst.write(blok)
                        h.update(blok)
                manifest["files"][naam] = h.hexdigest()
            archief.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        os.chmod(deels, 0o600)
        os.replace(deels, doel)
    return doel


def herstel_backup(archief_pad: Path, bestemming: Path) -> dict:
    """Controleer en pak uit naar een lege map (checksums, integriteit, verwijzingen)."""
    bestemming = bestemming.resolve()
    if bestemming.exists() and any(bestemming.iterdir()):
        raise ValueError("Herstel alleen naar een lege map; stop de app vóór omschakelen.")
    bestemming.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=bestemming.parent) as tmp:
        staging = Path(tmp)
        with zipfile.ZipFile(archief_pad) as archief:
            if len(archief.infolist()) > 100000:
                raise ValueError("Te veel archiefbestanden.")
            manifest = json.loads(archief.read("manifest.json"))
            if manifest.get("format") != 1 or manifest.get("database") != "app.db":
                raise ValueError("Onbekend back-upformaat.")
            toegestaan = set(manifest.get("files", {}))
            namen = [i.filename for i in archief.infolist()]
            if len(namen) != len(set(namen)) or set(namen) != toegestaan | {"manifest.json"}:
                raise ValueError("Bestandslijst komt niet overeen met manifest.")
            for naam, verwacht in manifest["files"].items():
                relatief = Path(naam)
                if relatief.is_absolute() or ".." in relatief.parts or "\\" in naam or naam == "manifest.json":
                    raise ValueError("Onveilig bestandspad.")
                pad = staging / relatief
                pad.parent.mkdir(parents=True, exist_ok=True)
                with archief.open(naam) as src, pad.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                if _digest(pad) != verwacht:
                    raise ValueError(f"Checksum ongeldig: {naam}")
            with sqlite3.connect(str(staging / "app.db")) as hersteld:
                if hersteld.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Herstelde database is beschadigd.")
                if hersteld.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ValueError("Herstelde database bevat ongeldige verwijzingen.")
        bestemming.mkdir(mode=0o700, exist_ok=True)
        for kind in staging.iterdir():
            shutil.move(str(kind), bestemming / kind.name)
        for pad in bestemming.rglob("*"):
            if pad.is_file():
                os.chmod(pad, 0o600)
    return manifest


def zet_herstel_klaar(archief_pad: Path) -> dict:
    """Controleer de back-up en zet hem klaar; toegepast bij de volgende start (`pas_herstel_toe`)."""
    klaar = DATA_DIR / "herstel" / "klaar"
    if klaar.exists():
        shutil.rmtree(klaar)
    return herstel_backup(archief_pad, klaar)


def pas_herstel_toe() -> str | None:
    """Bij het starten, vóór migraties: een klaargezette back-up op zijn plaats zetten."""
    klaar = DATA_DIR / "herstel" / "klaar"
    if not (klaar / "app.db").is_file():
        return None
    engine.dispose()
    oud = DATA_DIR / "herstel" / f"oud-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    oud.mkdir(parents=True)
    for naam in ("app.db-wal", "app.db-shm"):
        if (DATA_DIR / naam).exists():
            shutil.move(str(DATA_DIR / naam), oud / naam)
    for kind in list(klaar.iterdir()):
        if (DATA_DIR / kind.name).exists():
            shutil.move(str(DATA_DIR / kind.name), oud / kind.name)
        shutil.move(str(kind), DATA_DIR / kind.name)
    klaar.rmdir()
    return str(oud)


def main():
    parser = argparse.ArgumentParser(description="Lokale back-up en offline herstel")
    opdrachten = parser.add_subparsers(dest="opdracht", required=True)
    opdrachten.add_parser("create")
    herstel = opdrachten.add_parser("restore")
    herstel.add_argument("archief", type=Path)
    herstel.add_argument("bestemming", type=Path)
    args = parser.parse_args()
    if args.opdracht == "create":
        print(maak_backup())
    else:
        manifest = herstel_backup(args.archief, args.bestemming)
        print(f"Hersteld: {len(manifest['files'])} bestanden. Start met DATA_DIR={args.bestemming.resolve()}")


if __name__ == "__main__":
    main()
