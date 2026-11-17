"""Werken met het XLSX-pakket zelf: waarden in werkblad-XML schrijven, afdrukkopieën maken, bladen kopiëren.

Uitgangspunt (proef 2, docs/STATUS.md): **niet** via openpyxl laden en opslaan, want dat herschrijft stijlen en
alle werkbladen en laat afbeeldingen en vormen vallen. We bewerken alleen de XML-onderdelen die moeten
veranderen; al het andere blijft byte-voor-byte gelijk.

Deze module bevat geen bedrijfsregels (die staan in `teeltplanning/afdruk/`); alleen de techniek van het
Office Open XML-pakket.
"""

from __future__ import annotations

import html
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from defusedxml.ElementTree import fromstring
from openpyxl.utils import column_index_from_string, coordinate_to_tuple, get_column_letter

NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
TYPE_WERKBLAD = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
CT_WERKBLAD = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
# Relaties van het werkboek die bij een afdrukkopie verdwijnen (ze verwijzen naar weggelaten bladen of buiten).
WEG_BIJ_AFDRUK = ("worksheet", "chartsheet", "dialogsheet", "macrosheet", "externalLink", "calcChain",
                  "pivotCacheDefinition", "connections", "queryTable")  # fmt: skip
EXCEL_NUL = date(1899, 12, 30)


class PakketFout(ValueError):
    pass


# --- Celwaarden ----------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Datum:
    """Een datum die als Excel-datumgetal wordt geschreven (als de doelcel een datumnotatie heeft)."""

    waarde: date
    tekst: str  # wat er staat als de doelcel géén datumnotatie heeft (bijv. "44-6")


def excel_getal(d: date) -> int:
    return (d - EXCEL_NUL).days


def _cel_xml(ref: str, stijl: str | None, waarde: object, datumstijlen: frozenset[str] = frozenset()) -> str:
    s = f' s="{stijl}"' if stijl else ""
    if isinstance(waarde, Datum):
        waarde = excel_getal(waarde.waarde) if stijl in datumstijlen else waarde.tekst
    if waarde is None or waarde == "":
        return f'<c r="{ref}"{s}/>'
    if isinstance(waarde, bool):
        return f'<c r="{ref}"{s} t="b"><v>{int(waarde)}</v></c>'
    if isinstance(waarde, (int, float)):
        if isinstance(waarde, float) and waarde.is_integer():
            waarde = int(waarde)
        return f'<c r="{ref}"{s}><v>{waarde!r}</v></c>'
    if isinstance(waarde, (date, datetime)):
        raise PakketFout("Datums eerst omzetten naar Datum(...) of tekst")
    tekst = html.escape(str(waarde), quote=False)
    spatie = ' xml:space="preserve"' if tekst != tekst.strip() else ""
    return f'<c r="{ref}"{s} t="inlineStr"><is><t{spatie}>{tekst}</t></is></c>'


CEL = re.compile(r'<c r="(?P<kolom>[A-Z]{1,3})(?P<rij>\d+)"(?P<attrs>[^>]*?)(?:/>|>(?P<inhoud>.*?)</c>)', re.S)
RIJ = re.compile(r'<row r="(?P<nummer>\d+)"(?P<attrs>[^>]*?)(?:/>|>(?P<inhoud>.*?)</row>)', re.S)
FORMULE = re.compile(r"<f\b[^>]*?(?:/>|>.*?</f>)", re.S)


def _stijl(attrs: str) -> str | None:
    m = re.search(r'\bs="(?P<s>\d+)"', attrs)
    return m.group("s") if m else None


def _sheetdata(xml: str) -> tuple[int, int, str]:
    start = xml.index("<sheetData")
    if xml[start:].startswith("<sheetData/>"):
        xml = xml[:start] + "<sheetData></sheetData>" + xml[start + len("<sheetData/>") :]
    begin = xml.index(">", start) + 1
    eind = xml.index("</sheetData>")
    return begin, eind, xml


