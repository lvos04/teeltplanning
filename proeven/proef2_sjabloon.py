"""Proef 2 (BOUWPLAN §14, fase 0): een sjabloon vullen zonder opmaak of afdrukbereik te veranderen.

Aanpak: we schrijven **waarden** rechtstreeks in de XML van één werkblad in het XLSX-pakket. Alle andere
onderdelen (stijlen, thema, tekeningen, afbeeldingen, opmerkingen, afdrukinstellingen, andere bladen) blijven
byte-voor-byte gelijk. Alleen drie dingen veranderen buiten het blad zelf, met reden:

* `xl/calcChain.xml` wordt verwijderd (met de verwijzingen in `[Content_Types].xml` en `workbook.xml.rels`):
  de rekenketen verwijst naar cellen waar nu geen formule meer staat; Excel bouwt hem zelf opnieuw op.
* `xl/workbook.xml` krijgt `fullCalcOnLoad="1"`, zodat formules búíten de gevulde cellen bij openen opnieuw
  rekenen in plaats van een verouderde uitkomst te tonen.

Waarom niet openpyxl laden en opslaan? openpyxl schrijft het hele pakket opnieuw: afbeeldingen, vormen,
knoppen en sommige voorwaardelijke opmaak gaan verloren en stijlnummers worden hernummerd. De proef toont dat
verschil met `--vergelijk-openpyxl`.

Gebruik:

    python -m proeven.proef2_sjabloon <werkboek.xlsx> <uitvoermap> [--blad "Ronde 1"] [--pdf]
"""

from __future__ import annotations

import argparse
import html
import posixpath
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path

from defusedxml.ElementTree import fromstring
from openpyxl.utils import column_index_from_string, coordinate_to_tuple

NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def blad_onderdeel(pakket: zipfile.ZipFile, blad: str) -> str:
    """Het pad van het werkblad-XML voor een bladnaam, via workbook.xml en de relaties."""
    werkboek = fromstring(pakket.read("xl/workbook.xml"))
    relaties = fromstring(pakket.read("xl/_rels/workbook.xml.rels"))
    for sheet in werkboek.find(NS_MAIN + "sheets"):
        if sheet.get("name") == blad:
            rid = sheet.get(NS_REL + "id")
            for rel in relaties:
                if rel.get("Id") == rid:
                    doel = rel.get("Target", "")
                    pad = doel.lstrip("/") if doel.startswith("/") else posixpath.normpath("xl/" + doel)
                    if not pad.startswith("xl/worksheets/"):
                        raise ValueError("Onverwacht werkbladpad")
                    return pad
    raise ValueError(f"Werkblad {blad!r} niet gevonden")


def _cel_xml(ref: str, stijl: str | None, waarde: object) -> str:
    s = f' s="{stijl}"' if stijl else ""
    if waarde is None:
        return f'<c r="{ref}"{s}/>'
    if isinstance(waarde, bool):
        return f'<c r="{ref}"{s} t="b"><v>{int(waarde)}</v></c>'
    if isinstance(waarde, (int, float)):
        return f'<c r="{ref}"{s}><v>{waarde!r}</v></c>'
    if isinstance(waarde, (date, datetime)):
        raise ValueError("Datums eerst omzetten naar week-dagtekst of Excel-getal")
    tekst = html.escape(str(waarde), quote=False)
    spatie = ' xml:space="preserve"' if tekst != tekst.strip() else ""
    return f'<c r="{ref}"{s} t="inlineStr"><is><t{spatie}>{tekst}</t></is></c>'


CEL = re.compile(r'<c r="([A-Z]{1,3})(\d+)"([^>]*?)(?:/>|>(.*?)</c>)', re.S)
RIJ = re.compile(r'<row r="(\d+)"([^>]*?)(?:/>|>(.*?)</row>)', re.S)


def _stijl(attrs: str) -> str | None:
    m = re.search(r'\bs="(\d+)"', attrs)
    return m.group(1) if m else None


