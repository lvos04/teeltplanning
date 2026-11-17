"""Lichtsom: zon + lampen (BOUWPLAN §6.3) en weeknormalen van de straling (§10).

    par_zon_binnen [mol/m²/dag] = straling [J/cm²/dag] × omrekenfactor (start 0,023) × transmissie
    par_lamp       [mol/m²/dag] = Σ lampgroepen: intensiteit [µmol/m²/s] × dimgewogen_uren × 0,0036
    lichtsom       [mol/m²]     = Σ (par_zon_binnen + par_lamp) over de dagen plantdatum … oogst

0,0036 = 3600 s/uur ÷ 1.000.000 µmol/mol (eenheidsomrekening). De dagen lopen van de plantdatum tot (niet
tot en met) de oogstdatum, zodat het aantal dagen gelijk is aan de teeltduur.

* Verleden (vóór de peildatum): gemeten straling en geïmporteerde branduren. Toekomst: weersverwachting of
  weeknormaal P50 (met P10/P90 als band) en het eigen historische gemiddelde aan dimgewogen uren per module en
  kalenderweek.
* Ontbreekt licht voor één dag van de teelt, dan is de lichtsom `None` met een melding welke dagen en waarom.
  Er wordt niets stilzwijgend aangevuld.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, fields
from datetime import date, timedelta
from itertools import accumulate

from .kalender import waarde_op

SECONDEN_PER_UUR = 3600
MICRO = 1_000_000
UMOL_UREN_NAAR_MOL = SECONDEN_PER_UUR / MICRO  # 0,0036
MILLE = 1000  # kWh × €/MWh → €
DAGEN_PER_WEEK = 7


@dataclass(frozen=True)
class Lampgroep:
    naam: str
    intensiteit_umol: float | None
    kw_per_kap: float | None
    dimbaar: bool
    module_van: int | None
    geldig_vanaf: date | None
    geldig_tot: date | None
    kap: int | None = None  # None = alle kappen
    status: str = "bevestigd"

    def module(self, kap: int) -> int | None:
        if self.module_van is None:
            return None
        return self.module_van + kap - 1

    def toestand(self, d: date) -> str:
        """'ja' (geldt), 'nee' (geldt niet) of 'onbekend' (begindatum onbekend)."""
        if self.geldig_tot is not None and d > self.geldig_tot:
            return "nee"
        if self.geldig_vanaf is None:
            return "onbekend"
        return "ja" if self.geldig_vanaf <= d else "nee"


@dataclass(frozen=True)
class Zondag:
    straling_j_cm2: float | None
    soort: str  # gemeten | historie | verwachting | normaal
    p10: float | None = None
    p90: float | None = None


@dataclass(frozen=True)
class LichtInvoer:
    zon: dict[date, Zondag]
    dag_uren: dict[tuple[int, date], tuple[float | None, bool]]  # (module, datum) → (dimgewogen uren, volledig)
    week_uren: dict[tuple[int, int, int], float | None]  # (module, ISO-jaar, week) → uren in die week
    normaal_uren: dict[tuple[int, int], float]  # (module, ISO-week) → gemiddelde dimgewogen uren per dag
    lampgroepen: tuple[Lampgroep, ...]
    peildatum: date
    par_factor: float | None
    transmissie: tuple[tuple[date | None, float | None], ...]
    toekomst_branduren: str = "historisch_gemiddelde"
    onvolledig_is_ontbrekend: bool = True
    weekuren_verdelen: bool = True
    prijs: tuple[tuple[date | None, float | None], ...] = ()  # €/MWh
    opslagen: tuple[tuple[date | None, float | None], ...] = ()  # €/MWh
    zon_normaal: dict[int, Zondag] = field(default_factory=dict)  # ISO-week → daggemiddelde (P50, P10, P90)


@dataclass(frozen=True)
class Dag:
    zon: float | None
    zon_laag: float | None
    zon_hoog: float | None
    lamp: float | None
    kwh: float | None
    reden: str | None  # waarom het licht van deze dag onbekend is
    soort_zon: str | None
    notities: tuple[str, ...] = ()


@dataclass(frozen=True)
class Lichtsom:
    zon: float | None
    lamp: float | None
    totaal: float | None
    laag: float | None
    hoog: float | None
    dagen: int
    ontbrekend: tuple[tuple[date, date, str], ...] = ()  # (van, tot, reden)
    soorten: dict[str, int] = field(default_factory=dict)  # dagen per soort zonlicht
    notities: tuple[str, ...] = ()

    @property
    def bekend(self) -> bool:
        return self.totaal is not None


class Rekenaar:
    """Rekent dagwaarden één keer per (kap, dag) en per dag (groepen, zon); gebruikt door het hele plan."""

    def __init__(self, invoer: LichtInvoer):
        self.inv = invoer
        self._dag: dict[tuple[int, date], Dag] = {}
        self._groepen: dict[date, list[tuple[Lampgroep, str]]] = {}
        self._zon: dict[date, tuple[float | None, float | None, float | None, str | None, str | None]] = {}
        self._bereik: tuple[date, date] | None = None
        self._reeksen = {}
        self._prijzen = None
        self._dagen = None
        self._onbekend: str | None = None

    def altijd_onbekend(self) -> str | None:
        """Reden als het licht op geen enkele dag bekend kan zijn (dan hoeft er niet per dag gerekend te worden)."""
        inv = self.inv
        if inv.par_factor is None:
            return "omrekenfactor straling → PAR onbekend"
        if all(w is None for _, w in inv.transmissie) or not inv.transmissie:
            return "kastransmissie onbekend"
        return self.lampen_altijd_onbekend()

    def lampen_altijd_onbekend(self) -> str | None:
        """Reden als de lampen (en dus kWh) op geen enkele dag bekend kunnen zijn."""
        for g in self.inv.lampgroepen:
            if g.kap is None and g.geldig_vanaf is None and g.geldig_tot is None:
                return f"lampgroep {g.naam}: geldigheidsperiode onbekend"
        return None

    def _zon_van(self, d: date):
        if d in self._zon:
            return self._zon[d]
        inv = self.inv
        z = inv.zon.get(d)
        if z is None and d >= inv.peildatum:
            z = inv.zon_normaal.get(d.isocalendar().week)
        transmissie = waarde_op(inv.transmissie, d)
        if inv.par_factor is None:
            uit = (None, None, None, None, "omrekenfactor straling → PAR onbekend")
        elif transmissie is None:
            uit = (None, None, None, None, "kastransmissie onbekend")
        elif z is None or z.straling_j_cm2 is None:
            uit = (None, None, None, None, "geen straling (geen meting, verwachting of weeknormaal)")
        else:
            f = inv.par_factor * transmissie
            par = z.straling_j_cm2 * f
            laag = z.p10 * f if z.p10 is not None else par
            hoog = z.p90 * f if z.p90 is not None else par
            uit = (par, laag, hoog, z.soort, None)
        self._zon[d] = uit
        return uit

    def groepen_op(self, d: date):
        if d not in self._groepen:
            self._groepen[d] = [(g, t) for g in self.inv.lampgroepen if (t := g.toestand(d)) != "nee"]
        return self._groepen[d]

    def _uren(self, module: int, d: date) -> tuple[float | None, str | None, str | None]:
        """(uren, reden als onbekend, notitie)."""
        inv = self.inv
        if d < inv.peildatum:
            dag = inv.dag_uren.get((module, d))
            if dag is not None and dag[0] is not None:
                if dag[1]:
                    return dag[0], None, None
                if not inv.onvolledig_is_ontbrekend:
                    return dag[0], None, f"onvolledige belichtingsdag gebruikt (module {module})"
                return None, f"onvolledige belichtingsdag (module {module})", None
            if inv.weekuren_verdelen:
                iso = d.isocalendar()
                week = inv.week_uren.get((module, iso.year, iso.week))
                if week is not None:
                    return week / DAGEN_PER_WEEK, None, "weekuren gelijk over de dagen verdeeld"
            return None, f"geen branduren voor module {module}", None
        if inv.toekomst_branduren != "historisch_gemiddelde":
            return None, f"bron voor toekomstige branduren '{inv.toekomst_branduren}' is nog niet beschikbaar", None
        gemiddeld = inv.normaal_uren.get((module, d.isocalendar().week))
        if gemiddeld is None:
            return (
                None,
                f"geen historisch gemiddelde branduren voor module {module} in week {d.isocalendar().week}",
                None,
            )
        return gemiddeld, None, None

    def _waarde(self, kap: int, d: date) -> tuple:
        """(zon, laag, hoog, lamp, kwh, reden, soort, notities) voor één kap en dag; gecachet."""
        sleutel = (kap, d)
        uit = self._dag.get(sleutel)
        if uit is not None:
            return uit
        zon, laag, hoog, soort, reden = self._zon_van(d)
        lamp, kwh = 0.0, 0.0
        notities: tuple[str, ...] = ()
        peil = self.inv.peildatum
        dag_uren = self.inv.dag_uren
        for groep, toestand in self.groepen_op(d):
            if groep.kap is not None and groep.kap != kap:
                continue
            if toestand == "onbekend":
                reden = reden or f"lampgroep {groep.naam}: geldigheidsperiode onbekend"
                lamp = kwh = None
                break
            if groep.module_van is None:
                reden = reden or f"lampgroep {groep.naam}: modulenummer onbekend"
                lamp = kwh = None
                break
            module = groep.module_van + kap - 1
            snel = dag_uren.get((module, d)) if d < peil else None
            if snel is not None and snel[1] and snel[0] is not None:
                uren = snel[0]  # gewone geval: volledige dag met dagdata
            else:
                uren, uren_reden, notitie = self._uren(module, d)
                if uren is None:
                    reden = reden or uren_reden
                    lamp = kwh = None
                    break
                if notitie:
                    notities += (notitie,)
            if groep.intensiteit_umol is None:
                reden = reden or f"lampgroep {groep.naam}: intensiteit onbekend"
                lamp = None
            elif lamp is not None:
                lamp += groep.intensiteit_umol * uren * UMOL_UREN_NAAR_MOL
            if kwh is not None:
                kwh = None if groep.kw_per_kap is None else kwh + groep.kw_per_kap * uren
        uit = (zon, laag, hoog, lamp, kwh, reden if (zon is None or lamp is None) else None, soort, notities)
        self._dag[sleutel] = uit
        return uit

    def dag(self, kap: int, d: date) -> Dag:
        return Dag(*self._waarde(kap, d))

    # -- Snelle sommen over een periode (prefixsommen per kap) ----------------------------------------------
    def bereid_voor(self, van: date, tot: date) -> None:
        """Bereik waarvoor per kap prefixsommen worden gemaakt (het hele plan)."""
        self._bereik = (van, tot)
        self._reeksen = {}
        self._prijzen = None
        self._dagen = None

    def _prijsreeks(self) -> list:
        if self._prijzen is None:
            van, tot = self._bereik
            self._prijzen = [self.prijs(van + timedelta(days=i)) for i in range((tot - van).days)]
        return self._prijzen

    def _dagwaarden(self, kap: int) -> list[tuple]:
        """Dagwaarden over het hele bereik; het gewone geval (verleden, volledige dagdata) zonder omwegen."""
        peil = self.inv.peildatum
        dag_uren = self.inv.dag_uren
        uit = []
        for d in self._dagen:
            zon, laag, hoog, soort, reden = self._zon_van(d)
            lamp = kwh = 0.0
            snel = d < peil
            if snel:
                for groep, toestand in self.groepen_op(d):
                    if groep.kap is not None and groep.kap != kap:
                        continue
                    if toestand != "ja" or groep.module_van is None or groep.intensiteit_umol is None:
                        snel = False
                        break
                    uren = dag_uren.get((groep.module_van + kap - 1, d))
                    if uren is None or not uren[1] or uren[0] is None:
                        snel = False
                        break
                    lamp += groep.intensiteit_umol * uren[0] * UMOL_UREN_NAAR_MOL
                    kwh = None if kwh is None or groep.kw_per_kap is None else kwh + groep.kw_per_kap * uren[0]
            if snel:
                uit.append((zon, laag, hoog, lamp, kwh, reden if zon is None else None, soort, ()))
            else:
                uit.append(self._waarde(kap, d))
        return uit

    def _reeks(self, kap: int) -> dict:
        reeks = self._reeksen.get(kap)
        if reeks is not None:
            return reeks
        van, tot = self._bereik
        prijzen = self._prijsreeks()
        if self._dagen is None:
            self._dagen = [van + timedelta(days=i) for i in range((tot - van).days)]
        kolommen = list(zip(*self._dagwaarden(kap), strict=True)) or [() for _ in fields(Dag)]
        zon, laag, hoog, lamp, kwh, _, soort, notities = kolommen
        mis = [z is None or lp is None for z, lp in zip(zon, lamp, strict=True)]
        bekend = [not m for m in mis]

        def som(reeks) -> list:
            return list(accumulate(reeks, initial=0))

        reeks = {
            "zon": som(z if b else 0 for z, b in zip(zon, bekend, strict=True)),
            "lamp": som(lp if b else 0 for lp, b in zip(lamp, bekend, strict=True)),
            "laag": som(lg + lp if b else 0 for lg, lp, b in zip(laag, lamp, bekend, strict=True)),
            "hoog": som(h + lp if b else 0 for h, lp, b in zip(hoog, lamp, bekend, strict=True)),
            "mis": som(mis),
            "notities": som(bool(n) for n in notities),
            "kwh": som(k or 0 for k in kwh),
            "kosten": som(
                k * p / MILLE if k is not None and p is not None else 0 for k, p in zip(kwh, prijzen, strict=True)
            ),
            "zonder_kwh": som(k is None for k in kwh),
            "zonder_prijs": som(k is not None and p is None for k, p in zip(kwh, prijzen, strict=True)),
            "soort": list(soort),
        }
        self._reeksen[kap] = reeks
        return reeks

    def _binnen(self, van: date, tot: date) -> tuple[int, int] | None:
        bereik = self._bereik
        if bereik is None or van < bereik[0] or tot > bereik[1]:
            return None
        return (van - bereik[0]).days, (tot - bereik[0]).days

    def energie_som(self, kap: int, van: date, tot: date) -> tuple[float, float, int, int]:
        """(kWh, kosten, dagen zonder kWh, dagen met kWh maar zonder prijs) over [van, tot)."""
        if self._onbekend is None:
            self._onbekend = self.altijd_onbekend() or ""
        if self._onbekend.startswith("lampgroep"):
            return 0.0, 0.0, (tot - van).days, 0
        index = self._binnen(van, tot)
        if index is not None:
            r, (a, b) = self._reeks(kap), index
            return (r["kwh"][b] - r["kwh"][a], r["kosten"][b] - r["kosten"][a], r["zonder_kwh"][b] - r["zonder_kwh"][a],
                    r["zonder_prijs"][b] - r["zonder_prijs"][a])  # fmt: skip
        kwh = kosten = 0.0
        zonder_kwh = zonder_prijs = 0
        d = van
        while d < tot:
            w = self.dag(kap, d).kwh
            prijs = self.prijs(d)
            if w is None:
                zonder_kwh += 1
            else:
                kwh += w
                if prijs is None:
                    zonder_prijs += 1
                else:
                    kosten += w * prijs / MILLE
            d += timedelta(days=1)
        return kwh, kosten, zonder_kwh, zonder_prijs

    def lichtsom(self, kap: int, van: date | None, tot: date | None) -> Lichtsom | None:
        if van is None or tot is None or tot <= van:
            return None
        if self._onbekend is None:
            self._onbekend = self.altijd_onbekend() or ""
        if self._onbekend:
            return Lichtsom(
                None, None, None, None, None, (tot - van).days, ((van, tot - timedelta(days=1), self._onbekend),)
            )
        index = self._binnen(van, tot)
        if index is not None:
            r, (a, b) = self._reeks(kap), index
            if r["mis"][b] == r["mis"][a] and r["notities"][b] == r["notities"][a]:
                zon, lamp = r["zon"][b] - r["zon"][a], r["lamp"][b] - r["lamp"][a]
                soorten = dict(Counter(r["soort"][a:b]))
                return Lichtsom(zon, lamp, zon + lamp, r["laag"][b] - r["laag"][a], r["hoog"][b] - r["hoog"][a],
                                b - a, (), soorten, ())  # fmt: skip
        return self._lichtsom_per_dag(kap, van, tot)

    def _lichtsom_per_dag(self, kap: int, van: date, tot: date) -> Lichtsom:
        zon = lamp = laag = hoog = 0.0
        ontbrekend: list[dict] = []
        soorten: dict[str, int] = {}
        notities: set[str] = set()
        d = van
        dagen = (tot - van).days
        for _ in range(dagen):
            w_zon, w_laag, w_hoog, w_lamp, _, w_reden, w_soort, w_notities = self._waarde(kap, d)
            if w_zon is None or w_lamp is None:
                reden = w_reden or "onbekend"
                if ontbrekend and ontbrekend[-1]["tot"] == d - timedelta(days=1) and ontbrekend[-1]["reden"] == reden:
                    ontbrekend[-1]["tot"] = d
                else:
                    ontbrekend.append({"van": d, "tot": d, "reden": reden})
            else:
                zon += w_zon
                lamp += w_lamp
                laag += w_laag + w_lamp
                hoog += w_hoog + w_lamp
                soorten[w_soort] = soorten.get(w_soort, 0) + 1
                notities.update(w_notities)
            d += timedelta(days=1)
        if ontbrekend:
            return Lichtsom(None, None, None, None, None, dagen, tuple((o["van"], o["tot"], o["reden"]) for o in ontbrekend), soorten,
                            tuple(sorted(notities)))  # fmt: skip
        return Lichtsom(zon, lamp, zon + lamp, laag, hoog, dagen, (), soorten, tuple(sorted(notities)))

    def kwh(self, kap: int, van: date, tot: date) -> tuple[float | None, int]:
        """(kWh over [van, tot), aantal dagen zonder bekende kWh)."""
        totaal, onbekend = 0.0, 0
        d = van
        while d < tot:
            w = self.dag(kap, d)
            if w.kwh is None:
                onbekend += 1
            else:
                totaal += w.kwh
            d += timedelta(days=1)
        return (None if onbekend else totaal), onbekend

    def prijs(self, d: date) -> float | None:
        prijs = waarde_op(self.inv.prijs, d)
        if prijs is None:
            return None
        return prijs + (waarde_op(self.inv.opslagen, d) or 0)


# --- Weeknormalen ------------------------------------------------------------------------------------------
def percentiel(waarden: list[float], p: float) -> float | None:
    """Percentiel met lineaire interpolatie tussen de gesorteerde waarden (zoals Excel PERCENTIEL.INC)."""
    if not waarden:
        return None
    reeks = sorted(waarden)
    positie = (len(reeks) - 1) * p / 100
    onder = int(positie)
    boven = min(onder + 1, len(reeks) - 1)
    return reeks[onder] + (reeks[boven] - reeks[onder]) * (positie - onder)


def weeknormalen(dagen: dict[date, float], percentielen: tuple[float, float, float], max_jaren: int, min_jaren: int,
                 tot: date) -> dict[int, tuple[float | None, float | None, float | None, int]]:  # fmt: skip
    """P-laag/P50/P-hoog van de stralingssom per ISO-week, over de laatste `max_jaren` volledige weken vóór `tot`.

    Alleen weken met zeven dagen straling tellen. Een week met minder dan `min_jaren` jaren krijgt geen normaal.
    """
    sommen: dict[tuple[int, int], list[float]] = {}
    for d, waarde in dagen.items():
        if d >= tot or waarde is None:
            continue
        iso = d.isocalendar()
        sommen.setdefault((iso.year, iso.week), []).append(waarde)
    per_week: dict[int, list[tuple[int, float]]] = {}
    for (jaar, week), lijst in sommen.items():
        if len(lijst) == DAGEN_PER_WEEK:
            per_week.setdefault(week, []).append((jaar, sum(lijst)))
    uit = {}
    for week, reeks in per_week.items():
        reeks = sorted(reeks)[-max_jaren:]
        waarden = [w for _, w in reeks]
        if len(waarden) < min_jaren:
            uit[week] = (None, None, None, len(waarden))
        else:
            uit[week] = (*(percentiel(waarden, p) for p in percentielen), len(waarden))
    return uit
