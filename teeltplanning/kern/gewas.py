"""Aantallen (BOUWPLAN §6.2). LBE, dichtheid en gewicht (§6.5) volgen in fase 3.

geplant     = dichtheid × oppervlak_kap
oogstaantal = geplant × (1 − uitval)
"""

from __future__ import annotations

from datetime import date

from .typen import Uitvalregel


def geplant(dichtheid: float | None, oppervlak_m2: float | None) -> float | None:
    if dichtheid is None or oppervlak_m2 is None:
        return None
    return dichtheid * oppervlak_m2


def oogstaantal(geplant_aantal: float | None, uitval: float | None) -> float | None:
    if geplant_aantal is None or uitval is None:
        return None
    return geplant_aantal * (1 - uitval)


def uitval_voorstel(oogst: date | None, regels: tuple[Uitvalregel, ...]) -> float | None:
    """Uitvalfractie volgens de tabel "uitval per oogstweek" (ISO-week van de oogst)."""
    if oogst is None:
        return None
    week = oogst.isocalendar().week
    for regel in regels:
        if regel.week_van <= week <= regel.week_tot:
            return regel.fractie
    return None
