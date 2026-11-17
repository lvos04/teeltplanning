"""De synthetische werkboeken hebben de structuur uit BOUWPLAN §7."""

from openpyxl import load_workbook

from tests.synthetisch import AANTAL_RONDES, wb_optellen


def test_programma_tabbladen_in_volgorde(synthetische_werkboeken):
    wb = load_workbook(synthetische_werkboeken["programma"], read_only=True)
    verwacht = ["productie cijfers", *[f"Ronde {n}" for n in range(1, AANTAL_RONDES + 1)]]
    verwacht += ["productie", "teeltkaart", "weekplanning dekker", "Blad2"]
    assert wb.sheetnames == verwacht


def test_rondeblad_kop_regels_en_formules_met_cache(synthetische_werkboeken):
    pad = synthetische_werkboeken["programma"]
    formules = load_workbook(pad)["Ronde 2"]
    waarden = load_workbook(pad, data_only=True)["Ronde 2"]
    assert waarden["C8"].value == "Ronde 2"
    assert waarden["D10"].value == "schema" and waarden["E10"].value == "pl datum"
    assert waarden["H10"].value == "licht uit" and waarden["N10"].value == "bloei"
    assert waarden["C11"].value == "vak" and waarden["E11"].value == "w/d"
    assert [waarden[f"C{r}"].value for r in range(12, 52)] == list(range(1, 41))
    assert waarden["B12"].value is not None and waarden["B13"].value is None  # vakgroep alleen op eerste regel
    for kolom in ("H", "I", "N", "O", "P", "Z", "AB", "AD", "AF"):
        assert str(formules[f"{kolom}12"].value).startswith("="), kolom
        assert waarden[f"{kolom}12"].value is not None, f"cache ontbreekt in {kolom}12"
    assert formules["E12"].value == "='Ronde 1'!N12"  # verwijzing naar de bloeiweek van de vorige ronde
    assert formules.print_area == "'Ronde 2'!$B$4:$R$51"
    assert waarden["L12"].value in (0, 1, 2)


def test_cache_volgt_52_weken_rekenwerk(synthetische_werkboeken):
    ws = load_workbook(synthetische_werkboeken["programma"], data_only=True)["Ronde 3"]
    for r in range(12, 52):
        e, f, g, j, lv = (ws[f"{k}{r}"].value for k in "EFGJL")
        assert (ws[f"H{r}"].value, ws[f"I{r}"].value) == wb_optellen(e, f, g)
        assert (ws[f"N{r}"].value, ws[f"O{r}"].value) == wb_optellen(*wb_optellen(e, f, g), j + lv)
        assert ws[f"P{r}"].value == g + j + lv


def test_teeltkaart_afdrukbereik_en_streeflengtetabel(synthetische_werkboeken):
    ws = load_workbook(synthetische_werkboeken["programma"])["teeltkaart"]
    assert ws.print_area == "'teeltkaart'!$A$1:$AG$31"
    assert ws.page_setup.orientation == "landscape"
    assert [ws[f"{k}1"].value for k in ("AI", "AJ", "AK", "AL", "AM")] == [
        "LD-dag",
        "winter",
        "voorjaar",
        "zomer",
        "najaar",
    ]
    assert ws["AI41"].value == 39
    assert ws["AL6"].value == 30  # LD-dag 4, zomer


def test_belichting_structuur(synthetische_werkboeken):
    wb = load_workbook(synthetische_werkboeken["belichting"], read_only=True)
    assert wb.sheetnames == ["belichting", "teeltschema", "alar", "alar tutu", "data"]
    ws = wb["belichting"]
    rij2 = [c.value for c in next(ws.iter_rows(min_row=2, max_row=2, max_col=28))]
    assert rij2[0] == "module" and rij2[1] == 36 and rij2[27] == 10
    modules = [r[0] for r in ws.iter_rows(min_row=3, max_row=83, max_col=1, values_only=True)]
    assert modules[:40] == list(range(1, 41)) and modules[40] is None and modules[41:] == list(range(101, 141))
    kop87 = [c for c in next(ws.iter_rows(min_row=87, max_row=87, max_col=14, values_only=True))]
    assert "LBE prognose" in kop87 and "LBE realisatie" in kop87
    ts = wb["teeltschema"]
    kop = next(ts.iter_rows(min_row=1, max_row=1, max_col=42, values_only=True))
    assert (kop[0], kop[1], kop[6], kop[15], kop[18]) == ("kap", "planten", "licht uit", "leeg", "gewicht")
    assert ts.max_row == 861
    data = wb["data"]
    koppen = list(data.iter_rows(min_row=1, max_row=3, min_col=2, max_col=3, values_only=True))
    assert koppen == [("Gem. licht%", "Gem. licht%"), ("PCU 1:MOD. LICHT 101", "PCU 1:MOD. LICHT 102"), ("%", "%")]
