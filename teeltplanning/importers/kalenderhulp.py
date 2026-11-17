"""Week/dag-hulpfuncties, overgenomen uit lvos04/gewichtsverwachting `app/legacy_cultivation.py`
(branch ccr-f5cf979e-9oiocr, versie 0.4.0): `iso_date`, `next_week_date` en `group_plant_years` ongewijzigd.

`plantjaren` is een dunne aanpassing voor de regels van de programma-importer van Teeltplanning.
"""

from __future__ import annotations

from datetime import date, timedelta


def group_plant_years(group: dict, first_iso_year: int) -> dict[int, int]:
    """Apply an explicitly supplied first-week year across visible week wrap."""
    years = {}
    year = first_iso_year
    previous_week = None
    for row in group["rows"]:
        week = row["plant_week"]
        if week is not None and previous_week is not None and previous_week - week > 20:
            year += 1
        years[row["row_number"]] = year
        if week is not None:
            previous_week = week
    return years


def iso_date(year: int, week: int | None, weekday: int | None) -> date | None:
    if week is None or weekday is None:
        return None
    try:
        return date.fromisocalendar(year, week, weekday)
    except ValueError:
        return None


def next_week_date(start: date | None, week: int | None, weekday: int | None, max_days: int) -> date | None:
    if start is None or week is None or weekday is None:
        return None
    choices = [
        iso_date(year, week, weekday)
        for year in (start.isocalendar().year, start.isocalendar().year + 1, start.isocalendar().year + 2)
    ]
    choices = [day for day in choices if day is not None and start <= day <= start + timedelta(days=max_days)]
    return min(choices) if choices else None


def plantjaren(regels: list[dict], eerste_iso_jaar: int) -> dict[int, int]:
    """ISO-jaar per rij: het opgegeven jaar van de eerste planting, +1 bij een zichtbare weekomslag (>20 weken terug)."""
    groep = {"rows": [{"plant_week": r.get("plant_week"), "row_number": r["rij"]} for r in regels]}
    return group_plant_years(groep, eerste_iso_jaar)
