"""Synthetische testwerkboeken met de structuur uit BOUWPLAN §7 (geen bedrijfsgegevens).

Alle getallen hierin zijn verzonnen testwaarden en hebben geen agronomische betekenis. De indeling volgt
§7.1 (`Teeltprogramma maken …xlsx`) en §7.2 (`Belichting winterronde.xlsx`). Waar het bouwplan de indeling
niet beschrijft (kopteksten, de bladen `productie cijfers`, `productie`, `alar`, de kolommen van de
kaptabel vanaf rij 87 en van `teeltschema`), is een aanname gedaan; die staat in docs/EXCEL.md en moet bij de
eerste import van het echte werkboek worden gecontroleerd.

Formules worden geschreven mét gecachte uitkomst, zoals Excel dat doet. De formules rekenen bewust met 52
weken per jaar (net als het echte werkboek volgens §4.4), zodat de vergelijking de 52-wekenfout kan tonen.

Gebruik: ``python -m tests.synthetisch <uitvoermap>``.
"""

from __future__ import annotations

import json
import random
import re
import sys
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

PROGRAMMA_NAAM = "Teeltprogramma maken synthetisch.xlsx"
BELICHTING_NAAM = "Belichting winterronde synthetisch.xlsx"
AANTAL_RONDES = 34
EERSTE_RIJ, LAATSTE_RIJ = 12, 51
VAKGROEPEN = [11, 12, 21, 22, 23, 31, 32, 33]  # synthetisch: 8 groepen van 5 kappen
EERSTE_ISO_JAAR = 2020  # synthetisch; het echte jaar staat niet in het werkboek (open vraag 5)

DUN = Side(style="thin")
RAND = Border(left=DUN, right=DUN, top=DUN, bottom=DUN)
KOP_VULLING = PatternFill("solid", fgColor="FFDDEBF7")


