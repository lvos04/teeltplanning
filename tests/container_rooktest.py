"""Rooktest tegen een draaiende container (lokaal en in CI): eerste beheerder, synthetisch werkboek importeren,
sjabloon uploaden en rondeblad + teeltkaart als pdf ophalen (LibreOffice in de container).

Gebruik: ``python tests/container_rooktest.py http://127.0.0.1:8000 <werkmap>``
Alleen standaardbibliotheek + de generator van de synthetische werkboeken; geen bedrijfsgegevens.
"""

from __future__ import annotations

import http.cookiejar
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.synthetisch import BELICHTING_NAAM, PROGRAMMA_NAAM, maak_belichting, maak_programma  # noqa: E402

WACHTWOORD = "rooktest-wachtwoord-123"


class Sessie:
    def __init__(self, basis: str):
        self.basis = basis.rstrip("/")
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), _GeenDoorverwijzing()
        )

    def verzoek(self, pad: str, data: bytes | None = None, koppen: dict | None = None, methode: str | None = None):
        koppen = {"Origin": self.basis, **(koppen or {})}
        req = urllib.request.Request(self.basis + pad, data=data, headers=koppen, method=methode)
        try:
            with self.opener.open(req, timeout=300) as antwoord:
                return antwoord.status, dict(antwoord.headers), antwoord.read()
        except urllib.error.HTTPError as fout:
            return fout.code, dict(fout.headers), fout.read()

    def formulier(self, pad: str, velden: dict):
        return self.verzoek(pad, urllib.parse.urlencode(velden).encode(),
                            {"Content-Type": "application/x-www-form-urlencoded"}, "POST")  # fmt: skip

    def bestand(self, pad: str, naam: str, inhoud: bytes):
        grens = uuid.uuid4().hex
        lichaam = (f'--{grens}\r\nContent-Disposition: form-data; name="bestand"; filename="{naam}"\r\n'
                   "Content-Type: application/octet-stream\r\n\r\n").encode() + inhoud + f"\r\n--{grens}--\r\n".encode()  # fmt: skip
        return self.verzoek(pad, lichaam, {"Content-Type": f"multipart/form-data; boundary={grens}"}, "POST")


class _GeenDoorverwijzing(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def paginas(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page(?!s)", pdf))


def main(basis: str, map_: Path) -> int:
    map_.mkdir(parents=True, exist_ok=True)
    meta = maak_programma(map_ / PROGRAMMA_NAAM)
    werkboek = (map_ / PROGRAMMA_NAAM).read_bytes()
    s = Sessie(basis)
    for _ in range(60):
        if s.verzoek("/health")[0] == 200:
            break
        time.sleep(2)
    print("health:", s.verzoek("/health")[2].decode())
    status, koppen, _ = s.formulier("/eerste-start", {"gebruikersnaam": "rooktest", "wachtwoord": WACHTWOORD})
    assert status == 303, status
    status, koppen, _ = s.bestand("/import/upload", PROGRAMMA_NAAM, werkboek)
    assert status == 303, status
    batch = koppen["location"].rsplit("/", 1)[1]
    s.formulier(f"/import/{batch}/jaren", {f"jaar_{n}": j for n, j in meta["jaren"].items()})
    status, _, _ = s.formulier(f"/import/{batch}/bevestig", {})
    assert status == 303, status
    status, koppen, _ = s.bestand("/beheer/sjabloon/upload", PROGRAMMA_NAAM, werkboek)
    assert status == 303 and "opgeslagen" in urllib.parse.unquote(koppen["location"]), koppen
    uitkomst = {}
    for pad, verwacht in (("/afdruk/rondeblad.pdf?ronde=3", 1), ("/afdruk/teeltkaart.pdf?ronde=3&kap=7", 1),
                          ("/afdruk/teeltkaart.pdf?ronde=3", 20)):  # fmt: skip
        start = time.time()
        status, koppen, inhoud = s.verzoek(pad)
        assert status == 200 and koppen.get("content-type") == "application/pdf", (pad, status, inhoud[:300])
        assert paginas(inhoud) == verwacht, (pad, paginas(inhoud))
        uitkomst[pad] = {"paginas": paginas(inhoud), "bytes": len(inhoud), "seconden": round(time.time() - start, 1)}
        (map_ / (re.sub(r"\W+", "_", pad) + ".pdf")).write_bytes(inhoud)
    status, _, inhoud = s.verzoek("/afdruk/teeltkaart.xlsx?ronde=3&kap=7")
    assert status == 200 and inhoud[:2] == b"PK"
    # Fase 3: belichting importeren (achtergrondtaak in de container), energie- en LBE-schermen
    maak_belichting(map_ / BELICHTING_NAAM)
    status, koppen, inhoud = s.bestand(
        "/import/belichting/upload", BELICHTING_NAAM, (map_ / BELICHTING_NAAM).read_bytes()
    )
    assert status == 303, (status, inhoud[:300])
    pad = urllib.parse.urlparse(koppen["location"]).path
    s.formulier(pad + "/tijdstempel", {"waarde": "begin"})
    for _ in range(120):
        if b"Bezig met lezen" not in s.verzoek(pad)[2]:
            break
        time.sleep(1)
    s.formulier(pad + "/keuzes", {"jaar_weekuren": "2023", "jaar_teeltschema": "2023"})
    status, koppen, _ = s.formulier(pad + "/bevestig", {})
    uitkomst["belichting"] = urllib.parse.unquote(koppen.get("location", ""))
    assert "belichtingsdagen" in uitkomst["belichting"], uitkomst
    for pad in ("/energie?jaar=2024", "/beheer/lbe", "/beheer/bronnen", "/energie/vergelijking"):
        assert s.verzoek(pad)[0] == 200, pad
    print(json.dumps(uitkomst, indent=1, ensure_ascii=False))
    print("rooktest geslaagd")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], Path(sys.argv[2] if len(sys.argv) > 2 else "rooktest-uitvoer")))
