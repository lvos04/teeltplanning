"""Bedrijfskoppeling API v1 aanbieden (BOUWPLAN §11.1, contract in docs/koppeling-api.md), slug `teeltplanning`.

* Alleen GET, `Authorization: Bearer bk_…`; alleen de SHA-256-hash en een voorvoegsel van een sleutel worden
  bewaard. Volgorde van de controles: koppeling uit → 403 `koppeling_uitgeschakeld` (vóór alles, ook zonder
  sleutel en bij ongeldige parameters); sleutel → 401; te veel verzoeken → 429; onbekende dataset → 404;
  ongeldige parameter → 422.
* Alleen het **vastgestelde** scenario wordt gedeeld; zonder vastgesteld plan zijn de datasets leeg en de KPI's
  onbekend. Ontbrekende waarde = `null`. Geen vrije tekst en geen gebruikersnamen.
* De schakelaar staat in *Beheer → Koppelingen → Bedrijfskoppeling* (instelling `koppeling.ingeschakeld`,
  standaard uit).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from collections import deque
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import __version__, auth, instellingen, plannen, planning
from .db.basis import get_db
from .db.modellen import (
    Bronstatus,
    Gebruiker,
    Importbatch,
    Instelling,
    Kap,
    Koppelsleutel,
    Modelversie,
    Ronde,
    Scenario,
    Teelt,
    Veldwaarde,
    utcnu,
)
from .kern import afzet as kern_afzet
from .kern import energie as kern_energie
from .kern.plan import maak_rekenaar

APP_SLUG = "teeltplanning"
APP_NAAM = "Teeltplanning"
API_VERSIE = "1"
PREFIX = "/api/koppeling/v1"
SLEUTEL_PREFIX = "bk_"
GEBRUIK_BIJWERKEN_NA = timedelta(minutes=1)
DATASETS = ("planning", "oogstprognose", "energieprognose", "afzetmomenten")
HORIZON_DAGEN = 14
WEEK = 7
LIMIET_STANDAARD, LIMIET_MAX = 100, 500  # contract: limiet 1..500, standaard 100
SCHAKELAAR = "Beheer → Koppelingen → Bedrijfskoppeling"
NL = ZoneInfo("Europe/Amsterdam")


class KoppelingFout(Exception):
    def __init__(self, status: int, fout: str, **extra):
        self.status, self.fout, self.extra = status, fout, extra


async def _fout_handler(request: Request, fout: KoppelingFout):
    koppen = {"WWW-Authenticate": "Bearer"} if fout.status == 401 else None
    if fout.status == 429:
        koppen = {"Retry-After": "60"}
    return JSONResponse(status_code=fout.status, content={"fout": fout.fout, **fout.extra}, headers=koppen)


def sleutel_hash(sleutel: str) -> str:
    return hashlib.sha256(sleutel.encode()).hexdigest()


def nieuwe_sleutel() -> str:
    return SLEUTEL_PREFIX + secrets.token_urlsafe(32)


def tijd(waarde: datetime | None) -> str | None:
    """Opgeslagen tijden zijn naïeve UTC; altijd met zone uitgeven."""
    if waarde is None:
        return None
    if waarde.tzinfo is None:
        waarde = waarde.replace(tzinfo=UTC)
    return waarde.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _datum(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _vandaag() -> date:
    return datetime.now(NL).date()


# --- Snelheidsgrens per sleutel (één proces, dus geheugen volstaat) ----------------------------------------------
_verzoeken: dict[int, deque] = {}
_slot = threading.Lock()


def _binnen_limiet(sleutel_id: int, per_minuut: int) -> bool:
    moment = time.monotonic()
    with _slot:
        venster = _verzoeken.setdefault(sleutel_id, deque())
        while venster and moment - venster[0] >= 60:
            venster.popleft()
        if len(venster) >= per_minuut:
            return False
        venster.append(moment)
        return True


def vereis_koppelsleutel(request: Request, db: Session = Depends(get_db)) -> Koppelsleutel:
    snap = instellingen.laad(db)
    if not snap["koppeling.ingeschakeld"]:
        raise KoppelingFout(403, "koppeling_uitgeschakeld")
    schema, _, sleutel = request.headers.get("authorization", "").partition(" ")
    sleutel = sleutel.strip()
    if schema.lower() != "bearer" or not sleutel.startswith(SLEUTEL_PREFIX) or len(sleutel) > 200:
        raise KoppelingFout(401, "ongeldige_koppelsleutel")
    digest = sleutel_hash(sleutel)
    rij = db.scalar(select(Koppelsleutel).where(Koppelsleutel.sleutel_hash == digest))
    if rij is None or not hmac.compare_digest(rij.sleutel_hash, digest) or rij.ingetrokken_op is not None:
        raise KoppelingFout(401, "ongeldige_koppelsleutel")
    if not _binnen_limiet(rij.id, snap["koppeling.verzoeken_per_minuut"]):
        raise KoppelingFout(429, "te_veel_verzoeken")
    moment = utcnu()
    if rij.laatst_gebruikt_op is None or moment - rij.laatst_gebruikt_op >= GEBRUIK_BIJWERKEN_NA:
        rij.laatst_gebruikt_op = moment
        db.commit()
    return rij


router = APIRouter(prefix=PREFIX, dependencies=[Depends(vereis_koppelsleutel)])
beheer = APIRouter()


def installeer(app: FastAPI) -> None:
    app.add_exception_handler(KoppelingFout, _fout_handler)
    app.include_router(router)
    app.include_router(beheer)


# --- Gedeelde gegevens -------------------------------------------------------------------------------------------
class Gedeeld:
    """Het vastgestelde plan, één keer berekend per verzoek."""

    def __init__(self, db: Session):
        self.db = db
        self.scenario = db.scalar(select(Scenario).where(Scenario.status == "vastgesteld"))
        self.geladen, self.plan = planning.bereken(db, self.scenario.id) if self.scenario else (None, None)
        self.vandaag = _vandaag()
        self._wijziging = None

    @property
    def uitkomsten(self) -> list:
        return sorted(self.plan.teelten.values(), key=lambda u: u.id) if self.plan else []

    def wijziging(self) -> datetime | None:
        """Laatste wijziging die het hele plan kan raken (instellingen, kappen, model, imports, bronnen, vaststellen)."""
        if self._wijziging is None:
            db = self.db
            kandidaten = [
                db.scalar(select(func.max(Instelling.aangemaakt_op))),
                db.scalar(select(func.max(Kap.bijgewerkt_op))),
                db.scalar(select(func.max(Modelversie.klaar_op))),
                db.scalar(select(func.max(Importbatch.bevestigd_op))),
                db.scalar(select(func.max(Bronstatus.laatst_gelukt))),
                self.scenario.vastgesteld_op if self.scenario else None,
                self.scenario.aangemaakt_op if self.scenario else None,
            ]
            self._wijziging = max((k for k in kandidaten if k is not None), default=None)
        return self._wijziging

    def per_kap_wijziging(self) -> dict[int, datetime]:
        """Een wijziging in een teelt schuift de hele kap mee (keten): per kap de laatste teelt- of veldwijziging."""
        if not self.geladen:
            return {}
        ids = list(self.geladen.teelten)
        uit: dict[int, datetime] = {}
        for teelt in self.geladen.teelten.values():
            uit[teelt.kap] = max(uit.get(teelt.kap, teelt.bijgewerkt_op), teelt.bijgewerkt_op)
        for teelt_id, moment in self.db.execute(select(Veldwaarde.teelt_id, func.max(Veldwaarde.gewijzigd_op))
                                                .where(Veldwaarde.teelt_id.in_(ids)).group_by(Veldwaarde.teelt_id)):  # fmt: skip
            if moment is not None:
                kap = self.geladen.teelten[teelt_id].kap
                uit[kap] = max(uit[kap], moment)
        return uit


def _kpi(sleutel, label, waarde, eenheid, status, toelichting=None):
    return {"sleutel": sleutel, "label": label, "waarde": waarde, "eenheid": eenheid, "status": status,
            "toelichting": toelichting}  # fmt: skip


INDICATIEF_VELDEN = ("ld_dagen", "verlenging", "reactietijd", "gewicht", "dichtheid")


def is_indicatief(u) -> bool:
    """Rust een voorstel van deze teelt op een niet-gevalideerd model (of een indicatieve LBE)?"""
    for veld in INDICATIEF_VELDEN:
        if u.bron.get(veld) != "voorstel":
            continue
        toelichting = u.toelichting.get(veld)
        if toelichting is not None:
            if toelichting.status != "gevalideerd":
                return True
        elif veld in ("gewicht", "dichtheid"):
            if not u.lbe or u.lbe.status != "gevalideerd":
                return True
        else:
            return True  # opgeslagen voorstel (import of genereren): geen gevalideerd model
    return False


def kwh_periode(g: Gedeeld, van: date, tot: date) -> tuple[float | None, str | None]:
    if not g.plan:
        return None, "geen vastgesteld plan"
    datums = [d for u in g.uitkomsten for d in (u.effectief["plantdatum"], u.oogst) if d]
    rekenaar = maak_rekenaar(g.geladen.parameters, min(datums), max(datums)) if datums else None
    if rekenaar is None:
        return None, "geen lichtgegevens"
    totaal = 0.0
    for u in g.uitkomsten:
        plant, oogst = u.effectief["plantdatum"], u.oogst
        if plant is None or oogst is None:
            continue
        a, b = max(plant, van), min(oogst, tot)
        if a >= b:
            continue
        kwh, _, zonder, _ = rekenaar.energie_som(u.kap, a, b)
        if zonder:
            return None, "lichtdata of lampgroepperiode onbekend"
        totaal += kwh
    return totaal, None


def samenvatting_data(db: Session) -> dict:
    g = Gedeeld(db)
    snap = instellingen.laad(db)
    vandaag, horizon = g.vandaag, g.vandaag + timedelta(days=HORIZON_DAGEN)
    kpis, meldingen = [], []
    if not g.plan:
        for sleutel, label, eenheid in (("plantingen_komende_14_dagen", "Plantingen komende 14 dagen", "teelten"),
                                        ("oogsten_komende_14_dagen", "Oogsten komende 14 dagen", "teelten"),
                                        ("oogstprognose_deze_week", "Oogstprognose deze week", "takken"),
                                        ("planconflicten", "Planconflicten", "teelten"),
                                        ("voorstellen_indicatief", "Voorstellen op een niet-gevalideerd model", "%"),
                                        ("kwh_komende_week", "Stroom belichting komende week", "kWh")):  # fmt: skip
            kpis.append(_kpi(sleutel, label, None, eenheid, "onbekend", "Er is geen vastgesteld plan."))
        return {"app": APP_SLUG, "gegenereerd_op": tijd(utcnu()), "kpis": kpis, "meldingen": meldingen}
    alle = g.uitkomsten
    plant = [u for u in alle if u.effectief["plantdatum"] and vandaag <= u.effectief["plantdatum"] <= horizon]
    kpis.append(_kpi("plantingen_komende_14_dagen", "Plantingen komende 14 dagen", len(plant), "teelten", "ok",
                     f"Eerste: kap {min(plant, key=lambda u: (u.effectief['plantdatum'], u.kap)).kap} op "
                     f"{min(u.effectief['plantdatum'] for u in plant).isoformat()}" if plant else "Geen planting gepland"))  # fmt: skip
    oogst = [u for u in alle if u.oogst and vandaag <= u.oogst <= horizon]
    kpis.append(_kpi("oogsten_komende_14_dagen", "Oogsten komende 14 dagen", len(oogst), "teelten", "ok",
                     f"Eerste op {min(u.oogst for u in oogst).isoformat()}" if oogst else "Geen oogst gepland"))  # fmt: skip
    iso = vandaag.isocalendar()
    deze_week = [u for u in alle if u.oogst and u.oogst.isocalendar()[:2] == (iso.year, iso.week)]
    if any(u.oogstaantal is None for u in deze_week):
        kpis.append(_kpi("oogstprognose_deze_week", "Oogstprognose deze week", None, "takken", "onbekend",
                         "Oogstaantal onbekend (bijv. oppervlak per kap of uitval niet ingesteld)"))  # fmt: skip
    else:
        kpis.append(_kpi("oogstprognose_deze_week", "Oogstprognose deze week", round(sum(u.oogstaantal for u in deze_week)),
                         "takken", "ok", f"{len(deze_week)} teelt(en) in week {iso.week}"))  # fmt: skip
    open_ = [u for u in alle if u.oogst is None or u.oogst >= vandaag]
    verplicht = ("onbekend_plantdatum", "onbekend_ld_dagen", "onbekend_reactietijd", "onbekend_verlenging")
    conflicten = [u for u in open_ if u.heeft_fout or any(m.code in verplicht for m in u.meldingen)]
    kpis.append(_kpi("planconflicten", "Planconflicten", len(conflicten), "teelten", "actie" if conflicten else "ok",
                     "Overlap, waarde buiten de grenzen of ontbrekende verplichte waarde (lopende en geplande teelten)"))  # fmt: skip
    if conflicten:
        meldingen.append(
            {"niveau": "waarschuwing", "tekst": f"{len(conflicten)} teelt(en) met een planconflict", "sinds": None}
        )
    toekomst = [u for u in alle if u.effectief["plantdatum"] and u.effectief["plantdatum"] >= vandaag]
    if toekomst:
        aandeel = round(sum(1 for u in toekomst if is_indicatief(u)) / len(toekomst) * 100, 1)
        grens = snap["koppeling.indicatief_grens_pct"]
        status = "let_op" if aandeel > (grens if grens is not None else 0) else "ok"
        kpis.append(_kpi("voorstellen_indicatief", "Voorstellen op een niet-gevalideerd model", aandeel, "%", status,
                         f"van {len(toekomst)} geplande teelten" + ("" if grens is not None else "; grens niet ingesteld")))  # fmt: skip
    else:
        kpis.append(_kpi("voorstellen_indicatief", "Voorstellen op een niet-gevalideerd model", None, "%", "onbekend",
                         "Geen geplande teelten"))  # fmt: skip
    kwh, reden = kwh_periode(g, vandaag, vandaag + timedelta(days=WEEK))
    kpis.append(_kpi("kwh_komende_week", "Stroom belichting komende week", None if kwh is None else round(kwh), "kWh",
                     "onbekend" if kwh is None else "ok", reden))  # fmt: skip
    # Meldingen
    nu = utcnu()
    interval = timedelta(hours=snap["bronnen.ophaalinterval_uren"])
    for bron, sleutel in (("open_meteo", "bronnen.open_meteo_aan"), ("knmi", "bronnen.knmi_aan")):
        if not snap[sleutel]:
            continue
        rij = db.get(Bronstatus, bron)
        if rij is None or rij.laatst_gelukt is None or nu - rij.laatst_gelukt > 2 * interval:
            meldingen.append({"niveau": "waarschuwing", "tekst": f"Weerdata van {bron.replace('_', '-')} is verlopen",
                              "sinds": tijd(rij.laatst_gelukt) if rij else None})  # fmt: skip
    zonder_licht = [u for u in open_ if u.lichtsom and not u.lichtsom.bekend
                    and any("branduren" in r for _, _, r in u.lichtsom.ontbrekend)]  # fmt: skip
    if zonder_licht:
        meldingen.append(
            {
                "niveau": "info",
                "tekst": f"{len(zonder_licht)} teelt(en) zonder belichtingsdata (lichtsom onbekend)",
                "sinds": None,
            }
        )
    from .onderhoud import backupstatus

    status = backupstatus(snap["systeem.backup_bewaardagen"])
    if status["nieuwste"] is None:
        meldingen.append({"niveau": "waarschuwing", "tekst": "Nog geen lokale back-up gevonden", "sinds": None})
    elif datetime.now() - status["nieuwste"] > timedelta(hours=2 * snap["systeem.backup_interval_uren"]):
        meldingen.append({"niveau": "kritiek", "tekst": "Laatste lokale back-up is ouder dan twee back-upintervallen",
                          "sinds": tijd(status["nieuwste"].astimezone(UTC).replace(tzinfo=None))})  # fmt: skip
    return {"app": APP_SLUG, "gegenereerd_op": tijd(utcnu()), "kpis": kpis, "meldingen": meldingen}


# --- Datasets ------------------------------------------------------------------------------------------------------
def _parse_sinds(waarde: str | None) -> datetime | None:
    if waarde is None:
        return None
    try:
        # Een niet-geëscapete '+' in de query komt als spatie binnen.
        moment = datetime.fromisoformat(re.sub(r"(T[\d:.]+) (\d{2}:?\d{2})$", r"\1+\2", waarde.strip()))
    except ValueError:
        raise KoppelingFout(422, "ongeldige_parameter", veld="sinds") from None
    if moment.tzinfo is None:
        raise KoppelingFout(422, "ongeldige_parameter", veld="sinds")  # contract: ISO 8601 mét tijdzone
    return moment.astimezone(UTC).replace(tzinfo=None)


def _parse_limiet(waarde: str | None) -> int:
    if waarde is None:
        return LIMIET_STANDAARD
    if not re.fullmatch(r"\d{1,4}", waarde.strip()) or not 1 <= int(waarde) <= LIMIET_MAX:
        raise KoppelingFout(422, "ongeldige_parameter", veld="limiet")
    return int(waarde)


def _parse_cursor(waarde: str | None):
    if not waarde:
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(waarde + "=" * (-len(waarde) % 4)).decode())
    except (ValueError, UnicodeDecodeError, binascii.Error):
        raise KoppelingFout(422, "ongeldige_parameter", veld="cursor") from None


def _cursor(sleutel) -> str:
    return base64.urlsafe_b64encode(json.dumps(sleutel).encode()).decode().rstrip("=")


def _pagina(items: list[dict], sinds: datetime | None, na, limiet: int) -> tuple[list[dict], str | None]:
    """Stabiel gesorteerd op `_sleutel`; `sinds` filtert op `_bijgewerkt` (naïeve UTC)."""
    items = sorted(items, key=lambda i: i["_sleutel"])
    if na is not None:
        na = tuple(na) if isinstance(na, list) else na
        items = [i for i in items if (tuple(i["_sleutel"]) if isinstance(i["_sleutel"], list) else i["_sleutel"]) > na]
    if sinds is not None:
        items = [i for i in items if i["_bijgewerkt"] is not None and i["_bijgewerkt"] >= sinds]
    pagina = items[:limiet]
    volgende = _cursor(pagina[-1]["_sleutel"]) if len(items) > limiet else None
    return [{k: v for k, v in i.items() if not k.startswith("_")} for i in pagina], volgende


def _max(*tijden) -> datetime | None:
    return max((t for t in tijden if t is not None), default=None)


def _rond(waarde, decimalen=1):
    return None if waarde is None else round(waarde, decimalen)


def ds_planning(g: Gedeeld) -> list[dict]:
    if not g.plan:
        return []
    algemeen, per_kap = g.wijziging(), g.per_kap_wijziging()
    rondes = g.geladen.rondes
    items = []
    for u in g.uitkomsten:
        teelt = g.geladen.teelten[u.id]
        kap = g.geladen.kappen.get(teelt.kap)
        bedden = [r.bedden for r in teelt.rassen]
        bijgewerkt = _max(algemeen, per_kap.get(teelt.kap))
        items.append({
            "_sleutel": u.id, "_bijgewerkt": bijgewerkt,
            "id": u.id, "ronde": rondes[teelt.ronde_id].nummer, "kap": teelt.kap, "vakgroep": kap.vakgroep if kap else None,
            "volgnummer": teelt.volgnummer, "soort": teelt.schemacode,
            "bedden": sum(bedden) if bedden and all(b is not None for b in bedden) else None,
            "plantdatum": _datum(u.effectief["plantdatum"]), "ld_dagen": u.effectief["ld_dagen"],
            "licht_uit_datum": _datum(u.licht_uit), "reactietijd": u.effectief["reactietijd"],
            "verlenging": u.effectief["verlenging"], "oogstdatum": _datum(u.oogst), "teeltduur": u.teeltduur,
            "dichtheid": u.effectief["dichtheid"], "gepland_aantal": _rond(u.geplant, 0),
            "uitvalfractie": u.effectief["uitval"], "oogstaantal": _rond(u.oogstaantal, 0),
            "gewicht_g": _rond(u.effectief["gewicht"]),
            "gewicht_ondergrens_g": _rond(u.gewicht_band[0]) if u.bron["gewicht"] == "voorstel" else None,
            "gewicht_bovengrens_g": _rond(u.gewicht_band[1]) if u.bron["gewicht"] == "voorstel" else None,
            "handmatig_velden": sorted(v for v, b in u.bron.items() if b == "handmatig"),
            "bijgewerkt_op": tijd(bijgewerkt),
        })  # fmt: skip
    return items


def _gewogen(waarden: list, aantallen: list, takken: float | None) -> float | None:
    """Gemiddelde gewogen naar oogstaantal (onbekende aantallen: gewoon gemiddelde); één onbekende waarde = null."""
    if any(w is None for w in waarden):
        return None
    if takken:
        return sum(w * a for w, a in zip(waarden, aantallen, strict=True)) / takken
    return sum(waarden) / len(waarden)


def ds_oogstprognose(g: Gedeeld) -> list[dict]:
    if not g.plan:
        return []
    weken: dict[tuple[int, int], list] = {}
    for u in g.uitkomsten:
        if u.oogst:
            iso = u.oogst.isocalendar()
            weken.setdefault((iso.year, iso.week), []).append(u)
    bijgewerkt = g.wijziging()
    items = []
    for (jaar, week), lijst in sorted(weken.items()):
        aantallen = [u.oogstaantal for u in lijst]
        takken = None if any(a is None for a in aantallen) else sum(aantallen)

        gewicht = _gewogen([u.effectief["gewicht"] for u in lijst], aantallen, takken)
        laag = _gewogen([u.gewicht_band[0] for u in lijst], aantallen, takken)
        hoog = _gewogen([u.gewicht_band[1] for u in lijst], aantallen, takken)
        items.append({"_sleutel": [jaar, week], "_bijgewerkt": bijgewerkt, "id": f"{jaar}-{week:02d}", "iso_jaar": jaar,
                      "week": week, "aantal_takken": _rond(takken, 0), "gemiddeld_gewicht_g": _rond(gewicht),
                      "ondergrens_g": _rond(laag), "bovengrens_g": _rond(hoog), "aantal_teelten": len(lijst),
                      "bijgewerkt_op": tijd(bijgewerkt)})  # fmt: skip
    return items


def ds_energieprognose(g: Gedeeld) -> list[dict]:
    if not g.plan:
        return []
    datums = [d for u in g.uitkomsten for d in (u.effectief["plantdatum"], u.oogst) if d]
    rekenaar = maak_rekenaar(g.geladen.parameters, min(datums), max(datums)) if datums else None
    if rekenaar is None:
        return []
    bijgewerkt = g.wijziging()
    return [{"_sleutel": [r.iso_jaar, r.week, r.kap], "_bijgewerkt": bijgewerkt, "id": f"{r.iso_jaar}-{r.week:02d}-{r.kap}",
             "iso_jaar": r.iso_jaar, "week": r.week, "kap": r.kap, "dimgewogen_uren": None, "kwh": _rond(r.kwh),
             "kosten_eur": _rond(r.kosten, 2), "soort": r.soort, "bijgewerkt_op": tijd(bijgewerkt)}
            for r in kern_energie.per_teelt_per_week(rekenaar, g.uitkomsten)]  # fmt: skip


def ds_afzetmomenten(g: Gedeeld) -> list[dict]:
    if not g.plan:
        return []
    jaren = sorted({d.year for u in g.uitkomsten for d in (u.oogst,) if d})
    if not jaren:
        return []
    momenten, _ = plannen.momenten(g.db, range(jaren[0], jaren[-1] + 1))
    eenheid = kern_afzet.kies_eenheid(g.uitkomsten)
    dekking = kern_afzet.dekking(g.uitkomsten, momenten, eenheid)
    bijgewerkt = g.wijziging()
    items = []
    for md in momenten:
        venster = md.venster
        takken = dekking[(md.moment.id, md.datum)] if eenheid == "takken" else None
        items.append({"_sleutel": [md.datum.isoformat(), md.moment.id], "_bijgewerkt": bijgewerkt,
                      "id": f"{md.moment.id}-{md.datum.year}", "naam": md.moment.naam, "datum": _datum(md.datum),
                      "venster_begin": _datum(venster[0]) if venster else None,
                      "venster_eind": _datum(venster[1]) if venster else None, "gewicht": md.moment.gewicht,
                      "geplande_takken_in_venster": _rond(takken, 0)})  # fmt: skip
    return items


BOUWERS = {"planning": ds_planning, "oogstprognose": ds_oogstprognose, "energieprognose": ds_energieprognose,
           "afzetmomenten": ds_afzetmomenten}  # fmt: skip


_ITEMS: dict[str, tuple] = {}


def _items(db: Session, naam: str) -> list[dict]:
    """Items van een dataset, bewaard tot het plan verandert (een afnemer haalt alle pagina's na elkaar op)."""
    scenario_id = db.scalar(select(Scenario.id).where(Scenario.status == "vastgesteld"))
    versie = (planning.VERSIE.nummer, _vandaag(), scenario_id, db.scalar(select(func.max(Instelling.id))))
    bewaard = _ITEMS.get(naam)
    if bewaard is None or bewaard[0] != versie:
        bewaard = (versie, BOUWERS[naam](Gedeeld(db)))
        _ITEMS[naam] = bewaard
    return bewaard[1]


def gedeelde_datasets(db: Session) -> list[str]:
    gekozen = instellingen.laad(db)["koppeling.datasets"] or []
    return [d for d in DATASETS if d in gekozen]


# --- Endpoints -----------------------------------------------------------------------------------------------------
@router.get("/info")
def info(db: Session = Depends(get_db)):
    return {"app": APP_SLUG, "naam": APP_NAAM, "versie": __version__, "api_versie": API_VERSIE,
            "bedrijf": instellingen.laad(db)["bedrijf.naam"], "datasets": ["samenvatting", *gedeelde_datasets(db)],
            "tijd": tijd(utcnu())}  # fmt: skip


@router.get("/samenvatting")
def samenvatting(db: Session = Depends(get_db)):
    return samenvatting_data(db)


@router.get("/datasets/{naam}")
def dataset(naam: str, request: Request, db: Session = Depends(get_db)):
    if naam not in gedeelde_datasets(db):
        raise KoppelingFout(404, "onbekende_dataset")
    p = request.query_params
    onbekend = set(p) - {"sinds", "limiet", "cursor"}
    if onbekend:
        raise KoppelingFout(422, "ongeldige_parameter", veld=sorted(onbekend)[0])
    sinds, limiet, na = _parse_sinds(p.get("sinds")), _parse_limiet(p.get("limiet")), _parse_cursor(p.get("cursor"))
    items, volgende = _pagina(_items(db, naam), sinds, na, limiet)
    return {
        "app": APP_SLUG,
        "dataset": naam,
        "gegenereerd_op": tijd(utcnu()),
        "items": items,
        "volgende_cursor": volgende,
    }


# --- Sleutelbeheer (sessie + oorsprongcontrole, alleen beheerders) ---------------------------------------------------
def sleutel_dict(db: Session, rij: Koppelsleutel) -> dict:
    door = db.get(Gebruiker, rij.aangemaakt_door) if rij.aangemaakt_door else None
    return {"id": rij.id, "naam": rij.naam, "voorvoegsel": rij.voorvoegsel, "aangemaakt_op": tijd(rij.aangemaakt_op),
            "aangemaakt_door": door.gebruikersnaam if door else None, "laatst_gebruikt_op": tijd(rij.laatst_gebruikt_op),
            "ingetrokken_op": tijd(rij.ingetrokken_op), "actief": rij.ingetrokken_op is None}  # fmt: skip


def maak_sleutel(db: Session, naam: str, gebruiker: Gebruiker | None) -> tuple[Koppelsleutel, str]:
    naam = (naam or "").strip()
    if not naam or len(naam) > 100:
        raise HTTPException(422, "Naam is verplicht (maximaal 100 tekens).")
    sleutel = nieuwe_sleutel()
    rij = Koppelsleutel(naam=naam, voorvoegsel=sleutel[:8], sleutel_hash=sleutel_hash(sleutel),
                        aangemaakt_door=gebruiker.id if gebruiker else None)  # fmt: skip
    db.add(rij)
    db.flush()
    auth.audit(db, gebruiker, "koppelsleutel.aanmaken", f"koppelsleutel:{rij.id}",
               {"naam": rij.naam, "voorvoegsel": rij.voorvoegsel, **({} if gebruiker else {"via": "cli"})})  # fmt: skip
    db.commit()
    return rij, sleutel


def trek_in(db: Session, sleutel_id: int, gebruiker: Gebruiker | None) -> Koppelsleutel:
    rij = db.get(Koppelsleutel, sleutel_id)
    if rij is None:
        raise HTTPException(404, "Koppelsleutel niet gevonden.")
    if rij.ingetrokken_op is None:
        rij.ingetrokken_op = utcnu()
        rij.ingetrokken_door = gebruiker.id if gebruiker else None
        auth.audit(db, gebruiker, "koppelsleutel.intrekken", f"koppelsleutel:{rij.id}",
                   {"naam": rij.naam, "voorvoegsel": rij.voorvoegsel, **({} if gebruiker else {"via": "cli"})})  # fmt: skip
        db.commit()
    return rij


@beheer.get("/api/beheer/koppelsleutels")
def sleutels_lijst(gebruiker: Gebruiker = Depends(auth.vereis_beheer), db: Session = Depends(get_db)):
    return [sleutel_dict(db, r) for r in db.scalars(select(Koppelsleutel).order_by(Koppelsleutel.id.desc()))]


@beheer.post("/api/beheer/koppelsleutels", status_code=201)
async def sleutel_aanmaken(
    request: Request, gebruiker: Gebruiker = Depends(auth.vereis_beheer), db: Session = Depends(get_db)
):
    try:
        gegevens = await request.json()
    except ValueError:
        gegevens = None
    if not isinstance(gegevens, dict) or not isinstance(gegevens.get("naam"), str):
        raise HTTPException(422, 'Verwacht JSON {"naam": "..."}.')
    rij, sleutel = maak_sleutel(db, gegevens["naam"], gebruiker)
    return {
        **sleutel_dict(db, rij),
        "sleutel": sleutel,
        "melding": "Kopieer deze sleutel nu; hij wordt niet opnieuw getoond.",
    }


@beheer.delete("/api/beheer/koppelsleutels/{sleutel_id}", status_code=204)
def sleutel_intrekken(
    sleutel_id: int, gebruiker: Gebruiker = Depends(auth.vereis_beheer), db: Session = Depends(get_db)
):
    trek_in(db, sleutel_id, gebruiker)
    return Response(status_code=204)


def aantal_teelten(db: Session, scenario_id: int) -> int:
    return db.scalar(select(func.count(Teelt.id)).join(Ronde).where(Ronde.scenario_id == scenario_id)) or 0