def vul_blad_xml(xml: str, waarden: dict[str, object]) -> str:
    """Zet waarden in bestaande cellen (stijl blijft), of voeg cellen/rijen in op de juiste plek."""
    per_rij: dict[int, dict[int, tuple[str, object]]] = {}
    for ref, waarde in waarden.items():
        rij, kolom = coordinate_to_tuple(ref)
        per_rij.setdefault(rij, {})[kolom] = (ref, waarde)
    start = xml.index("<sheetData")
    if xml[start:].startswith("<sheetData/>"):
        xml = xml[:start] + "<sheetData></sheetData>" + xml[start + len("<sheetData/>") :]
    begin = xml.index(">", start) + 1
    eind = xml.index("</sheetData>")
    data = xml[begin:eind]

    def vervang_rij(m: re.Match) -> str:
        nummer = int(m.group(1))
        if nummer not in per_rij:
            return m.group(0)
        nieuw = per_rij.pop(nummer)
        cellen = []
        for c in CEL.finditer(m.group(3) or ""):
            kolom = column_index_from_string(c.group(1))
            if kolom in nieuw:
                ref, waarde = nieuw.pop(kolom)
                cellen.append((kolom, _cel_xml(ref, _stijl(c.group(3)), waarde)))
            else:
                cellen.append((kolom, c.group(0)))
        cellen += [(k, _cel_xml(ref, None, w)) for k, (ref, w) in nieuw.items()]
        cellen.sort(key=lambda x: x[0])
        attrs = re.sub(r'\s+spans="[^"]*"', "", m.group(2))
        return f'<row r="{nummer}"{attrs}>' + "".join(c for _, c in cellen) + "</row>"

    data = RIJ.sub(vervang_rij, data)
    if per_rij:  # rijen die nog niet bestonden, op volgorde invoegen
        rijen = [(int(m.group(1)), m.group(0)) for m in RIJ.finditer(data)]
        for nummer, cellen in per_rij.items():
            inhoud = "".join(_cel_xml(ref, None, w) for _, (ref, w) in sorted(cellen.items()))
            rijen.append((nummer, f'<row r="{nummer}">{inhoud}</row>'))
        rijen.sort(key=lambda x: x[0])
        data = "".join(r for _, r in rijen)
    return xml[:begin] + data + xml[eind:]


def vul_sjabloon(bron: Path, doel: Path, blad: str, waarden: dict[str, object]) -> dict:
    """Kopie van `bron` naar `doel` met `waarden` in `blad`. Geeft een overzicht van gewijzigde onderdelen."""
    gewijzigd: list[str] = []
    with zipfile.ZipFile(bron) as pakket:
        onderdeel = blad_onderdeel(pakket, blad)
        heeft_calcchain = "xl/calcChain.xml" in pakket.namelist()
        with zipfile.ZipFile(doel, "w", zipfile.ZIP_DEFLATED) as uit:
            for item in pakket.infolist():
                data = pakket.read(item.filename)
                naam = item.filename
                if naam == "xl/calcChain.xml":
                    gewijzigd.append(naam + " (verwijderd)")
                    continue
                if naam == onderdeel:
                    data = vul_blad_xml(data.decode("utf-8"), waarden).encode("utf-8")
                    gewijzigd.append(naam)
                elif naam == "xl/workbook.xml":
                    tekst = data.decode("utf-8")
                    if "<calcPr" in tekst:
                        tekst = re.sub(r"<calcPr\b([^>]*?)\s*fullCalcOnLoad=\"[^\"]*\"", r"<calcPr\1", tekst)
                        tekst = tekst.replace("<calcPr", '<calcPr fullCalcOnLoad="1"', 1)
                    else:
                        tekst = tekst.replace("</workbook>", '<calcPr fullCalcOnLoad="1"/></workbook>')
                    data = tekst.encode("utf-8")
                    gewijzigd.append(naam)
                elif heeft_calcchain and naam == "[Content_Types].xml":
                    data = re.sub(rb'<Override PartName="/xl/calcChain.xml"[^>]*/>', b"", data)
                    gewijzigd.append(naam)
                elif heeft_calcchain and naam == "xl/_rels/workbook.xml.rels":
                    data = re.sub(rb'<Relationship [^>]*Target="[^"]*calcChain.xml"[^>]*/>', b"", data)
                    gewijzigd.append(naam)
                uit.writestr(item, data)
    return {"blad": blad, "onderdeel": onderdeel, "gewijzigd": gewijzigd}


