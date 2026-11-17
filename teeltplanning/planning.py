"""Planningsdienst: database ⇄ rekenkern.

Laadt invoer en parameters uit de database en het instellingenregister, roept `kern.bereken_plan` aan en
bewaart het resultaat in een cache die vervalt zodra er iets relevants wordt opgeslagen (teelten,
veldwaarden, kappen, instellingen). Wijzigingen van veldwaarden lopen via deze module: opslaan, auditlog,
herberekenen.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from . import instellingen
from .auth import audit
from .db.modellen import Gebruiker, Instelling, Kap, Ronde, Scenario, Teelt, Veldwaarde, utcnu
from .kern import bereken_plan
from .kern.kalender import lees_week_dag
from .kern.typen import (
    DATUMVELDEN,
    GEHELE_VELDEN,
    VELDEN,
    Blokkade,
    KapInvoer,
    KernInstellingen,
    Parameters,
    Plan,
    PlanInvoer,
    TeeltInvoer,
    Uitvalregel,
    Veld,
)


class Ongeldig(ValueError):
    pass


# --- Cache ---------------------------------------------------------------------------------------------------
class _Versie:
    def __init__(self):
        self.nummer = 0
        self.slot = threading.Lock()

    def verhoog(self):
        with self.slot:
            self.nummer += 1


VERSIE = _Versie()
_cache: dict[int, tuple[int, Any]] = {}
_RELEVANT = (Teelt, Veldwaarde, Kap, Ronde, Scenario, Instelling)


@event.listens_for(Session, "after_flush")
def _markeer(session, _context):
    for obj in (*session.new, *session.dirty, *session.deleted):
        if isinstance(obj, _RELEVANT):
            session.info["plan_gewijzigd"] = True
            VERSIE.verhoog()
            return


@event.listens_for(Session, "after_commit")
def _na_vastleggen(session):
    session.info.pop("plan_gewijzigd", None)


@event.listens_for(Session, "after_rollback")
def _na_terugdraaien(session):
    # Een berekening op later teruggedraaide wijzigingen mag niet in de cache blijven.
    if session.info.pop("plan_gewijzigd", False):
        VERSIE.verhoog()


# --- Waarden (JSON ⇄ Python) ---------------------------------------------------------------------------------
def naar_json(veld: str, waarde: Any) -> Any:
    if waarde is None:
        return None
    if veld in DATUMVELDEN:
        return waarde.isoformat()
    return waarde


def uit_json(veld: str, waarde: Any) -> Any:
    if waarde is None:
        return None
    if veld in DATUMVELDEN:
        return date.fromisoformat(waarde)
    if veld in GEHELE_VELDEN:
        return int(waarde)
    return float(waarde)


# --- Parameters uit het register -----------------------------------------------------------------------------
def kern_instellingen(snap: instellingen.Snapshot) -> KernInstellingen:
    blokkades = tuple(
        Blokkade(date.fromisoformat(r["van"]), date.fromisoformat(r["tot"]), r.get("kap"))
        for r in snap["kalender.blokkadeperioden"]
    )
    uitval = tuple(Uitvalregel(r["week_van"], r["week_tot"], r["fractie"]) for r in snap["gewas.uitval_per_oogstweek"])
    grenzen = {
        "ld_dagen": (snap["keten.ld_dagen_min"], snap["keten.ld_dagen_max"]),
        "reactietijd": (snap["keten.reactietijd_min"], snap["keten.reactietijd_max"]),
        "verlenging": (snap["keten.verlenging_min"], snap["keten.verlenging_max"]),
        "dichtheid": (snap["gewas.dichtheid_min"], snap["gewas.dichtheid_max"]),
    }
    return KernInstellingen(
        licht_aan_na_licht_uit_dagen=snap["keten.licht_aan_na_licht_uit_dagen"],
        plantdagen=frozenset(snap["kalender.plantdagen"] or ()),
        wisseltijd_dagen=snap["kalender.wisseltijd_dagen"],
        plantdatum_volgt_keten=bool(snap["keten.plantdatum_volgt_keten"]),
        blokkades=blokkades,
        oppervlak_per_kap=tuple(snap.reeks("bedrijf.oppervlak_per_kap_m2")),
        uitval_per_oogstweek=uitval,
        grenzen=grenzen,
        overlap_blokkeert=snap["keten.overlap"] == "blokkeren",
    )


@dataclass
class Geladen:
    scenario: Scenario
    rondes: dict[int, Ronde]  # id → ronde
    teelten: dict[int, Teelt]
    veldwaarden: dict[tuple[int, str], Veldwaarde]
    kappen: dict[int, Kap]
    invoer: PlanInvoer
    parameters: Parameters
    snap: instellingen.Snapshot


def laad(db: Session, scenario_id: int) -> Geladen:
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise Ongeldig("Scenario bestaat niet.")
    rondes = {r.id: r for r in db.scalars(select(Ronde).where(Ronde.scenario_id == scenario_id))}
    teelten = {t.id: t for t in db.scalars(select(Teelt).where(Teelt.ronde_id.in_(list(rondes))))} if rondes else {}
    veldwaarden = (
        {(v.teelt_id, v.veld): v for v in db.scalars(select(Veldwaarde).where(Veldwaarde.teelt_id.in_(list(teelten))))}
        if teelten
        else {}
    )
    kappen = {k.nummer: k for k in db.scalars(select(Kap))}
    snap = instellingen.laad(db)
    invoer = []
    for t in teelten.values():
        velden = {}
        for naam in VELDEN:
            v = veldwaarden.get((t.id, naam))
            if v is not None:
                velden[naam] = Veld(voorstel=uit_json(naam, v.voorstel), handmatig=uit_json(naam, v.handmatig))
        invoer.append(
            TeeltInvoer(id=t.id, kap=t.kap, ronde=rondes[t.ronde_id].nummer, volgnummer=t.volgnummer, velden=velden)
        )
    parameters = Parameters(
        kappen={k.nummer: KapInvoer(k.nummer, k.oppervlak_m2, k.wisseltijd_dagen, k.actief) for k in kappen.values()},
        instellingen=kern_instellingen(snap),
    )
    return Geladen(scenario, rondes, teelten, veldwaarden, kappen, PlanInvoer(tuple(invoer)), parameters, snap)


def bereken(db: Session, scenario_id: int) -> tuple[Geladen, Plan]:
    versie = VERSIE.nummer
    gecacht = _cache.get(scenario_id)
    if gecacht and gecacht[0] == versie:
        return gecacht[1]
    geladen = laad(db, scenario_id)
    resultaat = (geladen, bereken_plan(geladen.invoer, geladen.parameters))
    _cache[scenario_id] = (versie, resultaat)
    return resultaat


# --- Velden wijzigen ----------------------------------------------------------------------------------------
def lees_invoer(veld: str, tekst: str, nabij: date) -> Any:
    """Zet schermtekst om naar een waarde; `ValueError` met een begrijpelijke melding."""
    tekst = (tekst or "").strip()
    if tekst == "":
        return None
    if veld in DATUMVELDEN:
        return lees_week_dag(tekst, nabij)
    try:
        getal = float(tekst.replace(",", "."))
    except ValueError as exc:
        raise Ongeldig("Vul een getal in.") from exc
    if veld in GEHELE_VELDEN:
        if not getal.is_integer():
            raise Ongeldig("Vul een geheel aantal dagen in.")
        return int(getal)
    if veld == "uitval" and getal > 1:
        getal = getal / 100  # 8 → 0,08: de gebruiker typt een percentage
    return getal


def zet_handmatig(db: Session, gebruiker: Gebruiker, teelt: Teelt, veld: str, waarde: Any, reden: str | None,
                  bron: str = "gebruiker", bestaand: Veldwaarde | None = None) -> tuple[Any, Any]:  # fmt: skip
    """Sla een handmatige waarde op (None = terug naar voorstel). Geeft (oud, nieuw) terug en schrijft audit."""
    if veld not in VELDEN:
        raise Ongeldig("Onbekend veld.")
    rij = (
        bestaand
        if bestaand is not None
        else db.scalar(select(Veldwaarde).where(Veldwaarde.teelt_id == teelt.id, Veldwaarde.veld == veld))
    )
    if rij is None:
        rij = Veldwaarde(teelt_id=teelt.id, veld=veld)
        db.add(rij)
    oud = rij.handmatig
    nieuw = naar_json(veld, waarde)
    if oud == nieuw and (nieuw is None or rij.handmatig_bron == bron):
        return oud, nieuw
    rij.handmatig = nieuw
    rij.handmatig_bron = bron if nieuw is not None else None
    rij.gewijzigd_door = gebruiker.id if gebruiker else None
    rij.gewijzigd_op = utcnu()
    rij.reden = (reden or "").strip()[:2000] or None
    teelt.bijgewerkt_op = utcnu()
    if bron == "gebruiker":
        audit(db, gebruiker, "veld_terug_naar_voorstel" if nieuw is None else "veld_gewijzigd", f"teelt:{teelt.id}:{veld}",
              {"oud": oud, "nieuw": nieuw, "reden": rij.reden})  # fmt: skip
    return oud, nieuw


def controleer_overlap(db: Session, scenario_id: int, teelt_id: int) -> str | None:
    """Bij de instelling 'blokkeren': melding als deze teelt na opslaan overlapt."""
    _, plan = bereken(db, scenario_id)
    uitkomst = plan.teelten.get(teelt_id)
    if uitkomst and any(m.code == "overlap" for m in uitkomst.meldingen):
        return next(m.tekst for m in uitkomst.meldingen if m.code == "overlap")
    return None


def vastgesteld_of_eerste(db: Session) -> Scenario | None:
    return db.scalar(select(Scenario).where(Scenario.status == "vastgesteld")) or db.scalar(
        select(Scenario).order_by(Scenario.id)
    )
