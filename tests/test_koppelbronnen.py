"""Afnemen uit Gewichtsverwachting (BOUWPLAN §11.2) met een nep-server: geen echte netwerkverbinding."""

import json
import urllib.parse
from datetime import date, timedelta

from sqlalchemy import select

from teeltplanning.db.basis import SessionLocal
from teeltplanning.db.modellen import (
    Koppelbron,
    KoppelItem,
    ParameterLbe,
    Ronde,
    Scenario,
    Teelt,
    TeeltRas,
    Veldwaarde,
    Weerdag,
)

SLEUTEL = "bk_nepsleutel_voor_de_test_0123456789abcdef"


class NepGewichtsverwachting:
    """Minimale Bedrijfskoppeling API v1 zoals Gewichtsverwachting die aanbiedt (paginering met cursor)."""

    def __init__(self, teelten, oogsten, prognoses, uit=False):
        self.data = {"teelten": teelten, "oogsten": oogsten, "metingen": [], "prognoses": prognoses}
        self.uit, self.verzoeken = uit, []

    def __call__(self, verzoek, tijdslimiet):
        url = urllib.parse.urlsplit(verzoek.full_url)
        self.verzoeken.append(verzoek.full_url)
        assert url.netloc == "gv.test" and url.path.startswith("/api/koppeling/v1/")
        if self.uit:
            return 403, json.dumps({"fout": "koppeling_uitgeschakeld"}).encode()
        if verzoek.get_header("Authorization") != f"Bearer {SLEUTEL}":
            return 401, json.dumps({"fout": "ongeldige_koppelsleutel"}).encode()
        pad = url.path.removeprefix("/api/koppeling/v1")
        if pad == "/info":
            return 200, json.dumps({"app": "gewichtsverwachting", "datasets": ["samenvatting", *self.data]}).encode()
        naam = pad.removeprefix("/datasets/")
        p = dict(urllib.parse.parse_qsl(url.query))
        limiet, na = int(p.get("limiet", 100)), int(p.get("cursor", 0))
        items = [i for i in self.data[naam] if i["id"] > na]
        pagina = items[:limiet]
        volgende = str(pagina[-1]["id"]) if len(items) > limiet else None
        return 200, json.dumps(
            {"app": "gewichtsverwachting", "dataset": naam, "items": pagina, "volgende_cursor": volgende}
        ).encode()


def _plan_met_licht(db):
    """Vastgesteld plan met vier teelten in juli 2024 (ras a), straling bekend en transmissie ingesteld: de
    lichtsom is bekend, het gewicht niet (dat komt uit Gewichtsverwachting)."""
    from teeltplanning import instellingen
    from teeltplanning.db.modellen import Lampgroep

    for g in db.scalars(select(Lampgroep)):
        g.actief = False
    for i in range(120):
        db.add(
            Weerdag(
                datum=date(2024, 6, 1) + timedelta(days=i), soort="historie", bron="open_meteo", straling_j_cm2=1800.0
            )
        )
    instellingen.wijzig(db, None, "licht.transmissie", 0.7, "test")
    instellingen.wijzig(db, None, "import.koppel_tolerantie_dagen", 1, "test")
    scenario = Scenario(naam="Plan", status="vastgesteld")
    db.add(scenario)
    db.flush()
    ronde = Ronde(scenario_id=scenario.id, nummer=1, iso_jaar_eerste_planting=2024)
    db.add(ronde)
    db.flush()
    ids = {}
    for kap, plant in ((1, date(2024, 7, 1)), (2, date(2024, 7, 2)), (3, date(2024, 7, 3)), (4, date(2024, 7, 4))):
        teelt = Teelt(ronde_id=ronde.id, kap=kap, volgnummer=kap, schemacode="a")
        teelt.rassen = [TeeltRas(code="a")]
        db.add(teelt)
        db.flush()
        for veld, waarde in (
            ("plantdatum", plant.isoformat()),
            ("ld_dagen", 10),
            ("reactietijd", 50),
            ("verlenging", 1),
            ("dichtheid", 50.0),
        ):
            db.add(Veldwaarde(teelt_id=teelt.id, veld=veld, handmatig=waarde, handmatig_bron="gebruiker"))
        ids[kap] = teelt.id
    db.commit()
    return ids


