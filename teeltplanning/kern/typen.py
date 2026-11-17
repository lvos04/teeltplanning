"""Dataclasses voor invoer en uitvoer van de rekenkern (BOUWPLAN §4.1 en §4.2).

De kern kent geen database en geen web. Alle instelbare waarden komen binnen via `KernInstellingen`, die de
planningsdienst opbouwt uit het instellingenregister (§4.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

# Velden met drie lagen (voorstel / handmatig / effectief). `tussenlicht` is de datum uit kolom K van het
# rondeblad; `tussenlicht_dagen` is het aantal dagen tussenlicht voor "tweede licht uit" (teeltkaart rij 7).
VELDEN = (
    "plantdatum",
    "ld_dagen",
    "reactietijd",
    "verlenging",
    "tussenlicht",
    "tussenlicht_dagen",
    "dichtheid",
    "gewicht",
    "uitval",
)
DATUMVELDEN = frozenset({"plantdatum", "tussenlicht"})
GEHELE_VELDEN = frozenset({"ld_dagen", "reactietijd", "verlenging", "tussenlicht_dagen"})


@dataclass(frozen=True)
class Veld:
    """Eén veld met drie lagen. Handmatig wint altijd."""

    voorstel: Any = None
    handmatig: Any = None

    @property
    def is_handmatig(self) -> bool:
        return self.handmatig is not None

    @property
    def effectief(self) -> Any:
        return self.handmatig if self.handmatig is not None else self.voorstel


@dataclass(frozen=True)
class TeeltInvoer:
    id: int
    kap: int
    ronde: int  # volgorde van de ronde binnen het scenario
    volgnummer: int | None = None
    velden: dict[str, Veld] = field(default_factory=dict)

    def veld(self, naam: str) -> Veld:
        return self.velden.get(naam, Veld())


@dataclass(frozen=True)
class KapInvoer:
    nummer: int
    oppervlak_m2: float | None = None  # eigen waarde van de kap; None = bedrijfsinstelling gebruiken
    wisseltijd_dagen: int | None = None  # None = bedrijfsinstelling gebruiken
    actief: bool = True


@dataclass(frozen=True)
class Blokkade:
    """Periode waarin niet geplant mag worden (bijv. stomen); `kap` None = alle kappen."""

    van: date
    tot: date
    kap: int | None = None


@dataclass(frozen=True)
class Uitvalregel:
    week_van: int
    week_tot: int
    fractie: float


@dataclass(frozen=True)
class KernInstellingen:
    licht_aan_na_licht_uit_dagen: int | None
    plantdagen: frozenset[int]
    wisseltijd_dagen: int | None
    plantdatum_volgt_keten: bool
    blokkades: tuple[Blokkade, ...] = ()
    # (geldig vanaf, waarde) oplopend; geldig vanaf None = altijd. Waarde None = onbekend.
    oppervlak_per_kap: tuple[tuple[date | None, float | None], ...] = ()
    uitval_per_oogstweek: tuple[Uitvalregel, ...] = ()
    grenzen: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)
    overlap_blokkeert: bool = False


@dataclass(frozen=True)
class Parameters:
    kappen: dict[int, KapInvoer]
    instellingen: KernInstellingen


@dataclass(frozen=True)
class PlanInvoer:
    teelten: tuple[TeeltInvoer, ...]


@dataclass(frozen=True)
class Melding:
    code: str
    niveau: str  # fout | waarschuwing | info
    tekst: str


@dataclass(frozen=True)
class TeeltUitkomst:
    id: int
    kap: int
    ronde: int
    effectief: dict[str, Any]
    bron: dict[str, str | None]  # handmatig | voorstel | None (onbekend)
    voorstel: dict[str, Any]
    licht_uit: date | None
    licht_aan: date | None
    licht_uit_2: date | None
    oogst: date | None
    teeltduur: int | None
    oppervlak_m2: float | None
    geplant: float | None
    oogstaantal: float | None
    meldingen: tuple[Melding, ...]

    @property
    def heeft_fout(self) -> bool:
        return any(m.niveau == "fout" for m in self.meldingen)


@dataclass(frozen=True)
class Plan:
    teelten: dict[int, TeeltUitkomst]

    def per_kap(self, kap: int) -> list[TeeltUitkomst]:
        return sorted((t for t in self.teelten.values() if t.kap == kap), key=lambda t: t.ronde)
