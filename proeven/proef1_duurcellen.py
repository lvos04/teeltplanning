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
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.datetime import CALENDAR_MAC_1904, CALENDAR_WINDOWS_1900, to_excel

UREN_PER_DAG = 24
TEKST_DUUR = re.compile(r"^\s*(?:(\d+)\s+days?,\s*)?(\d{1,3}):(\d{2})(?::(\d{2}))?\s*$")


@dataclass
class Duur:
    uren: float | None
    soort: str  # timedelta | datetime | time | getal | tekst | leeg | onbekend
    excel_dagen: float | None
    melding: str | None = None


def duur_naar_uren(waarde: object, epoch: datetime = CALENDAR_WINDOWS_1900) -> Duur:
    """Zet één celwaarde om naar uren, op basis van het celtype."""
    if waarde is None or (isinstance(waarde, str) and not waarde.strip()):
        return Duur(None, "leeg", None)
    if isinstance(waarde, bool):
        return Duur(None, "onbekend", None, "waarheidswaarde is geen duur")
    if isinstance(waarde, timedelta):
        dagen = waarde.total_seconds() / 86400
        return Duur(dagen * UREN_PER_DAG, "timedelta", dagen)
    if isinstance(waarde, datetime):
        # Datumnotatie op een duur: terugrekenen naar het Excel-getal in het datumstelsel van het werkboek.
        dagen = float(to_excel(waarde, epoch))
        return Duur(dagen * UREN_PER_DAG, "datetime", dagen)
    if isinstance(waarde, time):
        dagen = float(to_excel(waarde))
        return Duur(dagen * UREN_PER_DAG, "time", dagen, "tijdcel: kan alleen een duur onder 24 uur bevatten")
    if isinstance(waarde, (int, float)):
        return Duur(float(waarde) * UREN_PER_DAG, "getal", float(waarde))
    if isinstance(waarde, str):
        m = TEKST_DUUR.match(waarde)
        if m:
            dagen_deel, uren, minuten, seconden = (int(x) if x else 0 for x in m.groups())
            totaal = dagen_deel * UREN_PER_DAG + uren + minuten / 60 + seconden / 3600
            return Duur(totaal, "tekst", totaal / UREN_PER_DAG, "duur als tekst gelezen: controleer de bron")
        return Duur(None, "onbekend", None, f"tekst is geen herkenbare duur: {waarde[:40]!r}")
    return Duur(None, "onbekend", None, f"onbekend celtype {type(waarde).__name__}")


@dataclass
class Weekuren:
    cel: str
    module: object
    week: object
    duur: Duur
    getalnotatie: str | None
    buiten_grenzen: bool


def lees_weekuren(pad: Path, blad: str = "belichting", eerste_rij: int = 2, laatste_rij: int = 84,
                  min_uren: float = 0, max_uren: float = 168) -> list[Weekuren]:  # fmt: skip
    """Lees de weekurentabel streamend. Rij `eerste_rij` bevat de weeknummers, kolom A het modulenummer."""
    wb = load_workbook(pad, read_only=True, data_only=True, keep_links=False)
    try:
        epoch = CALENDAR_MAC_1904 if wb.epoch == CALENDAR_MAC_1904 else CALENDAR_WINDOWS_1900
        ws = wb[blad]
        weken: dict[int, object] = {}
        uitkomst: list[Weekuren] = []
        for rijnummer, rij in enumerate(ws.iter_rows(min_row=eerste_rij, max_row=laatste_rij), eerste_rij):
            if rijnummer == eerste_rij:
                # Alleen de aaneengesloten weekkolommen vanaf B (weken 1–53); daarna begint ander rekenwerk
                # (zoals de kostenvergelijking in AK–AM).
                weken = {}
                for k, c in enumerate(rij, 1):
                    if k == 1:
                        continue
                    if isinstance(c.value, int) and not isinstance(c.value, bool) and 1 <= c.value <= 53:
                        weken[k] = c.value
                    else:
                        break
                continue
            if not rij:
                continue
            module = rij[0].value
            if not isinstance(module, (int, float)) or isinstance(module, bool):
                continue  # lege scheidingsrij of totaalrij
            for kolom, cel in enumerate(rij, 1):
                if kolom not in weken:
                    continue
                duur = duur_naar_uren(cel.value, epoch)
                buiten = duur.uren is not None and not (min_uren <= duur.uren <= max_uren)
                ref = f"{get_column_letter(kolom)}{rijnummer}"
                notatie = getattr(cel, "number_format", None)
                uitkomst.append(Weekuren(ref, module, weken[kolom], duur, notatie, buiten))
        return uitkomst
    finally:
        wb.close()


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