def vergelijk_pakketten(a: Path, b: Path) -> dict:
    with zipfile.ZipFile(a) as za, zipfile.ZipFile(b) as zb:
        na, nb = set(za.namelist()), set(zb.namelist())
        verschillend = sorted(n for n in na & nb if za.read(n) != zb.read(n))
        return {"verdwenen": sorted(na - nb), "nieuw": sorted(nb - na), "verschillend": verschillend}


def vergelijk_opmaak(a: Path, b: Path, blad: str, bereik: str) -> list[str]:
    """Vergelijk via openpyxl: kolombreedtes, samenvoegingen, afdrukinstellingen en celstijlen in `bereik`."""
    from openpyxl import load_workbook

    verschillen = []
    wa, wb = load_workbook(a), load_workbook(b)
    sa, sb = wa[blad], wb[blad]
    if {k: v.width for k, v in sa.column_dimensions.items()} != {k: v.width for k, v in sb.column_dimensions.items()}:
        verschillen.append("kolombreedtes")
    if sorted(map(str, sa.merged_cells.ranges)) != sorted(map(str, sb.merged_cells.ranges)):
        verschillen.append("samengevoegde cellen")
    if sa.print_area != sb.print_area:
        verschillen.append("afdrukbereik")
    for veld in ("orientation", "paperSize", "fitToWidth", "fitToHeight", "scale"):
        if getattr(sa.page_setup, veld) != getattr(sb.page_setup, veld):
            verschillen.append(f"pagina-instelling {veld}")
    if sa.page_margins.__dict__ != sb.page_margins.__dict__:
        verschillen.append("marges")
    if sa.freeze_panes != sb.freeze_panes:
        verschillen.append("vastgezette deelvensters")
    for rij_a, rij_b in zip(sa[bereik], sb[bereik], strict=True):
        for ca, cb in zip(rij_a, rij_b, strict=True):
            for kenmerk in ("font", "fill", "border", "alignment", "number_format", "protection"):
                if repr(getattr(ca, kenmerk)) != repr(getattr(cb, kenmerk)):
                    verschillen.append(f"{ca.coordinate}: {kenmerk}")
    return verschillen


