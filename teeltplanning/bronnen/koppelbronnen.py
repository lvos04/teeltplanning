"""Afnemen via de Bedrijfskoppeling API v1 (BOUWPLAN §11.2): gegevens ophalen bij Gewichtsverwachting.

* Koppelbronnen staan in *Beheer → Koppelingen* (adres + koppelsleutel; de sleutel versleuteld met
  `DATA_DIR/geheim.key`, zoals het Bedrijfsdashboard). Ophalen alleen als *Koppelbronnen ophalen* aan staat
  (standaard uit), op het ingestelde interval of met de knop *Nu ophalen*.
* Datasets: `teelten`, `oogsten`, `metingen`, `prognoses` (volledig, met paginering via `volgende_cursor`).
* `403 {"fout": "koppeling_uitgeschakeld"}` is geen storing: status `uit_bij_bron` met de uitleg waar de
  schakelaar bij de bron staat.
* Koppelen op kap + plantdatum ± tolerantie (instelling *Import → Tolerantie koppelen op plantdatum*; leeg = alleen
  dezelfde dag). Nul of meer dan één kandidaat = niet gekoppeld, met de reden in een lijst; er wordt niet geraden.
* Gekoppelde teelten voeden de LBE-kalibratie en de voorstelladder (`waarnemingen`).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import instellingen, planning
from ..auth import audit
from ..db.basis import SessionLocal
from ..db.modellen import Koppelbron, KoppelItem, Ras, utcnu
from . import http, kluis

log = logging.getLogger("teeltplanning.koppelbronnen")
API_PREFIX = "/api/koppeling/v1"
DATASETS = ("teelten", "oogsten", "metingen", "prognoses")
PAGINA = 500  # contract: limiet 1..500
KOPPELING_UIT = "koppeling_uitgeschakeld"
# Waar de schakelaar "Bedrijfskoppeling ingeschakeld" in elke app staat (contract, docs/koppeling-api.md).
SCHAKELAAR = {
    "gewichtsverwachting": ("Gewichtsverwachting", "Beheer → Bedrijfskoppeling"),
    "milieu-registratie": ("Teeltregistratie", "Instellingen → Koppelingen"),
    "scouten": ("Scouten", "Beheer & instellingen → Koppelingen"),
    "tuinwerk-os": ("Tuinwerk-OS", "Beheer → Integraties → Bedrijfskoppeling"),
    "priva-bridge": ("Priva-bridge", "Beheer → Bedrijfskoppeling"),
    "bedrijfsdashboard": ("Bedrijfsdashboard", "Beheer → Koppelsleutels van dit dashboard"),
}


class Fout(ValueError):
    pass


def uitleg_uit(app: str) -> str:
    naam, plek = SCHAKELAAR.get(app, (app, "het beheer van de bron"))
    return f"Staat uit bij de bron ({naam}: {plek})"


# --- Beheer --------------------------------------------------------------------------------------------------------
def _controleer_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url.startswith(("https://", "http://")) or len(url) > 300 or " " in url:
        raise Fout("Vul een adres in dat begint met https:// (of http:// binnen het eigen netwerk).")
    return url


def maak(db: Session, gebruiker, naam: str, url: str, sleutel: str, app: str = "gewichtsverwachting") -> Koppelbron:
    naam = (naam or "").strip()[:100]
    if not naam or db.scalar(select(Koppelbron.id).where(Koppelbron.naam == naam)):
        raise Fout("Kies een unieke naam.")
    if app not in SCHAKELAAR:
        raise Fout("Onbekende app.")
    if not (sleutel or "").strip().startswith("bk_"):
        raise Fout("Een koppelsleutel begint met bk_.")
    bron = Koppelbron(naam=naam, app=app, basis_url=_controleer_url(url), sleutel_versleuteld=kluis.versleutel(sleutel.strip()),
                      actief=True)  # fmt: skip
    db.add(bron)
    db.flush()
    audit(
        db, gebruiker, "koppelbron_toegevoegd", f"koppelbron:{bron.id}", {"naam": naam, "url": bron.basis_url}
    )  # nooit de sleutel
    return bron


def wijzig(db: Session, gebruiker, bron: Koppelbron, url: str | None, sleutel: str | None, actief: bool | None) -> None:
    details = {}
    if url is not None and url.strip() and url.strip().rstrip("/") != bron.basis_url:
        bron.basis_url = _controleer_url(url)
        details["url"] = bron.basis_url
    if sleutel is not None and sleutel.strip():
        if not sleutel.strip().startswith("bk_"):
            raise Fout("Een koppelsleutel begint met bk_.")
        bron.sleutel_versleuteld = kluis.versleutel(sleutel.strip())
        details["sleutel"] = "gewijzigd"
    if actief is not None and actief != bron.actief:
        bron.actief = actief
        details["actief"] = actief
    if details:
        audit(db, gebruiker, "koppelbron_gewijzigd", f"koppelbron:{bron.id}", details)


# --- Ophalen -------------------------------------------------------------------------------------------------------
def _get(bron: Koppelbron, pad: str, parameters: dict | None, tijdslimiet: float):
    sleutel = kluis.ontsleutel(bron.sleutel_versleuteld) if bron.sleutel_versleuteld else ""
    return http.haal_json(bron.basis_url + API_PREFIX + pad, parameters,
                          {"Authorization": f"Bearer {sleutel}", "Accept": "application/json"}, tijdslimiet)  # fmt: skip


def _alle(bron: Koppelbron, dataset: str, tijdslimiet: float, max_paginas: int) -> list[dict]:
    items: list[dict] = []
    cursor = None
    for _ in range(max_paginas):
        parameters = {"limiet": PAGINA} | ({"cursor": cursor} if cursor else {})
        antwoord = _get(bron, f"/datasets/{dataset}", parameters, tijdslimiet)
        if not isinstance(antwoord, dict) or not isinstance(antwoord.get("items"), list):
            raise Fout(f"Ongeldig antwoord voor dataset {dataset}.")
        items += [i for i in antwoord["items"] if isinstance(i, dict) and i.get("id") is not None]
        cursor = antwoord.get("volgende_cursor")
        if not cursor:
            return items
    raise Fout(f"Dataset {dataset}: meer dan {max_paginas} pagina's (instelling Maximum pagina's per dataset).")


def haal_op(db: Session, bron: Koppelbron, ook_als_uit: bool = False) -> tuple[bool, str]:
    """Haal alle datasets op. Geeft (gelukt, melding); een fout wordt een melding, nooit een crash."""
    snap = instellingen.laad(db)
    if not snap["koppeling.bronnen_aan"] and not ook_als_uit:
        return False, "Koppelbronnen ophalen staat uit (Beheer → Koppelingen)."
    tijdslimiet, max_paginas = snap["bronnen.tijdslimiet_s"], snap["koppeling.max_paginas"]
    bron.laatst_geprobeerd = utcnu()
    try:
        info = _get(bron, "/info", None, tijdslimiet)
        if not isinstance(info, dict) or info.get("app") != bron.app:
            raise Fout(
                f"Dit adres is geen {SCHAKELAAR[bron.app][0]} (app = {info.get('app') if isinstance(info, dict) else '?'})."
            )
        aangeboden = info.get("datasets") or []
        tellingen = {}
        for dataset in DATASETS:
            if dataset not in aangeboden:
                tellingen[dataset] = None
                continue
            items = _alle(bron, dataset, tijdslimiet, max_paginas)
            db.execute(delete(KoppelItem).where(KoppelItem.bron_id == bron.id, KoppelItem.dataset == dataset))
            gezien = set()
            for item in items:
                sleutel = str(item["id"])[:100]
                if sleutel in gezien:
                    continue
                gezien.add(sleutel)
                db.add(KoppelItem(bron_id=bron.id, dataset=dataset, extern_id=sleutel, data=item))
            tellingen[dataset] = len(gezien)
    except http.BronFout as fout:
        db.rollback()
        bron = db.get(Koppelbron, bron.id)
        bron.laatst_geprobeerd = utcnu()
        if fout.status == 403 and isinstance(fout.data, dict) and fout.data.get("fout") == KOPPELING_UIT:
            bron.status, bron.melding = (
                "uit_bij_bron",
                uitleg_uit(bron.app) + ' (schakelaar "Bedrijfskoppeling ingeschakeld").',
            )
        elif fout.status == 401:
            bron.status, bron.melding = "fout", "Koppelsleutel geweigerd (401): controleer of de sleutel nog geldig is."
        else:
            bron.status, bron.melding = "fout", str(fout)
        db.commit()
        return False, bron.melding
    except (Fout, ValueError) as fout:
        db.rollback()
        bron = db.get(Koppelbron, bron.id)
        bron.laatst_geprobeerd, bron.status, bron.melding = utcnu(), "fout", str(fout)
        db.commit()
        return False, bron.melding
    bron.status, bron.laatst_gelukt = "ok", utcnu()
    bron.melding = ", ".join(f"{d}: {'niet aangeboden' if n is None else n}" for d, n in tellingen.items())
    db.commit()
    return True, f"Opgehaald: {bron.melding}."


def ververs_als_nodig() -> None:
    with SessionLocal() as db:
        snap = instellingen.laad(db)
        if not snap["koppeling.bronnen_aan"]:
            return
        interval = timedelta(hours=snap["koppeling.ophaalinterval_uren"])
        nu = datetime.now(UTC).replace(tzinfo=None)
        for bron in db.scalars(select(Koppelbron).where(Koppelbron.actief.is_(True))):
            if bron.laatst_geprobeerd is None or nu - bron.laatst_geprobeerd >= interval:
                gelukt, melding = haal_op(db, bron)
                if not gelukt:
                    log.warning("Koppelbron %s: %s", bron.naam, melding)


# --- Koppelen en gebruiken -----------------------------------------------------------------------------------------
def _datum(tekst) -> date | None:
    try:
        return date.fromisoformat(str(tekst)[:10]) if tekst else None
    except ValueError:
        return None


def items(db: Session, dataset: str) -> list[dict]:
    rijen = db.scalars(select(KoppelItem).join(Koppelbron).where(KoppelItem.dataset == dataset, Koppelbron.actief.is_(True))
                       .order_by(KoppelItem.bron_id, KoppelItem.id))  # fmt: skip
    return [r.data | {"_bron": r.bron_id} for r in rijen]


def koppel(db: Session) -> dict:
    """Koppel de teelten van de koppelbronnen aan het plan (vastgesteld, anders het eerste scenario)."""
    snap = instellingen.laad(db)
    tolerantie = snap["import.koppel_tolerantie_dagen"] or 0
    scenario = planning.vastgesteld_of_eerste(db)
    per_kap: dict[int, list[tuple[date, int]]] = {}
    plan = None
    if scenario is not None:
        _, plan = planning.bereken(db, scenario.id)
        for u in plan.teelten.values():
            if u.effectief["plantdatum"] is not None:
                per_kap.setdefault(u.kap, []).append((u.effectief["plantdatum"], u.id))
    gekoppeld, niet = [], []
    for t in items(db, "teelten"):
        kap, plant = t.get("kap"), _datum(t.get("plantdatum"))
        if not isinstance(kap, int) or plant is None:
            niet.append({"teelt": t, "reden": "kap of plantdatum ontbreekt bij de bron"})
            continue
        kandidaten = [tid for d, tid in per_kap.get(kap, []) if abs((d - plant).days) <= tolerantie]
        if not kandidaten:
            niet.append(
                {
                    "teelt": t,
                    "reden": f"geen teelt in het plan op kap {kap} binnen ± {tolerantie} dag(en) van {plant.isoformat()}",
                }
            )
        elif len(kandidaten) > 1:
            niet.append(
                {"teelt": t, "reden": f"{len(kandidaten)} teelten in het plan binnen de tolerantie; niet geraden"}
            )
        else:
            gekoppeld.append({"teelt": t, "teelt_id": kandidaten[0]})
    # Eén-op-één: twee bronteelten op dezelfde planteelt = beide niet gekoppeld
    tellingen: dict[int, int] = {}
    for g in gekoppeld:
        tellingen[g["teelt_id"]] = tellingen.get(g["teelt_id"], 0) + 1
    dubbel = [g for g in gekoppeld if tellingen[g["teelt_id"]] > 1]
    gekoppeld = [g for g in gekoppeld if tellingen[g["teelt_id"]] == 1]
    niet += [
        {"teelt": g["teelt"], "reden": "meer teelten bij de bron op dezelfde planteelt; niet geraden"} for g in dubbel
    ]
    return {
        "gekoppeld": gekoppeld,
        "niet_gekoppeld": niet,
        "tolerantie": tolerantie,
        "plan": plan,
        "scenario": scenario,
    }


def _ras(db: Session, soort) -> str | None:
    if not isinstance(soort, str) or not soort.strip():
        return None
    for r in db.scalars(select(Ras)):
        if soort.strip().casefold() in (r.code.casefold(), r.naam.casefold()):
            return r.code
    return None


def _gewogen(paren: list[tuple[float | None, float | None]]) -> float | None:
    waarden = [(w, a) for w, a in paren if w is not None]
    if not waarden:
        return None
    if all(a for _, a in waarden):
        return sum(w * a for w, a in waarden) / sum(a for _, a in waarden)
    return sum(w for w, _ in waarden) / len(waarden)


def waarnemingen(db: Session, peildatum: date | None = None):
    """Gekoppelde teelten als waarnemingen voor de voorstelladder en de LBE-kalibratie."""
    from ..voorstel.model import Waarneming

    peildatum = peildatum or date.today()
    uitkomst = koppel(db)
    if not uitkomst["gekoppeld"]:
        return []
    snap = instellingen.laad(db)
    welke = snap["koppeling.oogstdatum_realisatie"]
    oogsten: dict[tuple[int, object], list[dict]] = {}
    for o in items(db, "oogsten"):
        oogsten.setdefault((o["_bron"], o.get("teelt_id")), []).append(o)
    plan = uitkomst["plan"]
    uit = []
    for g in uitkomst["gekoppeld"]:
        t, u = g["teelt"], plan.teelten[g["teelt_id"]]
        plant, licht_uit = _datum(t.get("plantdatum")), _datum(t.get("licht_uit_datum"))
        if plant is None or plant >= peildatum:
            continue
        regels = oogsten.get((t["_bron"], t.get("id")), [])
        gewicht = _gewogen([(o.get("gemiddeld_takgewicht_g"), o.get("aantal_takken")) for o in regels])
        uitval = _gewogen([(o.get("uitvalfractie"), o.get("aantal_takken")) for o in regels])
        reactietijd = None
        if welke and licht_uit and regels and u.effectief["verlenging"] is not None:
            datums = [_datum(o.get(welke)) for o in regels]
            if all(datums):
                oogst = min(datums) if welke == "begindatum" else max(datums)
                reactietijd = (oogst - licht_uit).days - u.effectief["verlenging"]
        dichtheid = t.get("dichtheid") if isinstance(t.get("dichtheid"), (int, float)) else None
        uit.append(Waarneming(
            sleutel=f"koppeling:{t['_bron']}:{t.get('id')}", bron=f"Gewichtsverwachting teelt {t.get('id')}", kap=t.get("kap"),
            ras=_ras(db, t.get("soort")), plantdatum=plant,
            ld_dagen=(licht_uit - plant).days if licht_uit and licht_uit <= peildatum else None, reactietijd=reactietijd,
            gewicht=gewicht, dichtheid=dichtheid, lichtsom=u.lichtsom.totaal if u.lichtsom else None, uitval=uitval,
        ))  # fmt: skip
    return uit


def prognose(db: Session, teelt_id: int) -> dict | None:
    """De laatste prognose van Gewichtsverwachting voor een planteelt (via de koppeling), of None."""
    gekoppeld = {g["teelt_id"]: g["teelt"] for g in koppel(db)["gekoppeld"]}
    t = gekoppeld.get(teelt_id)
    if t is None:
        return None
    lijst = [p for p in items(db, "prognoses") if p["_bron"] == t["_bron"] and p.get("teelt_id") == t.get("id")]
    if not lijst:
        return {"teelt": t, "prognose": None}
    return {"teelt": t, "prognose": max(lijst, key=lambda p: (str(p.get("peilmoment") or ""), str(p.get("id"))))}
