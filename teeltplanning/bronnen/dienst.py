"""Externe bronnen bijwerken (§10). Elke bron staat standaard **uit**, heeft een eigen schakelaar, een tijdslimiet,
nette foutafhandeling en een zichtbaar laatste succesvolle ophaalmoment. Valt een bron weg, dan werkt de app door
met wat er al is (en de lichtsom wordt onbekend waar straling ontbreekt).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import instellingen
from ..auth import audit
from ..db.basis import SessionLocal
from ..db.modellen import BronGeheim, Bronstatus, Energieprijs, Weeknormaal, Weerdag, utcnu
from ..kern.licht import weeknormalen
from . import energyzero, kluis, knmi, open_meteo
from .http import BronFout

log = logging.getLogger("teeltplanning.bronnen")
BRONNEN = {
    "open_meteo": ("bronnen.open_meteo_aan", "Open-Meteo (historie en verwachting van de straling)"),
    "knmi": ("bronnen.knmi_aan", "KNMI Data Platform (gemeten straling)"),
    "energyzero": ("bronnen.energyzero_aan", "EnergyZero (dagvooruitprijzen stroom)"),
}
JAAR_STAP = 365  # dagen per verzoek bij het ophalen van historie


def _status(db: Session, bron: str) -> Bronstatus:
    rij = db.get(Bronstatus, bron)
    if rij is None:
        rij = Bronstatus(bron=bron)
        db.add(rij)
    return rij


def statussen(db: Session) -> list[dict]:
    snap = instellingen.laad(db)
    uit = []
    for bron, (sleutel, label) in BRONNEN.items():
        rij = db.get(Bronstatus, bron)
        uit.append({"bron": bron, "label": label, "aan": bool(snap[sleutel]), "instelling": sleutel,
                    "laatst_gelukt": rij.laatst_gelukt if rij else None,
                    "laatst_geprobeerd": rij.laatst_geprobeerd if rij else None,
                    "melding": rij.melding if rij else None, "aantal": rij.aantal if rij else None,
                    "geheim": db.get(BronGeheim, bron) is not None})  # fmt: skip
    return uit


# --- Geheimen ------------------------------------------------------------------------------------------------
def zet_geheim(db: Session, gebruiker, bron: str, waarde: str) -> None:
    rij = db.get(BronGeheim, bron)
    if not waarde.strip():
        if rij is not None:
            db.delete(rij)
        audit(db, gebruiker, "bronsleutel_verwijderd", f"bron:{bron}")
        return
    if rij is None:
        rij = BronGeheim(bron=bron, versleuteld=b"")
        db.add(rij)
    rij.versleuteld = kluis.versleutel(waarde.strip())
    rij.gewijzigd_op = utcnu()
    audit(db, gebruiker, "bronsleutel_gewijzigd", f"bron:{bron}")  # nooit de waarde zelf


def lees_geheim(db: Session, bron: str) -> str | None:
    rij = db.get(BronGeheim, bron)
    return kluis.ontsleutel(rij.versleuteld) if rij else None


# --- Bijwerken ----------------------------------------------------------------------------------------------
def _upsert_weer(db: Session, rijen, soort: str, bron: str) -> int:
    if not rijen:
        return 0
    van, tot = min(r[0] for r in rijen), max(r[0] for r in rijen)
    bestaand = {w.datum: w for w in db.scalars(select(Weerdag).where(Weerdag.soort == soort, Weerdag.bron == bron,
                                                                      Weerdag.datum >= van, Weerdag.datum <= tot))}  # fmt: skip
    for datum, straling, temperatuur in rijen:
        w = bestaand.get(datum) or Weerdag(datum=datum, soort=soort, bron=bron)
        w.straling_j_cm2, w.etmaaltemperatuur, w.opgehaald_op = straling, temperatuur, utcnu()
        db.add(w)
    return len(rijen)


def _open_meteo(db: Session, snap, vandaag: date) -> str:
    breedte, lengte = snap["bedrijf.breedtegraad"], snap["bedrijf.lengtegraad"]
    if breedte is None or lengte is None:
        raise BronFout("Breedte- en lengtegraad van de kas zijn niet ingevuld (Beheer → Instellingen → Bedrijf).")
    tijdslimiet = snap["bronnen.tijdslimiet_s"]
    laatste = db.scalar(select(Weerdag.datum).where(Weerdag.soort == "historie", Weerdag.bron == "open_meteo")
                        .order_by(Weerdag.datum.desc()).limit(1))  # fmt: skip
    begin = date(vandaag.year - snap["bronnen.normalen_jaren"], 1, 1)
    van = max(begin, laatste + timedelta(days=1)) if laatste else begin
    tot = vandaag - timedelta(days=1)
    aantal = 0
    while van <= tot:
        stuk_tot = min(tot, van + timedelta(days=JAAR_STAP - 1))
        rijen = open_meteo.historie(
            snap["bronnen.open_meteo_historie_url"], breedte, lengte, van, stuk_tot, tijdslimiet
        )
        aantal += _upsert_weer(db, [r for r in rijen if r[1] is not None], "historie", "open_meteo")
        van = stuk_tot + timedelta(days=1)
    dagen = snap["licht.verwachting_dagen"]
    if dagen:
        rijen = open_meteo.verwachting(snap["bronnen.open_meteo_verwachting_url"], breedte, lengte, dagen, tijdslimiet)
        db.execute(delete(Weerdag).where(Weerdag.soort == "verwachting", Weerdag.bron == "open_meteo"))
        aantal += _upsert_weer(db, [r for r in rijen if r[0] >= vandaag], "verwachting", "open_meteo")
    db.flush()
    normalen = herbereken_normalen(db, vandaag)
    return f"{aantal} dag(en) bijgewerkt; weeknormalen voor {normalen} weken."


def _knmi(db: Session, snap, vandaag: date) -> str:
    namen = knmi.bestanden(snap["bronnen.knmi_url"], snap["bronnen.knmi_dataset"], snap["bronnen.knmi_versie"],
                           lees_geheim(db, "knmi"), snap["bronnen.tijdslimiet_s"])  # fmt: skip
    if not snap["bronnen.knmi_variabele_straling"]:
        raise BronFout(f"Verbinding en sleutel werken ({len(namen)} bestand(en) gevonden), maar de variabele voor de "
                       "straling is niet geverifieerd (vraag B23); er worden geen waarden ingelezen.")  # fmt: skip
    knmi.waarden_ophalen()
    return ""  # pragma: no cover


def _energyzero(db: Session, snap, vandaag: date) -> str:
    laatste = db.scalar(select(Energieprijs.datum).order_by(Energieprijs.datum.desc()).limit(1))
    van = laatste + timedelta(days=1) if laatste else vandaag - timedelta(days=JAAR_STAP // 12)
    prijzen = energyzero.dagprijzen(
        snap["bronnen.energyzero_url"], van, vandaag + timedelta(days=1), snap["bronnen.tijdslimiet_s"]
    )
    for datum, prijs in prijzen.items():
        rij = db.get(Energieprijs, datum) or Energieprijs(datum=datum)
        rij.eur_per_mwh, rij.opgehaald_op = prijs, utcnu()
        db.add(rij)
    return f"{len(prijzen)} dagprijs(prijzen) bijgewerkt."


UITVOERDERS = {"open_meteo": _open_meteo, "knmi": _knmi, "energyzero": _energyzero}


def ververs(db: Session, bron: str, vandaag: date | None = None, ook_als_uit: bool = False) -> tuple[bool, str]:
    """Haal één bron op. Geeft (gelukt, melding); fouten worden een melding, nooit een crash."""
    snap = instellingen.laad(db)
    vandaag = vandaag or date.today()
    status = _status(db, bron)
    if not snap[BRONNEN[bron][0]] and not ook_als_uit:
        return False, "Deze bron staat uit."
    status.laatst_geprobeerd = utcnu()
    try:
        melding = UITVOERDERS[bron](db, snap, vandaag)
    except (BronFout, ValueError) as fout:
        db.rollback()
        status = _status(db, bron)
        status.laatst_geprobeerd, status.melding = utcnu(), str(fout)
        db.commit()
        return False, str(fout)
    status.laatst_gelukt, status.melding = utcnu(), melding
    db.commit()
    return True, melding


def ververs_als_nodig() -> None:
    """Achtergrondtaak: elke ingeschakelde bron één keer per ophaalinterval."""
    with SessionLocal() as db:
        snap = instellingen.laad(db)
        interval = timedelta(hours=snap["bronnen.ophaalinterval_uren"])
        nu = datetime.now(UTC).replace(tzinfo=None)
        for bron, (sleutel, _) in BRONNEN.items():
            if not snap[sleutel]:
                continue
            rij = db.get(Bronstatus, bron)
            if rij is None or rij.laatst_geprobeerd is None or nu - rij.laatst_geprobeerd >= interval:
                gelukt, melding = ververs(db, bron)
                if not gelukt:
                    log.warning("Bron %s: %s", bron, melding)


# --- Weeknormalen -------------------------------------------------------------------------------------------
def herbereken_normalen(db: Session, vandaag: date) -> int:
    snap = instellingen.laad(db)
    dagen: dict[date, float] = {}
    for w in db.scalars(select(Weerdag).where(Weerdag.soort.in_(("historie", "gemeten")))):
        if w.straling_j_cm2 is not None and (w.soort == "gemeten" or w.datum not in dagen):
            dagen[w.datum] = w.straling_j_cm2
    percentielen = tuple(float(p) for p in snap["bronnen.percentielen"])
    normalen = weeknormalen(
        dagen, percentielen, snap["bronnen.normalen_jaren"], snap["bronnen.normalen_min_jaren"], vandaag
    )
    db.execute(delete(Weeknormaal))
    for week, (laag, midden, hoog, jaren) in normalen.items():
        db.add(Weeknormaal(week=week, p10=laag, p50=midden, p90=hoog, jaren=jaren))
    return sum(1 for n in normalen.values() if n[1] is not None)