def vul_blad_xml(xml: str, waarden: dict[str, object], datumstijlen: frozenset[str] = frozenset()) -> str:
    """Zet waarden in bestaande cellen (stijl blijft), of voeg cellen/rijen in op de juiste plek.

    Een bestaande formule in een gevulde cel verdwijnt: de cel krijgt een waarde.
    """
    per_rij: dict[int, dict[int, tuple[str, object]]] = {}
    for ref, waarde in waarden.items():
        rij, kolom = coordinate_to_tuple(ref)
        per_rij.setdefault(rij, {})[kolom] = (ref, waarde)
    begin, eind, xml = _sheetdata(xml)
    data = xml[begin:eind]

    def vervang_rij(m: re.Match) -> str:
        nummer = int(m.group("nummer"))
        if nummer not in per_rij:
            return m.group(0)
        nieuw = per_rij.pop(nummer)
        cellen = []
        for c in CEL.finditer(m.group("inhoud") or ""):
            kolom = column_index_from_string(c.group("kolom"))
            if kolom in nieuw:
                ref, waarde = nieuw.pop(kolom)
                cellen.append((kolom, _cel_xml(ref, _stijl(c.group("attrs")), waarde, datumstijlen)))
            else:
                cellen.append((kolom, c.group(0)))
        cellen += [(k, _cel_xml(ref, None, w, datumstijlen)) for k, (ref, w) in nieuw.items()]
        cellen.sort(key=lambda x: x[0])
        attrs = re.sub(r'\s+spans="[^"]*"', "", m.group("attrs"))
        return f'<row r="{nummer}"{attrs}>' + "".join(c for _, c in cellen) + "</row>"

    data = RIJ.sub(vervang_rij, data)
    if per_rij:  # rijen die nog niet bestonden, op volgorde invoegen
        rijen = [(int(m.group("nummer")), m.group(0)) for m in RIJ.finditer(data)]
        for nummer, cellen in per_rij.items():
            inhoud = "".join(_cel_xml(ref, None, w, datumstijlen) for _, (ref, w) in sorted(cellen.items()))
            rijen.append((nummer, f'<row r="{nummer}">{inhoud}</row>'))
        rijen.sort(key=lambda x: x[0])
        data = "".join(r for _, r in rijen)
    return xml[:begin] + data + xml[eind:]


def formulecellen(xml: str) -> list[str]:
    """Verwijzingen van alle cellen met een formule."""
    begin, eind, xml = _sheetdata(xml)
    return [
        f"{c.group('kolom')}{c.group('rij')}"
        for c in CEL.finditer(xml[begin:eind])
        if "<f" in (c.group("inhoud") or "")
    ]


def zonder_formules(xml: str, leegmaken: set[str] | None = None) -> str:
    """Vervang elke formule door haar gecachte uitkomst; cellen in `leegmaken` worden leeg (stijl blijft).

    Een tekstuitkomst van een formule (`t="str"`) wordt een gewone tekstcel.
    """
    begin, eind, xml = _sheetdata(xml)
    leegmaken = leegmaken or set()

    def cel(m: re.Match) -> str:
        inhoud = m.group("inhoud") or ""
        ref = f"{m.group('kolom')}{m.group('rij')}"
        if "<f" not in inhoud:
            return m.group(0)
        stijl = _stijl(m.group("attrs"))
        s = f' s="{stijl}"' if stijl else ""
        if ref in leegmaken:
            return f'<c r="{ref}"{s}/>'
        inhoud = FORMULE.sub("", inhoud)
        attrs = m.group("attrs")
        v = re.search(r"<v>(?P<v>.*?)</v>", inhoud, re.S)
        if v is None:
            return f'<c r="{ref}"{s}/>'
        if re.search(r'\bt="str"', attrs):
            return f'<c r="{ref}"{s} t="inlineStr"><is><t xml:space="preserve">{v.group("v")}</t></is></c>'
        attrs = re.sub(r'\s+cm="[^"]*"|\s+vm="[^"]*"', "", attrs)
        return f'<c r="{ref}"{attrs}>{inhoud}</c>'

    return xml[:begin] + CEL.sub(cel, xml[begin:eind]) + xml[eind:]


# --- Afdrukinstellingen in het werkblad ----------------------------------------------------------------------
PAPIER = {"A4": "9", "A3": "8"}
RICHTING = {"staand": "portrait", "liggend": "landscape"}
# Volgorde van de elementen ná pageSetup in CT_Worksheet (pageSetup moet daarvóór staan).
_NA_PAGESETUP = ("headerFooter", "rowBreaks", "colBreaks", "customProperties", "cellWatches", "ignoredErrors",
                 "smartTags", "drawing", "legacyDrawing", "legacyDrawingHF", "drawingHF", "picture", "oleObjects",
                 "controls", "webPublishItems", "tableParts", "extLst")  # fmt: skip
