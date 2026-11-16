"""Fase 5: afzetmomenten, scenario's (kopiëren, vaststellen, vergelijken), genereren, zoeken en jaaroverzicht.

Alles rekent met `bereken_plan` (via `planning.bereken`). Genereren schrijft alleen **voorstellen** (nooit
handmatige waarden) in een **concept**scenario. Zoeken levert een lijst; toepassen gebeurt per voorstel door de
gebruiker en zet dan een handmatige waarde met reden en auditregel, maar alleen als dat veld op dat moment nog
geen handmatige waarde heeft.
"""

from __future__ import annotations

import logging
import multiprocessing
import threading
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import planning
from .auth import audit
from .bronnen import feestdagen
from .db.basis import SessionLocal
from .db.modellen import Afzetmoment, Ras, Ronde, Scenario, Teelt, TeeltRas, Veldwaarde, Zoekrun, utcnu
from .kern import afzet as kern_afzet
from .kern import energie as kern_energie
from .kern import genereren as kern_genereren
from .kern import zoeken as kern_zoeken
from .kern.kalender import weken_in_jaar
from .kern.plan import maak_rekenaar

log = logging.getLogger("teeltplanning.plannen")
_DRAAD = ThreadPoolExecutor(max_workers=1, thread_name_prefix="zoeken")
_SLOT = threading.Lock()


class Fout(ValueError):
    pass


# --- Afzetmomenten -----------------------------------------------------------------------------------------------
def startgegevens(db: Session) -> None:
    if db.scalar(select(Afzetmoment.id).limit(1)) is None:
        for naam, regel, markt in feestdagen.START_AFZETMOMENTEN:
            db.add(Afzetmoment(naam=naam, datumregel=regel, markt=markt, actief=True, bevestigd=False,
                               opmerking="Startinhoud uit het bouwplan; nog niet door de teler bevestigd."))  # fmt: skip


def afzetmomenten(db: Session) -> list[kern_afzet.Afzetmoment]:
    return [kern_afzet.Afzetmoment(m.id, m.naam, m.datumregel, m.venster_dagen_voor, m.gewicht, m.markt, m.actief)
            for m in db.scalars(select(Afzetmoment).order_by(Afzetmoment.id))]  # fmt: skip


def momenten(db: Session, jaren) -> tuple[list[kern_afzet.MomentDatum], list[str]]:
    """Datums van de actieve momenten in de jaren; een regel die niet klopt wordt overgeslagen met een melding."""
    uit, meldingen = [], []
    for m in afzetmomenten(db):
        try:
            uit += kern_afzet.momenten_in([m], jaren, feestdagen.pasen)
        except kern_afzet.OngeldigeRegel as fout:
            meldingen.append(f"{m.naam}: datumregel '{m.datumregel}' klopt niet ({fout}).")
    return sorted(uit, key=lambda md: (md.datum, md.moment.id)), meldingen


# --- Scenario's ----------------------------------------------------------------------------------------------------
def kopieer(db: Session, gebruiker, bron_id: int, naam: str) -> Scenario:
    bron = db.get(Scenario, bron_id)
    naam = (naam or "").strip()
    if bron is None:
        raise Fout("Onbekend scenario.")
    if not naam or db.scalar(select(Scenario.id).where(Scenario.naam == naam)):
        raise Fout("Kies een nieuwe, unieke naam.")
    kopie = Scenario(naam=naam[:100], status="concept", afgeleid_van=bron.id)
    db.add(kopie)
    db.flush()
    teelten = 0
    for ronde in db.scalars(select(Ronde).where(Ronde.scenario_id == bron.id)):
        r = Ronde(scenario_id=kopie.id, nummer=ronde.nummer, iso_jaar_eerste_planting=ronde.iso_jaar_eerste_planting,
                  label=ronde.label)  # fmt: skip
        db.add(r)
        db.flush()
        for t in db.scalars(select(Teelt).where(Teelt.ronde_id == ronde.id)):
            nt = Teelt(ronde_id=r.id, kap=t.kap, volgnummer=t.volgnummer, schemacode=t.schemacode, opmerking=t.opmerking,
                       geoogst_op=t.geoogst_op, gemiddeld_gewicht_g=t.gemiddeld_gewicht_g)  # fmt: skip
            nt.rassen = [TeeltRas(ras_id=x.ras_id, code=x.code, bedden=x.bedden) for x in t.rassen]
            db.add(nt)
            db.flush()
            for v in db.scalars(select(Veldwaarde).where(Veldwaarde.teelt_id == t.id)):
                db.add(Veldwaarde(teelt_id=nt.id, veld=v.veld, voorstel=v.voorstel, ondergrens=v.ondergrens,
                                  bovengrens=v.bovengrens, bron=v.bron, modelversie=v.modelversie, handmatig=v.handmatig,
                                  handmatig_bron=v.handmatig_bron, gewijzigd_door=v.gewijzigd_door,
                                  gewijzigd_op=v.gewijzigd_op, reden=v.reden))  # fmt: skip
            teelten += 1
    audit(
        db,
        gebruiker,
        "scenario_gekopieerd",
        f"scenario:{kopie.id}",
        {"van": bron.id, "naam": kopie.naam, "teelten": teelten},
    )
    return kopie


