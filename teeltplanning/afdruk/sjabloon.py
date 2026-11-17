"""Sjabloonbeheer (§9.3): het eigen werkboek één keer uploaden, controleren en bewaren onder DATA_DIR/sjabloon/.

De controle toont welke bladen er zijn, of het rondeblad en de teeltkaart uit de instellingen bestaan, het
afdrukbereik, welke lettertypen het werkboek gebruikt en of die in de container beschikbaar zijn (of metrisch
gelijk), en welke formulecellen de app niet vult.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

from defusedxml.ElementTree import fromstring
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import instellingen
from ..auth import audit
from ..db.basis import DATA_DIR
from ..db.modellen import Gebruiker, Sjabloon
from ..importers.werkboek import _validate_archive
from ..xlsxpakket import NS_MAIN, Pakket, PakketFout, formulecellen, kolom_verschuif
from .pdf import lettertypemap

LETTERTYPE_EXTENSIES = (".ttf", ".otf", ".ttc")
LETTERTYPE_MAGIC = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf")


def sjabloonmap() -> Path:
    map_ = DATA_DIR / "sjabloon"
    map_.mkdir(mode=stat.S_IRWXU, exist_ok=True)
    return map_


def actief(db: Session) -> Sjabloon | None:
    return db.scalar(select(Sjabloon).where(Sjabloon.actief.is_(True)).order_by(Sjabloon.id.desc()))


def pad(sjabloon: Sjabloon) -> Path:
    relatief = Path(sjabloon.pad)
    if relatief.is_absolute() or ".." in relatief.parts:
        raise ValueError("Ongeldig sjabloonpad")
    return DATA_DIR / relatief


def sla_op(db: Session, gebruiker: Gebruiker, bestandsnaam: str, inhoud: bytes) -> Sjabloon:
    snap = instellingen.laad(db)
    grens = snap["afdrukken.max_sjabloon_mb"] * 1024 * 1024
    if len(inhoud) > grens:
        raise ValueError("Het sjabloon is groter dan de ingestelde maximale grootte.")
    if not bestandsnaam.lower().endswith(".xlsx"):
        raise ValueError("Alleen .xlsx-werkboeken (geen macro's, geen .xls).")
    sha = hashlib.sha256(inhoud).hexdigest()
    doel = sjabloonmap() / f"sjabloon-{sha}.xlsx"
    tijdelijk = doel.with_suffix(".controle")
    tijdelijk.write_bytes(inhoud)
    try:
        _validate_archive(tijdelijk, grens, snap["import.max_uitgepakt_mb"] * 1024 * 1024)
        controle = analyseer(Pakket.lees(tijdelijk), snap)
    except (ValueError, PakketFout, KeyError) as fout:
        tijdelijk.unlink(missing_ok=True)
        raise ValueError(f"Geen bruikbaar sjabloon: {fout}") from fout
    tijdelijk.replace(doel)
    doel.chmod(stat.S_IRUSR | stat.S_IWUSR)
    for oud in db.scalars(select(Sjabloon).where(Sjabloon.actief.is_(True))):
        oud.actief = False
    rij = Sjabloon(bestandsnaam=Path(bestandsnaam).name[:100], pad=str(doel.relative_to(DATA_DIR)), sha256=sha,
                   grootte=len(inhoud), actief=True, controle=controle, geupload_door=gebruiker.id)  # fmt: skip
    db.add(rij)
    db.flush()
    audit(db, gebruiker, "sjabloon_geupload", f"sjabloon:{rij.id}", {"bestand": rij.bestandsnaam, "sha256": sha})
    return rij


def ververs_controle(db: Session, sjabloon: Sjabloon) -> dict:
    sjabloon.controle = analyseer(Pakket.lees(pad(sjabloon)), instellingen.laad(db))
    return sjabloon.controle


# --- Analyse -------------------------------------------------------------------------------------------------
def _afdrukbereiken(pakket: Pakket) -> dict[str, str]:
    werkboek = fromstring(pakket.onderdelen["xl/workbook.xml"])
    bladen = [s.get("name") for s in werkboek.find(NS_MAIN + "sheets")]
    uit = {}
    namen = werkboek.find(NS_MAIN + "definedNames")
    for naam in namen if namen is not None else []:
        if naam.get("name") == "_xlnm.Print_Area" and naam.get("localSheetId") is not None:
            index = int(naam.get("localSheetId"))
            if index < len(bladen):
                uit[bladen[index]] = (naam.text or "").split("!")[-1].replace("$", "")
    return uit


def gebruikte_lettertypen(pakket: Pakket) -> list[str]:
    namen: set[str] = set()
    if "xl/styles.xml" in pakket.onderdelen:
        for font in fromstring(pakket.onderdelen["xl/styles.xml"]).iter(NS_MAIN + "font"):
            naam = font.find(NS_MAIN + "name")
            if naam is not None and naam.get("val"):
                namen.add(naam.get("val"))
    for onderdeel, data in pakket.onderdelen.items():
        if onderdeel.startswith("xl/theme/") and onderdeel.endswith(".xml"):
            for m in re.finditer(rb'<a:(?:major|minor)Font>\s*<a:latin typeface="(?P<n>[^"]+)"', data):
                namen.add(m.group("n").decode("utf-8", "replace"))
    return sorted(namen)


def beschikbare_lettertypen() -> set[str]:
    """Families die fontconfig kent (systeem + DATA_DIR/lettertypen)."""
    fc = shutil.which("fc-list")
    if not fc:
        return set()
    omgeving = dict(os.environ)
    conf = lettertypemap() / ".fonts.conf"
    conf.write_text(
        '<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        f'<include ignore_missing="yes">/etc/fonts/fonts.conf</include><dir>{lettertypemap()}</dir></fontconfig>',
        encoding="utf-8",
    )
    omgeving["FONTCONFIG_FILE"] = str(conf)
    try:
        uit = subprocess.run([fc, ":", "family"], capture_output=True, text=True, timeout=60, env=omgeving).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    families = set()
    for regel in uit.splitlines():
        for deel in regel.split(","):
            if deel.strip():
                families.add(deel.strip())
    return families


def lettertype_status(namen: list[str], snap) -> list[dict]:
    beschikbaar = {n.lower() for n in beschikbare_lettertypen()}
    gelijk = snap["afdrukken.metrisch_gelijk"] or {}
    uit = []
    for naam in namen:
        if naam.lower() in beschikbaar:
            uit.append({"naam": naam, "status": "aanwezig", "tekst": "geïnstalleerd of geüpload"})
        elif naam in gelijk and gelijk[naam].lower() in beschikbaar:
            uit.append({"naam": naam, "status": "metrisch_gelijk",
                        "tekst": f"vervangen door {gelijk[naam]} (zelfde tekenbreedtes)"})  # fmt: skip
        else:
            uit.append({"naam": naam, "status": "ontbreekt",
                        "tekst": "ontbreekt: LibreOffice kiest een vervanger, regels kunnen verschuiven. "
                                 "Upload het lettertypebestand hieronder."})  # fmt: skip
    return uit


def _gevulde_cellen_kaart(snap) -> set[str]:
    cellen = set()
    per_blad = snap["afdrukken.teeltkaart_kaarten_per_blad"]
    for kaart in range(per_blad):
        verschuiving = kaart * snap["afdrukken.teeltkaart_tweede_kaart_kolommen"]
        cellen |= {kolom_verschuif(c, verschuiving) for c in snap["afdrukken.teeltkaart_cellen"].values()}
        for kolom in snap["afdrukken.teeltkaart_momentkolommen"].values():
            for i in range(snap["teeltkaart.aantal_momenten"]):
                cellen.add(kolom_verschuif(f"{kolom}{snap['afdrukken.teeltkaart_eerste_momentrij'] + i}", verschuiving))
    return cellen


def _gevulde_cellen_rondeblad(snap) -> set[str]:
    cellen = {snap["import.rondeblad_titelcel"]}
    for kolom in snap["import.rondeblad_kolommen"].values():
        for rij in range(snap["import.rondeblad_eerste_rij"], snap["import.rondeblad_laatste_rij"] + 1):
            cellen.add(f"{kolom}{rij}")
    return cellen


def niet_gevulde_formules(pakket: Pakket, blad: str, gevuld: set[str]) -> list[str]:
    try:
        xml = pakket.tekst(pakket.blad(blad).onderdeel)
    except PakketFout:
        return []
    return sorted(set(formulecellen(xml)) - gevuld, key=lambda c: (len(c), c))


def analyseer(pakket: Pakket, snap) -> dict:
    bladen = pakket.bladen()
    namen = [b.naam for b in bladen]
    bereiken = _afdrukbereiken(pakket)
    rondeblad = snap["afdrukken.rondeblad_sjabloonblad"]
    kaartblad = snap["afdrukken.teeltkaart_blad"]
    meldingen = []
    for blad, soort in ((rondeblad, "rondeblad"), (kaartblad, "teeltkaart")):
        if blad not in namen:
            meldingen.append(f"Het {soort} {blad!r} staat niet in het sjabloon (instelling Afdrukken).")
    for blad, sleutel in ((rondeblad, "afdrukken.rondeblad_bereik"), (kaartblad, "afdrukken.teeltkaart_bereik")):
        if blad in bereiken and bereiken[blad] != snap[sleutel]:
            meldingen.append(f"Afdrukbereik van {blad!r} in het sjabloon is {bereiken[blad]}, de instelling is "
                             f"{snap[sleutel]}; de app gebruikt de instelling.")  # fmt: skip
    lettertypen = gebruikte_lettertypen(pakket)
    return {
        "bladen": namen,
        "afdrukbereiken": bereiken,
        "lettertypen": lettertypen,
        "formules_niet_gevuld": {
            "rondeblad": niet_gevulde_formules(pakket, rondeblad, _gevulde_cellen_rondeblad(snap)),
            "teeltkaart": niet_gevulde_formules(pakket, kaartblad, _gevulde_cellen_kaart(snap)),
        },
        "meldingen": meldingen,
    }


# --- Lettertypen uploaden ------------------------------------------------------------------------------------
def sla_lettertype_op(db: Session, gebruiker: Gebruiker, bestandsnaam: str, inhoud: bytes) -> Path:
    snap = instellingen.laad(db)
    naam = Path(bestandsnaam).name
    if not naam.lower().endswith(LETTERTYPE_EXTENSIES):
        raise ValueError("Alleen lettertypebestanden (.ttf, .otf, .ttc).")
    if len(inhoud) > snap["afdrukken.max_lettertype_mb"] * 1024 * 1024:
        raise ValueError("Het lettertypebestand is te groot.")
    if not inhoud.startswith(LETTERTYPE_MAGIC):
        raise ValueError("Dit is geen TrueType- of OpenType-lettertype.")
    veilig = re.sub(r"[^A-Za-z0-9._ -]", "_", naam).strip(". ") or "lettertype.ttf"
    doel = lettertypemap() / veilig
    doel.write_bytes(inhoud)
    audit(db, gebruiker, "lettertype_geupload", f"lettertype:{veilig}",
          {"grootte": len(inhoud), "sha256": hashlib.sha256(inhoud).hexdigest()})  # fmt: skip
    return doel


def lettertypebestanden() -> list[str]:
    return sorted(p.name for p in lettertypemap().iterdir() if p.suffix.lower() in LETTERTYPE_EXTENSIES)
