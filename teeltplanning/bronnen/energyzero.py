"""EnergyZero (§10): dagvooruitprijzen stroom, alleen voor de kosten van de komende dag en achteraf. Voor de
planning geldt het prijsscenario (instelling *Stroomprijs voor de planning*).

Rechtstreeks via de openbare prijzen-API die ook het pakket `energyzero` gebruikt; geen extra afhankelijkheden
(aiohttp) in de image. Het antwoordformaat (`Prices[].price` in €/kWh, `readingDate` in UTC) is niet tegen de live
API geverifieerd in de bouwomgeving (geen netwerk in tests).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .http import BronFout, haal_json

NL = ZoneInfo("Europe/Amsterdam")
KWH_PER_MWH = 1000


def dagprijzen(basis_url: str, van: date, tot: date, tijdslimiet: float) -> dict[date, float]:
    """Gemiddelde uurprijs per dag (€/MWh, zonder btw) over [van, tot]."""
    begin = datetime.combine(van, datetime.min.time(), NL)
    eind = datetime.combine(tot + timedelta(days=1), datetime.min.time(), NL) - timedelta(seconds=1)
    antwoord = haal_json(basis_url, {
        "fromDate": begin.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "tillDate": eind.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.999Z"),
        "interval": 4, "usageType": 1, "inclBtw": "false",
    }, tijdslimiet=tijdslimiet)  # fmt: skip
    prijzen = antwoord.get("Prices") if isinstance(antwoord, dict) else None
    if not isinstance(prijzen, list):
        raise BronFout("Onverwacht antwoord van EnergyZero (geen Prices).")
    per_dag: dict[date, list[float]] = {}
    for p in prijzen:
        try:
            moment = datetime.fromisoformat(str(p["readingDate"]).replace("Z", "+00:00")).astimezone(NL)
            per_dag.setdefault(moment.date(), []).append(float(p["price"]) * KWH_PER_MWH)
        except (KeyError, TypeError, ValueError):
            continue
    return {d: sum(w) / len(w) for d, w in per_dag.items()}
