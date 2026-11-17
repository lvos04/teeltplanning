"""Optionele TOTP-tweestapsverificatie (RFC 6238), overgenomen uit Gewichtsverwachting `app/mfa.py`.

Standaard uit. Een gebruiker zet het zelf aan via Beheer → Tweestapsverificatie; een beheerder kan het voor een
ander account resetten, en via `python -m teeltplanning.beheer_cli mfa-uit <naam>` kan het bij verlies van de
telefoon worden uitgezet.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import secrets
import struct
import time
from urllib.parse import quote

from .db.modellen import Gebruiker

UITGEVER = "Teeltplanning"
STAP = 30
CIJFERS = 6
SPELING = 1  # één stap (30 s) speling voor klokverschil


def nieuw_geheim() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _sleutel(geheim: str) -> bytes:
    return base64.b32decode(geheim + "=" * (-len(geheim) % 8), casefold=True)


def totp(geheim: str, stap: int) -> str:
    digest = hmac.new(_sleutel(geheim), struct.pack(">Q", stap), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    waarde = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(waarde % 10**CIJFERS).zfill(CIJFERS)


def passende_stap(geheim: str, code: str, nu: float | None = None) -> int | None:
    code = (code or "").strip().replace(" ", "")
    if not geheim or len(code) != CIJFERS or not code.isdigit():
        return None
    huidig = int((time.time() if nu is None else nu) // STAP)
    for stap in range(huidig - SPELING, huidig + SPELING + 1):
        if hmac.compare_digest(totp(geheim, stap), code):
            return stap
    return None


def controleer_code(gebruiker: Gebruiker, code: str, nu: float | None = None) -> bool:
    """Controleer de code en voorkom hergebruik van dezelfde (of een oudere) tijdstap."""
    stap = passende_stap(gebruiker.mfa_geheim or "", code, nu)
    if stap is None or (gebruiker.mfa_laatste_stap is not None and stap <= gebruiker.mfa_laatste_stap):
        return False
    gebruiker.mfa_laatste_stap = stap
    return True


def otpauth_uri(gebruiker: Gebruiker, geheim: str) -> str:
    label = quote(f"{UITGEVER}:{gebruiker.gebruikersnaam}")
    return (
        f"otpauth://totp/{label}?secret={geheim}&issuer={quote(UITGEVER)}&algorithm=SHA1&digits={CIJFERS}&period={STAP}"
    )


def qr_svg(tekst: str) -> str | None:
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:  # pragma: no cover
        return None
    beeld = qrcode.make(tekst, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buffer = io.BytesIO()
    beeld.save(buffer)
    return "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode()


def reset(db, doel: Gebruiker, door: Gebruiker | None) -> None:
    from .auth import audit

    doel.mfa_aan = False
    doel.mfa_geheim = None
    doel.mfa_laatste_stap = None
    audit(
        db,
        door,
        "mfa_reset",
        f"gebruiker:{doel.id}",
        {"gebruikersnaam": doel.gebruikersnaam, **({} if door else {"via": "cli"})},
    )
