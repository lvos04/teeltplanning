"""`kern/` importeert niets van buiten zichzelf, en in kern/, voorstel/ en afdruk/ staan geen harde getallen
die niet uit het register komen (BOUWPLAN §3 en §4.3)."""

import ast
import sys
from pathlib import Path

PAKKET = Path(__file__).resolve().parent.parent / "teeltplanning"
STANDAARD = set(sys.stdlib_module_names)

# Toegestane getallen: wiskundige en kalenderconstanten die natuurkundig of per definitie vastliggen.
# 0 en 1 (nul, eenheid, "plus één dag"), 7 (dagen per week), 24 (uur per dag), 60 (minuten), 100 (procent),
# 3600 (seconden per uur), en eenheidsomrekeningen die natuurkundig vastliggen (§4.3): 1000 (kilo, bijv.
# kWh ↔ MWh), 1024 (kibi: MiB ↔ bytes) en 1.000.000 (micro/mega, bijv. µmol ↔ mol). Al het andere komt uit
# het instellingenregister.
TOEGESTAAN = {0, 1, 7, 24, 60, 100, 3600, 1000, 1024, 1_000_000}


def _bestanden(map_: str):
    return sorted((PAKKET / map_).rglob("*.py"))


def test_kern_importeert_niets_van_buiten():
    fouten = []
    for pad in _bestanden("kern"):
        for knoop in ast.walk(ast.parse(pad.read_text())):
            if isinstance(knoop, ast.ImportFrom):
                if knoop.level == 0 and knoop.module.split(".")[0] not in STANDAARD | {"__future__"}:
                    fouten.append(f"{pad.name}: from {knoop.module}")
                if knoop.level > 1:
                    fouten.append(f"{pad.name}: relatieve import buiten kern ({'.' * knoop.level}{knoop.module})")
            elif isinstance(knoop, ast.Import):
                for alias in knoop.names:
                    if alias.name.split(".")[0] not in STANDAARD:
                        fouten.append(f"{pad.name}: import {alias.name}")
    assert not fouten, fouten


def test_geen_harde_getallen_in_kern_voorstel_afdruk():
    fouten = []
    for map_ in ("kern", "voorstel", "afdruk"):
        for pad in _bestanden(map_):
            for knoop in ast.walk(ast.parse(pad.read_text())):
                if (
                    isinstance(knoop, ast.Constant)
                    and type(knoop.value) in (int, float)
                    and abs(knoop.value) not in TOEGESTAAN
                ):
                    fouten.append(f"{map_}/{pad.name}:{knoop.lineno}: {knoop.value!r}")
    assert not fouten, "Getallen horen in het instellingenregister: " + ", ".join(fouten)


def test_harde_getallen_test_vangt_een_getal(tmp_path):
    bron = "def f(x):\n    return x + 12\n"
    gevonden = [k.value for k in ast.walk(ast.parse(bron)) if isinstance(k, ast.Constant) and k.value not in TOEGESTAAN]
    assert gevonden == [12]
