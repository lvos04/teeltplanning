"""Aanmelden, sessies, rollen en auditlog (overgenomen uit Gewichtsverwachting `app/auth.py`, BOUWPLAN §12).

Zelfde gedrag: eerste beheerder bij eerste start (geen standaardaccounts), wachtwoord minimaal twaalf tekens,
scrypt-hashes, HTTP-only sessiecookies (SameSite=strict), strikte oorsprongcontrole op schrijfverzoeken,
aanmeldbeperking per account én per echt IP (vertrouwde proxy's), optionele MFA en "MFA verplicht voor
beheerders". Rollen: beheerder (alles), medewerker (plan bewerken, importeren), lezer (bekijken en afdrukken).
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import secrets
import time
from datetime import timedelta
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import instellingen
from .db.basis import get_db
from .db.modellen import Aanmeldpoging, Audit, Gebruiker, MfaStap, Sessie, utcnu

COOKIE = "teelt_sessie"
MFA_STAP_MINUTEN = 5
MFA_STAP_POGINGEN = 5
MIN_WACHTWOORD = 12
ROLLEN = ("beheerder", "medewerker", "lezer")


class NietAangemeld(Exception):
    """Wordt in main.py vertaald naar een doorverwijzing naar /aanmelden (pagina's) of 401 (API)."""


def audit(db: Session, gebruiker: Gebruiker | None, actie: str, onderwerp: str, details=None) -> None:
    db.add(
        Audit(gebruiker_id=gebruiker.id if gebruiker else None, actie=actie, onderwerp=str(onderwerp), details=details)
    )


def hash_wachtwoord(wachtwoord: str) -> str:
    zout = os.urandom(16)
    digest = hashlib.scrypt(wachtwoord.encode(), salt=zout, n=16384, r=8, p=1)
    return f"scrypt$16384$8$1${zout.hex()}${digest.hex()}"


def controleer_wachtwoord(wachtwoord: str, opgeslagen: str) -> bool:
    try:
        algoritme, n, r, p, zout, digest = opgeslagen.split("$")
        if algoritme != "scrypt":
            return False
        berekend = hashlib.scrypt(wachtwoord.encode(), salt=bytes.fromhex(zout), n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(berekend.hex(), digest)
    except (ValueError, TypeError):
        return False


def controleer_nieuw_wachtwoord(wachtwoord: str) -> None:
    if len(wachtwoord or "") < MIN_WACHTWOORD:
        raise HTTPException(422, f"Kies een wachtwoord van minimaal {MIN_WACHTWOORD} tekens.")


# --- Oorsprongcontrole ---------------------------------------------------------------------------------------
def _zelfde_host(waarde: str, request: Request) -> bool:
    deel = urlsplit(waarde)
    # Doorgestuurde hosts worden bewust niet vertrouwd.
    return deel.scheme in ("http", "https") and deel.netloc.lower() == request.headers.get("host", "").lower()


def controleer_oorsprong(request: Request, strikt: bool) -> None:
    """Weiger cross-site schrijfverzoeken (Origin altijd; strikt: zonder Origin een Referer van dezelfde host)."""
    origin = request.headers.get("origin")
    if origin:
        if not _zelfde_host(origin, request) or urlsplit(origin).path not in ("", "/"):
            raise HTTPException(403, "Ongeldige oorsprong")
        return
    if not strikt:
        return
    referer = request.headers.get("referer")
    if not referer or not _zelfde_host(referer, request):
        raise HTTPException(403, "Ongeldige of ontbrekende oorsprong")


# --- Echt IP-adres achter een vertrouwde proxy ---------------------------------------------------------------
def lees_proxys(waarde: str) -> list:
    netwerken = []
    for deel in (waarde or "").replace(";", ",").split(","):
        deel = deel.strip()
        if not deel:
            continue
        try:
            netwerken.append(ipaddress.ip_network(deel, strict=False))
        except ValueError:
            continue
    return netwerken


def vertrouwde_proxys(snap) -> list:
    return lees_proxys(snap["beveiliging.vertrouwde_proxys"] or os.environ.get("TRUSTED_PROXIES", ""))


def _in(netwerken, waarde) -> bool:
    try:
        adres = ipaddress.ip_address(waarde)
    except ValueError:
        return False
    return any(adres in n for n in netwerken)


def client_ip(request: Request, netwerken) -> str:
    """Directe verbinding is leidend; `X-Forwarded-For` alleen van vertrouwde proxy's, van rechts gelezen."""
    peer = request.client.host if request.client else "onbekend"
    if not netwerken or not _in(netwerken, peer):
        return peer
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    for hop in reversed(hops):
        try:
            ipaddress.ip_address(hop)
        except ValueError:
            return peer
        if not _in(netwerken, hop):
            return hop
    return hops[0] if hops else peer


# --- Sessies -------------------------------------------------------------------------------------------------
def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def sessie_gebruiker(request: Request, db: Session) -> Gebruiker | None:
    token = request.cookies.get(COOKIE)
    sessie = db.get(Sessie, _sha(token)) if token else None
    gebruiker = db.get(Gebruiker, sessie.gebruiker_id) if sessie and sessie.verloopt_op > utcnu() else None
    return gebruiker if gebruiker and gebruiker.actief else None


def maak_sessie(db: Session, response, gebruiker: Gebruiker, request: Request) -> None:
    uren = instellingen.laad(db)["beveiliging.sessie_uren"]
    token = secrets.token_urlsafe(32)
    db.add(Sessie(token_hash=_sha(token), gebruiker_id=gebruiker.id, verloopt_op=utcnu() + timedelta(hours=uren)))
    veilig = request.url.scheme == "https" or os.environ.get("SECURE_COOKIES", "").lower() in ("1", "true")
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=veilig, max_age=uren * 3600, path="/")


def beeindig_sessie(db: Session, request: Request) -> None:
    token = request.cookies.get(COOKIE)
    if token:
        db.execute(delete(Sessie).where(Sessie.token_hash == _sha(token)))


def mfa_instellen_verplicht(db: Session, gebruiker: Gebruiker) -> bool:
    if gebruiker.rol != "beheerder" or gebruiker.mfa_aan:
        return False
    return bool(instellingen.laad(db)["beveiliging.mfa_verplicht_beheerders"])


# --- Afhankelijkheden voor routes -----------------------------------------------------------------------------
def vereis_lezen(request: Request, db: Session = Depends(get_db)) -> Gebruiker:
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        controleer_oorsprong(request, instellingen.laad(db)["beveiliging.strikte_oorsprong"])
    gebruiker = sessie_gebruiker(request, db)
    if not gebruiker:
        raise NietAangemeld()
    return gebruiker


def vereis_schrijven(gebruiker: Gebruiker = Depends(vereis_lezen)) -> Gebruiker:
    if gebruiker.rol not in ("beheerder", "medewerker"):
        raise HTTPException(403, "Je hebt alleen leesrechten.")
    return gebruiker


def vereis_beheer(gebruiker: Gebruiker = Depends(vereis_lezen), db: Session = Depends(get_db)) -> Gebruiker:
    if gebruiker.rol != "beheerder":
        raise HTTPException(403, "Beheerrechten vereist.")
    if mfa_instellen_verplicht(db, gebruiker):
        raise HTTPException(
            403,
            "Tweestapsverificatie is verplicht voor beheerders. Stel deze eerst in bij Beheer → Tweestapsverificatie.",
        )
    return gebruiker


# --- Aanmeldbeperking ---------------------------------------------------------------------------------------
def limietsleutels(request: Request, db: Session, gebruikersnaam: str):
    snap = instellingen.laad(db)
    ip = client_ip(request, vertrouwde_proxys(snap))
    return snap, "ip:" + ip, "gebruiker:" + gebruikersnaam.strip().casefold()


def controleer_limieten(db: Session, snap, ip_sleutel: str, gebruiker_sleutel: str) -> int:
    grens = utcnu() - timedelta(minutes=snap["beveiliging.venster_minuten"])
    tellingen = dict(
        db.execute(
            select(Aanmeldpoging.sleutel, func.count())
            .where(Aanmeldpoging.sleutel.in_([ip_sleutel, gebruiker_sleutel]), Aanmeldpoging.op >= grens)
            .group_by(Aanmeldpoging.sleutel)
        ).all()
    )
    per_account = snap["beveiliging.inlogpogingen_account"]
    per_ip = snap["beveiliging.inlogpogingen_ip"] or per_account
    if tellingen.get(gebruiker_sleutel, 0) >= per_account or tellingen.get(ip_sleutel, 0) >= per_ip:
        raise HTTPException(429, "Te veel aanmeldpogingen; probeer het later opnieuw.")
    return tellingen.get(gebruiker_sleutel, 0)


def registreer_fout(db: Session, snap, sleutels, eerdere: int, gebruikersnaam: str, reden: str = "aanmelden_mislukt"):
    db.add_all([Aanmeldpoging(sleutel=s) for s in sleutels])
    audit(db, None, reden, "aanmelden", {"gebruikersnaam": gebruikersnaam.strip()[:80]})
    db.commit()
    vertraging = min(snap["beveiliging.vertraging_max_s"], 0.25 * eerdere)
    if vertraging > 0:
        time.sleep(vertraging)


def wis_pogingen(db: Session, sleutels) -> None:
    db.execute(delete(Aanmeldpoging).where(Aanmeldpoging.sleutel.in_(list(sleutels))))


def begin_mfa_stap(db: Session, gebruiker: Gebruiker) -> str:
    token = secrets.token_urlsafe(32)
    db.add(
        MfaStap(
            token_hash=_sha(token), gebruiker_id=gebruiker.id, verloopt_op=utcnu() + timedelta(minutes=MFA_STAP_MINUTEN)
        )
    )
    return token


def mfa_stap(db: Session, token: str) -> MfaStap | None:
    stap = db.get(MfaStap, _sha(token or ""))
    if not stap or stap.verloopt_op <= utcnu() or stap.pogingen >= MFA_STAP_POGINGEN:
        return None
    return stap


def herbevestig(db: Session, gebruiker: Gebruiker, wachtwoord: str, code: str | None) -> None:
    """Opnieuw bevestigen bij gevoelige acties (back-up terugzetten, volledige export, downloaden van back-ups)."""
    from .mfa import controleer_code

    if not controleer_wachtwoord(wachtwoord or "", gebruiker.wachtwoord_hash):
        audit(db, gebruiker, "herbevestiging_mislukt", f"gebruiker:{gebruiker.id}")
        db.commit()
        raise HTTPException(403, "Wachtwoord klopt niet.")
    if gebruiker.mfa_aan and not controleer_code(gebruiker, code or ""):
        audit(db, gebruiker, "herbevestiging_mislukt", f"gebruiker:{gebruiker.id}", {"mfa": True})
        db.commit()
        raise HTTPException(403, "Verificatiecode klopt niet.")
