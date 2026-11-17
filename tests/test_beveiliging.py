"""Beveiliging en beheer zoals Gewichtsverwachting (BOUWPLAN §12)."""

import io
import time
import zipfile

from fastapi.testclient import TestClient
from sqlalchemy import select

from teeltplanning import mfa
from teeltplanning.db.basis import DATA_DIR, SessionLocal
from teeltplanning.db.modellen import Audit, Gebruiker
from tests.conftest import WACHTWOORD, maak_gebruiker


def test_eerste_start_eenmalig_en_wachtwoordlengte(client):
    assert client.get("/", follow_redirects=False).headers["location"] == "/eerste-start"
    assert client.post("/eerste-start", data={"gebruikersnaam": "a", "wachtwoord": "kort"}).status_code == 422
    antwoord = client.post(
        "/eerste-start", data={"gebruikersnaam": "leon", "wachtwoord": WACHTWOORD}, follow_redirects=False
    )
    assert antwoord.status_code == 303
    cookie = antwoord.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.post("/eerste-start", data={"gebruikersnaam": "x", "wachtwoord": WACHTWOORD}).status_code == 409
    with SessionLocal() as db:
        assert db.scalar(select(Gebruiker)).rol == "beheerder"
        assert db.scalar(select(Gebruiker)).wachtwoord_hash.startswith("scrypt$")


def test_niet_aangemeld(client):
    client.post("/eerste-start", data={"gebruikersnaam": "leon", "wachtwoord": WACHTWOORD})
    client.post("/afmelden")
    assert (
        client.get("/ronde", headers={"accept": "text/html"}, follow_redirects=False).headers["location"]
        == "/aanmelden"
    )
    assert client.get("/ronde", headers={"accept": "application/json"}).status_code == 401


def test_strikte_oorsprongcontrole(beheerder):
    from teeltplanning.main import app

    with TestClient(app, headers={"origin": "https://kwaad.example"}) as vreemd:
        vreemd.cookies = beheerder.cookies
        assert vreemd.post("/beheer/backup/maken").status_code == 403
    zonder = TestClient(app, headers={"origin": ""})
    zonder.headers.pop("origin")
    zonder.cookies = beheerder.cookies
    assert zonder.post("/beheer/backup/maken").status_code == 403  # geen Origin en geen Referer
    assert (
        zonder.post(
            "/beheer/backup/maken", headers={"referer": "http://testserver/beheer"}, follow_redirects=False
        ).status_code
        == 303
    )


def test_aanmeldbeperking_per_account_en_ip(beheerder):
    c = beheerder
    c.post("/afmelden")
    for _ in range(5):
        assert c.post("/aanmelden", data={"gebruikersnaam": "leon", "wachtwoord": "fout-fout-fout"}).status_code == 401
    assert c.post("/aanmelden", data={"gebruikersnaam": "leon", "wachtwoord": WACHTWOORD}).status_code == 429
    # Een ander account achter hetzelfde IP is niet geblokkeerd (IP-limiet 25 is ruimer).
    assert c.post("/aanmelden", data={"gebruikersnaam": "iemand", "wachtwoord": "fout-fout-fout"}).status_code == 401
    with SessionLocal() as db:
        assert db.scalar(select(Audit).where(Audit.actie == "aanmelden_mislukt")) is not None


def test_echt_ip_alleen_via_vertrouwde_proxy():
    from starlette.requests import Request

    from teeltplanning.auth import client_ip, lees_proxys

    def verzoek(peer, xff):
        return Request({"type": "http", "client": (peer, 1), "headers": [(b"x-forwarded-for", xff.encode())]})

    assert client_ip(verzoek("10.0.0.5", "1.2.3.4"), []) == "10.0.0.5"
    proxys = lees_proxys("10.0.0.0/8")
    assert client_ip(verzoek("10.0.0.5", "6.6.6.6, 1.2.3.4"), proxys) == "1.2.3.4"
    assert client_ip(verzoek("8.8.8.8", "1.2.3.4"), proxys) == "8.8.8.8"


