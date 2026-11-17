"""Veilige werkboekcontrole en celhulpen, overgenomen uit lvos04/gewichtsverwachting `app/legacy_program.py`
(branch ccr-f5cf979e-9oiocr, versie 0.4.0): `_validate_archive`, `_json_value`, `_source`, `_int_literal`
en `parse_schema` ongewijzigd in werking. Aanpassingen: de groottegrenzen komen uit het instellingenregister
(parameters `max_file`/`max_expanded`), en de soortcodes zijn een parameter.
"""

from __future__ import annotations

import math
import re
import zipfile
from datetime import date, datetime, time
from pathlib import Path, PurePosixPath

from openpyxl.utils import get_column_letter

MAX_MEMBERS = 10000
MAX_CELL_TEXT = 16384
# Soortcodes uit Gewichtsverwachting; de koppeling naar rassen staat in Beheer → Rassen (code).
SPECIES = {"a": "antonov", "p": "pjotr", "t": "tutu", "b": "baltazar", "g": None, "k": None, "w": None, "altaj": None}


def _validate_archive(path, max_file: int, max_expanded: int):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > max_file:
        raise ValueError("Werkboek ontbreekt of is te groot")
    try:
        with zipfile.ZipFile(path) as z:
            items = z.infolist()
            names = [x.filename for x in items]
            if len(items) > MAX_MEMBERS or "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                raise ValueError("Geen ondersteund XLSX-werkboek")
            if len({x.casefold() for x in names}) != len(names):
                raise ValueError("Dubbele archiefpaden")
            total = 0
            for item in items:
                name = item.filename
                pure = PurePosixPath(name)
                printer = bool(re.fullmatch(r"xl/printerSettings/printerSettings[0-9]+\.bin", name, re.I))
                if (
                    pure.is_absolute()
                    or ".." in pure.parts
                    or "\\" in name
                    or item.flag_bits & 1
                    or "vbaproject" in name.lower()
                    or (name.lower().endswith((".bin", ".exe", ".dll")) and not printer)
                ):
                    raise ValueError("Onveilig of versleuteld werkboekonderdeel")
                total += item.file_size
                if total > max_expanded:
                    raise ValueError("Uitgepakt werkboek te groot")
                if name.lower().endswith((".xml", ".rels")):
                    with z.open(item) as stream:
                        tail = b""
                        while chunk := stream.read(65536):
                            probe = (tail + chunk).lower()
                            if b"<!doctype" in probe or b"<!entity" in probe:
                                raise ValueError("XML-entiteiten niet toegestaan")
                            tail = probe[-16:]
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise ValueError("Ongeldig XLSX-werkboek") from exc


def _json_value(value):
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, str):
        if len(value) > MAX_CELL_TEXT:
            raise ValueError("Celinhoud te groot")
        return value
    if value is None or isinstance(value, (int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Niet-eindige celinhoud")
        return value
    return str(value)[:MAX_CELL_TEXT]


def _source(cell, cached, row, column):
    raw = cell.value
    formula = raw if cell.data_type == "f" else None
    return {
        "cell": f"{get_column_letter(column)}{row}",
        "value": None if formula else _json_value(raw),
        "formula": _json_value(formula),
        "cached": _json_value(cached.value) if formula else None,
        "confidence": "formula_cache_unverified" if formula else "literal" if raw is not None else "missing",
    }


def _int_literal(source, low, high):
    value = source["value"]
    if source["formula"] is not None or isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    rounded = round(value)
    if abs(value - rounded) > 1e-9 or not low <= rounded <= high:
        return None
    if value != rounded:
        source["normalization"] = "floating_point_roundoff_to_integer"
    return int(rounded)


def parse_schema(value):
    """Parse explicit bed counts; never infer beds for a lone species code."""
    if value is None or not str(value).strip():
        return [], ["schema_empty"]
    text = re.sub(r"\s+", "", str(value).lower())
    if text in SPECIES:
        return [{"code": text, "variety": SPECIES[text], "beds": None, "inactive": SPECIES[text] is None}], [
            "bed_count_unknown"
        ] + (["inactive_species"] if SPECIES[text] is None else [])
    parts = re.findall(r"(\d+)(altaj|[aptbgkw])", text)
    if not parts or "".join(n + c for n, c in parts) != text:
        return [], ["schema_unrecognized"]
    rows = [
        {"code": code, "variety": SPECIES[code], "beds": int(number), "inactive": SPECIES[code] is None}
        for number, code in parts
    ]
    counts = [item["beds"] for item in rows]
    if any(n < 1 for n in counts) or sum(counts) > 6 or len({item["code"] for item in rows}) != len(rows):
        return [], ["schema_invalid_beds"]
    reasons = []
    if sum(counts) < 6:
        reasons.append("partial_bed_allocation")
    if any(item["inactive"] for item in rows):
        reasons.append("inactive_species")
    if len(rows) > 1:
        reasons.append("mixed_species_review")
    return rows, reasons
