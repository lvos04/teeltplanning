"""ISO-week/dag ⇄ datum en de weergave "week-dag" (BOUWPLAN §4.4).

Intern is alles een `date`. Het werkboek rekent met 52 weken per jaar; ISO-jaren met 53 weken (zoals 2020 en
2026: week 53 = 28 december 2026 t/m 3 januari 2027) rekent deze module wél goed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, timedelta

DAGEN_PER_WEEK = 7
WEERGAVEN = ("week-dag", "week-dag-jaar")
_WEEK_DAG = re.compile(r"^\s*(?P<week>\d{1,2})\s*-\s*(?P<dag>\d)\s*(?:[-/ ]\s*(?P<jaar>\d{4}))?\s*$")


def week_dag(d: date) -> tuple[int, int, int]:
    """(ISO-jaar, week, dag) met dag 1 = maandag … 7 = zondag."""
    iso = d.isocalendar()
    return iso.year, iso.week, iso.weekday


def van_week_dag(iso_jaar: int, week: int, dag: int) -> date | None:
    """Datum bij ISO-jaar, week en dag; None als de combinatie niet bestaat (zoals week 53 in 2025)."""
    try:
        return date.fromisocalendar(iso_jaar, week, dag)
    except ValueError:
        return None


def weken_in_jaar(iso_jaar: int) -> int:
    return (date.fromisocalendar(iso_jaar + 1, 1, 1) - date.fromisocalendar(iso_jaar, 1, 1)).days // DAGEN_PER_WEEK


def weergave(d: date | None, formaat: str = "week-dag") -> str:
    """`44-6`, of `44-6 (2026)` met het formaat `week-dag-jaar`. Onbekend → lege tekst."""
    if d is None:
        return ""
    jaar, week, dag = week_dag(d)
    if formaat == "week-dag-jaar":
        return f"{week}-{dag} ({jaar})"
    return f"{week}-{dag}"


def lees_week_dag(tekst: str, nabij: date) -> date:
    """Lees `44-6` (of `44-6-2026`). Zonder jaar: de datum met die week-dag die het dichtst bij `nabij` ligt.

    Geeft `ValueError` met een begrijpelijke melding bij ongeldige invoer.
    """
    m = _WEEK_DAG.match(tekst or "")
    if not m:
        raise ValueError("Gebruik het formaat week-dag, bijvoorbeeld 44-6 (of 44-6-2026).")
    week, dag = int(m["week"]), int(m["dag"])
    if not 1 <= dag <= DAGEN_PER_WEEK:
        raise ValueError("De dag moet 1 (maandag) t/m 7 (zondag) zijn.")
    if m["jaar"]:
        gevonden = van_week_dag(int(m["jaar"]), week, dag)
        if gevonden is None:
            raise ValueError(f"Week {week} bestaat niet in ISO-jaar {m['jaar']}.")
        return gevonden
    kandidaten = [van_week_dag(nabij.isocalendar().year + delta, week, dag) for delta in (-1, 0, 1)]
    kandidaten = [k for k in kandidaten if k is not None]
    if not kandidaten:
        raise ValueError(f"Week {week} bestaat niet rond {weergave(nabij, 'week-dag-jaar')}.")
    return min(kandidaten, key=lambda k: abs((k - nabij).days))


def is_geblokkeerd(d: date, blokkades: Iterable, kap: int | None) -> bool:
    return any(b.van <= d <= b.tot and (b.kap is None or b.kap == kap) for b in blokkades)


def eerstvolgende_toegestane_dag(
    vanaf: date, toegestaan: frozenset[int], blokkades: Iterable = (), kap: int | None = None
) -> date | None:
    """De eerste datum ≥ `vanaf` op een toegestane weekdag en buiten blokkadeperioden.

    None als er geen toegestane weekdag is ingesteld.
    """
    if not toegestaan:
        return None
    blokkades = tuple(blokkades)
    laatste_einde = max((b.tot for b in blokkades), default=vanaf)
    grens = max(vanaf, laatste_einde) + timedelta(days=DAGEN_PER_WEEK)
    d = vanaf
    while d <= grens:
        if d.isoweekday() in toegestaan and not is_geblokkeerd(d, blokkades, kap):
            return d
        d += timedelta(days=1)
    return None


def waarde_op(tijdreeks: Iterable[tuple[date | None, object]], op: date | None) -> object:
    """Waarde uit een reeks (geldig vanaf, waarde) die geldt op datum `op`; None als niets geldt."""
    gekozen = None
    for vanaf, waarde in tijdreeks:
        if vanaf is None or (op is not None and vanaf <= op):
            gekozen = waarde
    return gekozen
