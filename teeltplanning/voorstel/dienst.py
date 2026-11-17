"""Trainen en toetsen als achtergrondtaak (§8.3), en de actieve modelversie voor de planning.

* Gegevens verzamelen (database + plan) gebeurt in een achtergrondthread; het rekenwerk (backtest per doel en
  trede) in een **apart proces** (`spawn`), zodat de webapp niet wacht en de GIL niet deelt. Eén training
  tegelijk. Geen GPU.
* De nieuwste versie met status `klaar` levert de voorstellen; een mislukte training laat de vorige staan.
* Wijzigingen in de groepen *Voorstellen* en *Model* gelden na opnieuw trainen (het artefact bewaart de
  instellingen waarmee getoetst is); alleen de schakelaars per veld gelden direct.
"""

from __future__ import annotations

import logging
import multiprocessing
import threading
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures import TimeoutError as Tijdslimiet
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import instellingen
from ..auth import audit
from ..db.basis import SessionLocal
from ..db.modellen import Modelversie, Ras, utcnu
from . import historie, model

log = logging.getLogger("teeltplanning.model")
_DRAAD = ThreadPoolExecutor(max_workers=1, thread_name_prefix="model")
_SLOT = threading.Lock()
_VOORSPELLERS: dict[int, model.Voorspeller] = {}
MONOTOON = {"stijgend": 1, "dalend": -1}


def model_instellingen(db: Session, snap: instellingen.Snapshot) -> model.ModelInstellingen:
    try:
        laag, hoog = (float(p) for p in snap["voorstel.band_percentielen"] or [])
    except ValueError as fout:
        raise ValueError("Breedte van de bandbreedte: vul precies een onder- en een bovenpercentiel in.") from fout
    return model.ModelInstellingen(
        venster=snap["voorstel.seizoensvenster_weken"], terugkijk=snap["voorstel.terugkijk_jaren"],
        min_waarnemingen=snap["voorstel.min_waarnemingen"], band=(laag, hoog), treden=tuple(snap["model.treden"]),
        kenmerken_ridge=tuple(snap["model.kenmerken_ridge"]), kenmerken_bomen=tuple(snap["model.kenmerken_bomen"]),
        monotoon={k: MONOTOON[v] for k, v in (snap["model.monotoon"] or {}).items()},
        drempel_pct=snap["model.drempel_gevalideerd"], backtest_jaren=snap["model.backtest_jaren"],
        min_toets=snap["model.min_toetsteelten"], ridge_alpha=snap["model.ridge_alpha"],
        bomen_aantal=snap["model.bomen_aantal"], bomen_leersnelheid=snap["model.bomen_leersnelheid"],
        bomen_min_blad=snap["model.bomen_min_blad"], lbe_min_waarnemingen=snap["gewas.min_waarnemingen_lbe"],
        temperatuur_aan=bool(snap["voorstel.temperatuurcorrectie"]),
        referentietemperatuur=snap["voorstel.referentietemperatuur"],
        vergelijkbaar=snap["voorstel.aantal_vergelijkbaar"],
        gevoeligheid={r.code: r.temperatuurgevoeligheid_dagen_per_graad for r in db.scalars(select(Ras))},
    )  # fmt: skip


def bezig(db: Session) -> Modelversie | None:
    return db.scalar(select(Modelversie).where(Modelversie.status == "bezig").order_by(Modelversie.id.desc()))


def actief(db: Session) -> Modelversie | None:
    return db.scalar(select(Modelversie).where(Modelversie.status == "klaar").order_by(Modelversie.id.desc()))


def start_training(db: Session, gebruiker, extra_bron=None) -> Modelversie:
    """Maak een versie met status `bezig` en start op de achtergrond; komt meteen terug."""
    with _SLOT:
        lopend = bezig(db)
        if lopend is not None:
            return lopend
        nummer = (db.scalar(select(Modelversie.id).order_by(Modelversie.id.desc())) or 0) + 1
        versie = Modelversie(
            versie=f"m{nummer}-{date.today().isoformat()}", gestart_door=gebruiker.id if gebruiker else None
        )
        db.add(versie)
        db.flush()
        audit(db, gebruiker, "model_training_gestart", f"modelversie:{versie.id}", {"versie": versie.versie})
        db.commit()
        _DRAAD.submit(_train_veilig, versie.id, extra_bron)
        return versie


