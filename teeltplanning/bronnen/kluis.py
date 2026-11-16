"""Versleuteling van geheime bronsleutels met Fernet, zoals het Bedrijfsdashboard (`app/versleuteling.py`).

De Fernet-sleutel wordt bij het eerste gebruik gemaakt in DATA_DIR/geheim.key (rechten 0600) en gaat mee in de
back-up; zonder dat bestand zijn opgeslagen sleutels niet meer te lezen (opnieuw invullen in Beheer → Bronnen).
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

from ..db.basis import DATA_DIR


def _fernet() -> Fernet:
    pad = DATA_DIR / "geheim.key"
    if not pad.exists():
        sleutel = Fernet.generate_key()
        try:
            fd = os.open(pad, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(sleutel)
        except FileExistsError:
            pass
    return Fernet(pad.read_bytes().strip())


def versleutel(tekst: str) -> bytes:
    return _fernet().encrypt(tekst.encode("utf-8"))


def ontsleutel(data: bytes) -> str:
    try:
        return _fernet().decrypt(bytes(data)).decode("utf-8")
    except InvalidToken as fout:
        raise ValueError(
            "Sleutel kan niet worden ontsleuteld (is geheim.key vervangen?). Vul hem opnieuw in."
        ) from fout