def stel_vast(db: Session, gebruiker, scenario_id: int) -> Scenario:
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise Fout("Onbekend scenario.")
    vorige = db.scalar(select(Scenario).where(Scenario.status == "vastgesteld"))
    if vorige is not None and vorige.id != scenario.id:
        vorige.status = "concept"
        db.flush()
    scenario.status, scenario.vastgesteld_op = "vastgesteld", utcnu()
    audit(db, gebruiker, "scenario_vastgesteld", f"scenario:{scenario.id}", {"vorige": vorige.id if vorige else None})
    return scenario


# --- Genereren -----------------------------------------------------------------------------------------------------
def genereer(db: Session, gebruiker, scenario_id: int, jaar: int | None, rondes: int | None = None,
             start: date | None = None) -> dict:  # fmt: skip
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise Fout("Onbekend scenario.")
    if scenario.status != "concept":
        raise Fout("Genereren kan alleen in een conceptscenario; maak eerst een kopie van het vastgestelde plan.")
    if (jaar is None) == (rondes is None):
        raise Fout("Kies een jaar of een aantal rondes.")
    geladen, plan = planning.bereken(db, scenario_id)
    snap = geladen.snap
    if jaar is not None and any(u.effectief["plantdatum"] and u.effectief["plantdatum"].isocalendar().year == jaar
                                for u in plan.teelten.values()):  # fmt: skip
        raise Fout(f"In {jaar} staan al teelten in dit scenario; genereren vult alleen een leeg jaar.")
    laatste: dict[int, tuple] = {}
    invoer_per_id = {t.id: t for t in geladen.invoer.teelten}
    for kap in geladen.kappen:
        ketting = plan.per_kap(kap)
        if ketting:
            u = ketting[-1]
            laatste[kap] = (u.oogst, invoer_per_id[u.id].rassen)
    kappen = sorted(geladen.kappen.values(), key=lambda k: (k.plantvolgorde or k.nummer, k.nummer))
    kap_invoer = [geladen.parameters.kappen[k.nummer] for k in kappen]
    verdeling = tuple((r["ras"], r["aandeel"]) for r in snap["genereren.rasverdeling"] or [])
    bekend = {r.code for r in db.scalars(select(Ras))}
    onbekend = [code for code, _ in verdeling if code not in bekend]
    if onbekend:
        raise Fout(f"Rasverdeling: onbekend ras {', '.join(onbekend)} (Beheer → Rassen).")
    opties = kern_genereren.GenOpties(
        jaar=jaar, rondes=rondes, rasverdeling=verdeling,
        terugval={v: snap[f"genereren.{v}_terugval"] for v in kern_genereren.VELDEN},
        start=start or (date.fromisocalendar(jaar, 1, 1) if jaar else date.today()),
        kappen_per_plantdag=snap["genereren.kappen_per_plantdag"],
    )  # fmt: skip
    nieuw, meldingen = kern_genereren.genereer(kap_invoer, laatste, geladen.parameters.instellingen,
                                               geladen.parameters.voorspeller, opties)  # fmt: skip
    if not nieuw:
        return {"teelten": 0, "rondes": 0, "meldingen": meldingen or ["Er is niets gegenereerd."]}
    eerste_nummer = max((r.nummer for r in geladen.rondes.values()), default=0) + 1
    rondes_db: dict[int, Ronde] = {}
    ras_id = {r.code: r.id for r in db.scalars(select(Ras))}
    modelversie = geladen.parameters.voorspeller.versie if geladen.parameters.voorspeller is not None else None
    for nt in nieuw:
        ronde = rondes_db.get(nt.index)
        if ronde is None:
            ronde = Ronde(scenario_id=scenario.id, nummer=eerste_nummer + nt.index,
                          iso_jaar_eerste_planting=nt.plantdatum.isocalendar().year,
                          label=f"gegenereerd {jaar or ''} #{nt.index + 1}".strip())  # fmt: skip
            db.add(ronde)
            db.flush()
            rondes_db[nt.index] = ronde
        ronde.iso_jaar_eerste_planting = min(ronde.iso_jaar_eerste_planting, nt.plantdatum.isocalendar().year)
        kap = geladen.kappen[nt.kap]
        teelt = Teelt(ronde_id=ronde.id, kap=nt.kap, volgnummer=kap.plantvolgorde or kap.nummer,
                      schemacode="".join(f"{b or ''}{c}" for c, b in nt.rassen) or None)  # fmt: skip
        teelt.rassen = [TeeltRas(ras_id=ras_id.get(c), code=c, bedden=b) for c, b in nt.rassen]
        db.add(teelt)
        db.flush()
        if nt.eerste_in_kap:
            db.add(Veldwaarde(teelt_id=teelt.id, veld="plantdatum", voorstel=nt.plantdatum.isoformat(),
                              bron="genereren: startdatum"))  # fmt: skip
        for veld, waarde in nt.waarden.items():
            onder, boven, bron, versie = nt.toelichting.get(veld, (None, None, None, None))
            db.add(Veldwaarde(teelt_id=teelt.id, veld=veld, voorstel=waarde, ondergrens=onder, bovengrens=boven,
                              bron=f"genereren: {bron}"[:100] if bron else "genereren", modelversie=versie or modelversie))  # fmt: skip
    audit(db, gebruiker, "plan_gegenereerd", f"scenario:{scenario.id}",
          {"jaar": jaar, "rondes": len(rondes_db), "teelten": len(nieuw), "meldingen": meldingen[:50]})  # fmt: skip
    return {"teelten": len(nieuw), "rondes": len(rondes_db), "meldingen": meldingen}


