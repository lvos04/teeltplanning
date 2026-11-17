"""Afdrukken (BOUWPLAN §9.2, §9.3, §14 fase 2): sjabloon vullen, pdf via LibreOffice, controle tegen het werkboek."""

import io
import os
import shutil
import subprocess
import zipfile
from datetime import date
from pathlib import Path

import pytest
from openpyxl import load_workbook

from teeltplanning.xlsxpakket import Datum, NieuwBlad, Pakket, bouw_kopie, formulecellen, zonder_formules
from tests.conftest import importeer, maak_gebruiker

LO = pytest.mark.skipif(not shutil.which("soffice"), reason="LibreOffice (soffice) niet beschikbaar")
POPPLER = pytest.mark.skipif(not shutil.which("pdftoppm"), reason="pdftoppm/pdftotext (poppler-utils) niet beschikbaar")


def pdftekst(pdf: bytes, tmp_path: Path) -> str:
    bron = tmp_path / "t.pdf"
    bron.write_bytes(pdf)
    return subprocess.run(["pdftotext", "-layout", str(bron), "-"], capture_output=True, text=True, check=True).stdout


def paginas(pdf: bytes) -> int:
    from teeltplanning.afdruk.pdf import paginas as tel

    return tel(pdf)


def opzet(client, werkboeken, offsets: bool = False):
    importeer(client, werkboeken["programma"], werkboeken["meta"]["programma"]["jaren"])
    with open(werkboeken["programma"], "rb") as f:
        antwoord = client.post("/beheer/sjabloon/upload", files={"bestand": ("Teeltprogramma.xlsx", f, "x")},
                               follow_redirects=False)  # fmt: skip
    assert antwoord.status_code == 303 and "opgeslagen" in antwoord.headers["location"]
    if offsets:  # synthetische offsets van de testkaart (in de app start onbekend)
        for sleutel, waarde in (("teeltkaart.offset_slak", "7"), ("teeltkaart.offset_remstof", "14")):
            assert client.post(f"/beheer/instellingen/{sleutel}", data={"waarde": waarde}).status_code == 200


# --- Pakket: afdrukkopie ---------------------------------------------------------------------------------------
def test_afdrukkopie_bevat_alleen_de_gevraagde_bladen_zonder_formules(synthetische_werkboeken):
    pakket = Pakket.lees(synthetische_werkboeken["programma"])
    bladen = [NieuwBlad("teeltkaart", f"Kap {i}", {"N1": i, "C4": Datum(date(2026, 3, 2), "10-1")}, "A1:AG31", "liggend")
              for i in range(1, 21)]  # fmt: skip
    xlsx, meldingen = bouw_kopie(pakket, bladen, behoud_overige=False, papier="A4", passend=True,
                                 formules_bevriezen=True, volledig_herberekenen=False)  # fmt: skip
    wb = load_workbook(io.BytesIO(xlsx))
    assert wb.sheetnames == [f"Kap {i}" for i in range(1, 21)]
    for ws in wb.worksheets:
        assert ws.print_area == f"'{ws.title}'!$A$1:$AG$31"
        assert ws.page_setup.orientation == "landscape" and int(ws.page_setup.paperSize) == 9
        assert (
            ws.page_setup.fitToWidth == 1
            and ws.page_setup.fitToHeight == 1
            and ws.sheet_properties.pageSetUpPr.fitToPage
        )
        assert not any(isinstance(c.value, str) and c.value.startswith("=") for r in ws.iter_rows() for c in r)
    assert wb["Kap 3"]["N1"].value == 3 and wb["Kap 3"]["C4"].value == "10-1"  # geen datumnotatie → tekst
    # opmaak van het sjabloon blijft: samengevoegde cellen en kolombreedte
    assert "A1:F1" in {str(r) for r in wb["Kap 1"].merged_cells.ranges}
    assert (
        wb["Kap 1"].column_dimensions["A"].width
        == load_workbook(synthetische_werkboeken["programma"])["teeltkaart"].column_dimensions["A"].width
    )
    with zipfile.ZipFile(io.BytesIO(xlsx)) as z:
        namen = z.namelist()
        assert "xl/calcChain.xml" not in namen
        assert not any("Ronde" in z.read("xl/workbook.xml").decode() for _ in [0])
    assert meldingen == []


