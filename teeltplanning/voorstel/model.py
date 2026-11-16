"""Voorstelladder met voortschrijdende backtest (BOUWPLAN §6.7 en §8.3). Zuivere Python: geen database, geen web,
geen numpy/scikit-learn (zie docs/ARCHITECTUUR.md, "Model zonder extra pakketten").

Treden per doel (een hogere trede wordt pas gebruikt als hij de lagere in de backtest verslaat):

| doel | treden |
|---|---|
| `ld_dagen`, `verlenging` | 1 seizoensbasis |
| `reactietijd` | 1 seizoensbasis, 2 temperatuurcorrectie (§6.7; standaard uit) |
| `gewicht` | 1 seizoensbasis, 2 LBE-identiteit, 3 ridge op het residu, 4 bomen met monotone beperkingen |

* **Seizoensbasis**: mediaan van hetzelfde ras binnen ± venster plantweken in **eerdere** ISO-jaren (eventueel
  begrensd tot de terugkijkperiode). Band = percentielen van diezelfde waarden.
* **LBE-identiteit**: LBE (gemiddelde per ras en plantweek, terugval op het seizoensgemiddelde zoals de kern het
  doet) × lichtsom / dichtheid.
* **Ridge** (gesloten vorm) en **bomen** (gradient boosting met stompjes) voorspellen het residu van de
  LBE-identiteit. Monotone beperkingen gelden per stompje, dus ook voor de som: meer licht nooit lichter, hogere
  dichtheid nooit zwaarder.
* **Backtest**: voortschrijdend per ISO-jaar: trainen t/m jaar N, toetsen op N+1. Geen willekeurige kruisvalidatie.
  Een trede is "gevalideerd" als zijn gemiddelde absolute fout op dezelfde toetsteelten lager is dan die van de
  seizoensbasis (met de ingestelde drempel en minimum aantal toetsteelten). Anders blijft hij "indicatief".

Alle rekenwaarden (venster, percentielen, drempels, hyperparameters) komen uit het instellingenregister.
"""

from __future__ import annotations

import copy
import math
import statistics
from bisect import bisect_left
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ..kern import gewas
from ..kern.kalender import weken_in_jaar
from ..kern.licht import percentiel
from ..kern.typen import GEHELE_VELDEN, Vergelijkbaar, Voorspelling

DOELEN = ("ld_dagen", "verlenging", "reactietijd", "gewicht")
TREDEN = {
    "ld_dagen": ("seizoensbasis",),
    "verlenging": ("seizoensbasis",),
    "reactietijd": ("seizoensbasis", "temperatuur"),
    "gewicht": ("seizoensbasis", "lbe", "ridge", "bomen"),
}
ALLE_TREDEN = ("seizoensbasis", "temperatuur", "lbe", "ridge", "bomen")
KENMERKEN = ("plantweek", "lichtsom", "dichtheid", "ld_dagen", "ras")
TREDE_LABEL = {
    "seizoensbasis": "seizoensbasis",
    "temperatuur": "seizoensbasis met temperatuurcorrectie",
    "lbe": "LBE-identiteit",
    "ridge": "ridge-regressie op het residu",
    "bomen": "bomen met monotone beperkingen",
}
WAARNEMING_VELDEN = ("ld_dagen", "verlenging", "reactietijd", "gewicht", "dichtheid", "uitval")
EENHEID = {"ld_dagen": "dagen", "verlenging": "dagen", "reactietijd": "dagen", "gewicht": "g"}


# --- Waarnemingen ----------------------------------------------------------------------------------------------
@dataclass
class Waarneming:
    """Eén afgesloten teelt uit de historie (werkboek, teeltschema of Gewichtsverwachting)."""

    sleutel: str
    bron: str
    kap: int | None
    ras: str | None  # één ras; None = gemengde kap of onbekend (telt dan niet mee voor een ras)
    plantdatum: date
    ld_dagen: float | None = None
    verlenging: float | None = None
    reactietijd: float | None = None
    gewicht: float | None = None
    dichtheid: float | None = None
    lichtsom: float | None = None
    temperatuur: float | None = None  # gemiddelde etmaaltemperatuur in de korte dag
    uitval: float | None = None

    @property
    def jaar(self) -> int:
        return self.plantdatum.isocalendar().year

    @property
    def week(self) -> int:
        return self.plantdatum.isocalendar().week

    def naar_dict(self) -> dict:
        d = asdict(self)
        d["plantdatum"] = self.plantdatum.isoformat()
        return d

    @classmethod
    def uit_dict(cls, d: dict) -> Waarneming:
        return cls(**(d | {"plantdatum": date.fromisoformat(d["plantdatum"])}))


