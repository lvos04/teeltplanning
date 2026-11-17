"""Dataclasses voor invoer en uitvoer van de rekenkern (BOUWPLAN §4.1 en §4.2).

De kern kent geen database en geen web. Alle instelbare waarden komen binnen via `KernInstellingen`, die de
planningsdienst opbouwt uit het instellingenregister (§4.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

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
    rassen: tuple[tuple[str, int | None], ...] = ()  # (rascode, bedden); gemengde kap zoals 3a3p

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
    oogstdagen: frozenset[int] = frozenset()  # leeg = onbekend: geen controle


@dataclass(frozen=True)
class Vergelijkbaar:
    """Eén eerdere teelt die op een voorstel lijkt (uitleg bij het voorstel, §8.3)."""

    kap: int | None
    plantdatum: date | None
    waarde: Any
    bron: str
    ras: str | None = None


@dataclass(frozen=True)
class Voorspelling:
    """Een voorstel uit de voorstelladder (§6.7, §8.3), met alles wat de uitleg nodig heeft."""

    waarde: Any
    ondergrens: float | None
    bovengrens: float | None
    bron: str  # vaste zin, bijv. "seizoensbasis: mediaan van 7 teelten …"
    modelversie: str | None
    status: str  # gevalideerd | indicatief
    trede: str  # seizoensbasis | lbe | ridge | bomen | temperatuur
    aantal: int = 0  # waarnemingen waarop het voorstel rust
    vergelijkbaar: tuple[Vergelijkbaar, ...] = ()


class Voorspeller(Protocol):
    """Levert voorstellen voor de kern. De kern kent alleen dit protocol; de uitvoering staat in `voorstel/`.

    `rassen` is ((code, bedden), …) zoals in `TeeltInvoer`. Geen voorstel = None (dan blijft het veld onbekend of
    neemt de kern de eigen LBE-berekening voor het gewicht)."""

    def veld(self, naam: str, rassen: tuple, plantdatum: date, licht_uit: date | None) -> Voorspelling | None: ...

    def gewicht(self, rassen: tuple, plantdatum: date, kenmerken: dict[str, Any]) -> Voorspelling | None: ...


@dataclass(frozen=True)
class Parameters:
    kappen: dict[int, KapInvoer]
    instellingen: KernInstellingen
    licht: Any = None  # kern.licht.LichtInvoer; None = geen licht-, energie- en LBE-berekening
    gewas: Any = None  # kern.gewas.GewasInvoer
    voorspeller: Any = None  # Voorspeller (fase 4); None = geen modelvoorstellen


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
    lichtsom: Any = None  # kern.licht.Lichtsom
    energie: Any = None  # kern.energie.Energie
    lbe: Any = None  # kern.gewas.LbeKeuze
    dichtheid_onafgerond: float | None = None
    gewicht_band: tuple[float | None, float | None] = (None, None)
    toelichting: dict[str, Voorspelling] = field(default_factory=dict)  # per veld met een modelvoorstel

    @property
    def heeft_fout(self) -> bool:
        return any(m.niveau == "fout" for m in self.meldingen)


@dataclass(frozen=True)
class Plan:
    teelten: dict[int, TeeltUitkomst]

    def per_kap(self, kap: int) -> list[TeeltUitkomst]:
        return sorted((t for t in self.teelten.values() if t.kap == kap), key=lambda t: t.ronde)
