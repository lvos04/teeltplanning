"""Eén plek voor uitgaande HTTP-verzoeken van de bronnen (§10): tijdslimiet, nette foutmelding, JSON.

Tests vervangen `haal` door een nep-functie; een autouse-fixture in tests/conftest.py laat elk echt verzoek
mislukken, zodat er in tests nooit een netwerkverbinding wordt gemaakt.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .. import __version__


class BronFout(RuntimeError):
    def __init__(self, melding: str, status: int | None = None):
        super().__init__(melding)
        self.status = status


def _open(verzoek: urllib.request.Request, tijdslimiet: float) -> tuple[int, bytes]:
    with urllib.request.urlopen(verzoek, timeout=tijdslimiet) as antwoord:  # noqa: S310 - adres uit de instellingen
        return antwoord.status, antwoord.read()


def haal(url: str, parameters: dict | None = None, koppen: dict | None = None, tijdslimiet: float = 30) -> bytes:
    if not url.startswith(("https://", "http://")):
        raise BronFout("Ongeldig adres (alleen http of https).")
    if parameters:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(parameters)
    verzoek = urllib.request.Request(url, headers={"User-Agent": f"Teeltplanning/{__version__}", **(koppen or {})})
    try:
        status, inhoud = _open(verzoek, tijdslimiet)
    except urllib.error.HTTPError as fout:
        raise BronFout(f"De bron antwoordde met HTTP {fout.code}.", fout.code) from fout
    except (urllib.error.URLError, TimeoutError, OSError) as fout:
        raise BronFout(f"Geen verbinding met de bron: {getattr(fout, 'reason', fout)}.") from fout
    if status >= 400:
        raise BronFout(f"De bron antwoordde met HTTP {status}.", status)
    return inhoud


def haal_json(url: str, parameters: dict | None = None, koppen: dict | None = None, tijdslimiet: float = 30):
    inhoud = haal(url, parameters, koppen, tijdslimiet)
    try:
        return json.loads(inhoud)
    except json.JSONDecodeError as fout:
        raise BronFout("De bron gaf geen geldige JSON terug.") from fout
