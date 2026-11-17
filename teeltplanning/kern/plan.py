"""Eén rekenfunctie voor het hele plan (BOUWPLAN §4.1).

Deterministisch en zonder neveneffecten. Scherm, afdruk, koppeling, zoekfunctie en tests gebruiken allemaal
deze ene functie.
"""

from __future__ import annotations

from collections import defaultdict

from .keten import bereken_keten
from .typen import KapInvoer, Parameters, Plan, PlanInvoer, TeeltInvoer


def bereken_plan(invoer: PlanInvoer, parameters: Parameters) -> Plan:
    per_kap: dict[int, list[TeeltInvoer]] = defaultdict(list)
    for teelt in invoer.teelten:
        per_kap[teelt.kap].append(teelt)
    uitkomsten = {}
    for kapnummer, teelten in per_kap.items():
        kap = parameters.kappen.get(kapnummer, KapInvoer(nummer=kapnummer))
        for uitkomst in bereken_keten(teelten, kap, parameters.instellingen):
            uitkomsten[uitkomst.id] = uitkomst
    return Plan(teelten=uitkomsten)
