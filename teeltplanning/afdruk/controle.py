"""Controle van de afdruk tegen het werkboek zelf (BOUWPLAN §14 fase 2, §9.3).

1. **Velden en meetmomenten.** Het sjabloon (het echte werkboek) wordt in LibreOffice herberekend met kap en
   ronde ingevuld, voor een steekproef van kappen tegelijk (één kopie van de teeltkaart per kap, één conversie).
   Daarna worden alle toegewezen cellen vergeleken met wat de app voor die kap en ronde berekent.
2. **Overleggen.** De herberekende kaart uit het werkboek en de kaart uit de app worden allebei naar pdf en
   daarna naar een grijswaardenafbeelding omgezet (`pdftoppm`) en pixel voor pixel vergeleken.

Gebruik in de container (het werkboek blijft op de NAS):

    python -m teeltplanning.afdruk.controle "/data/bron/programma-….xlsx" --kaarten 10

Het werkboek moet eerst in de app zijn geïmporteerd (zelfde scenario), anders is er niets om mee te vergelijken.
"""

from __future__ import annotations

import argparse
import io
import random
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook

from ..xlsxpakket import Datum, NieuwBlad, Pakket, bouw_kopie
from . import pdf
from .kaart import celwaarden_kaart


@dataclass
class Verschil:
    kap: int
    ronde: int
    cel: str
    werkboek: object
    app: object


def _normaal(waarde: object) -> object:
    if waarde is None or (isinstance(waarde, str) and not waarde.strip()):
        return None
    if isinstance(waarde, Datum):
        return waarde.tekst
    if isinstance(waarde, datetime):
        return waarde.date()
    if isinstance(waarde, bool):
        return waarde
    if isinstance(waarde, (int, float)):
        return float(waarde)
    return str(waarde).strip()


def _gelijk(werkboek: object, app: object) -> bool:
    if isinstance(app, Datum) and isinstance(werkboek, (date, datetime)):
        return _normaal(werkboek) == app.waarde
    return _normaal(werkboek) == _normaal(app)


def herbereken_kaarten(pakket: Pakket, paren: list[tuple[int, int]], snap) -> list[dict[str, object]]:
    """Per (kap, ronde) de waarden van alle toegewezen cellen van de linker kaart, zoals het werkboek ze berekent."""
    cellen = snap["afdrukken.teeltkaart_cellen"]
    bron = snap["afdrukken.teeltkaart_blad"]
    bladen = [
        NieuwBlad(
            bron,
            f"controle {i}",
            {cellen["kap"]: kap, cellen["ronde"]: ronde},
            snap["afdrukken.teeltkaart_bereik"],
            snap["afdrukken.teeltkaart_richting"],
        )  # fmt: skip
        for i, (kap, ronde) in enumerate(paren, 1)
    ]
    xlsx, _ = bouw_kopie(pakket, bladen, behoud_overige=True, papier=snap["afdrukken.papierformaat"],
                         passend=snap["afdrukken.passend_op_een_pagina"], formules_bevriezen=False,
                         volledig_herberekenen=True)  # fmt: skip
    herberekend = pdf.converteer(xlsx, "xlsx", snap["afdrukken.tijdslimiet_s"], herberekenen=True)
    wb = load_workbook(io.BytesIO(herberekend), data_only=True)
    uit = []
    for blad in bladen:
        ws = wb[blad.naam]
        uit.append({"_xlsx": herberekend, "_blad": blad.naam, **{c: ws[c].value for c in _alle_cellen(snap)}})
    return uit


def _alle_cellen(snap) -> list[str]:
    cellen = list(snap["afdrukken.teeltkaart_cellen"].values())
    eerste = snap["afdrukken.teeltkaart_eerste_momentrij"]
    for kolom in snap["afdrukken.teeltkaart_momentkolommen"].values():
        cellen += [f"{kolom}{eerste + i}" for i in range(snap["teeltkaart.aantal_momenten"])]
    return cellen


