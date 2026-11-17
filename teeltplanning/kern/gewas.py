"""Aantallen (BOUWPLAN §6.2). LBE, dichtheid en gewicht (§6.5) volgen in fase 3.

geplant     = dichtheid × oppervlak_kap
oogstaantal = geplant × (1 − uitval)
"""

from __future__ import annotations

from dataclasses import dataclass, field
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


# --- LBE, dichtheid en gewicht (§6.5) ----------------------------------------------------------------------
#
#     productie [g/m²]   = lbe(ras, plantweek, lichtregime) × lichtsom
#     takgewicht [g]     = productie / dichtheid
#     dichtheid_voorstel = lbe × lichtsom / doelgewicht
#     lbe_gerealiseerd   = gemiddeld_takgewicht × dichtheid / lichtsom      (per afgesloten teelt)
#
# De dichtheid is een beslissing: de app kiest de dichtheid die het doelgewicht raakt, afgerond op de dichtheden
# die in de praktijk voorkomen (instelling), en toont ook de niet-afgeronde waarde.


def productie(lbe: float | None, lichtsom: float | None) -> float | None:
    if lbe is None or lichtsom is None:
        return None
    return lbe * lichtsom


def takgewicht(productie_g_m2: float | None, dichtheid: float | None) -> float | None:
    if productie_g_m2 is None or not dichtheid:
        return None
    return productie_g_m2 / dichtheid


def dichtheid_voorstel(lbe: float | None, lichtsom: float | None, doelgewicht: float | None) -> float | None:
    if lbe is None or lichtsom is None or not doelgewicht:
        return None
    return lbe * lichtsom / doelgewicht


def lbe_gerealiseerd(
    gemiddeld_takgewicht: float | None, dichtheid: float | None, lichtsom: float | None
) -> float | None:
    if gemiddeld_takgewicht is None or dichtheid is None or not lichtsom:
        return None
    return gemiddeld_takgewicht * dichtheid / lichtsom


def afronden(waarde: float | None, reeks: tuple[float, ...]) -> float | None:
    """Dichtstbijzijnde waarde uit de reeks (bij gelijke afstand de kleinste); lege reeks = niet afronden."""
    if waarde is None or not reeks:
        return waarde
    return min(sorted(reeks), key=lambda r: abs(r - waarde))


@dataclass(frozen=True)
class LbeCel:
    lbe: float | None
    aantal: int
    status: str = "onvoldoende"


@dataclass(frozen=True)
class GewasInvoer:
    lbe: dict[tuple[str, int, str], LbeCel] = field(default_factory=dict)  # (ras, plantweek, lichtregime)
    doelgewicht: dict[str, float | None] = field(default_factory=dict)  # ras → g
    min_waarnemingen: int | None = None
    venster_weken: int = 0
    dichtheid_reeks: tuple[float, ...] = ()
    lichtregimes: tuple[tuple[str, float, float], ...] = ()  # (naam, µmol van, µmol tot)


@dataclass(frozen=True)
class LbeKeuze:
    waarde: float | None
    status: str | None  # gevalideerd | indicatief | None (onbekend)
    uitleg: str
    aantal: int = 0


def _weekafstand(a: int, b: int, jaarweken: int) -> int:
    verschil = abs(a - b)
    return min(verschil, abs(verschil - jaarweken))


def kies_lbe(inv: GewasInvoer, ras: str, plantweek: int, regime: str, jaarweken: int) -> LbeKeuze:
    """LBE uit de tabel; te weinig waarnemingen → seizoensgemiddelde van het ras (± venster), status indicatief."""
    cel = inv.lbe.get((ras, plantweek, regime))
    if cel and cel.lbe is not None and inv.min_waarnemingen is not None and cel.aantal >= inv.min_waarnemingen:
        return LbeKeuze(cel.lbe, "gevalideerd", f"LBE-tabel {ras}, week {plantweek}", cel.aantal)
    gewogen, aantal = 0.0, 0
    for (r, week, reg), c in inv.lbe.items():
        if r == ras and reg == regime and c.lbe is not None and c.aantal > 0:
            if _weekafstand(week, plantweek, jaarweken) <= inv.venster_weken:
                gewogen += c.lbe * c.aantal
                aantal += c.aantal
    if aantal:
        reden = "minimum aantal waarnemingen onbekend" if inv.min_waarnemingen is None else "te weinig waarnemingen"
        return LbeKeuze(gewogen / aantal, "indicatief",
                        f"seizoensgemiddelde {ras}, week {plantweek} ± {inv.venster_weken} ({reden})", aantal)  # fmt: skip
    return LbeKeuze(None, None, f"geen LBE voor ras {ras} rond week {plantweek}")


def lichtregime(inv: GewasInvoer, umol: float | None) -> str:
    if not inv.lichtregimes:
        return ""
    if umol is None:
        return ""
    for naam, van, tot in inv.lichtregimes:
        if van <= umol < tot:
            return naam
    return ""


def lbe_teelt(
    inv: GewasInvoer, rassen: tuple[tuple[str, int | None], ...], plantweek: int, regime: str, jaarweken: int
):
    """(LBE, doelgewicht, keuzes) voor een teelt; gemengde kap = gewogen naar bedden."""
    if not rassen:
        return None, None, [LbeKeuze(None, None, "ras onbekend")]
    keuzes, gewichten = [], []
    for code, bedden in rassen:
        keuzes.append(kies_lbe(inv, code, plantweek, regime, jaarweken))
        gewichten.append(bedden if bedden else 1)
    totaal = sum(gewichten)
    if any(k.waarde is None for k in keuzes):
        lbe = None
    else:
        lbe = sum(k.waarde * g for k, g in zip(keuzes, gewichten, strict=True)) / totaal
    doelen = [inv.doelgewicht.get(code) for code, _ in rassen]
    doel = (
        None if any(d is None for d in doelen) else sum(d * g for d, g in zip(doelen, gewichten, strict=True)) / totaal
    )
    return lbe, doel, keuzes