def naar_pdf(xlsx: Path, uitvoermap: Path, tijdslimiet: int = 120) -> Path:
    """LibreOffice zonder scherm, eigen tijdelijk profiel, met tijdslimiet (voorloper van route B, §9.3)."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError("LibreOffice (soffice) niet gevonden")
    with tempfile.TemporaryDirectory(prefix="lo-profiel-") as profiel:
        subprocess.run(
            [soffice, f"-env:UserInstallation=file://{profiel}", "--headless", "--norestore",
             "--convert-to", "pdf", "--outdir", str(uitvoermap), str(xlsx)],  # fmt: skip
            check=True, capture_output=True, timeout=tijdslimiet,
        )  # fmt: skip
    return uitvoermap / (xlsx.stem + ".pdf")


def pdf_paginas(pdf: Path) -> int:
    return len(re.findall(rb"/Type\s*/Page(?!s)", pdf.read_bytes()))


def _workbook_met_een_blad(tekst: str, blad: str) -> str:
    tags = re.findall(r"<sheet [^>]*/>", tekst)
    naam = f'name="{html.escape(blad)}"'
    index = next(i for i, t in enumerate(tags) if naam in t)
    tekst = re.sub(r"<sheet [^>]*/>", lambda m: m.group(0) if naam in m.group(0) else "", tekst)

    def namen(m: re.Match) -> str:
        lokaal = re.search(r'localSheetId="(\d+)"', m.group(0))
        if lokaal is None or int(lokaal.group(1)) != index:
            return ""  # namen van andere bladen, of werkboekbreed (kan naar verwijderde bladen wijzen)
        return m.group(0).replace(lokaal.group(0), 'localSheetId="0"')

    tekst = re.sub(r"<definedName [^>]*>.*?</definedName>", namen, tekst, flags=re.S)
    tekst = re.sub(r'\s+(activeTab|firstSheet)="\d+"', "", tekst)
    return re.sub(r'(<sheet [^>]*?)\s+state="[^"]*"', r"\1", tekst)


def alleen_afdrukblad(bron: Path, doel: Path, blad: str) -> None:
    """Kopie met alleen het af te drukken blad, zodat de pdf alleen dat blad bevat.

    LibreOffice drukt bij pdf-export ook verborgen bladen af; daarom worden de andere bladen uit
    `workbook.xml` gehaald (hun onderdelen blijven ongebruikt in het pakket). Alleen voor de afdrukkopie.
    """
    with zipfile.ZipFile(bron) as pakket, zipfile.ZipFile(doel, "w", zipfile.ZIP_DEFLATED) as uit:
        for item in pakket.infolist():
            data = pakket.read(item.filename)
            if item.filename == "xl/workbook.xml":
                data = _workbook_met_een_blad(data.decode("utf-8"), blad).encode("utf-8")
            uit.writestr(item, data)


def proefwaarden(werkboek: Path, blad: str, bereik: str = "B4:R51") -> dict[str, object]:
    """Waarden voor de proef: de gecachte uitkomst van elke cel, en `PROEF` + datum in B4."""
    from openpyxl import load_workbook

    wb = load_workbook(werkboek, data_only=True, read_only=False)
    ws = wb[blad]
    waarden: dict[str, object] = {}
    for rij in ws[bereik]:
        for cel in rij:
            if type(cel).__name__ == "MergedCell":
                continue
            waarde = cel.value
            if isinstance(waarde, (date, datetime)):
                iso = waarde.isocalendar()
                waarde = f"{iso.week}-{iso.weekday}"
            if waarde is not None:
                waarden[cel.coordinate] = waarde
    waarden["B4"] = "PROEF gevuld sjabloon"
    return waarden


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Proef 2: sjabloon vullen")
    parser.add_argument("werkboek", type=Path)
    parser.add_argument("uitvoermap", type=Path)
    parser.add_argument("--blad", default="Ronde 1")
    parser.add_argument("--bereik", default="B4:R51")
    parser.add_argument("--pdf", action="store_true", help="ook naar pdf omzetten met LibreOffice")
    parser.add_argument("--vergelijk-openpyxl", action="store_true")
    args = parser.parse_args(argv)
    args.uitvoermap.mkdir(parents=True, exist_ok=True)
    doel = args.uitvoermap / "gevuld.xlsx"
    overzicht = vul_sjabloon(args.werkboek, doel, args.blad, proefwaarden(args.werkboek, args.blad, args.bereik))
    print(f"Gevuld: {doel}")
    print(f"Gewijzigde onderdelen: {overzicht['gewijzigd']}")
    print(f"Pakketvergelijking: {vergelijk_pakketten(args.werkboek, doel)}")
    verschillen = vergelijk_opmaak(args.werkboek, doel, args.blad, args.bereik)
    print(f"Opmaakverschillen in {args.blad}!{args.bereik}: {verschillen or 'geen'}")
    if args.vergelijk_openpyxl:
        from openpyxl import load_workbook

        kopie = args.uitvoermap / "openpyxl-opgeslagen.xlsx"
        load_workbook(args.werkboek).save(kopie)
        print(f"Ter vergelijking, openpyxl laden+opslaan: {vergelijk_pakketten(args.werkboek, kopie)}")
    if args.pdf:
        afdruk = args.uitvoermap / f"afdruk {args.blad}.xlsx"
        alleen_afdrukblad(doel, afdruk, args.blad)
        pdf = naar_pdf(afdruk, args.uitvoermap)
        print(f"Pdf: {pdf} ({pdf_paginas(pdf)} pagina('s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
