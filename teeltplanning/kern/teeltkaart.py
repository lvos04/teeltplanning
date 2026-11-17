"""De teeltkaart (BOUWPLAN §9.2): kop, meetmomenten met LD-dag en streeflengte, en de onderregel.

Regels uit het werkboek, als instellingen binnengekregen (`KaartInstellingen`):

* Het eerste meetmoment valt op de eerstvolgende meetdag (start ma/wo/vr) op of na licht uit. Volgens het
  werkboek, met weekdag van licht uit 1…7: moment op weekdag 1, 3, 3, 5, 5, 1, 1 en LD-dag 0, 1, 0, 1, 0, 2, 1;
  dat is precies "eerstvolgende meetdag ≥ licht uit" (test).
* Daarna steeds de volgende meetdag: ma → wo (+2), wo → vr (+2), vr → ma (+3).
* "LD-dag" op de kaart = dagen na licht uit (de streeflengtetabel loopt van LD-dag 4 tot 35).
* Streeflengte = opzoeken op LD-dag, in de kolom van het seizoen van de **plantweek** (start: winter 43–52,
  voorjaar 1–6, zomer 7–36, najaar 37–42). Geen waarde = leeg op de kaart.
* Slak, remstof, pluizen en netten = licht uit + een instelbaar aantal dagen (start onbekend).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .typen import Melding, TeeltUitkomst

DAGEN_PER_WEEK = 7
BEHANDELINGEN = ("slak", "remstof", "pluizen", "netten")


@dataclass(frozen=True)
class Seizoen:
    naam: str
    week_van: int
    week_tot: int  # van > tot = over de jaarwisseling (bijv. 43-6)

    def bevat(self, week: int) -> bool:
        if self.week_van <= self.week_tot:
            return self.week_van <= week <= self.week_tot
        return week >= self.week_van or week <= self.week_tot


@dataclass(frozen=True)
class KaartInstellingen:
    streeflengte: dict[int, dict[str, int | None]]  # LD-dag → seizoen → cm
    seizoenen: tuple[Seizoen, ...]
    meetdagen: frozenset[int]
    aantal_momenten: int
    offsets: dict[str, int | None] = field(default_factory=dict)


@dataclass(frozen=True)
class Moment:
    datum: date
    ld_dag: int
    streeflengte: int | None


@dataclass(frozen=True)
class Teeltkaart:
    kap: int
    ronde: int
    soort: str | None
    plantdatum: date | None
    dichtheid: float | None
    ld_dagen: int | None
    licht_uit: date | None
    licht_aan: date | None
    tussenlicht_dagen: int | None
    licht_uit_2: date | None
    behandelingen: dict[str, date | None]
    oogst: date | None
    reactietijd: int | None
    verlenging: int | None
    seizoen: str | None
    momenten: tuple[Moment, ...]
    meldingen: tuple[Melding, ...]


def seizoen_van(plantweek: int | None, seizoenen: tuple[Seizoen, ...]) -> str | None:
    if plantweek is None:
        return None
    for s in seizoenen:
        if s.bevat(plantweek):
            return s.naam
    return None


def eerstvolgende_meetdag(vanaf: date, meetdagen: frozenset[int]) -> date | None:
    if not meetdagen:
        return None
    d = vanaf
    for _ in range(DAGEN_PER_WEEK):
        if d.isoweekday() in meetdagen:
            return d
        d += timedelta(days=1)
    return None  # pragma: no cover - kan niet bij een niet-lege set weekdagen 1..7


def momenten(licht_uit: date | None, inst: KaartInstellingen, seizoen: str | None) -> tuple[Moment, ...]:
    if licht_uit is None:
        return ()
    uit = []
    d = eerstvolgende_meetdag(licht_uit, inst.meetdagen)
    while d is not None and len(uit) < inst.aantal_momenten:
        ld_dag = (d - licht_uit).days
        rij = inst.streeflengte.get(ld_dag) or {}
        uit.append(Moment(d, ld_dag, rij.get(seizoen) if seizoen else None))
        d = eerstvolgende_meetdag(d + timedelta(days=1), inst.meetdagen)
    return tuple(uit)


def maak_kaart(u: TeeltUitkomst, soort: str | None, inst: KaartInstellingen) -> Teeltkaart:
    meldingen: list[Melding] = []
    plant = u.effectief.get("plantdatum")
    plantweek = plant.isocalendar().week if plant else None
    seizoen = seizoen_van(plantweek, inst.seizoenen)
    if plant is not None and seizoen is None:
        meldingen.append(Melding("geen_seizoen", "waarschuwing",
                                 f"Plantweek {plantweek} valt in geen seizoen van de streeflengtetabel; "
                                 "de streeflengte blijft leeg (Beheer → Instellingen → Teeltkaart)."))  # fmt: skip
    if u.licht_uit is None:
        meldingen.append(Melding("geen_licht_uit", "waarschuwing", "Licht uit onbekend: geen meetmomenten."))
    if not inst.meetdagen:
        meldingen.append(Melding("geen_meetdagen", "waarschuwing", "Geen weekdagen voor de meetmomenten ingesteld."))
    behandelingen = {}
    for naam in BEHANDELINGEN:
        offset = inst.offsets.get(naam)
        behandelingen[naam] = u.licht_uit + timedelta(days=offset) if u.licht_uit and offset is not None else None
    return Teeltkaart(
        kap=u.kap,
        ronde=u.ronde,
        soort=soort,
        plantdatum=plant,
        dichtheid=u.effectief.get("dichtheid"),
        ld_dagen=u.effectief.get("ld_dagen"),
        licht_uit=u.licht_uit,
        licht_aan=u.licht_aan,
        tussenlicht_dagen=u.effectief.get("tussenlicht_dagen"),
        licht_uit_2=u.licht_uit_2,
        behandelingen=behandelingen,
        oogst=u.oogst,
        reactietijd=u.effectief.get("reactietijd"),
        verlenging=u.effectief.get("verlenging"),
        seizoen=seizoen,
        momenten=momenten(u.licht_uit, inst, seizoen),
        meldingen=tuple(meldingen),
    )