def test_formules_bevriezen_en_leegmaken():
    xml = ('<worksheet><sheetData><row r="1"><c r="A1" s="3"><f>B1*2</f><v>4</v></c>'
           '<c r="B1" t="str"><f>"x"&amp;"y"</f><v>xy</v></c><c r="C1"><f>1+1</f><v>2</v></c></row></sheetData></worksheet>')  # fmt: skip
    assert formulecellen(xml) == ["A1", "B1", "C1"]
    uit = zonder_formules(xml, {"C1"})
    assert "<f>" not in uit and '<c r="A1" s="3"><v>4</v></c>' in uit
    assert '<c r="B1" t="inlineStr"><is><t xml:space="preserve">xy</t></is></c>' in uit and '<c r="C1"/>' in uit


# --- Web: afdrukken ----------------------------------------------------------------------------------------------
@LO
def test_rondeblad_en_teeltkaart_met_een_klik_als_pdf(beheerder, synthetische_werkboeken, tmp_path):
    opzet(beheerder, synthetische_werkboeken)
    # rondeblad: 1 pagina
    antwoord = beheerder.get("/afdruk/rondeblad.pdf?ronde=3")
    assert antwoord.status_code == 200 and antwoord.headers["content-type"] == "application/pdf"
    assert antwoord.headers["content-disposition"].startswith("inline")
    assert paginas(antwoord.content) == 1
    # teeltkaart met alleen kap en ronde: 1 pagina (kap 7 en 8)
    kaart = beheerder.get("/afdruk/teeltkaart.pdf?ronde=3&kap=7")
    assert kaart.status_code == 200 and kaart.headers["content-type"] == "application/pdf"
    assert paginas(kaart.content) == 1
    # alle 40 kaarten van een ronde: 20 bladen = 20 pagina's
    alle = beheerder.get("/afdruk/teeltkaart.pdf?ronde=3")
    assert alle.headers["content-type"] == "application/pdf" and paginas(alle.content) == 20
    if shutil.which("pdftotext"):
        tekst = pdftekst(antwoord.content, tmp_path)
        assert "Ronde 3" in tekst and "Teeltprogramma (synthetisch testwerkboek)" in tekst
        tekst = pdftekst(kaart.content, tmp_path)
        assert "Teeltkaart (synthetisch)" in tekst and "begin oogst" in tekst
    # Excel van dezelfde kaart: waarden, geen formules; kap 7 links, kap 8 rechts
    excel = beheerder.get("/afdruk/teeltkaart.xlsx?ronde=3&kap=7")
    assert excel.headers["content-type"].startswith("application/vnd.openxmlformats")
    wb = load_workbook(io.BytesIO(excel.content))
    ws = wb[wb.sheetnames[0]]
    assert wb.sheetnames == ["Kap 7-8"] and ws["N1"].value == 7 and ws["AE1"].value == 8 and ws["N2"].value == 3
    assert isinstance(ws["A11"].value, str) and ws["C11"].value in (0, 1, 2)
    # het voorbeeld op het scherm
    pagina = beheerder.get("/afdruk?ronde=3&kap=7")
    assert pagina.status_code == 200 and "Kaart kap 7" in pagina.text and "streeflengte" in pagina.text


@LO
def test_lezer_mag_afdrukken(beheerder, synthetische_werkboeken):
    opzet(beheerder, synthetische_werkboeken)
    maak_gebruiker(beheerder, "lezer1", "lezer")
    beheerder.post("/afmelden")
    beheerder.post("/aanmelden", data={"gebruikersnaam": "lezer1", "wachtwoord": "geheim-wachtwoord-123"})
    assert beheerder.get("/afdruk/teeltkaart.xlsx?ronde=1&kap=1").status_code == 200
    assert beheerder.get("/beheer/sjabloon").status_code == 403


