"""KNMI Data Platform (§10): gemeten globale straling en temperatuur per dag.

Gebouwd op de Open Data API met de dataset `daily-in-situ-meteorological-observations-validated` (niet op het oude
script `daggegevens`, dat eind 2026 stopt). **Niet geverifieerd** (open vraag B23): de datasetversie, de naam en
eenheid van de variabele voor de dagsom van de straling, en het bestandsformaat (volgens de metadata NetCDF 4,
waarvoor de image nu geen lezer heeft). Daarom kan de app hier alleen de verbinding en de sleutel controleren
(bestandenlijst ophalen); het inlezen van waarden staat uit tot dit is vastgesteld.
"""

from __future__ import annotations

from .http import BronFout, haal_json


def bestanden(basis_url: str, dataset: str, versie: str | None, sleutel: str | None, tijdslimiet: float) -> list[str]:
    if not sleutel:
        raise BronFout("Geen KNMI-API-sleutel ingevuld (Beheer → Bronnen).")
    if not versie:
        raise BronFout("KNMI-datasetversie onbekend (instelling Bronnen → KNMI: datasetversie, vraag B23).")
    antwoord = haal_json(f"{basis_url.rstrip('/')}/datasets/{dataset}/versions/{versie}/files",
                         {"maxKeys": 10, "orderBy": "created", "sorting": "desc"},
                         {"Authorization": sleutel}, tijdslimiet)  # fmt: skip
    if not isinstance(antwoord, dict) or not isinstance(antwoord.get("files"), list):
        raise BronFout("Onverwacht antwoord van het KNMI Data Platform.")
    return [str(f.get("filename")) for f in antwoord["files"] if isinstance(f, dict)]


def waarden_ophalen(*_args, **_kwargs):
    raise BronFout("KNMI-waarden inlezen is nog niet mogelijk: variabelenaam, eenheid en bestandsformaat van de dataset "
                   "zijn niet geverifieerd (open vraag B23). Gebruik zolang Open-Meteo of laat de bron uit.")  # fmt: skip
