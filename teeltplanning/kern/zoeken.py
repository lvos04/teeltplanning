"""Zoekfunctie (BOUWPLAN §8.2): kleine wijzigingen proberen en als **lijst met voorstellen** teruggeven.

* Wijzigingen ("zetten"): plantdag ± stap, LD-dagen ± stap, verlenging binnen de grenzen, ras wisselen tussen twee
  kappen die in dezelfde week planten. Alleen teelten met de oogst in het gekozen jaar en de plantdatum na de
  peildatum. **Handmatige waarden zijn vaste randvoorwaarden**: een veld met een handmatige waarde wordt nooit
  geprobeerd.
* Score (lager is beter) = Σ gewicht × onderdeel / onderdeel van het uitgangsplan, over de onderdelen waarvan het
  gewicht is ingesteld en de waarde bekend is:
  - `afzet`       = −Σ belang × aanvoer in het venster van elk afzetmoment in het jaar
  - `gelijkmatig` = Σ over de weken van het jaar |weekaanvoer − gemiddelde weekaanvoer|
  - `doelgewicht` = Σ |gewicht − doelgewicht van het ras|
  - `energie`     = Σ stroomkosten (of kWh als de prijs onbekend is)
* Een zet die een nieuwe fout of waarschuwing geeft (overlap, buiten de grenzen, geen plantdag of oogstdag) telt
  niet mee.
* Gretig: steeds de beste zet, tot er geen verbetering meer is, het maximum aantal voorstellen of de maximale
  rekentijd. De invoer wordt **niet** gewijzigd (alle typen zijn onveranderlijk); toepassen gebeurt buiten de kern,
  één voorstel tegelijk, door de gebruiker.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

from . import afzet
from .kalender import weergave, weken_in_jaar
from .plan import bereken_kap, maak_rekenaar, per_kap
from .typen import Parameters, PlanInvoer, TeeltInvoer, Veld

ONDERDELEN = ("afzet", "gelijkmatig", "doelgewicht", "energie")
ZETTEN = ("plantdag", "ld_dagen", "verlenging", "ras_wisselen")
CONFLICTEN = ("overlap", "geen_plantdag", "geen_oogstdag")


@dataclass(frozen=True)
class ZoekOpties:
    jaar: int
    peildatum: date
    gewichten: dict[str, float | None]
    zetten: tuple[str, ...]
    stap_plantdag: int
    stap_ld_dagen: int
    max_voorstellen: int
    max_rekentijd_s: float
    doelgewicht: dict[str, float | None]


@dataclass
class Bijdrage:
    """Wat één kap bijdraagt aan de score."""

    weken: dict[int, float]
    dekking: dict[int, float]
    afwijking: float
    afwijking_n: int
    energie: float
    energie_onbekend: bool
    conflicten: frozenset
    uitkomsten: list


def _doel(rassen: tuple, doelgewicht: dict) -> float | None:
    if not rassen:
        return None
    gewichten = [(doelgewicht.get(code), b or 1) for code, b in rassen]
    if any(d is None for d, _ in gewichten):
        return None
    return sum(d * b for d, b in gewichten) / sum(b for _, b in gewichten)


class Zoeker:
    def __init__(
        self, invoer: PlanInvoer, parameters: Parameters, momenten: list[afzet.MomentDatum], opties: ZoekOpties
    ):
        self.p, self.o = parameters, opties
        self.kappen = {k: sorted(v, key=lambda t: t.ronde) for k, v in per_kap(invoer).items()}
        basis = {k: bereken_kap(v, parameters, None) for k, v in self.kappen.items()}
        datums = [d for lijst in basis.values() for u in lijst for d in (u.effectief["plantdatum"], u.oogst) if d]
        marge = opties.max_voorstellen * (opties.stap_plantdag + opties.stap_ld_dagen) + max(
            (h or 0 for _, h in parameters.instellingen.grenzen.values()), default=0
        )
        self.rekenaar = maak_rekenaar(parameters, min(datums) - timedelta(days=marge), max(datums) + timedelta(days=marge)) if datums else None  # fmt: skip
        self.momenten = [md for md in momenten if md.datum.year == opties.jaar and md.venster is not None
                         and md.moment.gewicht is not None]  # fmt: skip
        self.eenheid = "takken"
        self.versie = {k: 0 for k in self.kappen}
        self._cache: dict = {}
        self.bijdragen = {k: self._bijdrage(self._reken(v), v) for k, v in self.kappen.items()}
        alle = [u for b in self.bijdragen.values() for u in b.uitkomsten if self._in_jaar(u)]
        self.eenheid = afzet.kies_eenheid(alle)
        self.bijdragen = {k: self._bijdrage(b.uitkomsten, self.kappen[k]) for k, b in self.bijdragen.items()}
        self.ronde_van = {t.id: t.ronde for v in self.kappen.values() for t in v}
        self.basis_onderdelen = self.onderdelen(self.bijdragen)
        self.referentie = {n: (abs(w) if w else 1.0) for n, w in self.basis_onderdelen.items() if w is not None}

    # -- rekenen --
    def _reken(self, teelten: list[TeeltInvoer]):
        return bereken_kap(teelten, self.p, self.rekenaar)

    def _in_jaar(self, u) -> bool:
        return u.oogst is not None and u.oogst.isocalendar().year == self.o.jaar

    def _bijdrage(self, uitkomsten, teelten: list[TeeltInvoer]) -> Bijdrage:
        rassen = {t.id: t.rassen for t in teelten}
        scope = [u for u in uitkomsten if self._in_jaar(u)]
        weken: dict[int, float] = {}
        for u in scope:
            waarde = afzet.aanvoer(u, self.eenheid)
            if waarde is not None:
                weken[u.oogst.isocalendar().week] = weken.get(u.oogst.isocalendar().week, 0.0) + waarde
        dekking = {}
        for i, md in enumerate(self.momenten):
            van, tot = md.venster
            dekking[i] = sum(
                afzet.aanvoer(u, self.eenheid) or 0 for u in uitkomsten if u.oogst and van <= u.oogst <= tot
            )
        afwijking, n = 0.0, 0
        energie, onbekend = 0.0, False
        for u in scope:
            doel = _doel(rassen.get(u.id, ()), self.o.doelgewicht)
            if doel is not None and u.effectief["gewicht"] is not None:
                afwijking += abs(u.effectief["gewicht"] - doel)
                n += 1
            e = u.energie
            waarde = None if e is None else (e.kosten if e.kosten is not None else e.kwh)
            if waarde is None:
                onbekend = True
            else:
                energie += waarde
        conflicten = frozenset((u.id, m.code) for u in uitkomsten for m in u.meldingen
                               if m.niveau == "fout" or m.code in CONFLICTEN)  # fmt: skip
        return Bijdrage(weken, dekking, afwijking, n, energie, onbekend, conflicten, uitkomsten)

    def onderdelen(self, bijdragen: dict[int, Bijdrage]) -> dict[str, float | None]:
        uit: dict[str, float | None] = {}
        if self.momenten:
            uit["afzet"] = -sum(md.moment.gewicht * sum(b.dekking[i] for b in bijdragen.values())
                                for i, md in enumerate(self.momenten))  # fmt: skip
        else:
            uit["afzet"] = None
        weken = range(1, weken_in_jaar(self.o.jaar) + 1)
        reeks = [sum(b.weken.get(w, 0.0) for b in bijdragen.values()) for w in weken]
        gemiddeld = sum(reeks) / len(reeks)
        uit["gelijkmatig"] = sum(abs(x - gemiddeld) for x in reeks)
        n = sum(b.afwijking_n for b in bijdragen.values())
        uit["doelgewicht"] = sum(b.afwijking for b in bijdragen.values()) if n else None
        uit["energie"] = (
            None if any(b.energie_onbekend for b in bijdragen.values()) else sum(b.energie for b in bijdragen.values())
        )
        return uit

    def score(self, onderdelen: dict[str, float | None]) -> float:
        totaal = 0.0
        for naam in ONDERDELEN:
            gewicht, waarde = self.o.gewichten.get(naam), onderdelen.get(naam)
            if gewicht is None or waarde is None or naam not in self.referentie:
                continue
            totaal += gewicht * waarde / self.referentie[naam]
        return totaal

    # -- zetten --
    def _beweegbaar(self, u) -> bool:
        plant = u.effectief["plantdatum"]
        return self._in_jaar(u) and plant is not None and plant > self.o.peildatum

    def zetten(self) -> list[dict]:
        uit = []
        grenzen = self.p.instellingen.grenzen
        per_week: dict[tuple[int, int], list] = {}
        for kap, b in self.bijdragen.items():
            invoer = {t.id: t for t in self.kappen[kap]}
            for u in b.uitkomsten:
                if not self._beweegbaar(u):
                    continue
                t = invoer[u.id]
                if "plantdag" in self.o.zetten and t.veld("plantdatum").handmatig is None:
                    for stap in (-self.o.stap_plantdag, self.o.stap_plantdag):
                        uit.append({"soort": "plantdag", "veld": "plantdatum", "kap": kap, "teelt_id": u.id,
                                    "oud": u.effectief["plantdatum"], "nieuw": u.effectief["plantdatum"] + timedelta(days=stap)})  # fmt: skip
                ld = u.effectief["ld_dagen"]
                if "ld_dagen" in self.o.zetten and t.veld("ld_dagen").handmatig is None and ld is not None:
                    for stap in (-self.o.stap_ld_dagen, self.o.stap_ld_dagen):
                        if ld + stap >= 0:
                            uit.append({"soort": "ld_dagen", "veld": "ld_dagen", "kap": kap, "teelt_id": u.id, "oud": ld,
                                        "nieuw": ld + stap})  # fmt: skip
                verl = u.effectief["verlenging"]
                laag, hoog = grenzen.get("verlenging", (None, None))
                if (
                    "verlenging" in self.o.zetten
                    and t.veld("verlenging").handmatig is None
                    and laag is not None
                    and hoog is not None
                ):
                    for nieuw in range(int(laag), int(hoog) + 1):
                        if nieuw != verl:
                            uit.append({"soort": "verlenging", "veld": "verlenging", "kap": kap, "teelt_id": u.id,
                                        "oud": verl, "nieuw": nieuw})  # fmt: skip
                if "ras_wisselen" in self.o.zetten and len(t.rassen) == 1:
                    iso = u.effectief["plantdatum"].isocalendar()
                    per_week.setdefault((iso.year, iso.week), []).append((kap, t))
        for groep in per_week.values():
            for i, (k1, t1) in enumerate(groep):
                for k2, t2 in groep[i + 1 :]:
                    if k1 != k2 and t1.rassen[0][0] != t2.rassen[0][0]:
                        uit.append({"soort": "ras_wisselen", "veld": None, "kap": k1, "teelt_id": t1.id, "oud": t1.rassen,
                                    "nieuw": t2.rassen, "ander_kap": k2, "ander_teelt_id": t2.id})  # fmt: skip
        return uit

    def toepassen(self, zet: dict) -> dict[int, list[TeeltInvoer]]:
        """Nieuwe invoer voor de kappen die de zet raakt (de bestaande invoer blijft ongewijzigd)."""
        nieuw: dict[int, list[TeeltInvoer]] = {}
        if zet["soort"] == "ras_wisselen":
            for kap, teelt_id, rassen in ((zet["kap"], zet["teelt_id"], zet["nieuw"]),
                                          (zet["ander_kap"], zet["ander_teelt_id"], zet["oud"])):  # fmt: skip
                nieuw[kap] = [
                    replace(t, rassen=rassen) if t.id == teelt_id else t for t in nieuw.get(kap, self.kappen[kap])
                ]
            return nieuw
        lijst = []
        for t in self.kappen[zet["kap"]]:
            if t.id == zet["teelt_id"]:
                velden = dict(t.velden)
                velden[zet["veld"]] = Veld(voorstel=t.veld(zet["veld"]).voorstel, handmatig=zet["nieuw"])
                t = replace(t, velden=velden)
            lijst.append(t)
        nieuw[zet["kap"]] = lijst
        return nieuw

    def evalueer(self, zet: dict):
        """(score, onderdelen, nieuwe invoer, bijdragen) of None als de zet een nieuw conflict geeft. De bijdrage
        van de geraakte kappen wordt bewaard zolang die kappen niet veranderen."""
        kappen = (zet["kap"], zet.get("ander_kap")) if zet["soort"] == "ras_wisselen" else (zet["kap"],)
        sleutel = (zet["soort"], zet["teelt_id"], str(zet["nieuw"]), zet.get("ander_teelt_id"),
                   tuple(self.versie[k] for k in kappen))  # fmt: skip
        if sleutel not in self._cache:
            nieuw = self.toepassen(zet)
            geraakt = {}
            for kap, teelten in nieuw.items():
                b = self._bijdrage(self._reken(teelten), teelten)
                if not b.conflicten <= self.bijdragen[kap].conflicten:
                    geraakt = None
                    break
                geraakt[kap] = b
            self._cache[sleutel] = (nieuw, geraakt)
        nieuw, geraakt = self._cache[sleutel]
        if geraakt is None:
            return None
        bijdragen = dict(self.bijdragen) | geraakt
        onderdelen = self.onderdelen(bijdragen)
        return self.score(onderdelen), onderdelen, nieuw, bijdragen

    def neem_over(self, nieuw: dict[int, list[TeeltInvoer]], bijdragen: dict[int, Bijdrage]) -> None:
        for kap, teelten in nieuw.items():
            self.kappen[kap] = teelten
            self.versie[kap] += 1
        self.bijdragen = bijdragen


def _tekst_zet(zet: dict, ronde: dict[int, int]) -> str:
    r = ronde.get(zet["teelt_id"])
    if zet["soort"] == "plantdag":
        dagen = (zet["nieuw"] - zet["oud"]).days
        richting = "later" if dagen > 0 else "eerder"
        return (f"Kap {zet['kap']}, ronde {r}: plantdatum {abs(dagen)} dag(en) {richting} "
                f"({weergave(zet['oud'])} → {weergave(zet['nieuw'])})")  # fmt: skip
    if zet["soort"] == "ld_dagen":
        return f"Kap {zet['kap']}, ronde {r}: LD-dagen {zet['oud']} → {zet['nieuw']}"
    if zet["soort"] == "verlenging":
        return f"Kap {zet['kap']}, ronde {r}: verlenging {zet['oud']} → {zet['nieuw']} dag(en)"
    return (f"Ronde {r}: ras wisselen tussen kap {zet['kap']} ({zet['oud'][0][0]}) en kap {zet['ander_kap']} "
            f"({zet['nieuw'][0][0]})")  # fmt: skip


def _getal(waarde: float) -> str:
    return f"{round(waarde):,}".replace(",", ".")


def _tekst_effect(zoeker: Zoeker, voor: dict, na: dict, oude_bijdragen, nieuwe_bijdragen) -> str:
    winst = {}
    for naam in ONDERDELEN:
        g = zoeker.o.gewichten.get(naam)
        if g is None or voor.get(naam) is None or na.get(naam) is None or naam not in zoeker.referentie:
            continue
        winst[naam] = g * (voor[naam] - na[naam]) / zoeker.referentie[naam]
    if not winst:
        return "betere score"
    beste = max(winst, key=winst.get)
    eenheid = zoeker.eenheid
    if beste == "afzet":
        verschillen = []
        for i, md in enumerate(zoeker.momenten):
            oud = sum(b.dekking[i] for b in oude_bijdragen.values())
            nieuw = sum(b.dekking[i] for b in nieuwe_bijdragen.values())
            verschillen.append((nieuw - oud, md))
        delta, md = max(verschillen, key=lambda x: (abs(x[0]), -x[1].moment.id))
        teken = "+" if delta >= 0 else "−"
        return f"{teken}{_getal(abs(delta))} {eenheid} in het venster van {md.moment.naam} ({md.datum.isoformat()})"
    if beste == "gelijkmatig":
        return (
            f"gelijkmatigere weekoogst (afwijking {_getal(voor['gelijkmatig'] - na['gelijkmatig'])} {eenheid} kleiner)"
        )
    if beste == "doelgewicht":
        return f"dichter bij het doelgewicht ({_getal(voor['doelgewicht'] - na['doelgewicht'])} g minder afwijking)"
    return f"lagere energiekosten ({_getal(voor['energie'] - na['energie'])} minder)"


def _json(waarde: Any) -> Any:
    if isinstance(waarde, date):
        return waarde.isoformat()
    if isinstance(waarde, tuple):
        return [list(x) if isinstance(x, tuple) else x for x in waarde]
    return waarde


def zoek(invoer: PlanInvoer, parameters: Parameters, momenten: list[afzet.MomentDatum], opties: ZoekOpties,
         klok=time.monotonic) -> dict:  # fmt: skip
    """Voorstellen als lijst (JSON-waarden); past niets toe."""
    begin = klok()
    zoeker = Zoeker(invoer, parameters, momenten, opties)
    onderdelen_basis = dict(zoeker.basis_onderdelen)
    actief = [n for n in ONDERDELEN if opties.gewichten.get(n) is not None and onderdelen_basis.get(n) is not None]
    uit: dict[str, Any] = {"jaar": opties.jaar, "eenheid": zoeker.eenheid, "voorstellen": [], "gestopt": None,
                           "onderdelen": {}, "kandidaten": 0}  # fmt: skip
    for naam in ONDERDELEN:
        reden = None
        if opties.gewichten.get(naam) is None:
            reden = "gewicht niet ingesteld"
        elif onderdelen_basis.get(naam) is None:
            reden = {"afzet": "geen afzetmoment met venster én belang in dit jaar",
                     "doelgewicht": "doelgewicht of gewichtsprognose onbekend",
                     "energie": "energie onbekend voor een of meer teelten"}.get(naam, "onbekend")  # fmt: skip
        uit["onderdelen"][naam] = {"gewicht": opties.gewichten.get(naam), "voor": onderdelen_basis.get(naam),
                                   "na": None, "reden": reden}  # fmt: skip
    if not actief:
        uit["gestopt"] = "geen onderdeel met een ingesteld gewicht en een bekende waarde"
        uit["rekentijd_s"] = klok() - begin
        return uit
    score = zoeker.score(onderdelen_basis)
    for _ in range(opties.max_voorstellen):
        beste = None
        zetten = zoeker.zetten()
        uit["kandidaten"] += len(zetten)
        for zet in zetten:
            if klok() - begin > opties.max_rekentijd_s:
                uit["gestopt"] = "maximale rekentijd bereikt"
                break
            resultaat = zoeker.evalueer(zet)
            if resultaat is not None and resultaat[0] < score and (beste is None or resultaat[0] < beste[1][0]):
                beste = (zet, resultaat)
        if uit["gestopt"]:
            break
        if beste is None:
            uit["gestopt"] = "geen verbetering meer gevonden"
            break
        zet, (nieuwe_score, onderdelen, nieuw, bijdragen) = beste
        tekst = f"{_tekst_zet(zet, zoeker.ronde_van)}: {_tekst_effect(zoeker, zoeker.onderdelen(zoeker.bijdragen), onderdelen, zoeker.bijdragen, bijdragen)}"
        uit["voorstellen"].append({k: _json(v) for k, v in zet.items()} | {
            "tekst": tekst, "score_voor": score, "score_na": nieuwe_score,
            "onderdelen": {n: onderdelen.get(n) for n in ONDERDELEN}})  # fmt: skip
        zoeker.neem_over(nieuw, bijdragen)
        score = nieuwe_score
    else:
        uit["gestopt"] = "maximum aantal voorstellen bereikt"
    eind = zoeker.onderdelen(zoeker.bijdragen)
    for naam in ONDERDELEN:
        uit["onderdelen"][naam]["na"] = eind.get(naam)
    uit["rekentijd_s"] = klok() - begin
    return uit
