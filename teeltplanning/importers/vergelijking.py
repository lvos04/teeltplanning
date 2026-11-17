"""Vergelijking van de eigen datumketen met de gecachte uitkomsten van het werkboek (fase 1-acceptatie).

Per kapregel rekent de app licht uit, bloei en teeltduur met echte datums (`kern.keten`), uitgaande van de
**invoer zoals het werkboek die toont** (plantweek/-dag, LD-dagen, reactietijd, verlenging) en het opgegeven
ISO-jaar. Elke regel krijgt een klasse:

* `gelijk` — week-dag van licht uit en bloei en de teeltduur zijn gelijk aan de cache;
* `52_weken` — de app wijkt af, maar het werkboekrekenwerk met 52 weken per jaar (zie `werkboek_optellen`)
  geeft precies de cache: dit is de bekende 52-wekenfout (§4.4); de app heeft gelijk;
* `afwijking` — niet te verklaren; moet worden uitgezocht;
* `onvolledig` — invoer of cache ontbreekt.

Let op: hoe het échte werkboek rekent is niet bekend (open vraag B1). `werkboek_optellen` reproduceert de
fout zoals §4.4 hem beschrijft; bij het echte werkboek blijkt of dat klopt (regels in `afwijking`).
"""

from __future__ import annotations

import csv
import io
from datetime import date

from ..kern import keten
from ..kern.kalender import week_dag

WEKEN_IN_WERKBOEK = 52  # het werkboek rekent hard met 52 weken (§4.4); dit beschrijft de fout, geen instelling


def werkboek_optellen(week: int, dag: int, dagen: int) -> tuple[int, int]:
    t = (week - 1) * 7 + (dag - 1) + dagen
    return (t // 7) % WEKEN_IN_WERKBOEK + 1, t % 7 + 1


def _wd(d: date | None) -> tuple[int, int] | None:
    if d is None:
        return None
    _, week, dag = week_dag(d)
    return week, dag


def vergelijk_regel(regel: dict, plantdatum: date | None) -> dict:
    c = regel["cache"]
    ld, rt, verl = regel.get("ld_dagen"), regel.get("reactietijd"), regel.get("verlenging")
    uit = {"blad": regel["blad"], "rij": regel["rij"], "volgnummer": regel["volgnummer"]}
    cache_lu = (c.get("licht_uit_week"), c.get("licht_uit_dag"))
    cache_bloei = (c.get("bloei_week"), c.get("bloei_dag"))
    if (
        plantdatum is None
        or None in (ld, rt, verl)
        or None in cache_lu
        or None in cache_bloei
        or c.get("teeltduur") is None
    ):
        return uit | {"klasse": "onvolledig"}
    lu = keten.licht_uit(plantdatum, ld)
    oogst = keten.oogst(lu, rt, verl)
    app = {"licht_uit": _wd(lu), "bloei": _wd(oogst), "teeltduur": keten.teeltduur(plantdatum, oogst)}
    werkboek = {"licht_uit": cache_lu, "bloei": cache_bloei, "teeltduur": c["teeltduur"]}
    uit |= {
        "plantdatum": plantdatum.isoformat(),
        "app_licht_uit": f"{app['licht_uit'][0]}-{app['licht_uit'][1]}",
        "werkboek_licht_uit": f"{cache_lu[0]}-{cache_lu[1]}",
        "app_bloei": f"{app['bloei'][0]}-{app['bloei'][1]}",
        "werkboek_bloei": f"{cache_bloei[0]}-{cache_bloei[1]}",
        "app_teeltduur": app["teeltduur"],
        "werkboek_teeltduur": c["teeltduur"],
        "app_licht_uit_datum": lu.isoformat(),
        "app_bloei_datum": oogst.isoformat(),
    }
    if app == werkboek:
        return uit | {"klasse": "gelijk"}
    _, pw, pd = week_dag(plantdatum)
    na = werkboek_optellen(pw, pd, ld)
    nagebootst = {"licht_uit": na, "bloei": werkboek_optellen(*na, rt + verl), "teeltduur": ld + rt + verl}
    if nagebootst == werkboek:
        return uit | {"klasse": "52_weken"}
    return uit | {"klasse": "afwijking"}


def rapport(uitkomsten: list[dict]) -> dict:
    tellingen: dict[str, int] = {}
    for u in uitkomsten:
        tellingen[u["klasse"]] = tellingen.get(u["klasse"], 0) + 1
    return {
        "totaal": len(uitkomsten),
        "tellingen": tellingen,
        "geslaagd": tellingen.get("afwijking", 0) == 0 and tellingen.get("onvolledig", 0) == 0,
        "afwijkend": [u for u in uitkomsten if u["klasse"] != "gelijk"],
    }


KOLOMMEN = ["blad", "rij", "volgnummer", "klasse", "plantdatum", "app_licht_uit", "werkboek_licht_uit", "app_bloei",
            "werkboek_bloei", "app_teeltduur", "werkboek_teeltduur", "app_licht_uit_datum", "app_bloei_datum"]  # fmt: skip


def als_csv(uitkomsten: list[dict]) -> str:
    buffer = io.StringIO()
    schrijver = csv.DictWriter(buffer, fieldnames=KOLOMMEN, delimiter=";", extrasaction="ignore")
    schrijver.writeheader()
    for u in uitkomsten:
        schrijver.writerow(u)
    return "﻿" + buffer.getvalue()