def test_mislukte_conversie_geeft_melding_en_excel_blijft(beheerder, synthetische_werkboeken, monkeypatch, tmp_path):
    opzet(beheerder, synthetische_werkboeken)
    nep = tmp_path / "soffice"
    nep.write_text("#!/bin/sh\necho 'kapot' >&2\nexit 3\n")
    nep.chmod(0o755)
    from teeltplanning.afdruk import pdf

    monkeypatch.setattr(pdf, "soffice", lambda: str(nep))
    antwoord = beheerder.get("/afdruk/teeltkaart.pdf?ronde=2&kap=5", headers={"accept": "text/html"})
    assert antwoord.status_code == 200 and "text/html" in antwoord.headers["content-type"]
    assert "Pdf niet gelukt" in antwoord.text and "kapot" in antwoord.text
    assert "/afdruk/teeltkaart.xlsx?ronde=2&amp;kap=5" in antwoord.text
    excel = beheerder.get("/afdruk/teeltkaart.xlsx?ronde=2&kap=5")
    assert excel.status_code == 200 and load_workbook(io.BytesIO(excel.content)).sheetnames == ["Kap 5-6"]
    # tijdslimiet: een hangende conversie wordt afgebroken
    nep.write_text("#!/bin/sh\nsleep 30\n")
    assert beheerder.post("/beheer/instellingen/afdrukken.tijdslimiet_s", data={"waarde": "5"}).status_code == 200
    antwoord = beheerder.get("/afdruk/rondeblad.pdf?ronde=2")
    assert "langer dan 5 s" in antwoord.text and "rondeblad.xlsx?ronde=2" in antwoord.text
    assert not any((Path(os.environ["DATA_DIR"]) / "tmp").glob("afdruk-*"))  # tijdelijke bestanden opgeruimd


@LO
def test_verkeerd_aantal_paginas_geeft_melding(beheerder, synthetische_werkboeken, monkeypatch):
    opzet(beheerder, synthetische_werkboeken)
    # Zonder 'passend op één pagina' en met een te groot afdrukbereik valt het rondeblad over meerdere pagina's.
    beheerder.post("/beheer/instellingen/afdrukken.passend_op_een_pagina", data={"waarde": "uit"})
    beheerder.post("/beheer/instellingen/afdrukken.rondeblad_bereik", data={"waarde": "A1:AF200"})
    antwoord = beheerder.get("/afdruk/rondeblad.pdf?ronde=1")
    assert "text/html" in antwoord.headers["content-type"] and "in plaats van 1" in antwoord.text
    assert beheerder.get("/afdruk/rondeblad.xlsx?ronde=1").status_code == 200


def test_zonder_sjabloon_duidelijke_melding(beheerder, synthetische_werkboeken):
    importeer(beheerder, synthetische_werkboeken["programma"], synthetische_werkboeken["meta"]["programma"]["jaren"])
    antwoord = beheerder.get("/afdruk/rondeblad.xlsx?ronde=1")
    assert antwoord.status_code == 422 and "Beheer → Sjabloon" in antwoord.json()["detail"]
    assert "nog geen sjabloon" in beheerder.get("/afdruk").text


