"""Bedrijfskoppeling API v1 aanbieden (BOUWPLAN §11.1): contracttests."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from teeltplanning.db.basis import SessionLocal
from teeltplanning.db.modellen import Koppelbron, Koppelsleutel, Scenario

from .conftest import WACHTWOORD, importeer, maak_gebruiker

API = "/api/koppeling/v1"


def _zet(sleutel, waarde):
    from teeltplanning import instellingen

    with SessionLocal() as db:
        instellingen.wijzig(db, None, sleutel, waarde, "test")
        db.commit()


def _sleutel(client, naam="Bedrijfsdashboard") -> str:
    antwoord = client.post("/api/beheer/koppelsleutels", json={"naam": naam})
    assert antwoord.status_code == 201, antwoord.text
    return antwoord.json()["sleutel"]


def _kop(sleutel):
    return {"Authorization": f"Bearer {sleutel}"}


def test_schone_installatie_alle_externe_bronnen_en_koppeling_uit(beheerder):
    from teeltplanning import instellingen
    from teeltplanning.bronnen import dienst, koppelbronnen

    with SessionLocal() as db:
        snap = instellingen.laad(db)
        aan = [
            s
            for s, i in instellingen.REGISTER.items()
            if i.type == "bool" and s.startswith(("bronnen.", "koppeling.")) and snap[s]
        ]
        assert aan == []
        assert not snap["koppeling.ingeschakeld"] and not snap["koppeling.bronnen_aan"]
        assert db.scalars(select(Koppelbron)).all() == [] and db.scalars(select(Koppelsleutel)).all() == []
    dienst.ververs_als_nodig()  # geen netwerk (de fixture in conftest zou anders falen)
    koppelbronnen.ververs_als_nodig()
    for pad in ("/info", "/samenvatting", "/datasets/planning", "/datasets/bestaat_niet?limiet=0"):
        antwoord = beheerder.get(API + pad)
        assert antwoord.status_code == 403 and antwoord.json() == {"fout": "koppeling_uitgeschakeld"}
    pagina = beheerder.get("/beheer/koppelingen").text
    assert "Bedrijfskoppeling ingeschakeld:</strong> uit" in pagina and "Koppelbronnen ophalen:</strong> uit" in pagina


def test_403_uitgeschakeld_komt_voor_alle_andere_controles(beheerder):
    sleutel = _sleutel(beheerder)
    for kop in ({}, _kop("bk_onzin"), _kop(sleutel)):
        for pad in ("/info", "/datasets/onbekend", "/datasets/planning?limiet=9999&sinds=gisteren"):
            antwoord = beheerder.get(API + pad, headers=kop)
            assert antwoord.status_code == 403 and antwoord.json()["fout"] == "koppeling_uitgeschakeld", (kop, pad)


def test_sleutel_eenmalig_getoond_en_alleen_als_hash_opgeslagen(beheerder):
    antwoord = beheerder.post("/api/beheer/koppelsleutels", json={"naam": "Dashboard"})
    assert antwoord.status_code == 201
    gegevens = antwoord.json()
    sleutel = gegevens["sleutel"]
    assert sleutel.startswith("bk_") and len(sleutel) >= 3 + 43 and "niet opnieuw getoond" in gegevens["melding"]
    with SessionLocal() as db:
        rij = db.get(Koppelsleutel, gegevens["id"])
        assert rij.sleutel_hash == hashlib.sha256(sleutel.encode()).hexdigest() and rij.voorvoegsel == sleutel[:8]
        assert all(sleutel not in str(getattr(rij, k.name)) for k in Koppelsleutel.__table__.columns)
    lijst = beheerder.get("/api/beheer/koppelsleutels").json()
    assert sleutel not in str(lijst) and "sleutel_hash" not in str(lijst) and lijst[0]["voorvoegsel"] == sleutel[:8]
    # In de app: één keer op de pagina na het aanmaken, daarna nooit meer
    pagina = beheerder.post("/beheer/koppelingen/sleutels", data={"naam": "Tweede"}).text
    nieuw = pagina.split('<code class="sleutel">')[1].split("</code>")[0]
    assert nieuw.startswith("bk_") and nieuw not in beheerder.get("/beheer/koppelingen").text
    from teeltplanning.db.modellen import Audit

    with SessionLocal() as db:
        assert all(sleutel not in str(a.details) and nieuw not in str(a.details) for a in db.scalars(select(Audit)))


def test_401_zonder_ongeldige_en_ingetrokken_sleutel(beheerder):
    _zet("koppeling.ingeschakeld", True)
    sleutel = _sleutel(beheerder)
    assert beheerder.get(API + "/info", headers=_kop(sleutel)).status_code == 200
    for kop in (
        {},
        {"Authorization": "Basic abc"},
        _kop("bk_ongeldig"),
        _kop(sleutel[:-1] + "x"),
        {"Authorization": sleutel},
    ):
        antwoord = beheerder.get(API + "/info", headers=kop)
        assert antwoord.status_code == 401 and antwoord.json() == {"fout": "ongeldige_koppelsleutel"}, kop
    sleutel_id = beheerder.get("/api/beheer/koppelsleutels").json()[0]["id"]
    assert beheerder.delete(f"/api/beheer/koppelsleutels/{sleutel_id}").status_code == 204
    antwoord = beheerder.get(API + "/samenvatting", headers=_kop(sleutel))
    assert antwoord.status_code == 401 and antwoord.json() == {"fout": "ongeldige_koppelsleutel"}
    assert beheerder.get("/api/beheer/koppelsleutels").json()[0]["actief"] is False


def test_niet_beheerders_kunnen_geen_sleutels_beheren(beheerder):
    sleutel_id = beheerder.post("/api/beheer/koppelsleutels", json={"naam": "x"}).json()["id"]
    maak_gebruiker(beheerder, "medewerker1", "medewerker")
    maak_gebruiker(beheerder, "lezer1", "lezer")
    beheerder.post("/afmelden")
    for naam in ("medewerker1", "lezer1"):
        beheerder.post("/aanmelden", data={"gebruikersnaam": naam, "wachtwoord": WACHTWOORD})
        assert beheerder.get("/api/beheer/koppelsleutels").status_code == 403
        assert beheerder.post("/api/beheer/koppelsleutels", json={"naam": "y"}).status_code == 403
        assert beheerder.delete(f"/api/beheer/koppelsleutels/{sleutel_id}").status_code == 403
        assert beheerder.get("/beheer/koppelingen").status_code == 403
        assert beheerder.post("/beheer/koppelingen/sleutels", data={"naam": "z"}).status_code == 403
        beheerder.post("/afmelden")
    assert beheerder.get("/api/beheer/koppelsleutels").status_code == 401  # niet aangemeld
    with SessionLocal() as db:
        assert [r.ingetrokken_op for r in db.scalars(select(Koppelsleutel))] == [None]


def test_429_per_sleutel(beheerder):
    from teeltplanning import koppeling

    _zet("koppeling.ingeschakeld", True)
    _zet("koppeling.verzoeken_per_minuut", 3)
    koppeling._verzoeken.clear()
    a, b = _sleutel(beheerder, "a"), _sleutel(beheerder, "b")
    assert [beheerder.get(API + "/info", headers=_kop(a)).status_code for _ in range(3)] == [200, 200, 200]
    antwoord = beheerder.get(API + "/info", headers=_kop(a))
    assert (
        antwoord.status_code == 429
        and antwoord.json() == {"fout": "te_veel_verzoeken"}
        and antwoord.headers["retry-after"]
    )
    assert beheerder.get(API + "/info", headers=_kop(b)).status_code == 200  # per sleutel


@pytest.fixture
def gedeeld_plan(beheerder, synthetische_werkboeken):
    importeer(beheerder, synthetische_werkboeken["programma"], synthetische_werkboeken["meta"]["programma"]["jaren"])
    _zet("koppeling.ingeschakeld", True)
    _zet("bedrijf.oppervlak_per_kap_m2", 726.0)
    _zet("koppeling.verzoeken_per_minuut", 10000)  # de tests lopen alle pagina's veel vaker door dan een afnemer
    return beheerder, _sleutel(beheerder)


def test_info_samenvatting_en_alleen_het_vastgestelde_plan(gedeeld_plan):
    client, sleutel = gedeeld_plan
    info = client.get(API + "/info", headers=_kop(sleutel)).json()
    assert info["app"] == "teeltplanning" and info["api_versie"] == "1" and info["bedrijf"] is None
    assert info["datasets"] == ["samenvatting", "planning", "oogstprognose", "energieprognose", "afzetmomenten"]
    assert info["tijd"].endswith("Z")
    s = client.get(API + "/samenvatting", headers=_kop(sleutel)).json()
    sleutels = [k["sleutel"] for k in s["kpis"]]
    assert sleutels == ["plantingen_komende_14_dagen", "oogsten_komende_14_dagen", "oogstprognose_deze_week",
                        "planconflicten", "voorstellen_indicatief", "kwh_komende_week"]  # fmt: skip
    for k in s["kpis"]:
        assert k["status"] in ("ok", "let_op", "actie", "onbekend") and set(k) == {
            "sleutel",
            "label",
            "waarde",
            "eenheid",
            "status",
            "toelichting",
        }
        if k["waarde"] is None:
            assert k["status"] == "onbekend"
    kpi = {k["sleutel"]: k for k in s["kpis"]}
    assert (
        kpi["kwh_komende_week"]["waarde"] is None and kpi["kwh_komende_week"]["status"] == "onbekend"
    )  # geen lichtdata
    assert kpi["planconflicten"]["status"] == ("actie" if kpi["planconflicten"]["waarde"] else "ok")
    assert all(m["niveau"] in ("info", "waarschuwing", "kritiek") for m in s["meldingen"])
    assert any("back-up" in m["tekst"] for m in s["meldingen"])
    # Alleen het vastgestelde scenario; een concept wordt nooit gedeeld.
    with SessionLocal() as db:
        vast = db.scalar(select(Scenario).where(Scenario.status == "vastgesteld"))
    client.post(f"/scenarios/{vast.id}/kopie", data={"naam": "Concept"})
    from teeltplanning import planning

    with SessionLocal() as db:
        concept = db.scalar(select(Scenario).where(Scenario.naam == "Concept"))
        concept_ids = set(planning.bereken(db, concept.id)[0].teelten)
        vast_ids = set(planning.bereken(db, vast.id)[0].teelten)
    items = _alles(client, sleutel, "planning")
    assert {i["id"] for i in items} == vast_ids and not ({i["id"] for i in items} & concept_ids)
    eerste = items[0]
    assert set(eerste) == {"id", "ronde", "kap", "vakgroep", "volgnummer", "soort", "bedden", "plantdatum", "ld_dagen",
                           "licht_uit_datum", "reactietijd", "verlenging", "oogstdatum", "teeltduur", "dichtheid", "gepland_aantal",
                           "uitvalfractie", "oogstaantal", "gewicht_g", "gewicht_ondergrens_g", "gewicht_bovengrens_g",
                           "handmatig_velden", "bijgewerkt_op"}  # fmt: skip
    assert "opmerking" not in str(items) and "leon" not in str(items)  # geen vrije tekst, geen gebruikersnamen
    assert eerste["gepland_aantal"] == round(eerste["dichtheid"] * 726) and "dichtheid" in eerste["handmatig_velden"]
    # Zonder vastgesteld plan: lege datasets en onbekende KPI's
    with SessionLocal() as db:
        db.get(Scenario, vast.id).status = "concept"
        db.commit()
    assert _alles(client, sleutel, "planning") == []
    assert {k["status"] for k in client.get(API + "/samenvatting", headers=_kop(sleutel)).json()["kpis"]} == {
        "onbekend"
    }


def _alles(client, sleutel, dataset, limiet=500, **extra):
    items, cursor, paginas = [], None, 0
    while True:
        params = {"limiet": limiet, **extra} | ({"cursor": cursor} if cursor else {})
        antwoord = client.get(f"{API}/datasets/{dataset}", params=params, headers=_kop(sleutel))
        assert antwoord.status_code == 200, antwoord.text
        gegevens = antwoord.json()
        assert gegevens["app"] == "teeltplanning" and gegevens["dataset"] == dataset
        items += gegevens["items"]
        paginas += 1
        cursor = gegevens["volgende_cursor"]
        if not cursor:
            return items


def test_paginering_sinds_404_en_422(gedeeld_plan):
    client, sleutel = gedeeld_plan
    alles = _alles(client, sleutel, "planning")
    assert len(alles) == 1360
    in_stukjes = _alles(client, sleutel, "planning", limiet=97)
    assert [i["id"] for i in in_stukjes] == [i["id"] for i in alles] == sorted(i["id"] for i in alles)  # stabiel
    standaard = client.get(f"{API}/datasets/planning", headers=_kop(sleutel)).json()
    assert len(standaard["items"]) == 100 and standaard["volgende_cursor"]
    for dataset in ("oogstprognose", "energieprognose", "afzetmomenten"):
        a, b = _alles(client, sleutel, dataset), _alles(client, sleutel, dataset, limiet=211)
        assert [i["id"] for i in a] == [i["id"] for i in b]
    prognose = _alles(client, sleutel, "oogstprognose")
    assert prognose and set(prognose[0]) == {"id", "iso_jaar", "week", "aantal_takken", "gemiddeld_gewicht_g", "ondergrens_g",
                                             "bovengrens_g", "aantal_teelten", "bijgewerkt_op"}  # fmt: skip
    afzet = _alles(client, sleutel, "afzetmomenten")
    assert afzet and set(afzet[0]) == {
        "id",
        "naam",
        "datum",
        "venster_begin",
        "venster_eind",
        "gewicht",
        "geplande_takken_in_venster",
    }
    assert all(
        i["venster_begin"] is None and i["geplande_takken_in_venster"] is None for i in afzet
    )  # venster onbekend
    # sinds: alles is ouder dan morgen; niets is nieuwer dan morgen
    morgen = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    gisteren = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    assert _alles(client, sleutel, "planning", sinds=morgen) == []
    assert len(_alles(client, sleutel, "planning", sinds=gisteren)) == 1360
    # Een wijziging in één kap: met sinds = vlak ervoor komt alleen die kap terug
    import time

    time.sleep(1.1)
    grens = datetime.now(UTC).isoformat()
    time.sleep(1.1)
    teelt = alles[0]
    assert (
        client.post(f"/teelt/{teelt['id']}/veld/dichtheid", data={"waarde": "51,5"}, follow_redirects=False).status_code
        == 303
    )
    gewijzigd = _alles(client, sleutel, "planning", sinds=grens)
    assert gewijzigd and {i["kap"] for i in gewijzigd} == {teelt["kap"]}
    assert next(i for i in gewijzigd if i["id"] == teelt["id"])["dichtheid"] == 51.5
    # 404 en 422
    antwoord = client.get(f"{API}/datasets/bestaat_niet", headers=_kop(sleutel))
    assert antwoord.status_code == 404 and antwoord.json() == {"fout": "onbekende_dataset"}
    assert client.get(f"{API}/datasets/bestaat_niet?limiet=0", headers=_kop(sleutel)).status_code == 404  # 404 vóór 422
    for params in ({"limiet": 0}, {"limiet": 501}, {"limiet": "abc"}, {"sinds": "gisteren"}, {"sinds": "2026-01-01T00:00:00"},
                   {"cursor": "!!!"}, {"onbekend": 1}):  # fmt: skip
        antwoord = client.get(f"{API}/datasets/planning", params=params, headers=_kop(sleutel))
        assert antwoord.status_code == 422 and antwoord.json()["fout"] == "ongeldige_parameter", params
    # Een dataset die niet gedeeld wordt, bestaat niet voor afnemers
    _zet("koppeling.datasets", ["planning"])
    assert client.get(f"{API}/datasets/energieprognose", headers=_kop(sleutel)).status_code == 404
    assert client.get(f"{API}/info", headers=_kop(sleutel)).json()["datasets"] == ["samenvatting", "planning"]
    # Alleen GET
    assert client.post(f"{API}/info", headers=_kop(sleutel)).status_code in (403, 405)
