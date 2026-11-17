"""Importstroom voor `Belichting winterronde.xlsx` (§7.2), zoals de programma-import: uploaden → origineel met
checksum opslaan → lezen als **achtergrondtaak met voortgang** → controlevoorbeeld met meldingen → jaar van de
weekuren en van het teeltschema invullen → expliciet bevestigen → in één transactie wegschrijven.

De dagaggregaten van het tabblad `data` worden bij het lezen berekend en als JSON naast het origineel bewaard,
zodat bevestigen het grote blad niet opnieuw hoeft te lezen. Een tweede identieke bevestiging maakt niets dubbel.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import instellingen
from ..auth import audit
from ..db.basis import DATA_DIR, SessionLocal
from ..db.modellen import (
    BelichtingDag,
    BelichtingWeek,
    Gebruiker,
    Importbatch,
    LbeIjkpunt,
    TeeltschemaRegel,
    utcnu,
)
from . import belichting
from .dienst import bronmap
from .werkboek import _validate_archive

log = logging.getLogger("teeltplanning.belichting")
_UITVOERDER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="belichting")
_BEZIG: dict[int, object] = {}
_POGING: dict[int, int] = {}  # alleen de laatst gestarte leesbeurt van een import mag het voorbeeld schrijven
_SLOT = threading.Lock()


def _pad(batch: Importbatch) -> Path:
    pad = Path(batch.pad)
    if pad.is_absolute() or ".." in pad.parts:
        raise ValueError("Ongeldig bronpad")
    return DATA_DIR / pad


def _dagenbestand(batch: Importbatch) -> Path:
    return bronmap() / f"belichting-{batch.id}-dagen.json"


def sla_upload_op(db: Session, gebruiker: Gebruiker, bestandsnaam: str, inhoud: bytes) -> Importbatch:
    snap = instellingen.laad(db)
    if len(inhoud) > snap["import.max_upload_mb"] * 1024 * 1024:
        raise ValueError("Bestand is groter dan de ingestelde maximale uploadgrootte.")
    if not bestandsnaam.lower().endswith(".xlsx"):
        raise ValueError("Alleen .xlsx-werkboeken (geen macro's, geen .xls).")
    sha = hashlib.sha256(inhoud).hexdigest()
    pad = bronmap() / f"belichting-{sha}.xlsx"
    if not pad.exists():
        pad.write_bytes(inhoud)
        pad.chmod(0o600)
    _validate_archive(pad, snap["import.max_upload_mb"] * 1024 * 1024, snap["import.max_uitgepakt_mb"] * 1024 * 1024)
    batch = Importbatch(soort="belichting", bestandsnaam=Path(bestandsnaam).name[:300], pad=str(pad.relative_to(DATA_DIR)),
                        sha256=sha, status="lezen", aangemaakt_door=gebruiker.id, keuzes={},
                        voorbeeld={"voortgang": 0})  # fmt: skip
    db.add(batch)
    db.flush()
    audit(db, gebruiker, "import_geupload", f"importbatch:{batch.id}", {"bestand": batch.bestandsnaam, "sha256": sha,
                                                                         "soort": "belichting"})  # fmt: skip
    return batch


def start_lezen(batch_id: int) -> None:
    """Lees het werkboek op de achtergrond (één tegelijk); de webapp blijft bruikbaar."""
    with _SLOT:
        _POGING[batch_id] = _POGING.get(batch_id, 0) + 1
        _BEZIG[batch_id] = _UITVOERDER.submit(_lees, batch_id, _POGING[batch_id])


def wacht(batch_id: int, tijdslimiet: float = 600) -> None:
    while True:
        taak = _BEZIG.get(batch_id)
        if taak is None:
            return
        taak.result(timeout=tijdslimiet)
        if _BEZIG.get(batch_id) is taak:  # geen nieuwere leesbeurt gestart
            return


def _voortgang(batch_id: int, regels: int, poging: int = 0) -> None:
    if poging and not _actueel(batch_id, poging):
        return
    with SessionLocal() as db:
        batch = db.get(Importbatch, batch_id)
        batch.voorbeeld = {**(batch.voorbeeld or {}), "voortgang": regels}
        db.commit()


def _actueel(batch_id: int, poging: int) -> bool:
    return _POGING.get(batch_id) == poging


def _lees(batch_id: int, poging: int = 0) -> None:
    if poging and not _actueel(batch_id, poging):
        return  # er is intussen opnieuw gestart
    with SessionLocal() as db:
        batch = db.get(Importbatch, batch_id)
        inst = instellingen.laad(db).waarden
        pad = _pad(batch)
    voorbeeld: dict = {"meldingen": []}
    try:
        voorbeeld["weekuren"] = belichting.weekuren(pad, inst)
        voorbeeld["kaptabel"] = belichting.kaptabel(pad, inst)
        voorbeeld["kostenvergelijking"] = belichting.kostenvergelijking(pad, inst)
        voorbeeld["teeltschema"] = {"regels": belichting.teeltschema(pad, inst)}
        if inst["import.data_tijdstempel"] is None:
            voorbeeld["data"] = None
            voorbeeld["meldingen"].append("Het tabblad data is niet gelezen: stel eerst in of de tijdstempel het begin of "
                                          "het eind van het interval is (vraag B21), en lees daarna opnieuw.")  # fmt: skip
        else:
            data = belichting.dimgewogen_per_dag(pad, inst["import.data_interval_minuten"], inst["import.data_tijdstempel"],
                                                 lambda n: _voortgang(batch_id, n, poging))  # fmt: skip
            if poging and not _actueel(batch_id, poging):
                return
            with SessionLocal() as db:
                batch = db.get(Importbatch, batch_id)
                _dagenbestand(batch).write_text(json.dumps(data["dagen"]), encoding="utf-8")
            voorbeeld["data"] = data["rapport"] | {"tijdstempel": inst["import.data_tijdstempel"],
                                                   "interval": inst["import.data_interval_minuten"]}  # fmt: skip
        status = "voorbeeld"
    except Exception as fout:  # noqa: BLE001 - elke fout wordt een melding in het voorbeeld
        log.exception("Belichting lezen mislukt")
        voorbeeld["meldingen"].append(f"Lezen mislukt: {fout}")
        status = "mislukt"
    if poging and not _actueel(batch_id, poging):
        return
    with SessionLocal() as db:
        batch = db.get(Importbatch, batch_id)
        if batch.status == "bevestigd":
            return
        batch.voorbeeld = voorbeeld | {"voortgang": (voorbeeld.get("data") or {}).get("regels", 0), "klaar": True}
        batch.status = status
        db.commit()


def zet_keuzes(batch: Importbatch, keuzes: dict[str, int | None]) -> None:
    huidig = dict(batch.keuzes or {})
    for sleutel, jaar in keuzes.items():
        if jaar is None:
            huidig.pop(sleutel, None)
        elif not 1900 <= jaar <= 2200:
            raise ValueError(f"Ongeldig jaar {jaar}.")
        else:
            huidig[sleutel] = jaar
    batch.keuzes = huidig


def bevestig(db: Session, gebruiker: Gebruiker, batch: Importbatch) -> dict:
    if batch.status == "bevestigd":
        return {"al_bevestigd": True}
    if batch.status != "voorbeeld":
        raise ValueError("Het werkboek is nog niet (goed) gelezen.")
    v = batch.voorbeeld or {}
    keuzes = batch.keuzes or {}
    tellingen = {"weekuren": 0, "ijkpunten": 0, "teeltschema": 0, "belichtingsdagen": 0, "overgeslagen": []}
    # Weekuren: alleen met het ISO-jaar van de eerste weekkolom (vraag 2)
    if keuzes.get("jaar_weekuren"):
        jaren = belichting.jaren_van_weken(v["weekuren"]["weken"], keuzes["jaar_weekuren"])
        bestaand = {(r.module, r.iso_jaar, r.week): r for r in db.scalars(select(BelichtingWeek))}
        for regel in v["weekuren"]["regels"]:
            if regel["uren"] is None or regel["buiten_grenzen"]:
                continue
            sleutel = (regel["module"], jaren[regel["week"]], regel["week"])
            rij = bestaand.get(sleutel) or BelichtingWeek(module=sleutel[0], iso_jaar=sleutel[1], week=sleutel[2])
            rij.uren, rij.celsoort, rij.importbatch_id = regel["uren"], regel["soort"], batch.id
            db.add(rij)
            tellingen["weekuren"] += 1
    else:
        tellingen["overgeslagen"].append("weekuren (ISO-jaar van de eerste week niet ingevuld)")
    # Kaptabel: de nieuwste import vervangt de vorige ijkpunten
    db.execute(delete(LbeIjkpunt))
    for regel in v.get("kaptabel", []):
        plant = date.fromisoformat(regel["plantdatum"]) if regel["plantdatum"] else None
        db.add(
            LbeIjkpunt(
                importbatch_id=batch.id, rij=regel["rij"], kap=regel["kap"], plantdatum=plant, waarden=regel["waarden"]
            )
        )
        tellingen["ijkpunten"] += 1
    # Teeltschema: alleen met het ISO-jaar van de eerste regel
    if keuzes.get("jaar_teeltschema"):
        db.execute(delete(TeeltschemaRegel))
        for regel in belichting.teeltschema_datums(v["teeltschema"]["regels"], keuzes["jaar_teeltschema"]):
            datums = {k: d.isoformat() if d else None for k, d in regel["datums"].items()}
            db.add(TeeltschemaRegel(importbatch_id=batch.id, rij=regel["rij"], kap=regel["kap"],
                                    plantdatum=regel["datums"]["plantdatum"], waarden=regel["waarden"] | {"datums": datums}))  # fmt: skip
            tellingen["teeltschema"] += 1
    else:
        tellingen["overgeslagen"].append("teeltschema (ISO-jaar van de eerste regel niet ingevuld)")
    # Dimgewogen uren per dag
    if v.get("data") is not None:
        bestand = _dagenbestand(batch)
        bestaand = {(r.module, r.datum): r for r in db.scalars(select(BelichtingDag))}
        for module, datum, dimgewogen, branduren, volledig, kwaliteit in json.loads(
            bestand.read_text(encoding="utf-8")
        ):
            d = date.fromisoformat(datum)
            rij = bestaand.get((module, d)) or BelichtingDag(module=module, datum=d)
            rij.dimgewogen_uren, rij.branduren, rij.volledig, rij.kwaliteit = dimgewogen, branduren, volledig, kwaliteit
            rij.importbatch_id = batch.id
            db.add(rij)
            tellingen["belichtingsdagen"] += 1
    else:
        tellingen["overgeslagen"].append("tabblad data (niet gelezen)")
    batch.status = "bevestigd"
    batch.bevestigd_door = gebruiker.id
    batch.bevestigd_op = utcnu()
    audit(db, gebruiker, "import_bevestigd", f"importbatch:{batch.id}", tellingen | {"keuzes": keuzes})
    db.flush()
    return tellingen