def test_sjabloonbeheer_lettertypen_en_controle(beheerder, synthetische_werkboeken):
    opzet(beheerder, synthetische_werkboeken)
    pagina = beheerder.get("/beheer/sjabloon").text
    assert "Arial" in pagina and "Liberation Sans" in pagina
    fout = beheerder.post("/beheer/sjabloon/lettertype", files={"bestand": ("x.ttf", b"geen lettertype", "x")},
                          follow_redirects=False)  # fmt: skip
    assert "geen%20TrueType" in fout.headers["location"]
    ok = beheerder.post("/beheer/sjabloon/lettertype", files={"bestand": ("Eigen.ttf", b"\x00\x01\x00\x00" + b"0" * 100, "x")},
                        follow_redirects=False)  # fmt: skip
    assert "opgeslagen" in ok.headers["location"]
    assert (Path(os.environ["DATA_DIR"]) / "lettertypen" / "Eigen.ttf").is_file()
    slecht = beheerder.post(
        "/beheer/sjabloon/upload", files={"bestand": ("x.xlsx", b"geen zip", "x")}, follow_redirects=False
    )
    assert "Geen%20bruikbaar%20sjabloon" in slecht.headers["location"]
    # een formule die de app niet vult wordt gemeld en standaard leeggemaakt
    beheerder.post("/beheer/instellingen/afdrukken.teeltkaart_cellen",
                   data={"waarde": '{"kap": "N1", "ronde": "N2", "plantdatum_week": "C4"}'})  # fmt: skip
    beheerder.post("/beheer/sjabloon/controle")
    assert "D4" in beheerder.get("/beheer/sjabloon").text
    ws = load_workbook(io.BytesIO(beheerder.get("/afdruk/teeltkaart.xlsx?ronde=1&kap=1").content)).active
    assert ws["D4"].value is None and ws["C4"].value is not None


# --- Controle tegen het werkboek -------------------------------------------------------------------------------
def _steekproef(beheerder, aantal: int) -> list[tuple[int, int]]:
    """Kappen/rondes zonder jaarwisseling in de kaart (het werkboek rekent met 52 weken per jaar)."""
    import random

    from teeltplanning import planning
    from teeltplanning.db.basis import SessionLocal

    with SessionLocal() as db:
        scenario = planning.vastgesteld_of_eerste(db)
        _, plan = planning.bereken(db, scenario.id)
    kandidaten = sorted(
        (u.kap, u.ronde) for u in plan.teelten.values()
        if u.licht_uit and u.oogst and u.effectief["plantdatum"]
        and u.effectief["plantdatum"].isocalendar().year == u.oogst.isocalendar().year
        and u.licht_uit.isocalendar().week <= 45 and u.kap < 40
    )  # fmt: skip
    return random.Random(20261116).sample(kandidaten, aantal)


@LO
def test_tien_steekproefkappen_gelijk_aan_werkboek(beheerder, synthetische_werkboeken):
    """Alle velden en de 16 momenten met streeflengte zijn gelijk aan wat het werkboek (door LibreOffice
    herberekend met kap en ronde in N1/N2) toont."""
    from teeltplanning import planning
    from teeltplanning.afdruk.controle import controleer_kaarten
    from teeltplanning.db.basis import SessionLocal

    opzet(beheerder, synthetische_werkboeken, offsets=True)
    paren = _steekproef(beheerder, 10)
    with SessionLocal() as db:
        scenario = planning.vastgesteld_of_eerste(db)
        uitkomst = controleer_kaarten(db, scenario.id, Pakket.lees(synthetische_werkboeken["programma"]), paren)
    assert uitkomst["cellen_per_kaart"] == 21 + 3 * 16
    assert uitkomst["verschillen"] == [], uitkomst["verschillen"][:10]
    gevuld = sum(
        1 for wb in uitkomst["herberekend"] for c, v in wb.items() if not c.startswith("_") and v not in (None, "")
    )
    assert gevuld > 10 * 40  # de vergelijking ging over echte waarden, niet over lege cellen


