"""Proef 1: duurcellen als uren lezen (synthetisch; het echte bestand via TEELT_ECHT_BELICHTING)."""

import os
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest

from proeven.proef1_duurcellen import duur_naar_uren, lees_weekuren, rapport


@pytest.mark.parametrize(
    ("waarde", "uren", "soort"),
    [
        (timedelta(days=3, hours=10, minutes=35), 82 + 35 / 60, "timedelta"),
        (datetime(1900, 1, 3, 10, 35), 82 + 35 / 60, "datetime"),  # 'd "days," hh:mm' leest als datum
        (time(10, 35), 10 + 35 / 60, "time"),
        (3.440972222222222, 82 + 35 / 60, "getal"),
        ("3 days, 10:35", 82 + 35 / 60, "tekst"),
        ("82:35", 82 + 35 / 60, "tekst"),
        (None, None, "leeg"),
        ("onzin", None, "onbekend"),
    ],
)
def test_duur_op_celtype(waarde, uren, soort):
    duur = duur_naar_uren(waarde)
    assert duur.soort == soort
    if uren is None:
        assert duur.uren is None
    else:
        assert duur.uren == pytest.approx(uren, abs=1e-6)


def test_tekst_en_tijdcel_geven_melding():
    assert duur_naar_uren("3 days, 10:35").melding
    assert duur_naar_uren(time(1, 0)).melding


def test_alle_synthetische_weekuren_kloppen(synthetische_werkboeken):
    verwacht = synthetische_werkboeken["meta"]["belichting"]["verwacht_uren"]
    regels = lees_weekuren(synthetische_werkboeken["belichting"])
    assert len(regels) == 80 * 27
    gelezen = {r.cel: r for r in regels}
    for cel, uren in verwacht.items():
        if uren is None:
            assert gelezen[cel].duur.uren is None
        else:
            assert gelezen[cel].duur.uren == pytest.approx(uren, abs=1e-6), cel
    assert [r.cel for r in regels if r.buiten_grenzen] == ["D3"]  # 180 uur > 168
    assert gelezen["B3"].duur.uren == pytest.approx(82.5833333, abs=1e-6)  # "3 days, 10:35"
    soorten = {r.duur.soort for r in regels}
    assert {"timedelta", "datetime", "time", "getal", "tekst", "leeg"} <= soorten


def test_rapport_toont_tien_regels(synthetische_werkboeken):
    tekst = rapport(lees_weekuren(synthetische_werkboeken["belichting"]), 10, 1)
    assert len(tekst.splitlines()[1:11]) == 10
    assert "buiten 0–168 uur per week: 1" in tekst


ECHT = os.environ.get("TEELT_ECHT_BELICHTING")


@pytest.mark.skipif(
    not ECHT or not Path(ECHT).is_file(), reason="TEELT_ECHT_BELICHTING niet gezet of bestand ontbreekt"
)
def test_echt_belichtingsbestand():
    regels = lees_weekuren(Path(ECHT))
    assert regels, "geen weekuren gevonden op tabblad belichting"
    assert not [r.cel for r in regels if r.duur.soort == "onbekend"], "onbekende celtypen"
    print("\n" + rapport(regels, 10, 1))  # handmatig narekenen (pytest -s)
