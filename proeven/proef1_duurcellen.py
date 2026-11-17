"""Proef 1 (BOUWPLAN §14, fase 0): duurcellen uit `Belichting winterronde.xlsx` correct als uren lezen.

De weekuren op het tabblad `belichting` (rij 3–42 LED-modules 1–40, rij 44–83 SON-T-modules 101–140, kolommen
= weken 36…10) staan als Excel-duur in de cellen. Excel bewaart een duur als getal in dagen; openpyxl geeft
dat afhankelijk van de getalnotatie terug als `timedelta`, `datetime` (bij een datumnotatie zoals
`d "days," hh:mm`, gerekend vanaf het datumstelsel van het werkboek), `time` (alleen < 24 uur) of als kaal getal.
We normaliseren daarom op **celtype**, nooit op de getoonde tekst. Een tekstcel zoals `3 days, 10:35` wordt
alleen met een waarschuwing gelezen.

Gebruik (ook in de container op de NAS, zodat het echte bestand daar blijft):

    python -m proeven.proef1_duurcellen "/data/bron/Belichting winterronde.xlsx" --steekproef 10

De uitvoer is een tabel om met de hand na te rekenen: cel, celtype, getalnotatie, Excel-getal in dagen, uren en
uu:mm. Het bestand wordt alleen gelezen (streamend, `read_only=True`).
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

from teeltplanning.importers.duur import (  # noqa: E402,F401 - de proef gebruikt de importercode
    TEKST_DUUR,
    UREN_PER_DAG,
    Duur,
    Weekuren,
    duur_naar_uren,
    lees_weekuren,
)


def uu_mm(uren: float | None) -> str:
    if uren is None:
        return "onbekend"
    minuten = round(uren * 60)
    return f"{minuten // 60}:{minuten % 60:02d}"


def rapport(regels: list[Weekuren], steekproef: int, zaad: int) -> str:
    gevuld = [r for r in regels if r.duur.soort != "leeg"]
    keuze = random.Random(zaad).sample(gevuld, min(steekproef, len(gevuld)))
    keuze.sort(key=lambda r: (str(r.module), str(r.week)))
    regels_uit = ["cel    module week celtype    getalnotatie          Excel-dagen   uren      uu:mm  melding"]
    for r in keuze:
        dagen = "" if r.duur.excel_dagen is None else f"{r.duur.excel_dagen:.6f}"
        uren = "" if r.duur.uren is None else f"{r.duur.uren:.3f}"
        regels_uit.append(
            f"{r.cel:<6} {r.module!s:<6} {r.week!s:<4} {r.duur.soort:<10} {str(r.getalnotatie)[:20]:<21} "
            f"{dagen:>12}  {uren:>8}  {uu_mm(r.duur.uren):>6}  {r.duur.melding or ''}"
        )
    soorten: dict[str, int] = {}
    for r in regels:
        soorten[r.duur.soort] = soorten.get(r.duur.soort, 0) + 1
    regels_uit.append("")
    regels_uit.append(f"cellen gelezen: {len(regels)}; per celtype: {soorten}")
    buiten = [r for r in regels if r.buiten_grenzen]
    regels_uit.append(f"buiten 0–168 uur per week: {len(buiten)} {[r.cel for r in buiten][:20]}")
    meldingen = [r for r in regels if r.duur.melding and r.duur.soort != "time"]
    regels_uit.append(f"overige meldingen: {len(meldingen)} {[(r.cel, r.duur.melding) for r in meldingen][:10]}")
    return "\n".join(regels_uit)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Proef 1: duurcellen als uren lezen")
    parser.add_argument("werkboek", type=Path)
    parser.add_argument("--steekproef", type=int, default=10)
    parser.add_argument("--zaad", type=int, default=1)
    args = parser.parse_args(argv)
    print(rapport(lees_weekuren(args.werkboek), args.steekproef, args.zaad))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