def _nep():
    teelten = [
        {"id": 11, "kap": 1, "soort": "Antonov", "plantdatum": "2024-07-01", "licht_uit_datum": "2024-07-11", "dichtheid": 50, "status": "afgerond"},
        {"id": 12, "kap": 2, "soort": "Antonov", "plantdatum": "2024-07-03", "licht_uit_datum": "2024-07-13", "dichtheid": 50, "status": "afgerond"},  # ± 1 dag
        {"id": 13, "kap": 3, "soort": "Antonov", "plantdatum": "2024-07-20", "licht_uit_datum": "2024-07-30", "dichtheid": 50, "status": "afgerond"},  # te ver
        {"id": 14, "kap": 9, "soort": "Antonov", "plantdatum": "2024-07-01", "licht_uit_datum": None, "dichtheid": 50, "status": "gepland"},  # geen planteelt
        {"id": 15, "kap": 4, "soort": "Antonov", "plantdatum": "2024-07-04", "licht_uit_datum": "2024-07-14", "dichtheid": 50, "status": "afgerond"},
        {"id": 16, "kap": 4, "soort": "Antonov", "plantdatum": "2024-07-05", "licht_uit_datum": "2024-07-15", "dichtheid": 50, "status": "afgerond"},  # dubbel
    ]  # fmt: skip
    oogsten = [{"id": 100 + t["id"], "teelt_id": t["id"], "kap": t["kap"], "begindatum": "2024-09-01", "einddatum": "2024-09-03",
                "gemiddeld_takgewicht_g": 92.0, "aantal_takken": 30000, "uitvalfractie": 0.06} for t in teelten]  # fmt: skip
    prognoses = [{"id": 500, "teelt_id": 11, "kap": 1, "peilmoment": "2024-08-20T08:00:00Z", "status": "gevalideerd", "gewicht_g": 93.4,
                  "ondergrens_g": 88.0, "bovengrens_g": 98.0, "modeltype": "KD30", "toelichting": "Operationeel vrijgegeven model"}]  # fmt: skip
    return NepGewichtsverwachting(teelten, oogsten, prognoses)


def test_koppelbron_versleuteld_ophalen_koppelen_en_kalibratie(beheerder, monkeypatch):
    from teeltplanning import instellingen
    from teeltplanning.bronnen import http, kluis, koppelbronnen
    from teeltplanning.voorstel import dienst

    with SessionLocal() as db:
        ids = _plan_met_licht(db)
    nep = _nep()
    monkeypatch.setattr(http, "_open", nep)
    antwoord = beheerder.post("/beheer/koppelingen/bronnen", data={"naam": "Gewichtsverwachting", "url": "https://gv.test/", "sleutel": SLEUTEL},
                              follow_redirects=True)  # fmt: skip
    assert "versleuteld opgeslagen" in antwoord.text and SLEUTEL not in antwoord.text
    with SessionLocal() as db:
        bron = db.scalar(select(Koppelbron))
        assert (
            SLEUTEL.encode() not in bron.sleutel_versleuteld and kluis.ontsleutel(bron.sleutel_versleuteld) == SLEUTEL
        )
        assert bron.basis_url == "https://gv.test"
    # Staat standaard uit: "Nu ophalen" doet niets en er gaat geen verzoek de deur uit
    assert "staat uit" in beheerder.post(f"/beheer/koppelingen/bronnen/{bron.id}/ophalen", follow_redirects=True).text
    assert nep.verzoeken == []
    beheerder.post("/beheer/koppelingen/schakelaar", data={"welke": "afnemen", "aan": "1"})
    pagina = beheerder.post(f"/beheer/koppelingen/bronnen/{bron.id}/ophalen", follow_redirects=True).text
    assert "Opgehaald" in pagina and "teelten: 6" in pagina
    assert any("limiet=500" in u for u in nep.verzoeken)
    with SessionLocal() as db:
        assert db.scalar(select(Koppelbron)).status == "ok"
        assert len(db.scalars(select(KoppelItem).where(KoppelItem.dataset == "oogsten")).all()) == 6
        uitkomst = koppelbronnen.koppel(db)
    gekoppeld = {g["teelt"]["id"]: g["teelt_id"] for g in uitkomst["gekoppeld"]}
    assert gekoppeld == {11: ids[1], 12: ids[2]}  # 12: binnen ± 1 dag tolerantie
    redenen = {n["teelt"]["id"]: n["reden"] for n in uitkomst["niet_gekoppeld"]}
    assert set(redenen) == {13, 14, 15, 16}
    assert "binnen ± 1 dag" in redenen[13] and "geen teelt in het plan op kap 9" in redenen[14]
    assert "niet geraden" in redenen[15] and "niet geraden" in redenen[16]  # twee bronteelten op één planteelt
    pagina = beheerder.get("/beheer/koppelingen").text
    assert "2 teelt(en) gekoppeld, 4 niet gekoppeld" in pagina and "niet geraden" in pagina
    # Prognose uit Gewichtsverwachting naast de planprognose
    pagina = beheerder.get(f"/teelt/{ids[1]}").text
    assert "93,4 g" in pagina and "KD30" in pagina
    assert "Niet gekoppeld" in beheerder.get(f"/teelt/{ids[3]}").text
    # Gekoppelde teelten voeden de kalibratie: LBE = 92 × 50 / lichtsom
    with SessionLocal() as db:
        waarnemingen = koppelbronnen.waarnemingen(db)
        assert {w.sleutel for w in waarnemingen} == {f"koppeling:{bron.id}:11", f"koppeling:{bron.id}:12"}
        w = waarnemingen[0]
        assert w.ras == "a" and w.gewicht == 92.0 and w.ld_dagen == 10 and w.lichtsom and w.uitval == 0.06
        assert w.reactietijd is None  # gerealiseerde oogstdatum niet ingesteld (B40): niet overnemen
        instellingen.wijzig(db, None, "koppeling.oogstdatum_realisatie", "begindatum", "test")
        db.commit()
        w = next(x for x in koppelbronnen.waarnemingen(db) if x.sleutel.endswith(":11"))
        assert w.reactietijd == (date(2024, 9, 1) - date(2024, 7, 11)).days - 1
    tekst = beheerder.post("/beheer/lbe/kalibreren", follow_redirects=True).text
    assert "LBE-cel" in tekst
    with SessionLocal() as db:
        cellen = db.scalars(select(ParameterLbe).where(ParameterLbe.ras == "a")).all()
        assert cellen and all(c.bron == "kalibratie" for c in cellen)
        _, plan = __import__("teeltplanning.planning", fromlist=["x"]).bereken(db, db.scalar(select(Scenario.id)))
        lichtsom = plan.teelten[ids[1]].lichtsom.totaal
        assert any(abs(c.lbe_g_per_mol - 92 * 50 / lichtsom) < 1e-6 for c in cellen)
    # ... en het model
    beheerder.post("/beheer/model/trainen")
    dienst.wacht(timeout=300)
    with SessionLocal() as db:
        from teeltplanning.db.modellen import Modelversie

        assert db.scalar(select(Modelversie).order_by(Modelversie.id.desc())).herkomst["gewichtsverwachting"] == 2