def vergelijk(werkboek: dict[str, object], app: dict[str, object], kap: int, ronde: int) -> list[Verschil]:
    return [
        Verschil(kap, ronde, cel, werkboek.get(cel), waarde)
        for cel, waarde in app.items()
        if cel in werkboek and not _gelijk(werkboek.get(cel), waarde)
    ]


def controleer_kaarten(db, scenario_id: int, pakket: Pakket, paren: list[tuple[int, int]]) -> dict:
    """Vergelijk de app met het herberekende werkboek voor de gegeven (kap, ronde)-paren."""
    from .dienst import kaarten_van_ronde

    geladen = None
    app_waarden = {}
    for kap, ronde in paren:
        geladen, kaarten, teelten = kaarten_van_ronde(db, scenario_id, ronde)
        teelt = teelten.get(kap)
        app_waarden[(kap, ronde)] = celwaarden_kaart(kaarten.get(kap), kap, ronde, teelt.opmerking if teelt else None,
                                                     geladen.snap)  # fmt: skip
    snap = geladen.snap if geladen else None
    werkboek = herbereken_kaarten(pakket, paren, snap)
    verschillen: list[Verschil] = []
    for (kap, ronde), wb in zip(paren, werkboek, strict=True):
        app = {c: v for c, v in app_waarden[(kap, ronde)].items() if c in wb}
        verschillen += vergelijk(wb, app, kap, ronde)
    return {"paren": paren, "cellen_per_kaart": len(_alle_cellen(snap)), "verschillen": verschillen,
            "herberekend": werkboek}  # fmt: skip


# --- Overleggen (afbeeldingen: teeltplanning/beeld.py) ----------------------------------------------------------------------------------------------
def origineel_als_pdf(herberekend_xlsx: bytes, blad: str, snap, naam: str) -> bytes:
    """De door LibreOffice herberekende kaart uit het werkboek zelf, afgedrukt zoals de app afdrukt."""
    pakket = Pakket.lees(herberekend_xlsx)
    xlsx, _ = bouw_kopie(pakket, [NieuwBlad(blad, naam, {}, snap["afdrukken.teeltkaart_bereik"],
                                            snap["afdrukken.teeltkaart_richting"])],
                         behoud_overige=False, papier=snap["afdrukken.papierformaat"],
                         passend=snap["afdrukken.passend_op_een_pagina"], formules_bevriezen=True,
                         volledig_herberekenen=False)  # fmt: skip
    return pdf.converteer(xlsx, "pdf", snap["afdrukken.tijdslimiet_s"])


def main(argv: list[str] | None = None) -> int:
    from .. import planning
    from ..db.basis import SessionLocal
    from ..planning import vastgesteld_of_eerste

    parser = argparse.ArgumentParser(description="Teeltkaart uit de app vergelijken met het werkboek")
    parser.add_argument("werkboek", type=Path)
    parser.add_argument("--kaarten", type=int, required=True, help="aantal steekproefkaarten")
    parser.add_argument("--zaad", type=int, default=1)
    args = parser.parse_args(argv)
    with SessionLocal() as db:
        scenario = vastgesteld_of_eerste(db)
        if scenario is None:
            print("Er is nog geen plan: importeer eerst het werkboek in de app.")
            return 1
        geladen, plan = planning.bereken(db, scenario.id)
        kandidaten = sorted((u.kap, u.ronde) for u in plan.teelten.values() if u.licht_uit is not None)
        paren = random.Random(args.zaad).sample(kandidaten, min(args.kaarten, len(kandidaten)))
        uitkomst = controleer_kaarten(db, scenario.id, Pakket.lees(args.werkboek), paren)
    print(f"Gecontroleerd: {len(paren)} kaarten × {uitkomst['cellen_per_kaart']} cellen; paren (kap, ronde): {paren}")
    for v in uitkomst["verschillen"]:
        print(f"kap {v.kap} ronde {v.ronde} cel {v.cel}: werkboek {v.werkboek!r}, app {_normaal(v.app)!r}")
    print("Geen verschillen." if not uitkomst["verschillen"] else f"{len(uitkomst['verschillen'])} verschil(len).")
    return 0 if not uitkomst["verschillen"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
