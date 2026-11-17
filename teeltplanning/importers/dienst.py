"""Importstroom zoals Gewichtsverwachting: uploaden → origineel met checksum opslaan → controlevoorbeeld met
meldingen → expliciet bevestigen → in één transactie wegschrijven. Een tweede identieke bevestiging maakt niets
dubbel aan. Handmatige waarden die een gebruiker in de app heeft ingevuld, blijven bij een herimport staan.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import instellingen
from ..auth import audit
from ..db.basis import DATA_DIR
from ..db.modellen import (
    Gebruiker,
    Importbatch,
    Kap,
    Ras,
    Ronde,
    Scenario,
    Teelt,
    TeeltRas,
    Veldwaarde,
    Werkboekregel,
    utcnu,
)
from ..planning import naar_json, zet_handmatig
from . import programma, vergelijking

SCENARIO_WERKBOEK = "Werkboek"
VELD_UIT_REGEL = {"ld_dagen": "ld_dagen", "reactietijd": "reactietijd", "verlenging": "verlenging",
                  "dichtheid": "dichtheid", "gewicht": "gewicht", "uitval": "uitval"}  # fmt: skip


def bronmap() -> Path:
    map_ = DATA_DIR / "bron"
    map_.mkdir(mode=0o700, exist_ok=True)
    return map_


def sla_upload_op(db: Session, gebruiker: Gebruiker, bestandsnaam: str, inhoud: bytes) -> Importbatch:
    snap = instellingen.laad(db)
    if len(inhoud) > snap["import.max_upload_mb"] * 1024 * 1024:
        raise ValueError("Bestand is groter dan de ingestelde maximale uploadgrootte.")
    if not bestandsnaam.lower().endswith(".xlsx"):
        raise ValueError("Alleen .xlsx-werkboeken (geen macro's, geen .xls).")
    sha = hashlib.sha256(inhoud).hexdigest()
    pad = bronmap() / f"programma-{sha}.xlsx"
    if not pad.exists():
        pad.write_bytes(inhoud)
        pad.chmod(0o600)
    batch = Importbatch(soort="programma", bestandsnaam=Path(bestandsnaam).name[:300], pad=str(pad.relative_to(DATA_DIR)),
                        sha256=sha, status="voorbeeld", aangemaakt_door=gebruiker.id, keuzes={"jaren": {}})  # fmt: skip
    db.add(batch)
    db.flush()
    audit(db, gebruiker, "import_geupload", f"importbatch:{batch.id}", {"bestand": batch.bestandsnaam, "sha256": sha})
    return batch


def _pad(batch: Importbatch) -> Path:
    pad = Path(batch.pad)
    if pad.is_absolute() or ".." in pad.parts:
        raise ValueError("Ongeldig bronpad")
    return DATA_DIR / pad


def lees(db: Session, batch: Importbatch) -> dict:
    return programma.lees_programma(_pad(batch), instellingen.laad(db).waarden)


def maak_voorbeeld(db: Session, batch: Importbatch) -> dict:
    """Controlevoorbeeld: per ronde aantallen, meldingen en (als het jaar bekend is) de vergelijking."""
    gelezen = lees(db, batch)
    jaren = {int(k): v for k, v in (batch.keuzes or {}).get("jaren", {}).items() if v}
    behouden = _behouden_handmatig(db, gelezen, jaren)
    rondes = []
    alle_vergelijkingen = []
    for ronde in gelezen["rondes"]:
        jaar = jaren.get(ronde["nummer"])
        meldingtellingen: dict[str, int] = {}
        for r in ronde["regels"]:
            for m in r["meldingen"]:
                meldingtellingen[m] = meldingtellingen.get(m, 0) + 1
        vergelijkingen = []
        if jaar:
            data = programma.datums(ronde["regels"], jaar)
            for r in ronde["regels"]:
                for m in data[r["rij"]]["meldingen"]:
                    meldingtellingen[m] = meldingtellingen.get(m, 0) + 1
                vergelijkingen.append(vergelijking.vergelijk_regel(r, data[r["rij"]]["plantdatum"]))
        alle_vergelijkingen += vergelijkingen
        rondes.append({
            "nummer": ronde["nummer"], "blad": ronde["blad"], "titel": ronde["titel"], "titel_conflict": ronde["titel_conflict"],
            "regels": len(ronde["regels"]), "jaar": jaar, "meldingen": meldingtellingen,
            "verwijzingen": sum(1 for r in ronde["regels"] if r["plant_verwijzing"]),
            "vergelijking": vergelijking.rapport(vergelijkingen)["tellingen"] if jaar else None,
        })  # fmt: skip
    voorbeeld = {
        "rondes": rondes,
        "meldingen": gelezen["meldingen"],
        "regels": sum(r["regels"] for r in rondes),
        "behouden_handmatig": behouden,
        "vergelijking": vergelijking.rapport(alle_vergelijkingen) | {"regels": alle_vergelijkingen},
    }
    batch.voorbeeld = voorbeeld
    return voorbeeld


def _scenario(db: Session) -> Scenario | None:
    return db.scalar(select(Scenario).where(Scenario.naam == SCENARIO_WERKBOEK))


def _behouden_handmatig(db: Session, gelezen: dict, jaren: dict) -> int:
    """Aantal door gebruikers ingevulde waarden in het scenario Werkboek dat een herimport ongemoeid laat."""
    scenario = _scenario(db)
    if scenario is None:
        return 0
    return len(db.scalars(
        select(Veldwaarde.id).join(Teelt, Teelt.id == Veldwaarde.teelt_id).join(Ronde, Ronde.id == Teelt.ronde_id)
        .where(Ronde.scenario_id == scenario.id, Veldwaarde.handmatig_bron == "gebruiker")
    ).all())  # fmt: skip


def bevestig(db: Session, gebruiker: Gebruiker, batch: Importbatch) -> dict:
    """Schrijf alle rondes met een ISO-jaar weg in het scenario "Werkboek" (één transactie, idempotent)."""
    if batch.status == "bevestigd":
        return {"al_bevestigd": True}
    gelezen = lees(db, batch)
    snap = instellingen.laad(db)
    jaren = {int(k): v for k, v in (batch.keuzes or {}).get("jaren", {}).items() if v}
    if not jaren:
        raise ValueError("Vul voor minstens één ronde het ISO-jaar van de eerste planting in.")
    scenario = _scenario(db)
    if scenario is None:
        heeft_vastgesteld = db.scalar(select(Scenario.id).where(Scenario.status == "vastgesteld")) is not None
        scenario = Scenario(naam=SCENARIO_WERKBOEK, status="concept" if heeft_vastgesteld else "vastgesteld")
        db.add(scenario)
        db.flush()
    kappen_op_volgorde = {k.plantvolgorde: k for k in db.scalars(select(Kap)) if k.plantvolgorde is not None}
    rassen = {r.code: r for r in db.scalars(select(Ras))}
    vastzetten = snap["import.plantdatum_bij_verwijzing"] == "vastzetten"
    rondes = {r.nummer: r for r in db.scalars(select(Ronde).where(Ronde.scenario_id == scenario.id))}
    teelten = {
        (t.ronde_id, t.kap): t
        for t in db.scalars(select(Teelt).where(Teelt.ronde_id.in_([r.id for r in rondes.values()])))
    }
    velden = {
        (v.teelt_id, v.veld): v
        for v in db.scalars(select(Veldwaarde).where(Veldwaarde.teelt_id.in_([t.id for t in teelten.values()])))
    }
    regels_bewijs = {
        w.teelt_id: w
        for w in db.scalars(select(Werkboekregel).where(Werkboekregel.teelt_id.in_([t.id for t in teelten.values()])))
    }
    rondes_in_import = {r["nummer"] for r in gelezen["rondes"] if r["nummer"] in jaren} | set(rondes)
    tellingen = {"teelten": 0, "nieuw": 0, "velden_gezet": 0, "behouden_handmatig": 0, "overgeslagen": 0}
    for ronde_data in gelezen["rondes"]:
        jaar = jaren.get(ronde_data["nummer"])
        if not jaar:
            tellingen["overgeslagen"] += len(ronde_data["regels"])
            continue
        ronde = rondes.get(ronde_data["nummer"])
        if ronde is None:
            ronde = Ronde(scenario_id=scenario.id, nummer=ronde_data["nummer"])
            db.add(ronde)
            rondes[ronde.nummer] = ronde
        ronde.iso_jaar_eerste_planting = jaar
        ronde.label = ronde_data["titel"]
        db.flush()
        data = programma.datums(ronde_data["regels"], jaar)
        nieuwe: list[tuple[Teelt, dict]] = []
        with db.no_autoflush:
            for r in ronde_data["regels"]:
                kap = kappen_op_volgorde.get(r["volgnummer"])
                if kap is None:
                    tellingen["overgeslagen"] += 1
                    continue
                if kap.vakgroep is None and r["vakgroep"]:
                    kap.vakgroep = r["vakgroep"]
                teelt = teelten.get((ronde.id, kap.nummer))
                if teelt is None:
                    teelt = Teelt(ronde_id=ronde.id, kap=kap.nummer)
                    db.add(teelt)
                    teelten[(ronde.id, kap.nummer)] = teelt
                    tellingen["nieuw"] += 1
                tellingen["teelten"] += 1
                teelt.volgnummer = r["volgnummer"]
                teelt.schemacode = r["schema"]
                teelt.geoogst_op = data[r["rij"]]["geoogst"]
                teelt.gemiddeld_gewicht_g = r["gemiddeld_gewicht"]
                teelt.rassen.clear()
                for deel in r["rassen"]:
                    ras = rassen.get(deel["code"])
                    teelt.rassen.append(
                        TeeltRas(ras_id=ras.id if ras else None, code=deel["code"], bedden=deel["beds"])
                    )
                nieuwe.append((teelt, r))
        db.flush()  # teelt-id's
        with db.no_autoflush:
            for teelt, r in nieuwe:
                waarden = {veld: r[bronveld] for veld, bronveld in VELD_UIT_REGEL.items()}
                verwijzing = r["plant_verwijzing"]
                volgt_keten = verwijzing is not None and not vastzetten and verwijzing["ronde"] in rondes_in_import
                waarden["plantdatum"] = None if volgt_keten else data[r["rij"]]["plantdatum"]
                waarden["tussenlicht"] = data[r["rij"]]["tussenlicht"]
                for veld, waarde in waarden.items():
                    bestaand = velden.get((teelt.id, veld))
                    if bestaand is not None and bestaand.handmatig_bron == "gebruiker":
                        tellingen["behouden_handmatig"] += 1
                        continue
                    if (bestaand is None and waarde is None) or (
                        bestaand is not None and bestaand.handmatig == naar_json(veld, waarde)
                    ):
                        continue
                    if bestaand is None:
                        bestaand = Veldwaarde(teelt_id=teelt.id, veld=veld)
                        db.add(bestaand)
                        velden[(teelt.id, veld)] = bestaand
                    zet_handmatig(
                        db, gebruiker, teelt, veld, waarde, None, bron=f"import:{batch.id}", bestaand=bestaand
                    )
                    tellingen["velden_gezet"] += 1
                bewijs = r["broncellen"] | {
                    "plant_verwijzing": verwijzing,
                    "meldingen": r["meldingen"] + data[r["rij"]]["meldingen"],
                }
                oud = regels_bewijs.get(teelt.id)
                if oud is not None:
                    db.delete(oud)
                regels_bewijs[teelt.id] = Werkboekregel(
                    importbatch_id=batch.id, teelt_id=teelt.id, blad=r["blad"], rij=r["rij"], broncellen=bewijs
                )
                db.add(regels_bewijs[teelt.id])
    batch.status = "bevestigd"
    batch.scenario_id = scenario.id
    batch.bevestigd_door = gebruiker.id
    batch.bevestigd_op = utcnu()
    audit(db, gebruiker, "import_bevestigd", f"importbatch:{batch.id}", tellingen | {"jaren": jaren})
    db.flush()
    return tellingen


def zet_jaren(batch: Importbatch, jaren: dict[int, int | None]) -> None:
    huidig = dict((batch.keuzes or {}).get("jaren", {}))
    for nummer, jaar in jaren.items():
        if jaar is None:
            huidig.pop(str(nummer), None)
        else:
            if not 1900 <= jaar <= 2200:
                raise ValueError(f"Ronde {nummer}: ongeldig jaar {jaar}.")
            huidig[str(nummer)] = jaar
    batch.keuzes = {**(batch.keuzes or {}), "jaren": huidig}
