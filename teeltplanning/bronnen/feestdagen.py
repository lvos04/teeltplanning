"""Feestdagen (BOUWPLAN §8.1): Pasen en officiële feestdagen per land met het pakket `holidays` (lokaal, geen
netwerk), en de startinhoud van de afzetmomenten.

De bloemendagen staan als **datumregel** in de beheertabel *Afzetmomenten* (zie `kern/afzet.py` voor de taal van
de regels). De startinhoud komt uit het bouwplan en is "nog niet door de teler bevestigd"; venster en belang zijn
onbekend (open vraag 9).
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache

import holidays
from dateutil.easter import easter

# (naam, datumregel, markt) — §8.1. Markt alleen waar het bouwplan een land noemt; anders onbekend.
START_AFZETMOMENTEN = (
    ("Valentijnsdag", "vast 02-14", None),
    ("Vrouwendag", "vast 03-08", None),
    ("Britse moederdag", "pasen -21", "GB"),
    ("Pasen", "pasen 0", None),
    ("Moederdag", "weekdag 05 7 2", None),
    ("Franse moederdag", "weekdag 05 7 -1 behalve pasen 49 dan weekdag 06 7 1", "FR"),
    ("Allerheiligen", "vast 11-01", None),
    ("Totensonntag", "voor 12-25 7 5", "DE"),
    ("Kerst", "vast 12-25", None),
)


@lru_cache(maxsize=256)
def pasen(jaar: int) -> date:
    """Paaszondag (westers, gregoriaans), uit `dateutil`."""
    return easter(jaar)


def officiele_feestdagen(landen: list[str], jaren: list[int]) -> list[tuple[date, str, str]]:
    """(datum, land, naam) voor de ingestelde landen; onbekende landcodes worden overgeslagen."""
    uit = []
    for land in landen:
        try:
            kalender = holidays.country_holidays(land, years=jaren)
        except (NotImplementedError, KeyError):
            continue
        uit += [(d, land, naam) for d, naam in sorted(kalender.items())]
    return sorted(uit)


def onbekende_landen(landen: list[str]) -> list[str]:
    bekend = set(holidays.list_supported_countries())
    return [land for land in landen if land not in bekend]