def test_mfa_optioneel_en_verplicht_voor_beheerders(beheerder):
    c = beheerder
    # Verplicht zetten zonder eigen MFA wordt geweigerd (je sluit jezelf buiten).
    antwoord = c.post(
        "/beheer/instellingen/beveiliging.mfa_verplicht_beheerders", data={"waarde": "aan"}, follow_redirects=False
    )
    assert "Niet" in antwoord.headers["location"]
    c.post("/beheer/mfa/start")
    with SessionLocal() as db:
        geheim = db.scalar(select(Gebruiker)).mfa_geheim
    stap = int(time.time() // mfa.STAP)
    assert (
        "aan"
        in c.post("/beheer/mfa/aan", data={"code": mfa.totp(geheim, stap)}, follow_redirects=False).headers["location"]
    )
    c.post("/afmelden")
    eerste = c.post("/aanmelden", data={"gebruikersnaam": "leon", "wachtwoord": WACHTWOORD})
    assert "Verificatiecode" in eerste.text
    token = eerste.text.split('name="stap" value="')[1].split('"')[0]
    assert c.post("/aanmelden/mfa", data={"stap": token, "code": "000000"}).status_code == 401
    # Dezelfde tijdstap mag niet opnieuw worden gebruikt; de volgende wel.
    assert c.post("/aanmelden/mfa", data={"stap": token, "code": mfa.totp(geheim, stap)}).status_code == 401
    ok = c.post("/aanmelden/mfa", data={"stap": token, "code": mfa.totp(geheim, stap + 1)}, follow_redirects=False)
    assert ok.status_code == 303
    assert (
        c.post(
            "/beheer/instellingen/beveiliging.mfa_verplicht_beheerders", data={"waarde": "aan"}, follow_redirects=False
        ).status_code
        == 303
    )
    maak_gebruiker(c, "tweede", "beheerder")
    c.post("/afmelden")
    c.post("/aanmelden", data={"gebruikersnaam": "tweede", "wachtwoord": WACHTWOORD})
    assert c.get("/beheer/instellingen").status_code == 403  # eerst MFA instellen
    assert c.get("/beheer/mfa").status_code == 200


def test_herbevestiging_export_en_backup(beheerder):
    c = beheerder
    assert c.post("/beheer/export", data={"wachtwoord": "fout"}).status_code == 403
    export = c.post("/beheer/export", data={"wachtwoord": WACHTWOORD})
    assert export.status_code == 200
    namen = zipfile.ZipFile(io.BytesIO(export.content)).namelist()
    assert "instellingen.json" in namen and "gebruiker.csv" not in namen and "sessie.csv" not in namen
    assert c.post("/beheer/backup/maken", follow_redirects=False).status_code == 303
    archief = sorted((DATA_DIR / "backups").glob("*.zip"))[-1]
    assert c.post("/beheer/backup/download", data={"naam": archief.name, "wachtwoord": "fout"}).status_code == 403
    download = c.post("/beheer/backup/download", data={"naam": archief.name, "wachtwoord": WACHTWOORD})
    assert download.status_code == 200 and download.content[:2] == b"PK"
    assert c.post("/beheer/backup/download", data={"naam": "../app.db", "wachtwoord": WACHTWOORD}).status_code == 404
    # Terugzetten: zonder herbevestiging geweigerd; met: gecontroleerd en klaargezet.
    bestanden = {"bestand": (archief.name, download.content, "application/zip")}
    assert c.post("/beheer/backup/herstel", files=bestanden, data={"wachtwoord": "fout"}).status_code == 403
    klaar = c.post("/beheer/backup/herstel", files=bestanden, data={"wachtwoord": WACHTWOORD}, follow_redirects=False)
    assert "klaargezet" in klaar.headers["location"]
    assert (DATA_DIR / "herstel" / "klaar" / "app.db").is_file()
    kapot = c.post("/beheer/backup/herstel", files={"bestand": ("x.zip", b"geen zip", "application/zip")},
                   data={"wachtwoord": WACHTWOORD}, follow_redirects=False)  # fmt: skip
    assert "afgekeurd" in kapot.headers["location"]


def test_herstel_wordt_bij_start_toegepast(beheerder):
    from teeltplanning.backups import maak_backup, pas_herstel_toe, zet_herstel_klaar

    archief = maak_backup()
    maak_gebruiker(beheerder, "na-backup", "lezer")
    zet_herstel_klaar(archief)
    oud = pas_herstel_toe()
    assert oud and (DATA_DIR / "herstel").exists()
    with SessionLocal() as db:
        namen = [g.gebruikersnaam for g in db.scalars(select(Gebruiker))]
    assert namen == ["leon"]  # de gebruiker van na de back-up is weg: de back-up staat terug


def test_verzoekgrootte_en_koppen(beheerder):
    groot = beheerder.post("/beheer/tabel/kappen/csv", data={"csv_tekst": "x" * (1024 * 1024 + 10)})
    assert groot.status_code == 413
    antwoord = beheerder.get("/ronde")
    assert (
        antwoord.headers["x-frame-options"] == "DENY"
        and "default-src 'self'" in antwoord.headers["content-security-policy"]
    )
    assert beheerder.get("/health").json()["status"] == "ok"


def test_opschonen_verlopen_sessies(beheerder):
    from datetime import timedelta

    from teeltplanning.db.modellen import Sessie, utcnu
    from teeltplanning.onderhoud import opschonen

    with SessionLocal() as db:
        db.add(Sessie(token_hash="x" * 64, gebruiker_id=1, verloopt_op=utcnu() - timedelta(hours=1)))
        db.commit()
        assert opschonen(db)["sessies"] == 1
