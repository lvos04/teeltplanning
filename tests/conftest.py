"""Gemeenschappelijke testinstellingen.

De app weigert standaard schrijfverzoeken zonder `Origin`/`Referer` (strikte
oorsprongcontrole). Echte browsers sturen `Origin` altijd mee; de TestClient
niet. Daarom krijgt elke TestClient standaard dezelfde `Origin` als een browser
op de testserver. Tests die de controle zelf toetsen, overschrijven de header.
"""

import os
import tempfile

import starlette.testclient

# Eén tijdelijke DATA_DIR voor de hele testsessie, vóórdat de app wordt geïmporteerd.
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="teeltplanning-test-"))

_original_init = starlette.testclient.TestClient.__init__


def _init(self, *args, headers=None, **kwargs):
    merged = {"origin": "http://testserver"}
    merged.update(headers or {})
    _original_init(self, *args, headers=merged, **kwargs)


starlette.testclient.TestClient.__init__ = _init


# --- Synthetische werkboeken (één keer per testsessie) ---------------------------------------------------
import pytest  # noqa: E402


@pytest.fixture(scope="session")
def synthetische_werkboeken(tmp_path_factory):
    from tests.synthetisch import BELICHTING_NAAM, PROGRAMMA_NAAM, maak_alles

    map_ = tmp_path_factory.mktemp("synthetisch")
    meta = maak_alles(map_)
    return {"programma": map_ / PROGRAMMA_NAAM, "belichting": map_ / BELICHTING_NAAM, "meta": meta}


@pytest.fixture
def client():
    """Schone database en datamap per test; de app start met migraties en startgegevens."""
    import shutil

    from fastapi.testclient import TestClient

    from teeltplanning import planning
    from teeltplanning.db.basis import DATA_DIR, engine
    from teeltplanning.main import app

    engine.dispose()
    for kind in DATA_DIR.iterdir():
        shutil.rmtree(kind) if kind.is_dir() else kind.unlink()
    planning._cache.clear()
    with TestClient(app) as c:
        yield c
    engine.dispose()


WACHTWOORD = "geheim-wachtwoord-123"


@pytest.fixture
def beheerder(client):
    antwoord = client.post(
        "/eerste-start", data={"gebruikersnaam": "leon", "wachtwoord": WACHTWOORD}, follow_redirects=False
    )
    assert antwoord.status_code == 303, antwoord.text
    return client


def maak_gebruiker(client, naam: str, rol: str):
    antwoord = client.post("/beheer/gebruikers/nieuw", data={"gebruikersnaam": naam, "wachtwoord": WACHTWOORD, "rol": rol},
                           follow_redirects=False)  # fmt: skip
    assert antwoord.status_code == 303


def importeer(client, pad, jaren: dict[int, int]) -> int:
    with open(pad, "rb") as f:
        antwoord = client.post(
            "/import/upload", files={"bestand": (pad.name, f, "application/octet-stream")}, follow_redirects=False
        )
    assert antwoord.status_code == 303, antwoord.text
    batch = int(antwoord.headers["location"].rsplit("/", 1)[1])
    antwoord = client.post(
        f"/import/{batch}/jaren", data={f"jaar_{n}": j for n, j in jaren.items()}, follow_redirects=False
    )
    assert antwoord.status_code == 303, antwoord.text
    antwoord = client.post(f"/import/{batch}/bevestig", follow_redirects=False)
    assert antwoord.status_code == 303, antwoord.text
    return batch