def wb_optellen(week: int, dag: int, dagen: int) -> tuple[int, int]:
    """Week/dag-rekenwerk zoals de synthetische werkboekformules: 52 weken per jaar, hard."""
    t = (week - 1) * 7 + (dag - 1) + dagen
    return (t // 7) % 52 + 1, t % 7 + 1


# ---------------------------------------------------------------------------------------------------------
# Gecachte formule-uitkomsten in het XLSX-pakket zetten (openpyxl schrijft zelf een lege <v />)
# ---------------------------------------------------------------------------------------------------------
def _zet_caches(pad: Path, bladen: list[str], caches: dict[str, dict[str, object]]) -> None:
    tijdelijk = pad.with_suffix(".tmp")
    with zipfile.ZipFile(pad) as bron, zipfile.ZipFile(tijdelijk, "w", zipfile.ZIP_DEFLATED) as doel:
        for item in bron.infolist():
            data = bron.read(item.filename)
            m = re.fullmatch(r"xl/worksheets/sheet(\d+)\.xml", item.filename)
            if m:
                titel = bladen[int(m.group(1)) - 1]
                cache = caches.get(titel, {})
                if cache:
                    tekst = data.decode("utf-8")

                    def vervang(match: re.Match, cache=cache) -> str:
                        ref, attrs, formule = match.group(1), match.group(2) or "", match.group(3)
                        if ref not in cache:
                            return match.group(0)
                        waarde = cache[ref]
                        if isinstance(waarde, str):
                            return f'<c r="{ref}"{attrs} t="str"><f>{formule}</f><v>{waarde}</v></c>'
                        return f'<c r="{ref}"{attrs}><f>{formule}</f><v>{waarde!r}</v></c>'

                    tekst = re.sub(r'<c r="([A-Z]+\d+)"((?: s="\d+")?)><f>(.*?)</f><v\s*/></c>', vervang, tekst)
                    data = tekst.encode("utf-8")
            doel.writestr(item, data)
    tijdelijk.replace(pad)


# ---------------------------------------------------------------------------------------------------------
# Teeltprogramma maken
# ---------------------------------------------------------------------------------------------------------
KOP_RIJ10 = {
    "B": "groep", "C": "kap", "D": "schema", "E": "pl datum", "G": "dagen", "H": "licht uit", "J": "reactie",
    "K": "tussen", "L": "verl.", "M": "pl/m2", "N": "bloei", "P": "teelt", "R": "teelt", "T": "gewicht",
    "V": "datum", "X": "gem.", "Y": "uitval", "Z": "geplant", "AB": "oogst", "AD": "maand", "AF": "cumulatief",
}  # fmt: skip
KOP_RIJ11 = {
    "C": "vak", "E": "w/d", "G": "LD", "H": "w/d", "J": "tijd", "K": "licht", "L": "dagen", "N": "w/d",
    "P": "duur", "R": "verlenging", "T": "prognose", "V": "geoogst", "X": "gewicht", "Z": "aantal",
    "AB": "aantal",
}  # fmt: skip


def _ronde_opmaak(ws) -> None:
    breedtes = {"A": 2, "B": 6, "C": 5, "D": 8, "E": 5, "F": 4, "G": 6, "H": 5, "I": 4, "J": 7, "K": 8, "L": 6}
    breedtes |= {"M": 7, "N": 5, "O": 4, "P": 6, "Q": 2, "R": 9, "T": 8, "V": 8, "X": 7, "Y": 7, "Z": 8}
    for kolom, breedte in breedtes.items():
        ws.column_dimensions[kolom].width = breedte
    for bereik in ("C8:F8", "E10:F10", "H10:I10", "N10:O10"):
        ws.merge_cells(bereik)
    ws.print_area = "B4:R51"
    ws.page_setup.orientation = "portrait"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.freeze_panes = "D12"
    ws["B4"] = "Teeltprogramma (synthetisch testwerkboek)"
    ws["B4"].font = Font(name="Arial", size=14, bold=True)
    ws["C8"].font = Font(name="Arial", size=12, bold=True)
    ws["C8"].alignment = Alignment(horizontal="center")
    dv = DataValidation(type="whole", operator="between", formula1="0", formula2="2", allow_blank=True)
    dv.add("L12:L51")
    ws.add_data_validation(dv)
    ws["K10"].comment = Comment("synthetisch: tussenlicht als week-dag of datum", "test")


def _schrijf_ronde(ws, nummer: int, regels: list[dict], cache: dict[str, object], titel: str) -> None:
    _ronde_opmaak(ws)
    ws["C8"] = titel
    for kolom, tekst in KOP_RIJ10.items():
        ws[f"{kolom}10"] = tekst
        ws[f"{kolom}10"].fill = KOP_VULLING
        ws[f"{kolom}10"].font = Font(name="Arial", size=9, bold=True)
    for kolom, tekst in KOP_RIJ11.items():
        ws[f"{kolom}11"] = tekst
        ws[f"{kolom}11"].font = Font(name="Arial", size=8, italic=True)
    cumulatief = 0.0
    for regel in regels:
        r = regel["rij"]
        for kolom in ("B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "R"):
            ws[f"{kolom}{r}"].border = RAND
            ws[f"{kolom}{r}"].font = Font(name="Arial", size=9)
        if regel["vakgroep"] is not None:
            ws[f"B{r}"] = regel["vakgroep"]
        ws[f"C{r}"] = regel["volgnummer"]
        if regel["schema"] is not None:
            ws[f"D{r}"] = regel["schema"]
        if regel["plant_week_formule"]:
            ws[f"E{r}"] = regel["plant_week_formule"]
            cache[f"E{r}"] = regel["plant_week"]
        elif regel["plant_week"] is not None:
            ws[f"E{r}"] = regel["plant_week"]
        if regel["plant_dag"] is not None:
            ws[f"F{r}"] = regel["plant_dag"]
        ws[f"G{r}"] = regel["ld"]
        ws[f"J{r}"] = regel["rt"]
        if regel["tussenlicht"] is not None:
            ws[f"K{r}"] = regel["tussenlicht"]
            if isinstance(regel["tussenlicht"], datetime):
                ws[f"K{r}"].number_format = "dd-mm-yy"
        ws[f"L{r}"] = regel["verlenging"]
        ws[f"M{r}"] = regel["dichtheid"]
        ws[f"H{r}"] = f"=MOD(E{r}-1+INT((F{r}-1+G{r})/7),52)+1"
        ws[f"I{r}"] = f"=MOD(F{r}-1+G{r},7)+1"
        ws[f"N{r}"] = f"=MOD(H{r}-1+INT((I{r}-1+J{r}+L{r})/7),52)+1"
        ws[f"O{r}"] = f"=MOD(I{r}-1+J{r}+L{r},7)+1"
        ws[f"P{r}"] = f"=G{r}+J{r}+L{r}"
        if regel["plant_week"] is not None and regel["plant_dag"] is not None:
            cache[f"H{r}"], cache[f"I{r}"] = regel["licht_uit"]
            cache[f"N{r}"], cache[f"O{r}"] = regel["bloei"]
            cache[f"P{r}"] = regel["ld"] + regel["rt"] + regel["verlenging"]
        if regel["teeltverlenging"] is not None:
            ws[f"R{r}"] = regel["teeltverlenging"]
        if regel["gewicht"] is not None:
            ws[f"T{r}"] = regel["gewicht"]
        if regel["geoogst"] is not None:
            ws[f"V{r}"] = regel["geoogst"]
        if regel["gem_gewicht"] is not None:
            ws[f"X{r}"] = regel["gem_gewicht"]
        ws[f"Y{r}"] = regel["uitval"]
        ws[f"Z{r}"] = f"=M{r}*726"
        ws[f"AB{r}"] = f"=Z{r}-Y{r}*Z{r}"
        ws[f"AD{r}"] = f"=ROUNDUP(N{r}/4.35,0)"
        ws[f"AF{r}"] = f"=SUM($AB${EERSTE_RIJ}:AB{r})"
        geplant = regel["dichtheid"] * 726
        oogst = geplant - regel["uitval"] * geplant
        cumulatief += oogst
        cache[f"Z{r}"] = geplant
        cache[f"AB{r}"] = oogst
        if f"N{r}" in cache:
            cache[f"AD{r}"] = -(-cache[f"N{r}"] // 4.35)
        cache[f"AF{r}"] = cumulatief


def maak_programma(pad: Path, zaad: int = 20261116) -> dict:
    """Schrijf het synthetische programmawerkboek; geeft metadata terug (o.a. ISO-jaar per ronde)."""
    rnd = random.Random(zaad)
    wb = Workbook()
    wb.remove(wb.active)
    titels = ["productie cijfers", *[f"Ronde {n}" for n in range(1, AANTAL_RONDES + 1)]]
    titels += ["productie", "teeltkaart", "weekplanning dekker", "Blad2"]
    bladen = {titel: wb.create_sheet(titel) for titel in titels}
    caches: dict[str, dict[str, object]] = {titel: {} for titel in titels}

    pc = bladen["productie cijfers"]
    pc["A1"] = "synthetisch: indeling van het echte blad is onbekend (open vraag in docs/OPEN_VRAGEN.md)"

    vorige: dict[int, dict] = {}
    jaren: dict[int, int] = {}
    alle_regels: dict[int, list[dict]] = {}
    jaar = EERSTE_ISO_JAAR
    vorige_week_r12 = None
    for nummer in range(1, AANTAL_RONDES + 1):
        regels = []
        for i in range(40):
            rij = EERSTE_RIJ + i
            ld = rnd.randint(7, 21)
            rt = rnd.randint(49, 56)
            verlenging = rnd.choice([0, 0, 0, 1, 2])
            formule = None
            if nummer == 1:
                week, dag = wb_optellen(30, 1, (i // 3) * 7 + [0, 2, 4][i % 3])
            else:
                vorig = vorige[rij]
                week, dag = vorig["bloei"]
                if dag == 7:  # zondag: werkboek plant maandag erna (letterlijke invoer)
                    week, dag = wb_optellen(week, dag, 1)
                elif i % 2 == 0:
                    formule = f"='Ronde {nummer - 1}'!N{rij}"
            plant_week, plant_dag = week, dag
            if nummer == 5 and i == 7:
                plant_week = plant_dag = None  # ontbrekende plantdatum (meldingstest)
                formule = None
            licht_uit = wb_optellen(week, dag, ld)
            bloei = wb_optellen(*licht_uit, rt + verlenging)
            schema = rnd.choice(["a", "p", "t", "a", "p", "3a3p"])
            if nummer == 3 and i == 4:
                schema = "proef"  # vrije tekst
            tussenlicht: object = None
            if i % 4 == 0:
                tl = wb_optellen(*licht_uit, 12)
                tussenlicht = f"{tl[0]}-{tl[1]}"
            elif i % 4 == 1:
                tussenlicht = datetime(2001, 1, 1) + timedelta(days=rnd.randint(0, 300))  # datumcel
            verleden = nummer <= 30
            regels.append(
                {
                    "rij": rij,
                    "vakgroep": VAKGROEPEN[i // 5] if i % 5 == 0 else None,
                    "volgnummer": i + 1,
                    "schema": schema,
                    "plant_week": plant_week,
                    "plant_dag": plant_dag,
                    "plant_week_formule": formule,
                    "ld": ld,
                    "rt": rt,
                    "verlenging": verlenging,
                    "tussenlicht": tussenlicht,
                    "dichtheid": round(rnd.uniform(41.1, 58.6), 1),
                    "licht_uit": licht_uit,
                    "bloei": bloei,
                    "teeltverlenging": rnd.choice([None, None, 1]),
                    "gewicht": rnd.choice([None, 95, 100, 105]),
                    "geoogst": f"{bloei[0]}-{bloei[1]}" if verleden else None,
                    "gem_gewicht": round(rnd.uniform(85, 110), 1) if verleden else None,
                    "uitval": rnd.choice([0.05, 0.08]),
                }
            )
            vorige[rij] = regels[-1]
        week_r12 = regels[0]["plant_week"]
        if vorige_week_r12 is not None and week_r12 < vorige_week_r12:
            jaar += 1
        vorige_week_r12 = week_r12
        jaren[nummer] = jaar
        titel = f"Ronde {nummer}" if nummer != AANTAL_RONDES else f"Ronde {nummer + 1}"  # bewust afwijkend label
        _schrijf_ronde(bladen[f"Ronde {nummer}"], nummer, regels, caches[f"Ronde {nummer}"], titel)
        alle_regels[nummer] = regels

    pr = bladen["productie"]
    pr["A1"] = "synthetisch: begroting tegenover laatste schatting per maand (indeling onbekend)"

    tk = bladen["teeltkaart"]
    _schrijf_teeltkaart(tk)

    wb.save(pad)
    _zet_caches(pad, titels, caches)
    return {"jaren": jaren, "regels": alle_regels}


# Streeflengte uit BOUWPLAN §9.2 (controletabel); in het echte werkboek staat hij op teeltkaart!AI1:AM41.
STREEFLENGTE_CONTROLE = {
    4: (None, None, 30, None), 7: (45, 39, 35, 39), 8: (48, 42, 40, 42), 9: (51, 45, 42, 45),
    10: (54, 48, 44, 48), 11: (56, 51, 46, 51), 12: (58, 56, 48, 56), 13: (60, 58, 50, 58),
    14: (62, 60, 52, 60), 15: (64, 62, 54, 62), 16: (66, 64, 56, 64), 17: (68, 66, 58, 66),
    18: (69, 67, 60, 67), 19: (70, 68, 62, 68), 20: (71, 69, 63, 69), 21: (72, 70, 65, 70),
    22: (73, 71, 67, 71), 23: (74, 72, 69, 72), 24: (75, 73, 71, 73), 25: (76, 74, 73, 74),
    26: (77, 75, 75, 75), 27: (77, 76, 77, 76), 28: (78, 77, 78, 77), 29: (78, 78, 79, 78),
    30: (79, 78, 80, 78), 31: (79, 79, 80, 79), 32: (80, 79, 80, 79), 33: (80, 80, 80, 80),
    34: (80, 80, 80, 80), 35: (80, 80, 80, 80),
}  # fmt: skip


def _schrijf_teeltkaart(ws) -> None:
    ws.print_area = "A1:AG31"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws["A1"] = "Teeltkaart (synthetisch)"
    ws["A1"].font = Font(name="Arial", size=14, bold=True)
    ws["M1"], ws["N1"] = "kap", 1
    ws["M2"], ws["N2"] = "ronde", 1
    ws["R1"] = "Teeltkaart (synthetisch)"
    for kop, kolom in (("datum", "A"), ("LD-dag", "C"), ("streef", "E"), ("soort", "G"), ("cm", "I")):
        ws[f"{kolom}10"] = kop
    for r in range(11, 27):
        for kolom in ("A", "C", "E", "G", "I", "K"):
            ws[f"{kolom}{r}"].border = RAND
    ws.merge_cells("A1:F1")
    ws.merge_cells("R1:W1")
    for kolom, tekst in zip(
        ("AI", "AJ", "AK", "AL", "AM"), ("LD-dag", "winter", "voorjaar", "zomer", "najaar"), strict=True
    ):
        ws[f"{kolom}1"] = tekst
    for i, ld in enumerate(range(0, 40), start=2):
        ws[f"AI{i}"] = ld
        waarden = STREEFLENGTE_CONTROLE.get(ld)
        if waarden:
            for kolom, waarde in zip(("AJ", "AK", "AL", "AM"), waarden, strict=True):
                if waarde is not None:
                    ws[f"{kolom}{i}"] = waarde


# ---------------------------------------------------------------------------------------------------------
# Belichting winterronde
# ---------------------------------------------------------------------------------------------------------
WEKEN_BELICHTING = [*range(36, 53), *range(1, 11)]  # week 36 t/m 10


def maak_belichting(pad: Path, zaad: int = 1611, teeltschema_regels: int = 860) -> dict:
    """Schrijf het synthetische belichtingswerkboek; geeft de verwachte uren per duurcel terug."""
    rnd = random.Random(zaad)
    wb = Workbook()
    ws = wb.active
    ws.title = "belichting"
    titels = ["belichting", "teeltschema", "alar", "alar tutu", "data"]
    caches: dict[str, dict[str, object]] = {t: {} for t in titels}
    verwacht: dict[str, float | None] = {}
    soorten: dict[str, str] = {}

    ws["A2"] = "module"
    for j, week in enumerate(WEKEN_BELICHTING, start=2):
        ws.cell(row=2, column=j, value=week)
    modules = [(r, r - 2) for r in range(3, 43)] + [(r, r - 44 + 101) for r in range(44, 84)]
    vormen = ["timedelta", "timedelta", "timedelta", "datetime", "time", "getal"]
    for rij, module in modules:
        ws.cell(row=rij, column=1, value=module)
        for j in range(2, 2 + len(WEKEN_BELICHTING)):
            cel = ws.cell(row=rij, column=j)
            ref = cel.coordinate
            uren = round(rnd.uniform(20, 140) * 12) / 12  # hele 5 minuten
            vorm = vormen[(rij + j) % len(vormen)]
            if vorm == "time":
                uren = round(rnd.uniform(1, 23) * 12) / 12  # time-cel kan alleen < 24 uur
            dagen = uren / 24
            cel.value = dagen
            cel.number_format = {
                "timedelta": "[h]:mm",
                "datetime": 'd "days," hh:mm',
                "time": "hh:mm",
                "getal": "0.000",
            }[vorm]
            verwacht[ref] = uren
            soorten[ref] = vorm
    # Bijzondere gevallen voor de controles uit §7.2
    ws["B3"].value, ws["B3"].number_format = 82 / 24 + 35 / 1440, "[h]:mm"  # 3 days, 10:35 = 82,583 uur
    verwacht["B3"], soorten["B3"] = 82 + 35 / 60, "timedelta"
    ws["C3"].value = "3 days, 10:35"  # tekst: alleen met waarschuwing te lezen
    verwacht["C3"], soorten["C3"] = 82 + 35 / 60, "tekst"
    ws["D3"].value, ws["D3"].number_format = 7.5, "[h]:mm"  # 180 uur > 168: moet worden afgekeurd
    verwacht["D3"], soorten["D3"] = 180.0, "timedelta"
    ws["E3"].value = None
    verwacht["E3"], soorten["E3"] = None, "leeg"
    ws["A84"] = "totaal"
    for j in range(2, 2 + len(WEKEN_BELICHTING)):
        kolom = get_column_letter(j)
        ws[f"{kolom}84"] = f"=SUM({kolom}3:{kolom}83)"

    kop = ["kap", "plantdatum", "LD dagen", "KD dagen", "uren LD", "uren KD", "uren LED", "uren SON-T", "µmol"]
    kop += ["gewicht prognose", "gewicht realisatie", "LBE prognose", "LBE realisatie", "kosten"]
    for j, tekst in enumerate(kop, start=1):
        ws.cell(row=87, column=j, value=tekst)
    for i in range(40):
        rij = 88 + i
        ws.cell(row=rij, column=1, value=i + 1)
        if i >= 22:
            continue
        ws.cell(row=rij, column=2, value=date(2023, 9, 4) + timedelta(days=i * 2)).number_format = "dd-mm-yy"
        ws.cell(row=rij, column=3, value=rnd.randint(10, 20))
        ws.cell(row=rij, column=4, value=rnd.randint(50, 56))
        for j in (5, 6, 7, 8):
            ws.cell(row=rij, column=j, value=round(rnd.uniform(200, 900) / 24, 4)).number_format = "[h]:mm"
        ws.cell(row=rij, column=9, value=140)
        ws.cell(row=rij, column=10, value=rnd.randint(90, 110))
        ws.cell(row=rij, column=11, value=rnd.randint(85, 115))
        ws.cell(row=rij, column=12, value=round(rnd.uniform(4.8, 5.5), 2))
        ws.cell(row=rij, column=13, value=round(rnd.uniform(5.3, 6.4), 2))
        ws.cell(row=rij, column=14, value=round(rnd.uniform(1000, 3000), 2))

    ws["AK2"], ws["AL2"], ws["AM2"] = "kostenvergelijking (synthetisch)", 150, 200
    for i, label in enumerate(["uren per teelt", "MWh per kap", "€/MWh", "takken per m2", "kosten per tak"], 3):
        ws[f"AK{i}"] = label
        ws[f"AL{i}"] = round(rnd.uniform(1, 100), 2)
        ws[f"AM{i}"] = round(rnd.uniform(1, 100), 2)

    _schrijf_teeltschema(wb.create_sheet("teeltschema"), rnd, teeltschema_regels, caches["teeltschema"])
    for titel in ("alar", "alar tutu"):
        blad = wb.create_sheet(titel)
        blad["A1"] = "synthetisch: remstofdoseringen (pas na fase 3 gebruikt)"
    _schrijf_data(wb.create_sheet("data"), rnd)

    wb.save(pad)
    _zet_caches(pad, titels, caches)
    return {"verwacht_uren": verwacht, "soorten": soorten}


def _schrijf_teeltschema(ws, rnd: random.Random, aantal: int, cache: dict[str, object]) -> None:
    koppen = {1: "kap", 2: "planten", 4: "KD", 7: "licht uit", 10: "pluizen", 13: "netten", 16: "leeg"}
    koppen |= {19: "gewicht", 20: "pl-lu", 21: "lu-pluizen", 22: "lu-netten", 23: "teeltduur", 24: "teeltduur KD"}
    koppen |= {38: "lbe", 39: "uitval", 40: "opmerking", 41: "alar", 42: "reactietijd"}
    for kolom, tekst in koppen.items():
        ws.cell(row=1, column=kolom, value=tekst)
    for i in range(aantal):
        r = 2 + i
        kap = i % 40 + 1
        pw, pd = wb_optellen(1, 1, i * 3 % 364)
        ld = rnd.randint(7, 21)
        kd_duur = rnd.randint(52, 60)
        lu = wb_optellen(pw, pd, ld)
        leeg = wb_optellen(*lu, kd_duur)
        waarden = {1: kap, 2: pw, 3: pd, 4: lu[0], 5: lu[1], 7: lu[0], 8: lu[1], 16: leeg[0], 17: leeg[1]}
        waarden |= {10: wb_optellen(*lu, 21)[0], 11: wb_optellen(*lu, 21)[1]}
        waarden |= {13: wb_optellen(*lu, 28)[0], 14: wb_optellen(*lu, 28)[1]}
        waarden |= {19: rnd.choice([None, round(rnd.uniform(80, 115), 1)]), 23: ld + kd_duur, 24: kd_duur}
        waarden |= {38: round(rnd.uniform(4.5, 6.5), 2), 39: rnd.choice([0.05, 0.08]), 41: rnd.choice([None, 2])}
        for kolom, waarde in waarden.items():
            if waarde is not None:
                ws.cell(row=r, column=kolom, value=waarde)
        ws.cell(row=r, column=42, value=f"=X{r}-2-3")
        cache[f"AP{r}"] = kd_duur - 5


def _schrijf_data(ws, rnd: random.Random) -> None:
    ws["A1"], ws["A2"], ws["A3"] = "Tijd", "PCU 1", ""
    for j, module in enumerate(range(101, 141), start=2):
        ws.cell(row=1, column=j, value="Gem. licht%")
        ws.cell(row=2, column=j, value=f"PCU 1:MOD. LICHT {module}")
        ws.cell(row=3, column=j, value="%")
    start = datetime(2024, 10, 26)  # 27 oktober 2024: overgang zomer- naar wintertijd
    tijden = [start + timedelta(minutes=5 * k) for k in range(3 * 288)]
    tijden = [t for t in tijden if not (datetime(2024, 10, 28, 3) <= t < datetime(2024, 10, 28, 4))]  # gat
    tijden.insert(100, tijden[99])  # dubbele tijdstempel
    tijden[200:210] = list(reversed(tijden[200:210]))  # niet op tijdvolgorde
    for i, tijd in enumerate(tijden, start=4):
        ws.cell(row=i, column=1, value=tijd).number_format = "dd-mm-yyyy hh:mm"
        for j in range(2, 42):
            ws.cell(row=i, column=j, value=rnd.choice([0, 0, 50, 75, 100]))


def maak_alles(map_: Path) -> dict:
    map_.mkdir(parents=True, exist_ok=True)
    programma = maak_programma(map_ / PROGRAMMA_NAAM)
    belichting = maak_belichting(map_ / BELICHTING_NAAM)
    meta = {"jaren": programma["jaren"], "verwacht_uren": belichting["verwacht_uren"]}
    (map_ / "synthetisch.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    return {"programma": programma, "belichting": belichting}


if __name__ == "__main__":
    doel = Path(sys.argv[1] if len(sys.argv) > 1 else "proeven/uitvoer")
    maak_alles(doel)
    print(f"Synthetische werkboeken geschreven naar {doel}")