@LO
@POPPLER
def test_afdruk_niet_te_onderscheiden_van_origineel(beheerder, tmp_path):
    """Teeltkaart en rondeblad uit de app over het origineel uit het werkboek gelegd: geen pixel verschil."""
    from teeltplanning import instellingen, planning
    from teeltplanning.afdruk import pdf
    from teeltplanning.afdruk.controle import herbereken_kaarten, origineel_als_pdf
    from teeltplanning.beeld import beeldverschil, pdf_naar_beeld
    from teeltplanning.db.basis import SessionLocal

    # Het synthetische werkboek rekent met 52 weken per jaar. Begint ronde 1 in 2021, dan vallen de eerste
    # rondes in jaren met 52 weken en verstoort de 52-wekenfout van het werkboek de vergelijking niet.
    from tests.synthetisch import maak_programma

    pad = tmp_path / "Teeltprogramma 2021.xlsx"
    synthetische_werkboeken = {"programma": pad, "meta": {"programma": maak_programma(pad, eerste_iso_jaar=2021)}}
    opzet(beheerder, synthetische_werkboeken, offsets=True)
    kap, ronde = _steekproef(beheerder, 1)[0]
    with SessionLocal() as db:
        snap = instellingen.laad(db)
        herberekend = herbereken_kaarten(Pakket.lees(synthetische_werkboeken["programma"]), [(kap, ronde)], snap)[0]
        origineel = origineel_als_pdf(herberekend["_xlsx"], herberekend["_blad"], snap, f"Kap {kap}-{kap + 1}")
        planning.vastgesteld_of_eerste(db)
    app = beheerder.get(f"/afdruk/teeltkaart.pdf?ronde={ronde}&kap={kap}").content
    verschil = beeldverschil(pdf_naar_beeld(origineel), pdf_naar_beeld(app))
    print(f"\nteeltkaart kap {kap} ronde {ronde}: {verschil:.6%} pixels verschillend")
    assert verschil == 0
    # Rondeblad: het origineel is het rondeblad uit het werkboek met de gecachte uitkomsten van Excel.
    ronde_nr = 4  # ronde 5 heeft een regel zonder plantdatum
    with SessionLocal() as db:
        snap = instellingen.laad(db)
        xlsx, _ = bouw_kopie(Pakket.lees(synthetische_werkboeken["programma"]),
                             [NieuwBlad(f"Ronde {ronde_nr}", f"Ronde {ronde_nr}", {}, snap["afdrukken.rondeblad_bereik"], "staand")],
                             behoud_overige=False, papier="A4", passend=True, formules_bevriezen=True,
                             volledig_herberekenen=False)  # fmt: skip
        origineel = pdf.converteer(xlsx, "pdf", snap["afdrukken.tijdslimiet_s"])
    app = beheerder.get(f"/afdruk/rondeblad.pdf?ronde={ronde_nr}").content
    verschil = beeldverschil(pdf_naar_beeld(origineel), pdf_naar_beeld(app))
    print(f"rondeblad ronde {ronde_nr}: {verschil:.6%} pixels verschillend")
    assert verschil == 0


ECHT = os.environ.get("TEELT_ECHT_PROGRAMMA")
ECHT_JAREN = os.environ.get("TEELT_ECHT_JAREN")


@LO
@pytest.mark.skipif(not ECHT or not Path(ECHT).is_file() or not ECHT_JAREN,
                    reason="TEELT_ECHT_PROGRAMMA/TEELT_ECHT_JAREN niet gezet of bestand ontbreekt")  # fmt: skip
def test_echt_werkboek_tien_kaarten(beheerder):
    """Met het echte werkboek: importeren, als sjabloon gebruiken en tien steekproefkaarten vergelijken."""
    from teeltplanning import planning
    from teeltplanning.afdruk.controle import controleer_kaarten
    from teeltplanning.db.basis import SessionLocal

    jaren = {int(n): int(j) for n, j in (d.split(":") for d in ECHT_JAREN.split(","))}
    werkboeken = {"programma": Path(ECHT), "meta": {"programma": {"jaren": jaren}}}
    opzet(beheerder, werkboeken)
    paren = _steekproef(beheerder, 10)
    with SessionLocal() as db:
        uitkomst = controleer_kaarten(db, planning.vastgesteld_of_eerste(db).id, Pakket.lees(Path(ECHT)), paren)
    for v in uitkomst["verschillen"]:
        print(f"kap {v.kap} ronde {v.ronde} {v.cel}: werkboek {v.werkboek!r} app {v.app!r}")
    assert uitkomst["verschillen"] == []