# --- Zoeken ------------------------------------------------------------------------------------------------------
def zoekopties(snap, db: Session, jaar: int) -> kern_zoeken.ZoekOpties:
    return kern_zoeken.ZoekOpties(
        jaar=jaar, peildatum=date.today(),
        gewichten={n: snap[f"zoeken.gewicht_{n}"] for n in kern_zoeken.ONDERDELEN},
        zetten=tuple(snap["zoeken.zetten"]), stap_plantdag=snap["zoeken.stap_plantdag"],
        stap_ld_dagen=snap["zoeken.stap_ld_dagen"], max_voorstellen=snap["zoeken.max_voorstellen"],
        max_rekentijd_s=float(snap["zoeken.max_rekentijd_s"]),
        doelgewicht={r.code: r.doelgewicht_g for r in db.scalars(select(Ras))},
    )  # fmt: skip


def start_zoeken(db: Session, gebruiker, scenario_id: int, jaar: int) -> Zoekrun:
    if db.get(Scenario, scenario_id) is None:
        raise Fout("Onbekend scenario.")
    with _SLOT:
        lopend = db.scalar(select(Zoekrun).where(Zoekrun.status == "bezig"))
        if lopend is not None:
            return lopend
        run = Zoekrun(scenario_id=scenario_id, jaar=jaar, gestart_door=gebruiker.id if gebruiker else None)
        db.add(run)
        db.flush()
        audit(db, gebruiker, "zoeken_gestart", f"zoekrun:{run.id}", {"scenario": scenario_id, "jaar": jaar})
        db.commit()
        _DRAAD.submit(_zoek_veilig, run.id)
        return run


def _zoek_veilig(run_id: int) -> None:
    try:
        with SessionLocal() as db:
            run = db.get(Zoekrun, run_id)
            geladen, _ = planning.bereken(db, run.scenario_id)
            md, _ = momenten(db, [run.jaar - 1, run.jaar, run.jaar + 1])
            opties = zoekopties(geladen.snap, db, run.jaar)
            invoer, parameters = geladen.invoer, geladen.parameters
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=1, mp_context=context) as proces:
            uitkomst = proces.submit(kern_zoeken.zoek, invoer, parameters, md, opties).result()
        with SessionLocal() as db:
            run = db.get(Zoekrun, run_id)
            run.uitkomst, run.status, run.klaar_op, run.toegepast = uitkomst, "klaar", utcnu(), []
            run.melding = f"{len(uitkomst['voorstellen'])} voorstel(len); {uitkomst['gestopt']}."
            db.commit()
    except Exception as fout:  # noqa: BLE001
        log.exception("Zoeken mislukt")
        with SessionLocal() as db:
            run = db.get(Zoekrun, run_id)
            run.status, run.klaar_op, run.melding = "mislukt", utcnu(), f"Zoeken mislukt: {fout}"
            db.commit()