def test_403_bij_de_bron_is_staat_uit_geen_storing(beheerder, monkeypatch):
    from teeltplanning.bronnen import http

    nep = _nep()
    nep.uit = True
    monkeypatch.setattr(http, "_open", nep)
    beheerder.post("/beheer/koppelingen/bronnen", data={"naam": "GV", "url": "https://gv.test", "sleutel": SLEUTEL})
    beheerder.post("/beheer/koppelingen/schakelaar", data={"welke": "afnemen", "aan": "1"})
    with SessionLocal() as db:
        bron_id = db.scalar(select(Koppelbron.id))
    pagina = beheerder.post(f"/beheer/koppelingen/bronnen/{bron_id}/ophalen", follow_redirects=True).text
    assert "Staat uit bij de bron (Gewichtsverwachting: Beheer → Bedrijfskoppeling)" in pagina
    assert "staat uit bij de bron</strong>" in pagina
    with SessionLocal() as db:
        bron = db.get(Koppelbron, bron_id)
        assert bron.status == "uit_bij_bron" and bron.laatst_gelukt is None
    # Een ongeldige sleutel is wel een fout (401), met uitleg
    nep.uit = False
    beheerder.post(f"/beheer/koppelingen/bronnen/{bron_id}", data={"sleutel": "bk_verkeerd", "actief": ""})
    pagina = beheerder.post(f"/beheer/koppelingen/bronnen/{bron_id}/ophalen", follow_redirects=True).text
    assert "Koppelsleutel geweigerd (401)" in pagina
    with SessionLocal() as db:
        assert db.get(Koppelbron, bron_id).status == "fout"


def test_koppelbron_alleen_voor_beheerders_en_geldig_adres(beheerder):
    from .conftest import WACHTWOORD, maak_gebruiker

    assert "Niet toegevoegd" in beheerder.post("/beheer/koppelingen/bronnen", data={"naam": "x", "url": "ftp://x", "sleutel": SLEUTEL},
                                              follow_redirects=True).text  # fmt: skip
    assert "bk_" in beheerder.post("/beheer/koppelingen/bronnen", data={"naam": "x", "url": "https://x.test", "sleutel": "geheim"},
                                   follow_redirects=True).text  # fmt: skip
    maak_gebruiker(beheerder, "m", "medewerker")
    beheerder.post("/afmelden")
    beheerder.post("/aanmelden", data={"gebruikersnaam": "m", "wachtwoord": WACHTWOORD})
    assert (
        beheerder.post(
            "/beheer/koppelingen/bronnen", data={"naam": "x", "url": "https://x.test", "sleutel": SLEUTEL}
        ).status_code
        == 403
    )
