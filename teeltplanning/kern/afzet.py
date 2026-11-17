"""Afzetmomenten en terugrekenen (BOUWPLAN §8.1).

Een afzetmoment heeft een **datumregel** (tekst, in Beheer → Afzetmomenten aan te passen):

| regel | betekenis | voorbeeld |
|---|---|---|
| `vast MM-DD` | elk jaar dezelfde datum | `vast 02-14` (Valentijnsdag) |
| `pasen N` | N dagen na (of vóór, negatief) Pasen | `pasen -21` (Britse moederdag) |
| `weekdag MM WD N` | de N-de weekdag WD (1 = maandag … 7 = zondag) van maand MM; N = -1 is de laatste | `weekdag 05 7 2` (moederdag) |
| `voor MM-DD WD N` | de N-de weekdag WD strikt vóór MM-DD | `voor 12-25 7 5` (Totensonntag) |
| `<regel> behalve <regel> dan <regel>` | valt de eerste regel op de tweede, dan geldt de derde | Franse moederdag |

Pasen wordt meegegeven (`pasen`: ISO-jaar → datum); de kern rekent zelf geen paasdatum uit.

Venster: er moet geoogst zijn in de `venster_dagen_voor` dagen vóór de dag: oogst ∈ [datum − venster, datum − 1].
Terugrekenen: `plantdatum = gewenste_oogstdatum − reactietijd − verlenging − ld_dagen`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta

DAGEN_PER_WEEK = 7


class OngeldigeRegel(ValueError):
    pass


@dataclass(frozen=True)
class Afzetmoment:
    id: int
    naam: str
    datumregel: str
    venster_dagen_voor: int | None = None
    gewicht: float | None = None  # belang 0–10
    markt: str | None = None
    actief: bool = True


@dataclass(frozen=True)
class MomentDatum:
    moment: Afzetmoment
    datum: date

    @property
    def venster(self) -> tuple[date, date] | None:
        if self.moment.venster_dagen_voor is None:
            return None
        return self.datum - timedelta(days=self.moment.venster_dagen_voor), self.datum - timedelta(days=1)


def _maand_dag(tekst: str) -> tuple[int, int]:
    try:
        maand, dag = (int(x) for x in tekst.split("-"))
    except ValueError as fout:
        raise OngeldigeRegel(f"'{tekst}' is geen MM-DD") from fout
    return maand, dag


def _geheel(tekst: str, wat: str) -> int:
    try:
        return int(tekst)
    except ValueError as fout:
        raise OngeldigeRegel(f"{wat} '{tekst}' is geen geheel getal") from fout


def _weekdag(tekst: str) -> int:
    wd = _geheel(tekst, "weekdag")
    if not 1 <= wd <= DAGEN_PER_WEEK:
        raise OngeldigeRegel("weekdag loopt van 1 (maandag) t/m 7 (zondag)")
    return wd


def _enkel(delen: list[str], jaar: int, pasen: Callable[[int], date]) -> date:
    if not delen:
        raise OngeldigeRegel("lege regel")
    soort, rest = delen[0].lower(), delen[1:]
    try:
        if soort == "vast" and len(rest) == 1:
            return date(jaar, *_maand_dag(rest[0]))
        if soort == "pasen" and len(rest) == 1:
            return pasen(jaar) + timedelta(days=_geheel(rest[0], "aantal dagen"))
        if soort == "weekdag" and len(rest) == len(("MM", "WD", "N")):
            maand_t, wd_t, n_t = rest
            maand, wd, n = _geheel(maand_t, "maand"), _weekdag(wd_t), _geheel(n_t, "volgnummer")
            if n == 0:
                raise OngeldigeRegel("volgnummer 0 bestaat niet (1, 2, … of -1 voor de laatste)")
            if n > 0:
                d = date(jaar, maand, 1)
                d += timedelta(days=(wd - d.isoweekday()) % DAGEN_PER_WEEK + (n - 1) * DAGEN_PER_WEEK)
            else:
                d = date(jaar, maand, 1)
                while (d + timedelta(days=1)).month == maand:
                    d += timedelta(days=1)
                d -= timedelta(days=(d.isoweekday() - wd) % DAGEN_PER_WEEK + (-n - 1) * DAGEN_PER_WEEK)
            if d.month != maand:
                raise OngeldigeRegel(f"maand {maand} van {jaar} heeft geen {n}e weekdag {wd}")
            return d
        if soort == "voor" and len(rest) == len(("MM-DD", "WD", "N")):
            datum_t, wd_t, n_t = rest
            grens = date(jaar, *_maand_dag(datum_t))
            wd, n = _weekdag(wd_t), _geheel(n_t, "volgnummer")
            if n < 1:
                raise OngeldigeRegel("volgnummer bij 'voor' is 1 of meer")
            stap = (grens.isoweekday() - wd) % DAGEN_PER_WEEK or DAGEN_PER_WEEK
            return grens - timedelta(days=stap + (n - 1) * DAGEN_PER_WEEK)
    except ValueError as fout:
        if isinstance(fout, OngeldigeRegel):
            raise
        raise OngeldigeRegel(str(fout)) from fout
    raise OngeldigeRegel(f"onbekende regel '{' '.join(delen)}' (vast, pasen, weekdag, voor)")


def datum_van(regel: str, jaar: int, pasen: Callable[[int], date]) -> date:
    """Datum van een afzetmoment in een kalenderjaar; `OngeldigeRegel` met uitleg bij een fout."""
    tekst = " ".join((regel or "").split())
    if " behalve " in tekst:
        hoofd, rest = tekst.split(" behalve ", 1)
        if " dan " not in rest:
            raise OngeldigeRegel("gebruik: <regel> behalve <regel> dan <regel>")
        uitzondering, anders = rest.split(" dan ", 1)
        d = _enkel(hoofd.split(), jaar, pasen)
        return _enkel(anders.split(), jaar, pasen) if d == _enkel(uitzondering.split(), jaar, pasen) else d
    return _enkel(tekst.split(), jaar, pasen)


def controleer(regel: str, pasen: Callable[[int], date], jaar: int) -> str | None:
    """Foutmelding of None (voor validatie in Beheer)."""
    try:
        datum_van(regel, jaar, pasen)
    except OngeldigeRegel as fout:
        return str(fout)
    return None


def momenten_in(
    momenten: Iterable[Afzetmoment], jaren: Iterable[int], pasen: Callable[[int], date]
) -> list[MomentDatum]:
    uit = []
    for jaar in sorted(set(jaren)):
        for m in momenten:
            if m.actief:
                uit.append(MomentDatum(m, datum_van(m.datumregel, jaar, pasen)))
    return sorted(uit, key=lambda md: (md.datum, md.moment.id))


def terugrekenen(oogst: date, reactietijd: int | None, verlenging: int | None, ld_dagen: int | None) -> date | None:
    if any(x is None for x in (reactietijd, verlenging, ld_dagen)):
        return None
    return oogst - timedelta(days=reactietijd + verlenging + ld_dagen)


# --- Aanvoer en dekking ---------------------------------------------------------------------------------------
def aanvoer(uitkomst, eenheid: str) -> float | None:
    """Aanvoer van één teelt: oogstaantal (takken) of één teelt (als oogstaantallen onbekend zijn)."""
    if eenheid == "takken":
        return uitkomst.oogstaantal
    return 1.0


def kies_eenheid(uitkomsten) -> str:
    return "takken" if all(u.oogstaantal is not None for u in uitkomsten if u.oogst is not None) else "teelten"


def per_week(uitkomsten, eenheid: str) -> dict[tuple[int, int], float]:
    weken: dict[tuple[int, int], float] = {}
    for u in uitkomsten:
        waarde = aanvoer(u, eenheid) if u.oogst else None
        if waarde is None:
            continue
        iso = u.oogst.isocalendar()
        weken[(iso.year, iso.week)] = weken.get((iso.year, iso.week), 0.0) + waarde
    return weken


def dekking(uitkomsten, momenten: list[MomentDatum], eenheid: str) -> dict[tuple[int, date], float | None]:
    """Aanvoer met de oogst in het venster van elk moment; None als het venster onbekend is."""
    uit: dict[tuple[int, date], float | None] = {}
    for md in momenten:
        venster = md.venster
        if venster is None:
            uit[(md.moment.id, md.datum)] = None
            continue
        van, tot = venster
        uit[(md.moment.id, md.datum)] = sum(
            aanvoer(u, eenheid) or 0 for u in uitkomsten if u.oogst and van <= u.oogst <= tot
        )
    return uit