def wacht_zoeken(timeout: float | None = None) -> None:
    _DRAAD.submit(lambda: None).result(timeout=timeout)


def pas_toe(db: Session, gebruiker, run: Zoekrun, nummer: int) -> str:
    """Eén voorstel toepassen. Een veld dat intussen handmatig is, wordt niet aangeraakt."""
    voorstellen = (run.uitkomst or {}).get("voorstellen") or []
    if not 0 <= nummer < len(voorstellen):
        raise Fout("Onbekend voorstel.")
    if nummer in (run.toegepast or []):
        return "Dit voorstel is al toegepast."
    v = voorstellen[nummer]
    reden = f"zoekvoorstel {run.id}.{nummer + 1}: {v['tekst']}"[:2000]
    if v["soort"] == "ras_wisselen":
        a, b = db.get(Teelt, v["teelt_id"]), db.get(Teelt, v["ander_teelt_id"])
        if a is None or b is None:
            raise Fout("Teelt bestaat niet meer.")
        huidig_a, huidig_b = [(r.code, r.bedden) for r in a.rassen], [(r.code, r.bedden) for r in b.rassen]
        if huidig_a != [tuple(x) for x in v["oud"]] or huidig_b != [tuple(x) for x in v["nieuw"]]:
            raise Fout("De rassen zijn intussen gewijzigd; dit voorstel is niet meer geldig.")
        ras_id = {r.code: r.id for r in db.scalars(select(Ras))}
        a.rassen = [TeeltRas(ras_id=ras_id.get(c), code=c, bedden=bd) for c, bd in huidig_b]
        b.rassen = [TeeltRas(ras_id=ras_id.get(c), code=c, bedden=bd) for c, bd in huidig_a]
        a.schemacode, b.schemacode = b.schemacode, a.schemacode
        a.bijgewerkt_op = b.bijgewerkt_op = utcnu()
        audit(db, gebruiker, "ras_gewisseld", f"teelt:{a.id}", {"met": b.id, "reden": reden})
    else:
        teelt = db.get(Teelt, v["teelt_id"])
        if teelt is None:
            raise Fout("Teelt bestaat niet meer.")
        bestaand = db.scalar(select(Veldwaarde).where(Veldwaarde.teelt_id == teelt.id, Veldwaarde.veld == v["veld"]))
        if bestaand is not None and bestaand.handmatig is not None:
            raise Fout("Dit veld heeft intussen een handmatige waarde; die wordt niet overschreven.")
        waarde = date.fromisoformat(v["nieuw"]) if v["veld"] == "plantdatum" else v["nieuw"]
        planning.zet_handmatig(db, gebruiker, teelt, v["veld"], waarde, reden, bestaand=bestaand)
    run.toegepast = [*(run.toegepast or []), nummer]
    return f"Toegepast: {v['tekst']}"


# --- Vergelijken en jaaroverzicht ----------------------------------------------------------------------------------
def _weekreeks(jaar: int) -> list[int]:
    return list(range(1, weken_in_jaar(jaar) + 1))


def kerncijfers(db: Session, scenario_id: int, jaar: int) -> dict:
    """Oogst per week, afzetdekking per moment en kWh per week van één scenario in één ISO-jaar."""
    geladen, plan = planning.bereken(db, scenario_id)
    uitkomsten = list(plan.teelten.values())
    in_jaar = [u for u in uitkomsten if u.oogst and u.oogst.isocalendar().year == jaar]
    eenheid = kern_afzet.kies_eenheid(in_jaar)
    weken = kern_afzet.per_week(in_jaar, eenheid)
    md, meldingen = momenten(db, [jaar])
    dekking = kern_afzet.dekking(uitkomsten, md, eenheid)
    datums = [d for u in uitkomsten for d in (u.effectief["plantdatum"], u.oogst) if d]
    rekenaar = maak_rekenaar(geladen.parameters, min(datums), max(datums)) if datums else None
    kwh: dict[int, float | None] = {}
    if rekenaar is not None:
        kwh = {w: 0.0 for w in _weekreeks(jaar)}
        for regel in kern_energie.per_teelt_per_week(rekenaar, uitkomsten):
            if regel.iso_jaar == jaar and kwh.get(regel.week) is not None:
                kwh[regel.week] = None if regel.kwh is None else kwh[regel.week] + regel.kwh
    return {
        "scenario": geladen.scenario, "eenheid": eenheid,
        "oogst": {w: weken.get((jaar, w), 0.0) for w in _weekreeks(jaar)},
        "kwh": kwh,
        "dekking": [{"naam": m.moment.naam, "datum": m.datum, "venster": m.venster, "gewicht": m.moment.gewicht,
                     "aanvoer": dekking[(m.moment.id, m.datum)]} for m in md],  # fmt: skip
        "meldingen": meldingen, "teelten": len(in_jaar),
        "conflicten": sum(1 for u in in_jaar if u.heeft_fout),
    }  # fmt: skip


