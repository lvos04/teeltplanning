"""Afdrukdienst: plan → gevuld sjabloon (route A) → pdf via LibreOffice (route B).

Mislukt de pdf, dan blijft het gevulde Excel-bestand altijd beschikbaar (§9.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import planning
from ..db.modellen import Ronde, Teelt, Werkboekregel
from ..kern.kalender import weergave
from ..kern.teeltkaart import maak_kaart
from ..xlsxpakket import Datum, NieuwBlad, Pakket, bouw_kopie
from . import pdf, sjabloon
from .kaart import celwaarden_kaart, celwaarden_rondeblad, kaart_instellingen


class AfdrukFout(ValueError):
    pass


@dataclass
class Afdruk:
    xlsx: bytes
    naam: str  # bestandsnaam zonder extensie
    bladen: int
    meldingen: list[str] = field(default_factory=list)


def _sjabloon(db: Session) -> Pakket:
    rij = sjabloon.actief(db)
    if rij is None:
        raise AfdrukFout("Er is nog geen sjabloon. Upload je werkboek in Beheer → Sjabloon.")
    return Pakket.lees(sjabloon.pad(rij))


def _ronde(geladen, nummer: int) -> Ronde:
    ronde = next((r for r in geladen.rondes.values() if r.nummer == nummer), None)
    if ronde is None:
        raise AfdrukFout(f"Ronde {nummer} bestaat niet in dit scenario.")
    return ronde


def _leegmaken(pakket: Pakket, blad: str, gevuld: set[str], snap) -> frozenset[str]:
    if snap["afdrukken.niet_gevulde_formules"] != "leegmaken":
        return frozenset()
    return frozenset(sjabloon.niet_gevulde_formules(pakket, blad, gevuld))


def _bouw(pakket: Pakket, bladen: list[NieuwBlad], snap) -> tuple[bytes, list[str]]:
    return bouw_kopie(pakket, bladen, behoud_overige=False, papier=snap["afdrukken.papierformaat"],
                      passend=snap["afdrukken.passend_op_een_pagina"], formules_bevriezen=True,
                      volledig_herberekenen=False)  # fmt: skip


def _week(d):
    return d.isocalendar().week if d else None


def _dag(d):
    return d.isoweekday() if d else None


# --- Rondeblad -----------------------------------------------------------------------------------------------
def rondeblad_regels(db: Session, geladen, plan, ronde: Ronde) -> list[dict]:
    teelten = sorted((t for t in geladen.teelten.values() if t.ronde_id == ronde.id),
                     key=lambda t: (t.volgnummer or 0, t.kap))  # fmt: skip
    ruw = {}
    if teelten:
        for regel in db.scalars(select(Werkboekregel).where(Werkboekregel.teelt_id.in_([t.id for t in teelten]))):
            ruw[regel.teelt_id] = ((regel.broncellen or {}).get("teeltverlenging") or {}).get("value")
    regels = []
    vorige_groep = None
    cumulatief, bekend = 0.0, True
    for t in teelten:
        u = plan.teelten[t.id]
        kap = geladen.kappen.get(t.kap)
        groep = kap.vakgroep if kap else None
        groep = int(groep) if isinstance(groep, str) and groep.isdigit() else groep  # zoals in het werkboek: een getal
        plant, lu, oogst = u.effectief["plantdatum"], u.licht_uit, u.oogst
        if u.oogstaantal is None:
            bekend = False
        else:
            cumulatief += u.oogstaantal
        tussenlicht = u.effectief["tussenlicht"]
        regels.append({
            "vakgroep": groep if groep != vorige_groep else None,
            "volgnummer": t.volgnummer, "schema": t.schemacode,
            "plant_week": _week(plant), "plant_dag": _dag(plant),
            "ld_dagen": u.effectief["ld_dagen"],
            "licht_uit_week": _week(lu), "licht_uit_dag": _dag(lu),
            "reactietijd": u.effectief["reactietijd"],
            "tussenlicht": Datum(tussenlicht, weergave(tussenlicht)) if tussenlicht else None,
            "verlenging": u.effectief["verlenging"], "dichtheid": u.effectief["dichtheid"],
            "bloei_week": _week(oogst), "bloei_dag": _dag(oogst),
            "teeltduur": u.teeltduur, "teeltverlenging": ruw.get(t.id),
            "gewicht": u.effectief["gewicht"],
            "geoogst": Datum(t.geoogst_op, weergave(t.geoogst_op)) if t.geoogst_op else None,
            "gemiddeld_gewicht": t.gemiddeld_gewicht_g, "uitval": u.effectief["uitval"],
            "geplant": u.geplant, "oogstaantal": u.oogstaantal, "maand": oogst.month if oogst else None,
            "cumulatief": cumulatief if bekend and u.oogstaantal is not None else None,
        })  # fmt: skip
        vorige_groep = groep
    return regels


def maak_rondeblad(db: Session, scenario_id: int, ronde_nummer: int) -> Afdruk:
    pakket = _sjabloon(db)
    geladen, plan = planning.bereken(db, scenario_id)
    snap = geladen.snap
    ronde = _ronde(geladen, ronde_nummer)
    regels = rondeblad_regels(db, geladen, plan, ronde)
    titel = f"Ronde {ronde.nummer}"
    waarden = celwaarden_rondeblad(regels, snap, titel)
    bron = snap["afdrukken.rondeblad_sjabloonblad"]
    blad = NieuwBlad(bron, titel, waarden, snap["afdrukken.rondeblad_bereik"], snap["afdrukken.rondeblad_richting"],
                     _leegmaken(pakket, bron, set(waarden), snap))  # fmt: skip
    xlsx, meldingen = _bouw(pakket, [blad], snap)
    return Afdruk(xlsx, f"Rondeblad ronde {ronde.nummer}", 1, meldingen)


# --- Teeltkaart ----------------------------------------------------------------------------------------------
def kaarten_van_ronde(db: Session, scenario_id: int, ronde_nummer: int):
    """(geladen, kaart per kap, teelt per kap) voor één ronde."""
    geladen, plan = planning.bereken(db, scenario_id)
    ronde = _ronde(geladen, ronde_nummer)
    inst = kaart_instellingen(geladen.snap)
    teelten: dict[int, Teelt] = {t.kap: t for t in geladen.teelten.values() if t.ronde_id == ronde.id}
    kaarten = {kap: maak_kaart(plan.teelten[t.id], t.schemacode, inst) for kap, t in teelten.items()}
    return geladen, kaarten, teelten


def maak_teeltkaarten(db: Session, scenario_id: int, ronde_nummer: int, kap: int | None = None) -> Afdruk:
    """Eén blad (kap en kap+1 bij twee kaarten per blad), of alle kappen van de ronde (`kap` None)."""
    pakket = _sjabloon(db)
    geladen, kaarten, teelten = kaarten_van_ronde(db, scenario_id, ronde_nummer)
    snap = geladen.snap
    per_blad = snap["afdrukken.teeltkaart_kaarten_per_blad"]
    if kap is not None:
        if kap not in teelten:
            raise AfdrukFout(f"Kap {kap} heeft geen teelt in ronde {ronde_nummer}.")
        groepen = [[kap + i for i in range(per_blad)]]
    else:
        kappen = sorted(teelten)
        if not kappen:
            raise AfdrukFout(f"Ronde {ronde_nummer} heeft geen teelten.")
        groepen = [kappen[i : i + per_blad] for i in range(0, len(kappen), per_blad)]
    bron = snap["afdrukken.teeltkaart_blad"]
    bladen = []
    for groep in groepen:
        waarden: dict[str, object] = {}
        for positie in range(per_blad):
            k = groep[positie] if positie < len(groep) else None
            teelt = teelten.get(k) if k is not None else None
            waarden |= celwaarden_kaart(kaarten.get(k) if k is not None else None, k if teelt else None,
                                        ronde_nummer if teelt else None, teelt.opmerking if teelt else None, snap,
                                        positie * snap["afdrukken.teeltkaart_tweede_kaart_kolommen"])  # fmt: skip
        echte = [k for k in groep if k in teelten]
        naam = f"Kap {echte[0]}" + (f"-{echte[-1]}" if len(echte) > 1 else "")
        bladen.append(NieuwBlad(bron, naam, waarden, snap["afdrukken.teeltkaart_bereik"],
                                snap["afdrukken.teeltkaart_richting"], _leegmaken(pakket, bron, set(waarden), snap)))  # fmt: skip
    xlsx, meldingen = _bouw(pakket, bladen, snap)
    if kap is None:
        naam = f"Teeltkaarten ronde {ronde_nummer}"
    else:
        naam = f"Teeltkaart ronde {ronde_nummer} {bladen[0].naam.lower()}"
    return Afdruk(xlsx, naam, len(bladen), meldingen)


# --- Pdf -----------------------------------------------------------------------------------------------------
def naar_pdf(db: Session, afdruk: Afdruk) -> bytes:
    """Pdf van een afdruk; `pdf.ConversieFout` bij mislukken of bij een afwijkend aantal pagina's."""
    from .. import instellingen

    snap = instellingen.laad(db)
    resultaat = pdf.converteer(afdruk.xlsx, "pdf", snap["afdrukken.tijdslimiet_s"])
    aantal = pdf.paginas(resultaat)
    if aantal != afdruk.bladen:
        raise pdf.ConversieFout(
            f"De pdf heeft {aantal} pagina('s) in plaats van {afdruk.bladen} (één per blad); waarschijnlijk past "
            "het afdrukbereik niet op één pagina. Gebruik het Excel-bestand, of controleer in Beheer → Instellingen "
            "→ Afdrukken het afdrukbereik en 'passend op één pagina'."
        )
    return resultaat
