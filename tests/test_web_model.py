"""Fase 4 in de webapp: trainen op de achtergrond (apart proces), backtest zichtbaar, uitleg bij elk voorstel."""

import os
import re
import time

from sqlalchemy import select

from teeltplanning.db.basis import SessionLocal
from teeltplanning.db.modellen import Modelversie, Ronde, Scenario, Teelt, Veldwaarde

from .conftest import importeer


def _train(client) -> Modelversie:
    from teeltplanning.voorstel import dienst

    begin = time.monotonic()
    antwoord = client.post("/beheer/model/trainen", follow_redirects=False)
    duur = time.monotonic() - begin
    assert antwoord.status_code == 303 and "loopt op de achtergrond" in client.get(antwoord.headers["location"]).text
    assert duur < 5, f"trainen blokkeerde het verzoek {duur:.1f} s"
    # Tijdens de training blijft de webapp antwoorden.
    assert client.get("/health").status_code == 200
    dienst.wacht(timeout=300)
    with SessionLocal() as db:
        return db.scalar(select(Modelversie).order_by(Modelversie.id.desc()))


def test_trainen_backtest_en_uitleg_bij_voorstel(beheerder, synthetische_werkboeken):
    jaren = synthetische_werkboeken["meta"]["programma"]["jaren"]
    importeer(beheerder, synthetische_werkboeken["programma"], jaren)
    # Zonder training: geen modelvoorstellen, wel uitleg dat er nog geen model is
    assert "Nog niet getraind" in beheerder.get("/beheer/model").text
    versie = _train(beheerder)
    assert versie.status == "klaar", versie.melding
    artefact = versie.artefact
    assert artefact["pid"] != os.getpid()  # rekenwerk in een apart proces: de webapp blokkeert niet
    assert versie.herkomst["plan"] > 500 and versie.aantal_waarnemingen == len(artefact["waarnemingen"])
    bt = artefact["backtest"]
    # Synthetisch werkboek: gewicht (kolom X) is ruis en de lichtsom is onbekend → niets verslaat de seizoensbasis.
    assert bt["gewicht"]["gekozen"] == "seizoensbasis"
    assert all(t["status"] != "gevalideerd" for n, t in bt["gewicht"]["treden"].items() if n != "seizoensbasis")
    assert bt["ld_dagen"]["toetsjaren"] and bt["ld_dagen"]["treden"]["seizoensbasis"]["n"] > 100
    pagina = beheerder.get("/beheer/model").text
    assert "Backtest" in pagina and versie.versie in pagina and "(actief)" in pagina
    for jaar in bt["ld_dagen"]["toetsjaren"]:
        assert f"<th>{jaar}</th>" in pagina
    assert "referentie" in pagina and ("onvoldoende gegevens" in pagina or "indicatief" in pagina)

    # Een teelt zonder handmatige LD-dagen krijgt een voorstel met bron, band, modelversie en drie vergelijkbare teelten.
    with SessionLocal() as db:
        scenario = db.scalar(select(Scenario).where(Scenario.status == "vastgesteld"))
        ronde = db.scalar(select(Ronde).where(Ronde.scenario_id == scenario.id).order_by(Ronde.nummer.desc()))
        teelt = db.scalar(select(Teelt).where(Teelt.ronde_id == ronde.id, Teelt.schemacode == "a"))
        teelt_id = teelt.id
        hand = db.scalar(select(Veldwaarde).where(Veldwaarde.teelt_id == teelt_id, Veldwaarde.veld == "ld_dagen"))
        oud_handmatig = hand.handmatig
    antwoord = beheerder.post(f"/teelt/{teelt_id}/veld/ld_dagen", data={"actie": "voorstel"}, follow_redirects=False)
    assert antwoord.status_code == 303
    pagina = beheerder.get(f"/teelt/{teelt_id}").text
    blok = pagina[pagina.index("dagen vegetatief (LD)") :]
    blok = blok[: blok.index("</tr>")]
    assert "Seizoensbasis: mediaan van" in blok and "Bandbreedte:</strong> " in blok and versie.versie in blok
    assert "status:</strong> indicatief" in blok
    assert len(re.findall(r"kap \d+, \d+-\d \(\d{4}\)", blok)) == 3
    # Elk ander voorstel heeft ook een uitleg (bron, band, modelversie, vergelijkbare teelten of waarom niet).
    assert pagina.count("<strong>Bron:</strong>") >= 2 and "rekenregel" in pagina
    # Handmatig wint: terugzetten naar de oude waarde
    beheerder.post(f"/teelt/{teelt_id}/veld/ld_dagen", data={"waarde": str(oud_handmatig)}, follow_redirects=False)
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(Veldwaarde.handmatig).where(Veldwaarde.teelt_id == teelt_id, Veldwaarde.veld == "ld_dagen")
            )
            == oud_handmatig
        )


def test_hertrainen_overschrijft_geen_handmatige_waarden(beheerder, synthetische_werkboeken):
    importeer(beheerder, synthetische_werkboeken["programma"], synthetische_werkboeken["meta"]["programma"]["jaren"])
    with SessionLocal() as db:
        voor = {(v.teelt_id, v.veld): v.handmatig for v in db.scalars(select(Veldwaarde))}
    _train(beheerder)
    with SessionLocal() as db:
        na = {(v.teelt_id, v.veld): v.handmatig for v in db.scalars(select(Veldwaarde))}
    assert voor == na


def test_lezer_ziet_backtest_maar_traint_niet(beheerder):
    from .conftest import WACHTWOORD, maak_gebruiker

    maak_gebruiker(beheerder, "lezer1", "lezer")
    beheerder.post("/afmelden")
    beheerder.post("/aanmelden", data={"gebruikersnaam": "lezer1", "wachtwoord": WACHTWOORD})
    assert beheerder.get("/beheer/model").status_code == 200
    assert beheerder.post("/beheer/model/trainen", follow_redirects=False).status_code == 403
