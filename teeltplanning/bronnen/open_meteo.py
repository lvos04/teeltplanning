"""Open-Meteo (§10): historie (ERA5) en verwachting van de dagsom van de straling en de etmaaltemperatuur.

`shortwave_radiation_sum` is in MJ/m²; × 100 = J/cm². Let op: de gratis API is voor niet-commercieel gebruik;
de basis-URL is instelbaar (eigen server of betaald abonnement).
"""

from __future__ import annotations

from datetime import date

from .http import BronFout, haal_json

MJ_M2_NAAR_J_CM2 = 100  # 1 MJ/m² = 10⁶ J / 10⁴ cm² = 100 J/cm²


def _lees(antwoord: dict) -> list[tuple[date, float | None, float | None]]:
    dagelijks = antwoord.get("daily") if isinstance(antwoord, dict) else None
    if not isinstance(dagelijks, dict) or "time" not in dagelijks:
        raise BronFout("Onverwacht antwoord van Open-Meteo (geen dagwaarden).")
    straling = dagelijks.get("shortwave_radiation_sum") or [None] * len(dagelijks["time"])
    temperatuur = dagelijks.get("temperature_2m_mean") or [None] * len(dagelijks["time"])
    uit = []
    for dag, s, t in zip(dagelijks["time"], straling, temperatuur, strict=False):
        uit.append((date.fromisoformat(dag), None if s is None else float(s) * MJ_M2_NAAR_J_CM2,
                    None if t is None else float(t)))  # fmt: skip
    return uit


def historie(basis_url: str, breedte: float, lengte: float, van: date, tot: date, tijdslimiet: float):
    return _lees(haal_json(basis_url, {
        "latitude": breedte, "longitude": lengte, "start_date": van.isoformat(), "end_date": tot.isoformat(),
        "daily": "shortwave_radiation_sum,temperature_2m_mean", "timezone": "Europe/Amsterdam",
    }, tijdslimiet=tijdslimiet))  # fmt: skip


def verwachting(basis_url: str, breedte: float, lengte: float, dagen: int, tijdslimiet: float):
    return _lees(haal_json(basis_url, {
        "latitude": breedte, "longitude": lengte, "forecast_days": dagen,
        "daily": "shortwave_radiation_sum,temperature_2m_mean", "timezone": "Europe/Amsterdam",
    }, tijdslimiet=tijdslimiet))  # fmt: skip
