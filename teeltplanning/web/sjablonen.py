"""Jinja2-omgeving en weergavehulpen."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from .. import __version__
from ..kern.kalender import weergave

MAP = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(MAP / "templates"))
NL = ZoneInfo("Europe/Amsterdam")


def wd(d: date | None, formaat: str = "week-dag") -> str:
    return weergave(d, formaat) if d else ""


def getal(waarde, decimalen: int = 0, leeg: str = "") -> str:
    if waarde is None:
        return leeg
    if isinstance(waarde, (int, float)) and decimalen == 0:
        tekst = f"{round(waarde):,}".replace(",", ".")
        return tekst
    return f"{waarde:,.{decimalen}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def tijd(waarde: datetime | None) -> str:
    if waarde is None:
        return ""
    if waarde.tzinfo is None:
        waarde = waarde.replace(tzinfo=ZoneInfo("UTC"))
    return waarde.astimezone(NL).strftime("%d-%m-%Y %H:%M")


templates.env.filters["wd"] = wd
templates.env.filters["getal"] = getal
templates.env.filters["tijd"] = tijd
templates.env.globals["versie"] = __version__