@dataclass(frozen=True)
class ModelInstellingen:
    venster: int
    terugkijk: int | None
    min_waarnemingen: int
    band: tuple[float, float]
    treden: tuple[str, ...]
    kenmerken_ridge: tuple[str, ...]
    kenmerken_bomen: tuple[str, ...]
    monotoon: dict[str, int]
    drempel_pct: float | None
    backtest_jaren: int | None
    min_toets: int | None
    ridge_alpha: float
    bomen_aantal: int
    bomen_leersnelheid: float
    bomen_min_blad: int
    lbe_min_waarnemingen: int | None
    temperatuur_aan: bool
    referentietemperatuur: float | None
    vergelijkbaar: int
    gevoeligheid: dict[str, float | None] = field(default_factory=dict)

    def naar_dict(self) -> dict:
        d = asdict(self)
        d["band"] = list(self.band)
        return d

    @classmethod
    def uit_dict(cls, d: dict) -> ModelInstellingen:
        tupels = ("treden", "kenmerken_ridge", "kenmerken_bomen")
        return cls(**(d | {"band": tuple(d["band"])} | {k: tuple(d[k]) for k in tupels}))


# --- Hulpjes ---------------------------------------------------------------------------------------------------
def weekafstand(a: int, b: int, jaarweken: int) -> int:
    verschil = abs(a - b)
    return min(verschil, abs(verschil - jaarweken))