_NA_SHEETPR = ("dimension", "sheetViews", "sheetFormatPr", "cols", "sheetData")


def _voeg_in_voor(xml: str, element: str, namen: tuple[str, ...]) -> str:
    posities = [m.start() for naam in namen for m in [re.search(rf"<{naam}\b", xml)] if m]
    pos = min(posities) if posities else xml.index("</worksheet>")
    return xml[:pos] + element + xml[pos:]


def zet_afdrukinstellingen(xml: str, richting: str, papier: str, passend: bool, geselecteerd: bool) -> str:
    """Richting, papierformaat en "passend op één pagina breed en hoog" expliciet in het werkblad zetten."""
    # Pagina-einden zouden een blad toch over meerdere pagina's splitsen.
    xml = re.sub(r"<(rowBreaks|colBreaks)\b.*?(?:/>|</\1>)", "", xml, flags=re.S)
    attrs = f' paperSize="{PAPIER[papier]}" orientation="{RICHTING[richting]}"'
    if passend:
        attrs += ' fitToWidth="1" fitToHeight="1"'
    m = re.search(r"<pageSetup\b(?P<attrs>[^>]*?)(?P<eind>/>|>)", xml)
    if m:
        oud = re.sub(r'\s+(paperSize|orientation|fitToWidth|fitToHeight|scale|r:id|[a-zA-Z0-9]+:id)="[^"]*"', "",
                     m.group("attrs"))  # fmt: skip
        nieuw = f"<pageSetup{oud}{attrs}{m.group('eind')}"
        if m.group("eind") == ">":  # zeldzaam: pageSetup met inhoud
            nieuw = f"<pageSetup{oud}{attrs}/>"
            einde = xml.index("</pageSetup>", m.end()) + len("</pageSetup>")
            xml = xml[: m.start()] + nieuw + xml[einde:]
        else:
            xml = xml[: m.start()] + nieuw + xml[m.end() :]
    else:
        xml = _voeg_in_voor(xml, f"<pageSetup{attrs}/>", _NA_PAGESETUP)
        if "<pageMargins" not in xml:
            marges = '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>'
            xml = xml.replace("<pageSetup", marges + "<pageSetup", 1)
    # sheetPr/pageSetUpPr fitToPage
    if not passend:
        xml = re.sub(r'(<pageSetUpPr\b[^>]*?)\s*fitToPage="[^"]*"', r"\1", xml)
    if passend:
        if "<sheetPr" not in xml:
            begin = xml.index(">", xml.index("<worksheet")) + 1
            xml = xml[:begin] + '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>' + xml[begin:]
        elif re.search(r"<sheetPr\b[^>]*/>", xml):
            xml = re.sub(
                r"<sheetPr\b(?P<a>[^>]*)/>", r'<sheetPr\g<a>><pageSetUpPr fitToPage="1"/></sheetPr>', xml, count=1
            )
        elif "<pageSetUpPr" in xml:
            xml = re.sub(r"<pageSetUpPr\b(?P<a>[^>]*?)\s*fitToPage=\"[^\"]*\"", r"<pageSetUpPr\g<a>", xml, count=1)
            xml = xml.replace("<pageSetUpPr", '<pageSetUpPr fitToPage="1"', 1)
        else:
            # pageSetUpPr komt na tabColor en outlinePr binnen sheetPr
            xml = re.sub(r"</sheetPr>", '<pageSetUpPr fitToPage="1"/></sheetPr>', xml, count=1)
    xml = re.sub(r'\s+tabSelected="[^"]*"', "", xml)
    if geselecteerd:
        xml = re.sub(r"<sheetView\b", '<sheetView tabSelected="1"', xml, count=1)
    return xml


# --- Het pakket ---------------------------------------------------------------------------------------------
def _absoluut(basis: str, doel: str) -> str:
    if doel.startswith("/"):
        return doel.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(basis), doel))


def _rels_pad(onderdeel: str) -> str:
    return posixpath.join(posixpath.dirname(onderdeel), "_rels", posixpath.basename(onderdeel) + ".rels")


def _relatief(van: str, naar: str) -> str:
    return posixpath.relpath(naar, posixpath.dirname(van))


@dataclass
class Blad:
    naam: str
    rid: str
    onderdeel: str
    zichtbaar: bool = True


