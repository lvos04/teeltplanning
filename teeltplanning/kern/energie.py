"""Energie en kosten van de belichting (BOUWPLAN §6.6).

    vermogen [W/m²]   = intensiteit [µmol/m²/s] / rendement [µmol/J]      (of rechtstreeks kW per kap)
    verbruik [kWh]    = kW per kap × dimgewogen uren
    kosten   [€]      = verbruik [kWh] × (prijs + opslagen) [€/MWh] / 1000
    kosten per tak    = kosten / oogstaantal

Per teelt, per kap per week en per week voor het hele bedrijf. De scenariovergelijking zet twee lichtniveaus naast
elkaar (zoals "150 tegenover 200 µmol" in het werkboek).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .licht import UMOL_UREN_NAAR_MOL, Rekenaar

KILO = 1000


@dataclass(frozen=True)
class Energie:
    kwh: float | None
    kosten: float | None
    kosten_per_tak: float | None
    dagen_zonder_kwh: int = 0
    dagen_zonder_prijs: int = 0


def vermogen_w_per_m2(intensiteit: float | None, rendement: float | None) -> float | None:
    if intensiteit is None or not rendement:
        return None
    return intensiteit / rendement


def kosten(kwh: float | None, prijs_eur_per_mwh: float | None) -> float | None:
    if kwh is None or prijs_eur_per_mwh is None:
        return None
    return kwh * prijs_eur_per_mwh / KILO


def per_tak(bedrag: float | None, takken: float | None) -> float | None:
    if bedrag is None or not takken:
        return None
    return bedrag / takken


def energie_teelt(
    rekenaar: Rekenaar, kap: int, van: date | None, tot: date | None, oogstaantal: float | None
) -> Energie | None:
    if van is None or tot is None or tot <= van:
        return None
    totaal_kwh, totaal_kosten, zonder_kwh, zonder_prijs = rekenaar.energie_som(kap, van, tot)
    kwh = None if zonder_kwh else totaal_kwh
    bedrag = None if (zonder_kwh or zonder_prijs) else totaal_kosten
    return Energie(kwh, bedrag, per_tak(bedrag, oogstaantal), zonder_kwh, zonder_prijs)


@dataclass(frozen=True)
class Weekenergie:
    kap: int
    iso_jaar: int
    week: int
    kwh: float | None
    kosten: float | None
    dimgewogen_uren: float | None
    soort: str  # gerealiseerd | gepland | gemengd


def per_kap_per_week(rekenaar: Rekenaar, kappen: list[int], van: date, tot: date) -> list[Weekenergie]:
    """kWh en kosten per kap per ISO-week over [van, tot)."""
    uit = []
    for kap in kappen:
        per_week: dict[tuple[int, int], dict] = {}
        d = van
        while d < tot:
            iso = d.isocalendar()
            w = rekenaar.dag(kap, d)
            regel = per_week.setdefault((iso.year, iso.week), {"kwh": 0.0, "kosten": 0.0, "kwh_onbekend": False,
                                                               "prijs_onbekend": False, "soorten": set()})  # fmt: skip
            if w.kwh is None:
                regel["kwh_onbekend"] = True
            else:
                regel["kwh"] += w.kwh
                prijs = rekenaar.prijs(d)
                if prijs is None:
                    regel["prijs_onbekend"] = True
                else:
                    regel["kosten"] += w.kwh * prijs / KILO
            regel["soorten"].add("gerealiseerd" if d < rekenaar.inv.peildatum else "gepland")
            d += timedelta(days=1)
        for (jaar, week), r in sorted(per_week.items()):
            soort = r["soorten"].pop() if len(r["soorten"]) == 1 else "gemengd"
            uit.append(Weekenergie(kap, jaar, week, None if r["kwh_onbekend"] else r["kwh"],
                                   None if (r["kwh_onbekend"] or r["prijs_onbekend"]) else r["kosten"], None, soort))  # fmt: skip
    return uit


def bedrijf_per_week(regels: list[Weekenergie]) -> list[dict]:
    """Optellen over de kappen; een week met één onbekende kap is onbekend."""
    weken: dict[tuple[int, int], dict] = {}
    for r in regels:
        w = weken.setdefault((r.iso_jaar, r.week), {"iso_jaar": r.iso_jaar, "week": r.week, "kwh": 0.0, "kosten": 0.0,
                                                    "kappen": 0, "soorten": set()})  # fmt: skip
        w["kappen"] += 1
        w["soorten"].add(r.soort)
        w["kwh"] = None if (w["kwh"] is None or r.kwh is None) else w["kwh"] + r.kwh
        w["kosten"] = None if (w["kosten"] is None or r.kosten is None) else w["kosten"] + r.kosten
    return [weken[k] for k in sorted(weken)]


# --- Scenariovergelijking (§6.6) ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Lichtscenario:
    naam: str
    intensiteit_umol: float | None
    uren_per_teelt: float | None
    kw_per_kap: float | None


@dataclass(frozen=True)
class Gemeenschappelijk:
    oppervlak_m2: float | None
    lichtsom_zon: float | None  # mol/m² over de teelt
    lbe: float | None  # g/mol
    doelgewicht_g: float | None
    prijs_eur_per_mwh: float | None
    opbrengst_per_tak: float | None
    overige_kosten_per_tak: float | None = None


def _vermenigvuldig(*waarden):
    uit = 1.0
    for w in waarden:
        if w is None:
            return None
        uit *= w
    return uit


def reken_scenario(s: Lichtscenario, g: Gemeenschappelijk) -> dict:
    lamp = _vermenigvuldig(s.intensiteit_umol, s.uren_per_teelt, UMOL_UREN_NAAR_MOL)
    lichtsom = None if lamp is None or g.lichtsom_zon is None else lamp + g.lichtsom_zon
    kwh = _vermenigvuldig(s.kw_per_kap, s.uren_per_teelt)
    mwh = kwh / KILO if kwh is not None else None
    stroom = kosten(kwh, g.prijs_eur_per_mwh)
    takken_m2 = None if lichtsom is None or g.lbe is None or not g.doelgewicht_g else g.lbe * lichtsom / g.doelgewicht_g
    takken_kap = _vermenigvuldig(takken_m2, g.oppervlak_m2)
    overig = _vermenigvuldig(takken_kap, g.overige_kosten_per_tak) if g.overige_kosten_per_tak else 0.0
    totaal_kosten = None if stroom is None or overig is None else stroom + overig
    opbrengst = _vermenigvuldig(takken_kap, g.opbrengst_per_tak)
    return {
        "naam": s.naam, "lichtsom_lamp": lamp, "lichtsom": lichtsom, "mwh_per_kap": mwh, "stroomkosten": stroom,
        "takken_per_m2": takken_m2, "takken_per_kap": takken_kap, "kosten": totaal_kosten,
        "kosten_per_tak": per_tak(totaal_kosten, takken_kap), "opbrengst": opbrengst,
        "saldo": None if opbrengst is None or totaal_kosten is None else opbrengst - totaal_kosten,
    }  # fmt: skip


def vergelijk(a: Lichtscenario, b: Lichtscenario, g: Gemeenschappelijk) -> dict:
    ra, rb = reken_scenario(a, g), reken_scenario(b, g)
    verschil = {}
    for sleutel, waarde in ra.items():
        if sleutel == "naam":
            continue
        verschil[sleutel] = None if waarde is None or rb[sleutel] is None else rb[sleutel] - waarde
    return {"a": ra, "b": rb, "verschil": verschil, "meeropbrengst": verschil["saldo"]}
