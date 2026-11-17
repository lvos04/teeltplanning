"""Route B (BOUWPLAN §9.3): het gevulde sjabloon in de container met LibreOffice omzetten naar pdf.

* Afzonderlijk proces met een tijdslimiet (instelling *Afdrukken → Tijdslimiet pdf-conversie*); bij overschrijding
  wordt de hele procesgroep gestopt.
* Per aanroep een eigen tijdelijke profielmap onder `DATA_DIR/tmp/`, die altijd wordt opgeruimd.
* Eén conversie tegelijk (wachtrij via een slot); de webapp draait met één worker.
* Zonder netwerk: externe koppelingen zitten niet in de afdrukkopie (`xlsxpakket.bouw_kopie`); het proces krijgt
  een proxy die nergens heen wijst, en als het besturingssysteem het toestaat draait het in een eigen
  netwerknaamruimte zonder netwerkkaart (`unshare -rn`).
* Lettertypen uit `DATA_DIR/lettertypen/` worden via een eigen fontconfig-bestand meegegeven.
* Draait als dezelfde gebruiker als de app (in de image: 568).
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
from functools import cache
from pathlib import Path

from ..db.basis import DATA_DIR

_SLOT = threading.Lock()
NERGENS = "http://127.0.0.1:9"  # discard-poort: elk netwerkverzoek mislukt direct


class ConversieFout(RuntimeError):
    pass


def soffice() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def lettertypemap() -> Path:
    map_ = DATA_DIR / "lettertypen"
    map_.mkdir(exist_ok=True)
    return map_


@cache
def _unshare_werkt() -> bool:
    pad = shutil.which("unshare")
    if not pad:
        return False
    try:
        return subprocess.run([pad, "-rn", "true"], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _fontconfig(profiel: Path) -> Path:
    conf = profiel / "fonts.conf"
    conf.write_text(
        '<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        '<include ignore_missing="yes">/etc/fonts/fonts.conf</include>'
        f"<dir>{lettertypemap()}</dir><cachedir>{profiel / 'fontcache'}</cachedir></fontconfig>",
        encoding="utf-8",
    )
    return conf


def _registry(profiel: Path, herberekenen: bool) -> None:
    """Profielinstellingen: geen herstelvragen; bij `herberekenen` formules van xlsx-bestanden altijd herrekenen."""
    gebruiker = profiel / "user"
    gebruiker.mkdir(parents=True, exist_ok=True)
    items = []
    if herberekenen:
        for sleutel in ("OOXMLRecalcMode", "ODFRecalcMode"):
            items.append(f'<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="{sleutel}" '
                         'oor:op="fuse"><value>0</value></prop></item>')  # fmt: skip
    (gebruiker / "registrymodifications.xcu").write_text(
        '<?xml version="1.0" encoding="UTF-8"?><oor:items xmlns:oor="http://openoffice.org/2001/registry" '
        'xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        + "".join(items)
        + "</oor:items>",
        encoding="utf-8",
    )


def converteer(xlsx: bytes, doelformaat: str, tijdslimiet: int, herberekenen: bool = False) -> bytes:
    """Zet een xlsx (als bytes) om naar `doelformaat` ('pdf' of 'xlsx'). `ConversieFout` met een melding."""
    programma = soffice()
    if not programma:
        raise ConversieFout("LibreOffice (soffice) is niet geïnstalleerd.")
    tmp = DATA_DIR / "tmp"
    tmp.mkdir(exist_ok=True)
    with _SLOT:
        werkmap = Path(tempfile.mkdtemp(prefix="afdruk-", dir=tmp))
        try:
            profiel = werkmap / "profiel"
            uit = werkmap / "uit"
            profiel.mkdir()
            uit.mkdir()
            bron = werkmap / "afdruk.xlsx"
            bron.write_bytes(xlsx)
            _registry(profiel, herberekenen)
            filter_ = "pdf:calc_pdf_Export" if doelformaat == "pdf" else "xlsx:Calc MS Excel 2007 XML"
            opdracht = [programma, f"-env:UserInstallation=file://{profiel}", "--headless", "--norestore",
                        "--nologo", "--nolockcheck", "--convert-to", filter_, "--outdir", str(uit), str(bron)]  # fmt: skip
            if _unshare_werkt():
                opdracht = [shutil.which("unshare"), "-rn", *opdracht]
            omgeving = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": str(werkmap),
                "TMPDIR": str(werkmap),
                "LANG": "C.UTF-8",
                "TZ": os.environ.get("TZ", "Europe/Amsterdam"),
                "FONTCONFIG_FILE": str(_fontconfig(profiel)),
                "http_proxy": NERGENS, "https_proxy": NERGENS, "HTTP_PROXY": NERGENS, "HTTPS_PROXY": NERGENS,
                "no_proxy": "", "NO_PROXY": "",
            }  # fmt: skip
            proces = subprocess.Popen(opdracht, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=omgeving,
                                      cwd=werkmap, start_new_session=True)  # fmt: skip
            try:
                _, fout = proces.communicate(timeout=tijdslimiet)
            except subprocess.TimeoutExpired as exc:
                try:
                    os.killpg(proces.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proces.communicate()
                raise ConversieFout(f"De pdf-conversie duurde langer dan {tijdslimiet} s en is afgebroken.") from exc
            resultaat = uit / f"afdruk.{doelformaat}"
            if proces.returncode != 0 or not resultaat.is_file() or resultaat.stat().st_size == 0:
                tekst = fout.decode("utf-8", "replace").strip().splitlines()
                raise ConversieFout(
                    "LibreOffice kon het bestand niet omzetten" + (f": {tekst[-1][:100]}" if tekst else ".")
                )
            return resultaat.read_bytes()
        finally:
            shutil.rmtree(werkmap, ignore_errors=True)


def paginas(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page(?!s)", pdf))