def _train_veilig(versie_id: int, extra_bron=None) -> None:
    try:
        _train(versie_id, extra_bron)
    except Exception as fout:  # noqa: BLE001 - elke fout wordt een melding bij de versie
        log.exception("Training mislukt")
        with SessionLocal() as db:
            versie = db.get(Modelversie, versie_id)
            if versie is not None:
                versie.status, versie.klaar_op, versie.melding = "mislukt", utcnu(), f"Training mislukt: {fout}"
                db.commit()


def _train(versie_id: int, extra_bron=None) -> None:
    with SessionLocal() as db:
        versie = db.get(Modelversie, versie_id)
        snap = instellingen.laad(db)
        if extra_bron is None:
            from ..bronnen import koppelbronnen

            extra = koppelbronnen.waarnemingen(db)  # gekoppelde teelten uit Gewichtsverwachting (fase 6)
        else:
            extra = extra_bron(db)
        waarnemingen, herkomst = historie.verzamel(db, extra=extra)
        inst = model_instellingen(db, snap)
        tijdslimiet = snap["model.max_rekentijd_s"]
        naam = versie.versie
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=1, mp_context=context) as proces:
        taak = proces.submit(model.train, [w.naar_dict() for w in waarnemingen], inst.naar_dict(), naam)
        try:
            artefact = taak.result(timeout=tijdslimiet)
        except Tijdslimiet:
            for p in list(getattr(proces, "_processes", {}).values()):
                p.terminate()
            raise RuntimeError(f"afgebroken na {tijdslimiet} s (instelling Maximale rekentijd training)") from None
    with SessionLocal() as db:
        versie = db.get(Modelversie, versie_id)
        versie.artefact = artefact
        versie.herkomst = herkomst
        versie.aantal_waarnemingen = len(waarnemingen)
        versie.status, versie.klaar_op = "klaar", utcnu()
        gekozen = {d: r["gekozen"] for d, r in artefact["backtest"].items()}
        versie.melding = (f"{len(waarnemingen)} teelt(en) uit de historie; gekozen trede per doel: "
                          + ", ".join(f"{d} {t}" for d, t in gekozen.items()) + ".")  # fmt: skip
        audit(
            db, None, "model_training_klaar", f"modelversie:{versie.id}", {"versie": versie.versie, "gekozen": gekozen}
        )
        db.commit()


def wacht(timeout: float | None = None) -> None:
    """Wacht tot de lopende training klaar is (tests en de CLI)."""
    _DRAAD.submit(lambda: None).result(timeout=timeout)


def voorspeller(db: Session, snap: instellingen.Snapshot, temperaturen: dict | None = None) -> model.Voorspeller | None:
    versie = actief(db)
    if versie is None or not versie.artefact:
        return None
    basis = _VOORSPELLERS.get(versie.id)
    if basis is None:
        basis = model.Voorspeller(versie.artefact, {})
        _VOORSPELLERS.clear()
        _VOORSPELLERS[versie.id] = basis
    aan = {veld: bool(snap[f"voorstel.{veld}_aan"]) for veld in model.DOELEN}
    return basis.met(aan, temperaturen)


def hertrain_als_nodig() -> None:
    """Achtergrondtaak: automatisch hertrainen als de instelling een interval heeft (standaard handmatig)."""
    with SessionLocal() as db:
        uren = instellingen.laad(db)["model.hertrainen_uren"]
        if not uren or bezig(db) is not None:
            return
        laatste = db.scalar(select(Modelversie).order_by(Modelversie.id.desc()))
        nu = datetime.now(UTC).replace(tzinfo=None)
        if laatste is None or nu - laatste.gestart_op >= timedelta(hours=uren):
            start_training(db, None)