def afronden_geheel(waarde: float) -> int:
    return int(Decimal(str(waarde)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _gewichten(rassen: tuple) -> list[tuple[str, float]]:
    return [(code, float(bedden) if bedden else 1.0) for code, bedden in rassen]


def mae(fouten: list[float]) -> float | None:
    return sum(abs(f) for f in fouten) / len(fouten) if fouten else None


# --- Trede 1: seizoensbasis -------------------------------------------------------------------------------------
class Seizoensbasis:
    """Mediaan per ras binnen ± venster plantweken in eerdere ISO-jaren."""

    def __init__(self, waarnemingen: list[Waarneming], doel: str, inst: ModelInstellingen):
        self.doel, self.inst = doel, inst
        self.per_ras: dict[str, dict[int, list[tuple[int, Waarneming]]]] = {}
        for w in waarnemingen:
            if w.ras is None or getattr(w, doel) is None:
                continue
            self.per_ras.setdefault(w.ras, {}).setdefault(w.week, []).append((w.jaar, w))
        for weken in self.per_ras.values():
            for lijst in weken.values():
                lijst.sort(key=lambda x: (x[0], x[1].sleutel))
        self._cache: dict = {}

    def venster(self, ras: str, plantdatum: date) -> list[Waarneming]:
        iso = plantdatum.isocalendar()
        sleutel = (ras, iso.week, iso.year)
        if sleutel in self._cache:
            return self._cache[sleutel]
        jaarweken = weken_in_jaar(iso.year)
        laagste_jaar = iso.year - self.inst.terugkijk if self.inst.terugkijk else None
        uit = []
        for week, lijst in sorted(self.per_ras.get(ras, {}).items()):
            if weekafstand(week, iso.week, jaarweken) > self.inst.venster:
                continue
            eind = bisect_left(lijst, iso.year, key=lambda x: x[0])  # alleen eerdere ISO-jaren
            uit += [w for jaar, w in lijst[:eind] if laagste_jaar is None or jaar >= laagste_jaar]
        self._cache[sleutel] = uit
        return uit

    def van_ras(self, ras: str, plantdatum: date) -> Schatting | None:
        waarden = self.venster(ras, plantdatum)
        if len(waarden) < max(self.inst.min_waarnemingen, 1):
            return None
        getallen = [getattr(w, self.doel) for w in waarden]
        laag, hoog = self.inst.band
        return Schatting(statistics.median(getallen), percentiel(getallen, laag), percentiel(getallen, hoog), waarden)

    def voorspel(self, rassen: tuple, plantdatum: date) -> Schatting | None:
        """Mediaan met band en venster; gemengde kap = gewogen naar bedden. None = geen eerdere teelten."""
        if not rassen:
            return None
        delen = []
        for code, gewicht in _gewichten(rassen):
            deel = self.van_ras(code, plantdatum)
            if deel is None:
                return None
            delen.append((deel, gewicht))
        totaal = sum(g for _, g in delen)

        def gewogen(naam):
            if any(getattr(d, naam) is None for d, _ in delen):
                return None
            return sum(getattr(d, naam) * g for d, g in delen) / totaal

        venster = [w for d, _ in delen for w in d.venster]
        return Schatting(gewogen("waarde"), gewogen("laag"), gewogen("hoog"), venster)


@dataclass(frozen=True)
class Schatting:
    waarde: float
    laag: float | None
    hoog: float | None
    venster: list

    @property
    def aantal(self) -> int:
        return len(self.venster)


# --- Trede 2: LBE-identiteit ------------------------------------------------------------------------------------
class LbeTabel:
    """LBE per (ras, plantweek) uit de trainingsteelten; keuze en terugval zoals de kern (`kern.gewas.kies_lbe`)."""

    def __init__(self, waarnemingen: list[Waarneming], inst: ModelInstellingen):
        per_cel: dict[tuple[str, int, str], list[float]] = {}
        for w in waarnemingen:
            lbe = gewas.lbe_gerealiseerd(w.gewicht, w.dichtheid, w.lichtsom)
            if w.ras is not None and lbe is not None:
                per_cel.setdefault((w.ras, w.week, ""), []).append(lbe)
        cellen = {k: gewas.LbeCel(sum(v) / len(v), len(v), "kalibratie") for k, v in per_cel.items()}
        self.invoer = gewas.GewasInvoer(lbe=cellen, min_waarnemingen=inst.lbe_min_waarnemingen,
                                        venster_weken=inst.venster)  # fmt: skip

    def lbe(self, rassen: tuple, plantdatum: date) -> float | None:
        iso = plantdatum.isocalendar()
        waarde, _, _ = gewas.lbe_teelt(self.invoer, rassen, iso.week, "", weken_in_jaar(iso.year))
        return waarde

    def voorspel(self, rassen: tuple, plantdatum: date, lichtsom, dichtheid) -> float | None:
        return gewas.takgewicht(gewas.productie(self.lbe(rassen, plantdatum), lichtsom), dichtheid)


# --- Kenmerken voor ridge en bomen ------------------------------------------------------------------------------
def kenmerk_namen(namen: tuple[str, ...], rassen: list[str]) -> list[str]:
    uit = []
    for naam in namen:
        if naam == "plantweek":
            uit += ["plantweek_sin", "plantweek_cos"]
        elif naam == "ras":
            uit += [f"ras_{r}" for r in rassen]
        else:
            uit.append(naam)
    return uit


def kenmerken(namen: tuple[str, ...], rassen_model: list[str], rassen: tuple, plantdatum: date,
              waarden: dict[str, Any]) -> list[float] | None:  # fmt: skip
    """Kenmerkvector; None als een gevraagd kenmerk ontbreekt."""
    uit: list[float] = []
    for naam in namen:
        if naam == "plantweek":
            iso = plantdatum.isocalendar()
            hoek = math.tau * (iso.week - 1) / weken_in_jaar(iso.year)
            uit += [math.sin(hoek), math.cos(hoek)]
        elif naam == "ras":
            if not rassen:
                return None
            gewichten = _gewichten(rassen)
            totaal = sum(g for _, g in gewichten)
            aandeel = {code: g / totaal for code, g in gewichten}
            if any(code not in rassen_model for code in aandeel):
                return None
            uit += [aandeel.get(r, 0.0) for r in rassen_model]
        else:
            waarde = waarden.get(naam)
            if waarde is None:
                return None
            uit.append(float(waarde))
    return uit


def _rassen_tupel(w: Waarneming) -> tuple:
    return ((w.ras, None),) if w.ras else ()


# --- Trede 3: ridge ---------------------------------------------------------------------------------------------
def _los_op(a: list[list[float]], b: list[float]) -> list[float]:
    """Gauss-eliminatie met gedeeltelijke pivotering (kleine, positief-definiete stelsels)."""
    n = len(b)
    m = [rij[:] + [b[i]] for i, rij in enumerate(a)]
    for k in range(n):
        p = max(range(k, n), key=lambda i: abs(m[i][k]))
        m[k], m[p] = m[p], m[k]
        if m[k][k] == 0:
            raise ValueError("stelsel is singulier")
        for i in range(k + 1, n):
            f = m[i][k] / m[k][k]
            for j in range(k, n + 1):
                m[i][j] -= f * m[k][j]
    x = [0.0] * n
    for i in reversed(range(n)):
        x[i] = (m[i][n] - sum(m[i][j] * x[j] for j in range(i + 1, n))) / m[i][i]
    return x


@dataclass
class Ridge:
    gemiddelden: list[float]
    schalen: list[float]
    coef: list[float]
    snijpunt: float

    @classmethod
    def fit(cls, x: list[list[float]], y: list[float], alpha: float) -> Ridge:
        n, p = len(x), len(x[0])
        gem = [sum(r[j] for r in x) / n for j in range(p)]
        schaal = []
        for j in range(p):
            sd = math.sqrt(sum((r[j] - gem[j]) * (r[j] - gem[j]) for r in x) / n)
            schaal.append(sd if sd > 0 else 1.0)
        z = [[(r[j] - gem[j]) / schaal[j] for j in range(p)] for r in x]
        ygem = sum(y) / n
        yc = [v - ygem for v in y]
        a = [
            [sum(z[i][j] * z[i][k] for i in range(n)) + (alpha if j == k else 0.0) for k in range(p)] for j in range(p)
        ]
        b = [sum(z[i][j] * yc[i] for i in range(n)) for j in range(p)]
        return cls(gem, schaal, _los_op(a, b), ygem)

    def voorspel(self, rij: list[float]) -> float:
        return self.snijpunt + sum(c * (v - g) / s for c, v, g, s in
                                   zip(self.coef, rij, self.gemiddelden, self.schalen, strict=True))  # fmt: skip


# --- Trede 4: bomen met monotone beperkingen ---------------------------------------------------------------------
@dataclass
class Bomen:
    """Som van stompjes per kenmerk, opgeslagen als trapfunctie: f_j(x) = rechts_j + Σ_{t ≥ x} (links − rechts)."""

    drempels: list[list[float]]
    sprongen: list[list[float]]  # staartsommen van (links − rechts), zelfde volgorde als drempels
    basis: float

    @classmethod
    def fit(cls, x: list[list[float]], y: list[float], richting: list[int], aantal: int, leersnelheid: float,
            min_blad: int) -> Bomen:  # fmt: skip
        n, p = len(x), len(x[0])
        volgorde = [sorted(range(n), key=lambda i, j=j: x[i][j]) for j in range(p)]
        f = [0.0] * n
        stompjes: list[tuple[int, float, float, float]] = []
        for _ in range(aantal):
            rest = [y[i] - f[i] for i in range(n)]
            totaal = sum(rest)
            beste = None
            for j in range(p):
                orde = volgorde[j]
                links = 0.0
                for pos in range(n - 1):
                    links += rest[orde[pos]]
                    n_links = pos + 1
                    n_rechts = n - n_links
                    if n_links < min_blad or n_rechts < min_blad:
                        continue
                    if x[orde[pos]][j] == x[orde[pos + 1]][j]:
                        continue
                    gem_l, gem_r = links / n_links, (totaal - links) / n_rechts
                    if (richting[j] > 0 and gem_l > gem_r) or (richting[j] < 0 and gem_l < gem_r):
                        continue
                    winst = links * links / n_links + (totaal - links) * (totaal - links) / n_rechts
                    if beste is None or winst > beste[0]:
                        beste = (winst, j, x[orde[pos]][j], gem_l, gem_r)
            if beste is None:
                break
            _, j, drempel, gem_l, gem_r = beste
            links_w, rechts_w = gem_l * leersnelheid, gem_r * leersnelheid
            stompjes.append((j, drempel, links_w, rechts_w))
            for i in range(n):
                f[i] += links_w if x[i][j] <= drempel else rechts_w
        drempels: list[list[float]] = [[] for _ in range(p)]
        verschil: list[list[float]] = [[] for _ in range(p)]
        basis = 0.0
        per_kenmerk: dict[int, list[tuple[float, float]]] = {}
        for j, drempel, lw, rw in stompjes:
            basis += rw
            per_kenmerk.setdefault(j, []).append((drempel, lw - rw))
        for j, lijst in per_kenmerk.items():
            lijst.sort()
            drempels[j] = [t for t, _ in lijst]
            verschil[j] = [sum(d for _, d in lijst[i:]) for i in range(len(lijst))]  # staartsommen
        return cls(drempels, verschil, basis)

    def voorspel(self, rij: list[float]) -> float:
        som = self.basis
        for j, waarde in enumerate(rij):
            drempels = self.drempels[j]
            if drempels:
                i = bisect_left(drempels, waarde)  # eerste drempel ≥ waarde: daar geldt "links"
                if i < len(drempels):
                    som += self.sprongen[j][i]
        return som


# --- Een getraind model per doel en trede --------------------------------------------------------------------------
class Ladder:
    """Alle treden van één doel, getraind op één verzameling waarnemingen."""

    def __init__(self, doel: str, waarnemingen: list[Waarneming], inst: ModelInstellingen, fit: bool = True):
        self.doel, self.inst = doel, inst
        self.basis = Seizoensbasis(waarnemingen, doel, inst)
        self.lbe = LbeTabel(waarnemingen, inst) if doel == "gewicht" else None
        self.rassen = sorted({w.ras for w in waarnemingen if w.ras})
        self.ridge: Ridge | None = None
        self.bomen: Bomen | None = None
        self.redenen: dict[str, str] = {}
        if doel == "gewicht" and fit:
            self._fit_residu(waarnemingen)

    def modellen(self) -> dict:
        return {"rassen": self.rassen, "ridge": asdict(self.ridge) if self.ridge else None,
                "bomen": asdict(self.bomen) if self.bomen else None, "redenen": self.redenen}  # fmt: skip

    def laad_modellen(self, d: dict) -> None:
        self.rassen = d.get("rassen") or self.rassen
        self.ridge = Ridge(**d["ridge"]) if d.get("ridge") else None
        self.bomen = Bomen(**d["bomen"]) if d.get("bomen") else None
        self.redenen = d.get("redenen") or {}

    def _residu_rijen(self, waarnemingen, namen):
        x, y = [], []
        for w in waarnemingen:
            if w.gewicht is None:
                continue
            basis = self.lbe.voorspel(_rassen_tupel(w), w.plantdatum, w.lichtsom, w.dichtheid)
            rij = kenmerken(namen, self.rassen, _rassen_tupel(w), w.plantdatum, asdict(w))
            if basis is None or rij is None:
                continue
            x.append(rij)
            y.append(w.gewicht - basis)
        return x, y

    def _fit_residu(self, waarnemingen):
        minimum = max(self.inst.min_waarnemingen, self.inst.bomen_min_blad, 1)
        if "ridge" in self.inst.treden:
            x, y = self._residu_rijen(waarnemingen, self.inst.kenmerken_ridge)
            if len(x) > minimum and x and x[0]:
                try:
                    self.ridge = Ridge.fit(x, y, self.inst.ridge_alpha)
                except ValueError as fout:
                    self.redenen["ridge"] = str(fout)
            else:
                self.redenen["ridge"] = "te weinig teelten met lichtsom, dichtheid en gewicht"
        if "bomen" in self.inst.treden:
            namen = self.inst.kenmerken_bomen
            x, y = self._residu_rijen(waarnemingen, namen)
            if len(x) > minimum and x and x[0]:
                richting = []
                for naam in kenmerk_namen(namen, self.rassen):
                    keuze = self.inst.monotoon.get(naam)
                    richting.append(1 if keuze == 1 else (-1 if keuze == -1 else 0))
                self.bomen = Bomen.fit(x, y, richting, self.inst.bomen_aantal, self.inst.bomen_leersnelheid,
                                       self.inst.bomen_min_blad)  # fmt: skip
            else:
                self.redenen["bomen"] = "te weinig teelten met lichtsom, dichtheid en gewicht"

    def voorspel(self, trede: str, rassen: tuple, plantdatum: date, waarden: dict[str, Any]) -> float | None:
        """Puntschatting van één trede; None als de invoer of het model ontbreekt."""
        if trede == "seizoensbasis":
            uit = self.basis.voorspel(rassen, plantdatum)
            return None if uit is None else uit.waarde
        if trede == "temperatuur":
            uit = self.basis.voorspel(rassen, plantdatum)
            t, ref = waarden.get("temperatuur"), self.inst.referentietemperatuur
            if uit is None or t is None or ref is None or not self.inst.temperatuur_aan or not rassen:
                return None
            gevoeligheden = [(self.inst.gevoeligheid.get(code), g) for code, g in _gewichten(rassen)]
            if any(gv is None for gv, _ in gevoeligheden):
                return None
            gevoeligheid = sum(gv * g for gv, g in gevoeligheden) / sum(g for _, g in gevoeligheden)
            return uit.waarde + gevoeligheid * (ref - t)
        if self.lbe is None:
            return None
        basis = self.lbe.voorspel(rassen, plantdatum, waarden.get("lichtsom"), waarden.get("dichtheid"))
        if trede == "lbe" or basis is None:
            return basis
        model, namen = (self.ridge, self.inst.kenmerken_ridge) if trede == "ridge" else (self.bomen,
                                                                                      self.inst.kenmerken_bomen)  # fmt: skip
        if model is None:
            return None
        rij = kenmerken(namen, self.rassen, rassen, plantdatum, waarden)
        return None if rij is None else basis + model.voorspel(rij)


# --- Backtest ------------------------------------------------------------------------------------------------------
def _toetsjaren(waarnemingen: list[Waarneming], doel: str, inst: ModelInstellingen) -> list[int]:
    jaren = sorted({w.jaar for w in waarnemingen if getattr(w, doel) is not None and w.ras})
    toets = jaren[1:]
    if inst.backtest_jaren:
        toets = toets[-inst.backtest_jaren :]
    return toets


def backtest(waarnemingen: list[Waarneming], doel: str, inst: ModelInstellingen) -> dict:
    """Voortschrijdend: per toetsjaar Y trainen op alle jaren < Y en toetsen op Y. Per trede: fouten, MAE per jaar,
    gepaarde vergelijking met de seizoensbasis, status en de gekozen trede."""
    treden = [t for t in TREDEN[doel] if t == "seizoensbasis" or t in inst.treden]
    fouten: dict[str, dict[int, list[tuple[str, float, float]]]] = {t: {} for t in treden}  # sleutel, fout, basisfout
    for jaar in _toetsjaren(waarnemingen, doel, inst):
        training = [w for w in waarnemingen if w.jaar < jaar]
        ladder = Ladder(doel, training, inst)
        for w in waarnemingen:
            echt = getattr(w, doel)
            if w.jaar != jaar or echt is None or not w.ras:
                continue
            rassen, waarden = _rassen_tupel(w), asdict(w)
            basis = ladder.voorspel("seizoensbasis", rassen, w.plantdatum, waarden)
            if basis is None:
                continue
            for trede in treden:
                schatting = basis if trede == "seizoensbasis" else ladder.voorspel(trede, rassen, w.plantdatum, waarden)
                if schatting is not None:
                    fouten[trede].setdefault(jaar, []).append((w.sleutel, echt - schatting, echt - basis))
    uit: dict[str, Any] = {"doel": doel, "toetsjaren": _toetsjaren(waarnemingen, doel, inst), "treden": {}}
    drempel = (inst.drempel_pct or 0) / 100
    for trede in treden:
        alle = [f for lijst in fouten[trede].values() for f in lijst]
        resultaat = {
            "n": len(alle),
            "mae": mae([f for _, f, _ in alle]),
            "mae_basis": mae([b for _, _, b in alle]),
            "jaren": {
                str(j): {"n": len(r), "mae": mae([f for _, f, _ in r]), "mae_basis": mae([b for _, _, b in r])}
                for j, r in sorted(fouten[trede].items())
            },  # fmt: skip
            "residu": [percentiel([f for _, f, _ in alle], p) for p in inst.band] if alle else [None, None],
        }
        if trede == "seizoensbasis":
            resultaat["status"] = "basis" if alle else "onvoldoende"
            resultaat["reden"] = None if alle else "geen toetsteelten met een seizoensbasis"
        elif not alle:
            resultaat["status"], resultaat["reden"] = "onvoldoende", "geen toetsteelten waarvoor deze trede rekent"
        elif inst.min_toets is not None and len(alle) < inst.min_toets:
            resultaat["status"] = "indicatief"
            resultaat["reden"] = f"te weinig toetsteelten ({len(alle)} < {inst.min_toets})"
        elif resultaat["mae"] < resultaat["mae_basis"] * (1 - drempel):
            resultaat["status"], resultaat["reden"] = "gevalideerd", None
        else:
            resultaat["status"], resultaat["reden"] = "indicatief", "verslaat de seizoensbasis niet"
        if resultaat["mae"] is not None and resultaat["mae_basis"]:
            resultaat["verbetering_pct"] = (resultaat["mae_basis"] - resultaat["mae"]) / resultaat["mae_basis"] * 100
        else:
            resultaat["verbetering_pct"] = None
        resultaat["fouten"] = {k: [[s, f] for s, f, _ in v] for k, v in ((str(j), r) for j, r in fouten[trede].items())}
        uit["treden"][trede] = resultaat
    # Ladder: een hogere trede alleen als hij gevalideerd is én de tot dan gekozen trede op dezelfde teelten verslaat.
    gekozen = "seizoensbasis"
    for trede in treden[1:]:
        r = uit["treden"][trede]
        if r["status"] != "gevalideerd":
            continue
        if gekozen == "seizoensbasis" or _verslaat(fouten[trede], fouten[gekozen], drempel, inst.min_toets):
            gekozen = trede
    uit["gekozen"] = gekozen
    return uit


def _verslaat(hoger: dict, lager: dict, drempel: float, min_toets: int | None) -> bool:
    a = {s: f for lijst in hoger.values() for s, f, _ in lijst}
    b = {s: f for lijst in lager.values() for s, f, _ in lijst}
    gedeeld = sorted(set(a) & set(b))
    if not gedeeld or (min_toets is not None and len(gedeeld) < min_toets):
        return False
    return mae([a[s] for s in gedeeld]) < mae([b[s] for s in gedeeld]) * (1 - drempel)


# --- Trainen (draait in een apart proces) ----------------------------------------------------------------------------
def train(waarnemingen: list[dict], instellingen: dict, versie: str) -> dict:
    """Backtest per doel en het artefact voor de live voorstellen. Invoer en uitvoer zijn gewone JSON-waarden."""
    import os
    import time

    begin = time.monotonic()
    inst = ModelInstellingen.uit_dict(instellingen)
    obs = [Waarneming.uit_dict(w) for w in waarnemingen]
    resultaten = {doel: backtest(obs, doel, inst) for doel in DOELEN}
    for r in resultaten.values():
        for t in r["treden"].values():
            t.pop("fouten", None)
    # Eindmodel op alle waarnemingen; de webapp laadt alleen de parameters en hoeft niets te fitten.
    modellen = {doel: Ladder(doel, obs, inst).modellen() for doel in DOELEN}
    return {
        "versie": versie,
        "instellingen": inst.naar_dict(),
        "waarnemingen": waarnemingen,
        "backtest": resultaten,
        "modellen": modellen,
        "rekentijd_s": time.monotonic() - begin,
        "pid": os.getpid(),
    }


# --- Live voorstellen ---------------------------------------------------------------------------------------------
class Voorspeller:
    """Voorstellen voor de kern (protocol `kern.typen.Voorspeller`) uit één getrainde modelversie.

    `aan`: per veld aan/uit (instellingen *Voorstellen*). `temperaturen`: verwachte etmaaltemperatuur per dag
    (gemeten, verwachting of weeknormaal) voor de temperatuurcorrectie."""

    def __init__(self, artefact: dict, aan: dict[str, bool], temperaturen: dict[date, float] | None = None):
        self.versie = artefact["versie"]
        self.inst = ModelInstellingen.uit_dict(artefact["instellingen"])
        self.obs = [Waarneming.uit_dict(w) for w in artefact["waarnemingen"]]
        self.backtest = artefact["backtest"]
        self.aan = aan
        self.temperaturen = temperaturen or {}
        self.ladders = {doel: Ladder(doel, self.obs, self.inst, fit=False) for doel in DOELEN}
        for doel, d in (artefact.get("modellen") or {}).items():
            self.ladders[doel].laad_modellen(d)
        self._cache: dict = {}
        self._extra: dict[str, Seizoensbasis] = {}
        schaal = {}
        for naam in ("lichtsom", "dichtheid"):
            waarden = [getattr(w, naam) for w in self.obs if getattr(w, naam) is not None]
            schaal[naam] = statistics.pstdev(waarden) if len(waarden) > 1 else None
        self.schaal = schaal

    def met(self, aan: dict[str, bool], temperaturen: dict[date, float] | None) -> Voorspeller:
        """Zelfde getrainde versie met andere schakelaars per veld en temperaturen (geen nieuwe training)."""
        kopie = copy.copy(self)
        kopie.aan, kopie.temperaturen, kopie._cache = aan, temperaturen or {}, {}
        return kopie

    # -- protocol --
    def veld(self, naam: str, rassen: tuple, plantdatum: date, licht_uit: date | None) -> Voorspelling | None:
        if naam not in ("ld_dagen", "verlenging", "reactietijd") or not self.aan.get(naam):
            return None
        temperatuur = self._temperatuur(rassen, plantdatum, licht_uit) if naam == "reactietijd" else None
        iso = plantdatum.isocalendar()
        sleutel = (naam, rassen, iso.year, iso.week, temperatuur)
        if sleutel not in self._cache:
            self._cache[sleutel] = self._voorspel(naam, rassen, plantdatum, {"temperatuur": temperatuur})
        return self._cache[sleutel]

    def gewicht(self, rassen: tuple, plantdatum: date, kenmerken_: dict[str, Any]) -> Voorspelling | None:
        if not self.aan.get("gewicht"):
            return None
        return self._voorspel("gewicht", rassen, plantdatum, kenmerken_)

    # -- intern --
    def _temperatuur(self, rassen: tuple, plantdatum: date, licht_uit: date | None) -> float | None:
        """Gemiddelde verwachte etmaaltemperatuur over licht uit + de reactietijd van de seizoensbasis."""
        if licht_uit is None or not self.inst.temperatuur_aan or not self.temperaturen:
            return None
        basis = self.ladders["reactietijd"].basis.voorspel(rassen, plantdatum)
        if basis is None:
            return None
        dagen = [self.temperaturen.get(date.fromordinal(licht_uit.toordinal() + i)) for i in range(int(basis.waarde))]
        if not dagen or any(t is None for t in dagen):
            return None
        return sum(dagen) / len(dagen)

    def _voorspel(self, doel: str, rassen: tuple, plantdatum: date, waarden: dict[str, Any]) -> Voorspelling | None:
        ladder = self.ladders[doel]
        resultaat = self.backtest.get(doel, {})
        treden = resultaat.get("treden", {})
        gekozen = resultaat.get("gekozen", "seizoensbasis")
        volgorde = list(TREDEN[doel])
        kandidaten = [t for t in reversed(volgorde[: volgorde.index(gekozen) + 1])
                      if t == "seizoensbasis" or treden.get(t, {}).get("status") == "gevalideerd"]  # fmt: skip
        basis = ladder.basis.voorspel(rassen, plantdatum)
        for trede in kandidaten:
            punt = ladder.voorspel(trede, rassen, plantdatum, waarden)
            if punt is None:
                continue
            if trede == "seizoensbasis":
                laag, hoog = basis.laag, basis.hoog
            else:
                onder, boven = treden.get(trede, {}).get("residu") or (None, None)
                laag = None if onder is None else punt + onder
                hoog = None if boven is None else punt + boven
            if doel in GEHELE_VELDEN:
                punt = afronden_geheel(punt)
            status = "gevalideerd" if trede != "seizoensbasis" else "indicatief"
            venster = basis.venster if basis else []
            return Voorspelling(
                waarde=punt, ondergrens=laag, bovengrens=hoog, bron=self._zin(doel, trede, rassen, plantdatum, basis),
                modelversie=self.versie, status=status, trede=trede, aantal=basis.aantal if basis else 0,
                vergelijkbaar=self._vergelijkbaar(doel, venster, plantdatum, waarden),
            )  # fmt: skip
        return None

    def _zin(self, doel, trede, rassen, plantdatum, basis) -> str:
        rassen_tekst = "/".join(code for code, _ in rassen)
        iso = plantdatum.isocalendar()
        zin = (f"Seizoensbasis: mediaan van {basis.aantal if basis else 0} eerdere teelt(en) van ras {rassen_tekst} "
               f"met plantweek {iso.week} ± {self.inst.venster}"
               + (f" in de laatste {self.inst.terugkijk} jaar" if self.inst.terugkijk else " in eerdere jaren") + ".")  # fmt: skip
        if trede == "temperatuur":
            zin += f" Gecorrigeerd voor de verwachte temperatuur (referentie {self.inst.referentietemperatuur:g} °C)."
        elif trede != "seizoensbasis":
            r = self.backtest[doel]["treden"][trede]
            zin = (f"{TREDE_LABEL[trede].capitalize()}: verslaat de seizoensbasis in de backtest "
                   f"(gemiddelde fout {r['mae']:.1f} tegenover {r['mae_basis']:.1f} {EENHEID[doel]}, "
                   f"{r['n']} toetsteelten).")  # fmt: skip
        return zin

    def vergelijkbaar_voor(self, veld: str, rassen: tuple, plantdatum: date,
                           waarden: dict[str, Any]) -> tuple[Vergelijkbaar, ...]:  # fmt: skip
        """De meest vergelijkbare eerdere teelten voor een veld zonder modelvoorstel (bijv. dichtheid of uitval)."""
        if not rassen or veld not in WAARNEMING_VELDEN:
            return ()
        basis = self._extra.get(veld)
        if basis is None:
            basis = self._extra[veld] = Seizoensbasis(self.obs, veld, self.inst)
        venster = [w for code, _ in rassen for w in basis.venster(code, plantdatum)]
        return self._vergelijkbaar(veld, venster, plantdatum, waarden)

    def _vergelijkbaar(self, doel, venster: list[Waarneming], plantdatum: date, waarden) -> tuple[Vergelijkbaar, ...]:
        iso = plantdatum.isocalendar()
        jaarweken = weken_in_jaar(iso.year)

        def afstand(w: Waarneming):
            d = weekafstand(w.week, iso.week, jaarweken) / max(self.inst.venster, 1)
            for naam in ("lichtsom", "dichtheid"):
                eigen, ander, schaal = waarden.get(naam), getattr(w, naam), self.schaal.get(naam)
                if eigen is not None and ander is not None and schaal:
                    d += abs(eigen - ander) / schaal
            return (d, -w.jaar, w.kap or 0, w.sleutel)

        gekozen = sorted({w.sleutel: w for w in venster}.values(), key=afstand)[: self.inst.vergelijkbaar]
        return tuple(Vergelijkbaar(w.kap, w.plantdatum, getattr(w, doel), w.bron, w.ras) for w in gekozen)