def vergelijk(db: Session, a: int, b: int, jaar: int) -> dict:
    ka, kb = kerncijfers(db, a, jaar), kerncijfers(db, b, jaar)
    weken = []
    for w in _weekreeks(jaar):
        oa, ob = ka["oogst"][w], kb["oogst"][w]
        wa, wb = ka["kwh"].get(w), kb["kwh"].get(w)
        weken.append({"week": w, "oogst_a": oa, "oogst_b": ob, "oogst_verschil": ob - oa, "kwh_a": wa, "kwh_b": wb,
                      "kwh_verschil": None if wa is None or wb is None else wb - wa})  # fmt: skip
    dekking = []
    for da, dbk in zip(ka["dekking"], kb["dekking"], strict=True):
        dekking.append(da | {"aanvoer_a": da["aanvoer"], "aanvoer_b": dbk["aanvoer"],
                             "verschil": None if da["aanvoer"] is None or dbk["aanvoer"] is None else dbk["aanvoer"] - da["aanvoer"]})  # fmt: skip

    def som(reeks):
        return None if any(v is None for v in reeks) else sum(reeks)

    return {"a": ka, "b": kb, "weken": weken, "dekking": dekking, "eenheid_gelijk": ka["eenheid"] == kb["eenheid"],
            "totaal": {"oogst_a": sum(ka["oogst"].values()), "oogst_b": sum(kb["oogst"].values()),
                       "kwh_a": som([x["kwh_a"] for x in weken]) if ka["kwh"] else None,
                       "kwh_b": som([x["kwh_b"] for x in weken]) if kb["kwh"] else None}}  # fmt: skip


def jaaroverzicht(db: Session, scenario_id: int, jaar: int) -> dict:
    """Kappen × weken (fase per week), oogst per week, afzetmomenten, feestdagen, kWh per week."""
    geladen, plan = planning.bereken(db, scenario_id)
    weken = _weekreeks(jaar)
    begin, eind = date.fromisocalendar(jaar, 1, 1), date.fromisocalendar(jaar, weken[-1], 7)
    rijen = []
    for kap in sorted(geladen.kappen):
        cellen = {w: "" for w in weken}
        for u in plan.per_kap(kap):
            plant, lu, oogst = u.effectief["plantdatum"], u.licht_uit, u.oogst
            if plant is None or oogst is None or oogst < begin or plant > eind:
                continue
            d = max(plant, begin)
            while d <= min(oogst, eind):
                w = d.isocalendar().week
                fase = "O" if d == oogst else ("KD" if lu is not None and d >= lu else "LD")
                rang = {"": 0, "LD": 1, "KD": 2, "O": 3}
                if rang[fase] > rang[cellen[w]]:
                    cellen[w] = fase
                d += timedelta(days=1)
            if plant.isocalendar().year == jaar and cellen[plant.isocalendar().week] in ("", "LD"):
                cellen[plant.isocalendar().week] = (
                    "P" if cellen[plant.isocalendar().week] == "" else cellen[plant.isocalendar().week]
                )
        rijen.append({"kap": kap, "cellen": [cellen[w] for w in weken]})
    cijfers = kerncijfers(db, scenario_id, jaar)
    per_week_momenten: dict[int, list[str]] = {}
    for d in cijfers["dekking"]:
        if d["datum"].isocalendar().year == jaar:
            per_week_momenten.setdefault(d["datum"].isocalendar().week, []).append(d["naam"])
    snap = geladen.snap
    feest: dict[int, list[str]] = {}
    for datum, land, naam in feestdagen.officiele_feestdagen(snap["afzet.landen"] or [], [jaar]):
        if datum.isocalendar().year == jaar:
            feest.setdefault(datum.isocalendar().week, []).append(f"{land}: {naam}")
    laag = set()
    for r in snap["afzet.lage_prijs_weken"] or []:
        laag |= (
            set(range(r["week_van"], r["week_tot"] + 1))
            if r["week_van"] <= r["week_tot"]
            else set(range(r["week_van"], weken[-1] + 1)) | set(range(1, r["week_tot"] + 1))
        )
    return {"weken": weken, "rijen": rijen, "cijfers": cijfers, "momenten": per_week_momenten, "feestdagen": feest,
            "lage_prijs": laag, "scenario": geladen.scenario}  # fmt: skip
