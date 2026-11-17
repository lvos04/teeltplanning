"""Pdf → grijswaardenafbeelding en pixelvergelijking, voor het overleggen van afdrukken (§9.3).

Gebruikt `pdftoppm` (poppler-utils); dat zit niet in de image, alleen in de test- en controleomgeving.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def pdf_naar_beeld(pdf_bytes: bytes, dpi: int = 60) -> tuple[int, int, bytes]:
    """Eerste pagina als grijswaardenafbeelding (breedte, hoogte, pixels) via pdftoppm."""
    programma = shutil.which("pdftoppm")
    if not programma:
        raise RuntimeError("pdftoppm (poppler-utils) is niet geïnstalleerd")
    with tempfile.TemporaryDirectory() as map_:
        bron = Path(map_) / "a.pdf"
        bron.write_bytes(pdf_bytes)
        subprocess.run([programma, "-r", str(dpi), "-gray", "-singlefile", str(bron), str(Path(map_) / "beeld")],
                       check=True, capture_output=True, timeout=300)  # fmt: skip
        data = (Path(map_) / "beeld.pgm").read_bytes()
    # PGM (P5): kop "P5\n<b> <h>\n<max>\n" gevolgd door de pixels
    delen = data.split(maxsplit=4)
    breedte, hoogte = int(delen[1]), int(delen[2])
    return breedte, hoogte, delen[4][-breedte * hoogte :]


def beeldverschil(a: tuple[int, int, bytes], b: tuple[int, int, bytes]) -> float:
    """Fractie van de pixels die verschillen (1 als de afmetingen verschillen)."""
    if a[:2] != b[:2]:
        return 1
    verschillend = sum(1 for x, y in zip(a[2], b[2], strict=True) if x != y)
    return verschillend / len(a[2])
