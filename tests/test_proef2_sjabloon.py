"""Proef 2: sjabloon vullen; opmaak en afdrukbereik blijven ongewijzigd."""

import os
import shutil
import zipfile
from pathlib import Path

import pytest
from openpyxl import load_workbook

from proeven.proef2_sjabloon import (
    alleen_afdrukblad,
    naar_pdf,
    pdf_paginas,
    proefwaarden,
    vergelijk_opmaak,
    vergelijk_pakketten,
    vul_sjabloon,
)


def _vul(bron: Path, map_: Path, blad: str, bereik: str) -> Path:
    doel = map_ / "gevuld.xlsx"
    vul_sjabloon(bron, doel, blad, proefwaarden(bron, blad, bereik))
    return doel


def test_rondeblad_vullen_raakt_alleen_blad_en_rekeninstelling(synthetische_werkboeken, tmp_path):
    bron = synthetische_werkboeken["programma"]
    doel = _vul(bron, tmp_path, "Ronde 1", "B4:R51")
    verschil = vergelijk_pakketten(bron, doel)
    assert verschil["verdwenen"] == [] and verschil["nieuw"] == []
    assert verschil["verschillend"] == ["xl/workbook.xml", "xl/worksheets/sheet2.xml"]
    assert vergelijk_opmaak(bron, doel, "Ronde 1", "B4:R51") == []
    formules = load_workbook(doel)["Ronde 1"]
    assert formules["B4"].value == "PROEF gevuld sjabloon"
    assert formules["H12"].value == load_workbook(bron, data_only=True)["Ronde 1"]["H12"].value  # waarde, geen formule
    assert formules["AF12"].value.startswith("=")  # buiten het bereik blijft de formule staan
    with zipfile.ZipFile(doel) as z:
        assert 'fullCalcOnLoad="1"' in z.read("xl/workbook.xml").decode()


def test_nieuwe_cel_en_rij_worden_op_volgorde_ingevoegd(synthetische_werkboeken, tmp_path):
    bron = synthetische_werkboeken["programma"]
    doel = tmp_path / "x.xlsx"
    vul_sjabloon(bron, doel, "Ronde 1", {"A5": "nieuw", "Q12": 3.5, "C3": "<&>"})
    ws = load_workbook(doel)["Ronde 1"]
    assert ws["A5"].value == "nieuw" and ws["Q12"].value == 3.5 and ws["C3"].value == "<&>"
    assert ws["R12"].value == load_workbook(bron)["Ronde 1"]["R12"].value


def test_teeltkaart_vullen(synthetische_werkboeken, tmp_path):
    bron = synthetische_werkboeken["programma"]
    doel = tmp_path / "kaart.xlsx"
    vul_sjabloon(bron, doel, "teeltkaart", {"N1": 7, "N2": 3, "A11": "45-1"})
    assert vergelijk_opmaak(bron, doel, "teeltkaart", "A1:AG31") == []
    assert load_workbook(doel)["teeltkaart"].print_area == "'teeltkaart'!$A$1:$AG$31"


@pytest.mark.skipif(not shutil.which("soffice"), reason="LibreOffice (soffice) niet beschikbaar")
@pytest.mark.parametrize(("blad", "bereik"), [("Ronde 1", "B4:R51"), ("teeltkaart", "A1:AG31")])
def test_pdf_via_libreoffice_een_pagina(synthetische_werkboeken, tmp_path, blad, bereik):
    gevuld = _vul(synthetische_werkboeken["programma"], tmp_path, blad, bereik)
    afdruk = tmp_path / "afdruk.xlsx"
    alleen_afdrukblad(gevuld, afdruk, blad)
    pdf = naar_pdf(afdruk, tmp_path)
    assert pdf_paginas(pdf) == 1


ECHT = os.environ.get("TEELT_ECHT_PROGRAMMA")


@pytest.mark.skipif(not ECHT or not Path(ECHT).is_file(), reason="TEELT_ECHT_PROGRAMMA niet gezet of bestand ontbreekt")
def test_echt_sjabloon_vullen(tmp_path):
    bron = Path(ECHT)
    doel = _vul(bron, tmp_path, "Ronde 1", "B4:R51")
    verschil = vergelijk_pakketten(bron, doel)
    assert verschil["nieuw"] == []
    assert set(verschil["verschillend"]) <= {"xl/workbook.xml", "[Content_Types].xml", "xl/_rels/workbook.xml.rels"} | {
        n for n in verschil["verschillend"] if n.startswith("xl/worksheets/")
    }
    assert vergelijk_opmaak(bron, doel, "Ronde 1", "B4:R51") == []
    print(f"\nGevuld werkboek voor controle in Excel: {doel}")
