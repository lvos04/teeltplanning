"""Importer §7.1 en de vergelijking met de gecachte werkboekuitkomsten (fase 1-acceptatie, synthetisch)."""

import os
from pathlib import Path

import pytest

from teeltplanning.importers import programma, vergelijking
from teeltplanning.instellingen import REGISTER

INSTELLING = {k: i.startwaarde for k, i in REGISTER.items()}


@pytest.fixture(scope="module")
def gelezen(synthetische_werkboeken):
    return programma.lees_programma(synthetische_werkboeken["programma"], INSTELLING)


def test_alle_rondes_en_kapregels(gelezen):
    assert [r["nummer"] for r in gelezen["rondes"]] == list(range(1, 35))
    assert sum(len(r["regels"]) for r in gelezen["rondes"]) == 1360
    r34 = gelezen["rondes"][-1]
    assert r34["titel_conflict"] and r34["titel"] == "Ronde 35"
    assert any(m["code"] == "rondelabel_wijkt_af" for m in gelezen["meldingen"])


def test_regels_bewaren_formule_en_cache(gelezen):
    regel = gelezen["rondes"][1]["regels"][0]  # Ronde 2, rij 12
    assert regel["plant_verwijzing"] == {"ronde": 1, "cel": "N12"}
    bron = regel["broncellen"]["licht_uit_week"]
    assert bron["formula"].startswith("=MOD(") and bron["cached"] == regel["cache"]["licht_uit_week"]
    assert regel["vakgroep"] == "11" and not regel["vakgroep_doorgevuld"]
    assert gelezen["rondes"][1]["regels"][1]["vakgroep_doorgevuld"]
    r5 = gelezen["rondes"][4]["regels"][7]
    assert "plantdatum_onbekend" in r5["meldingen"]
    r3 = gelezen["rondes"][2]["regels"][4]
    assert "schema_unrecognized" in r3["meldingen"]
    gemengd = next(r for ronde in gelezen["rondes"] for r in ronde["regels"] if r["schema"] == "3a3p")
    assert [(d["code"], d["beds"]) for d in gemengd["rassen"]] == [("a", 3), ("p", 3)]


def test_verplaatste_kolom_via_instelling(synthetische_werkboeken):
    anders = dict(INSTELLING)
    anders["import.rondeblad_koppen"] = {"D10": "iets anders"}
    gelezen = programma.lees_programma(synthetische_werkboeken["programma"], anders)
    assert gelezen["rondes"] == [] and gelezen["meldingen"][0]["code"] == "rondeblad_niet_herkend"


def test_vergelijking_alleen_52_wekenfout(gelezen, synthetische_werkboeken):
    jaren = synthetische_werkboeken["meta"]["programma"]["jaren"]
    uitkomsten = []
    for ronde in gelezen["rondes"]:
        data = programma.datums(ronde["regels"], jaren[ronde["nummer"]])
        uitkomsten += [vergelijking.vergelijk_regel(r, data[r["rij"]]["plantdatum"]) for r in ronde["regels"]]
    rapport = vergelijking.rapport(uitkomsten)
    assert rapport["totaal"] == 1360
    assert rapport["tellingen"].get("afwijking", 0) == 0
    assert rapport["tellingen"]["onvolledig"] == 1  # ronde 5, kap 8: geen plantdatum (bewust)
    assert rapport["tellingen"]["52_weken"] > 0 and rapport["tellingen"]["gelijk"] > 1000
    for u in rapport["afwijkend"]:
        if u["klasse"] == "52_weken":
            # Alleen in ISO-jaren met 53 weken (2020 en 2026 in het synthetische werkboek).
            assert u["app_licht_uit_datum"][:4] in ("2020", "2021", "2026", "2027") or u["app_bloei_datum"][:4] in (
                "2020",
                "2021",
                "2026",
                "2027",
            )
    csv = vergelijking.als_csv(rapport["afwijkend"])
    assert csv.splitlines()[0].lstrip("﻿").startswith("blad;rij;volgnummer;klasse")


def test_vergelijking_herkent_onverklaarde_afwijking():
    regel = {"blad": "Ronde 1", "rij": 12, "volgnummer": 1, "ld_dagen": 7, "reactietijd": 50, "verlenging": 0,
             "cache": {"licht_uit_week": 3, "licht_uit_dag": 1, "bloei_week": 10, "bloei_dag": 2, "teeltduur": 57}}  # fmt: skip
    from datetime import date

    assert vergelijking.vergelijk_regel(regel, date.fromisocalendar(2026, 1, 1))["klasse"] == "afwijking"


ECHT = os.environ.get("TEELT_ECHT_PROGRAMMA")


@pytest.mark.skipif(not ECHT or not Path(ECHT).is_file(), reason="TEELT_ECHT_PROGRAMMA niet gezet of bestand ontbreekt")
def test_echt_werkboek_1360_kapregels():
    """Met het echte werkboek: alle kapregels lezen. De vergelijking vraagt de ISO-jaren per ronde
    (TEELT_ECHT_JAREN, bijvoorbeeld '1:2018,2:2018,…'); zonder die variabele alleen het inlezen."""
    gelezen = programma.lees_programma(Path(ECHT), INSTELLING)
    aantal = sum(len(r["regels"]) for r in gelezen["rondes"])
    print(f"\n{len(gelezen['rondes'])} rondes, {aantal} kapregels; meldingen: {gelezen['meldingen']}")
    assert aantal == 1360
    jaren_tekst = os.environ.get("TEELT_ECHT_JAREN")
    if jaren_tekst:
        jaren = {int(a): int(b) for a, b in (deel.split(":") for deel in jaren_tekst.split(","))}
        uitkomsten = []
        for ronde in gelezen["rondes"]:
            if ronde["nummer"] in jaren:
                data = programma.datums(ronde["regels"], jaren[ronde["nummer"]])
                uitkomsten += [vergelijking.vergelijk_regel(r, data[r["rij"]]["plantdatum"]) for r in ronde["regels"]]
        rapport = vergelijking.rapport(uitkomsten)
        Path(os.environ.get("TEELT_RAPPORT", "vergelijking-echt.csv")).write_text(
            vergelijking.als_csv(rapport["afwijkend"]), encoding="utf-8"
        )
        print(rapport["tellingen"])
        assert rapport["tellingen"].get("afwijking", 0) == 0