@dataclass
class Pakket:
    """Een XLSX-pakket in het geheugen: onderdelen in de oorspronkelijke volgorde."""

    onderdelen: dict[str, bytes] = field(default_factory=dict)
    infos: dict[str, zipfile.ZipInfo] = field(default_factory=dict)

    @classmethod
    def lees(cls, bron: Path | bytes) -> Pakket:
        import io

        pakket = cls()
        with zipfile.ZipFile(io.BytesIO(bron) if isinstance(bron, bytes) else bron) as z:
            for item in z.infolist():
                pakket.onderdelen[item.filename] = z.read(item.filename)
                pakket.infos[item.filename] = item
        if "xl/workbook.xml" not in pakket.onderdelen:
            raise PakketFout("Geen werkboek (xl/workbook.xml ontbreekt)")
        return pakket

    def tekst(self, naam: str) -> str:
        return self.onderdelen[naam].decode("utf-8")

    def zet(self, naam: str, inhoud: str | bytes) -> None:
        self.onderdelen[naam] = inhoud.encode("utf-8") if isinstance(inhoud, str) else inhoud

    def als_bytes(self) -> bytes:
        import io

        uit = io.BytesIO()
        with zipfile.ZipFile(uit, "w", zipfile.ZIP_DEFLATED) as z:
            for naam, data in self.onderdelen.items():
                info = self.infos.get(naam) or zipfile.ZipInfo(naam, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, data)
        return uit.getvalue()

    # -- relaties
    def relaties(self, onderdeel: str) -> list[dict]:
        pad = _rels_pad(onderdeel)
        if pad not in self.onderdelen:
            return []
        uit = []
        for rel in fromstring(self.onderdelen[pad]):
            uit.append({"Id": rel.get("Id"), "Type": rel.get("Type", ""), "Target": rel.get("Target", ""),
                        "TargetMode": rel.get("TargetMode")})  # fmt: skip
        return uit

    def schrijf_relaties(self, onderdeel: str, relaties: list[dict]) -> None:
        regels = []
        for r in relaties:
            modus = f' TargetMode="{r["TargetMode"]}"' if r.get("TargetMode") else ""
            regels.append(f'<Relationship Id="{html.escape(r["Id"])}" Type="{html.escape(r["Type"])}" '
                          f'Target="{html.escape(r["Target"])}"{modus}/>')  # fmt: skip
        self.zet(_rels_pad(onderdeel), '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 + "".join(regels) + "</Relationships>")  # fmt: skip

    def bladen(self) -> list[Blad]:
        werkboek = fromstring(self.onderdelen["xl/workbook.xml"])
        relaties = {r["Id"]: r for r in self.relaties("xl/workbook.xml")}
        uit = []
        for sheet in werkboek.find(NS_MAIN + "sheets"):
            rel = relaties.get(sheet.get(NS_REL + "id"))
            if rel is None or rel.get("TargetMode") == "External" or not rel["Type"].endswith("/worksheet"):
                continue
            pad = _absoluut("xl/workbook.xml", rel["Target"])
            if not pad.startswith("xl/") or ".." in pad.split("/"):
                raise PakketFout("Onverwacht werkbladpad")
            uit.append(Blad(sheet.get("name"), rel["Id"], pad, sheet.get("state", "visible") == "visible"))
        return uit

    def blad(self, naam: str) -> Blad:
        for b in self.bladen():
            if b.naam == naam:
                return b
        raise PakketFout(f"Werkblad {naam!r} niet gevonden in het sjabloon")

    # -- inhoudstypen
    def _content_types(self) -> str:
        return self.tekst("[Content_Types].xml")

    def override_type(self, onderdeel: str) -> str | None:
        m = re.search(
            rf'<Override PartName="/{re.escape(onderdeel)}" ContentType="(?P<t>[^"]+)"\s*/>', self._content_types()
        )
        return m.group("t") if m else None

    def zet_override(self, onderdeel: str, inhoudstype: str) -> None:
        ct = re.sub(rf'<Override PartName="/{re.escape(onderdeel)}"[^>]*/>', "", self._content_types())
        ct = ct.replace("</Types>", f'<Override PartName="/{onderdeel}" ContentType="{inhoudstype}"/></Types>')
        self.zet("[Content_Types].xml", ct)

    def verwijder(self, onderdeel: str) -> None:
        self.onderdelen.pop(onderdeel, None)
        self.onderdelen.pop(_rels_pad(onderdeel), None)
        ct = re.sub(rf'<Override PartName="/{re.escape(onderdeel)}"[^>]*/>', "", self._content_types())
        self.zet("[Content_Types].xml", ct)

    def _vrije_naam(self, voorbeeld: str, achtervoegsel: str) -> str:
        stam, ext = posixpath.splitext(voorbeeld)
        stam = re.sub(r"\d+$", "", stam)
        teller = 1
        while f"{stam}{achtervoegsel}{teller}{ext}" in self.onderdelen:
            teller += 1
        return f"{stam}{achtervoegsel}{teller}{ext}"

    def kopieer_werkblad(self, onderdeel: str) -> tuple[str, list[str]]:
        """Kopie van een werkbladonderdeel met eigen kopieën van per-blad onderdelen (opmerkingen, tekeningen).

        Geeft (nieuw onderdeel, meldingen). Tabellen worden niet gekopieerd (unieke namen); de kopie verwijst
        er dan niet meer naar.
        """
        meldingen: list[str] = []
        nieuw = self._vrije_naam(onderdeel, "_kopie")
        xml = self.tekst(onderdeel)
        nieuwe_relaties = []
        for rel in self.relaties(onderdeel):
            if rel.get("TargetMode") == "External":
                continue  # externe koppelingen niet meenemen
            soort = rel["Type"].rsplit("/", 1)[-1]
            doel = _absoluut(onderdeel, rel["Target"])
            if soort in ("table", "pivotTable", "queryTable"):
                xml = re.sub(rf'<(tablePart)\b[^>]*"{re.escape(rel["Id"])}"[^>]*/>', "", xml)
                meldingen.append(f"{soort} in het blad is niet meegekopieerd")
                continue
            if doel not in self.onderdelen:
                continue
            kopie = self._vrije_naam(doel, "_kopie")
            self.onderdelen[kopie] = self.onderdelen[doel]
            if _rels_pad(doel) in self.onderdelen:  # bijv. tekening → afbeelding: zelfde map, zelfde doelen
                self.onderdelen[_rels_pad(kopie)] = self.onderdelen[_rels_pad(doel)]
            ct = self.override_type(doel)
            if ct:
                self.zet_override(kopie, ct)
            nieuwe_relaties.append(rel | {"Target": _relatief(onderdeel, kopie)})
        if "<tableParts" in xml:
            xml = re.sub(r'<tableParts\b[^>]*count="\d+"', "<tableParts", xml)
            if not re.search(r"<tablePart\b", xml):
                xml = re.sub(r"<tableParts\b.*?(?:/>|</tableParts>)", "", xml, flags=re.S)
        self.zet(nieuw, xml)
        self.schrijf_relaties(nieuw, nieuwe_relaties)
        self.zet_override(nieuw, CT_WERKBLAD)
        return nieuw, meldingen


@dataclass(frozen=True)
class NieuwBlad:
    """Een blad in een afdrukkopie: kopie van `bron`, met de naam `naam` en de `waarden`."""

    bron: str
    naam: str
    waarden: dict[str, object]
    bereik: str
    richting: str
    leegmaken: frozenset[str] = frozenset()


def _sheetnaam_ref(naam: str) -> str:
    return "'" + naam.replace("'", "''") + "'"


def _absoluut_bereik(bereik: str) -> str:
    begin, _, eind = bereik.partition(":")
    delen = []
    for ref in (begin, eind or begin):
        m = re.fullmatch(r"(?P<k>[A-Z]{1,3})(?P<r>\d+)", ref.replace("$", "").upper())
        if not m:
            raise PakketFout(f"Ongeldig bereik {bereik!r}")
        delen.append(f"${m.group('k')}${m.group('r')}")
    return ":".join(delen)


def datumstijlen(pakket: Pakket) -> frozenset[str]:
    """Stijlnummers (cellXfs) met een datumnotatie: daar schrijven we datums als Excel-datumgetal."""
    if "xl/styles.xml" not in pakket.onderdelen:
        return frozenset()
    stijlen = fromstring(pakket.onderdelen["xl/styles.xml"])
    eigen = {}
    numfmts = stijlen.find(NS_MAIN + "numFmts")
    for fmt in numfmts if numfmts is not None else []:
        eigen[fmt.get("numFmtId")] = fmt.get("formatCode", "")
    ingebouwd_datum = {"14", "15", "16", "17", "22"}  # ECMA-376 deel 1, §18.8.30: ingebouwde datumnotaties
    uit = set()
    xfs = stijlen.find(NS_MAIN + "cellXfs")
    for index, xf in enumerate(xfs if xfs is not None else []):
        fid = xf.get("numFmtId", "0")
        code = re.sub(r'"[^"]*"|\[[^\]]*\]', "", eigen.get(fid, "")).lower()
        if fid in ingebouwd_datum or (code and re.search(r"[dmy]", code) and not re.search(r"[h]", code)):
            uit.add(str(index))
    return frozenset(uit)


def bouw_kopie(pakket: Pakket, nieuwe: list[NieuwBlad], *, behoud_overige: bool, papier: str, passend: bool,
               formules_bevriezen: bool, volledig_herberekenen: bool) -> tuple[bytes, list[str]]:  # fmt: skip
    """Bouw een nieuw pakket met de gevraagde bladen (kopieën van bronbladen, gevuld met waarden).

    * `behoud_overige=False` (afdrukkopie): alleen de nieuwe bladen blijven in het werkboek; de andere bladen,
      externe koppelingen, draaitabelcaches en de rekenketen verdwijnen, net als gedefinieerde namen (behalve
      de afdrukbereiken van de nieuwe bladen). LibreOffice drukt anders ook verborgen bladen af (proef 2).
    * `formules_bevriezen`: in de nieuwe bladen wordt elke formule vervangen door haar gecachte uitkomst (zodat
      niets naar weggelaten bladen verwijst); cellen in `leegmaken` worden leeg.
    * `volledig_herberekenen`: `fullCalcOnLoad` zetten (voor het herberekenen van het sjabloon zelf).
    """
    pakket = Pakket(dict(pakket.onderdelen), dict(pakket.infos))
    meldingen: list[str] = []
    stijlen_datum = datumstijlen(pakket)
    oude_bladen = pakket.bladen()
    werkboek = pakket.tekst("xl/workbook.xml")
    m_prefix = re.search(rf'xmlns:(?P<p>\w+)="{re.escape(REL_NS)}"', werkboek)
    prefix = m_prefix.group("p") if m_prefix else "r"
    if not m_prefix:
        werkboek = werkboek.replace("<workbook ", f'<workbook xmlns:r="{REL_NS}" ', 1)
    relaties = pakket.relaties("xl/workbook.xml")
    if not behoud_overige:
        bewaard = []
        for rel in relaties:
            soort = rel["Type"].rsplit("/", 1)[-1]
            if soort in WEG_BIJ_AFDRUK:
                if rel.get("TargetMode") != "External":
                    doel = _absoluut("xl/workbook.xml", rel["Target"])
                    if soort not in ("worksheet",):
                        pakket.verwijder(doel)
            else:
                bewaard.append(rel)
        relaties = bewaard
        for element in ("externalReferences", "pivotCaches"):
            werkboek = re.sub(rf"<{element}\b.*?(?:/>|</{element}>)", "", werkboek, flags=re.S)
    else:
        relaties = [r for r in relaties if r["Type"].rsplit("/", 1)[-1] != "calcChain"]
        pakket.onderdelen.pop("xl/calcChain.xml", None)
    pakket.zet("[Content_Types].xml", re.sub(r'<Override PartName="/xl/calcChain.xml"[^>]*/>', "",
                                              pakket.tekst("[Content_Types].xml")))  # fmt: skip

    bron_per_naam = {b.naam: b for b in oude_bladen}
    gebruikte_ids = {r["Id"] for r in relaties}
    nieuwe_sheets = []
    namen = [] if not behoud_overige else [b.naam for b in oude_bladen]
    for index, nb in enumerate(nieuwe):
        if nb.bron not in bron_per_naam:
            raise PakketFout(f"Werkblad {nb.bron!r} niet gevonden in het sjabloon")
        if nb.naam in namen:
            raise PakketFout(f"Bladnaam {nb.naam!r} komt dubbel voor")
        namen.append(nb.naam)
        onderdeel, extra = pakket.kopieer_werkblad(bron_per_naam[nb.bron].onderdeel)
        meldingen += extra
        xml = pakket.tekst(onderdeel)
        if formules_bevriezen:
            xml = zonder_formules(xml, set(nb.leegmaken) - set(nb.waarden))
        xml = vul_blad_xml(xml, nb.waarden, stijlen_datum)
        xml = zet_afdrukinstellingen(xml, nb.richting, papier, passend, geselecteerd=index == 0 and not behoud_overige)
        pakket.zet(onderdeel, xml)
        rid = "rIdKopie1"
        teller = 1
        while rid in gebruikte_ids:
            teller += 1
            rid = f"rIdKopie{teller}"
        gebruikte_ids.add(rid)
        relaties.append({"Id": rid, "Type": TYPE_WERKBLAD, "Target": _relatief("xl/workbook.xml", onderdeel)})
        nieuwe_sheets.append((nb, rid))

    # Bladenlijst en namen in workbook.xml
    sheets_m = re.search(r"<sheets>(?P<inhoud>.*?)</sheets>", werkboek, re.S)
    bestaande = re.findall(r"<sheet\b[^>]*/>", sheets_m.group("inhoud")) if behoud_overige else []
    max_id = max([int(x) for x in re.findall(r'sheetId="(\d+)"', sheets_m.group("inhoud"))] or [0])
    tags = list(bestaande)
    for teller, (nb, rid) in enumerate(nieuwe_sheets, 1):
        tags.append(f'<sheet name="{html.escape(nb.naam)}" sheetId="{max_id + teller}" {prefix}:id="{rid}"/>')
    werkboek = werkboek[: sheets_m.start()] + "<sheets>" + "".join(tags) + "</sheets>" + werkboek[sheets_m.end() :]
    if not behoud_overige:
        werkboek = re.sub(r"<definedNames>.*?</definedNames>|<definedNames/>", "", werkboek, flags=re.S)
        werkboek = re.sub(r'\s+(activeTab|firstSheet)="\d+"', "", werkboek)
        namen_xml = "".join(
            f'<definedName name="_xlnm.Print_Area" localSheetId="{i}">'
            f"{html.escape(_sheetnaam_ref(nb.naam))}!{_absoluut_bereik(nb.bereik)}</definedName>"
            for i, (nb, _) in enumerate(nieuwe_sheets)
        )
        if namen_xml:
            werkboek = _voeg_in_voor(werkboek.replace("</workbook>", "</workbook>"), f"<definedNames>{namen_xml}</definedNames>",
                                     ("calcPr", "oleSize", "customWorkbookViews", "pivotCaches", "smartTagPr",
                                      "smartTagTypes", "webPublishing", "fileRecoveryPr", "webPublishObjects", "extLst"))  # fmt: skip
    if volledig_herberekenen:
        if "<calcPr" in werkboek:
            werkboek = re.sub(r"<calcPr\b(?P<a>[^>]*?)\s*fullCalcOnLoad=\"[^\"]*\"", r"<calcPr\g<a>", werkboek)
            werkboek = werkboek.replace("<calcPr", '<calcPr fullCalcOnLoad="1"', 1)
        else:
            werkboek = _voeg_in_voor(werkboek, '<calcPr fullCalcOnLoad="1"/>',
                                     ("oleSize", "customWorkbookViews", "pivotCaches", "smartTagPr", "smartTagTypes",
                                      "webPublishing", "fileRecoveryPr", "webPublishObjects", "extLst"))  # fmt: skip
    pakket.zet("xl/workbook.xml", werkboek.replace("<workbook>", "<workbook>"))
    pakket.schrijf_relaties("xl/workbook.xml", relaties)
    if not behoud_overige:
        # Oude werkbladen (en hun per-blad onderdelen) uit het pakket halen.
        for blad in oude_bladen:
            for rel in pakket.relaties(blad.onderdeel):
                if rel.get("TargetMode") == "External":
                    continue
                soort = rel["Type"].rsplit("/", 1)[-1]
                if soort in ("comments", "vmlDrawing", "drawing", "printerSettings", "table"):
                    pakket.verwijder(_absoluut(blad.onderdeel, rel["Target"]))
            pakket.verwijder(blad.onderdeel)
    return pakket.als_bytes(), meldingen


def bladnamen(pad: Path | bytes) -> list[str]:
    return [b.naam for b in Pakket.lees(pad).bladen()]


def kolom_verschuif(ref: str, kolommen: int) -> str:
    rij, kolom = coordinate_to_tuple(ref)
    return f"{get_column_letter(kolom + kolommen)}{rij}"
