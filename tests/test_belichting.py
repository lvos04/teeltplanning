"""Importer `Belichting winterronde` (§7.2), lampgroepen (§6.4), lichtsom in het plan, LBE-ijkpunten en energie."""

import json
from datetime import date, datetime, timedelta

import pytest
from openpyxl import load_workbook
from sqlalchemy import func, select

from teeltplanning.db.basis import SessionLocal
from teeltplanning.db.modellen import (
    BelichtingDag,
    BelichtingWeek,
    Lampgroep,
    LbeIjkpunt,
    Ras,
    Ronde,
    Scenario,
    Teelt,
    TeeltRas,
    TeeltschemaRegel,
    Veldwaarde,
    Weerdag,
)
from teeltplanning.importers import belichting_dienst


def upload(client, pad) -> int:
    with open(pad, "rb") as f:
        antwoord = client.post(
            "/import/belichting/upload", files={"bestand": (pad.name, f, "x")}, follow_redirects=False
        )
    assert antwoord.status_code == 303, antwoord.text
    batch = int(antwoord.headers["location"].rsplit("/", 1)[1])
    belichting_dienst.wacht(batch)
    return batch


def importeer_belichting(client, pad, jaar_weekuren=2023, jaar_teeltschema=2023) -> int:
    batch = upload(client, pad)
    client.post(f"/import/belichting/{batch}/tijdstempel", data={"waarde": "begin"})
    belichting_dienst.wacht(batch)
    client.post(
        f"/import/belichting/{batch}/keuzes",
        data={"jaar_weekuren": jaar_weekuren, "jaar_teeltschema": jaar_teeltschema},
    )
    antwoord = client.post(f"/import/belichting/{batch}/bevestig", follow_redirects=False)
    assert antwoord.status_code == 303, antwoord.text
    return batch


def test_lampgroepen_startgegevens_met_onbekende_maanden(beheerder):
    with SessionLocal() as db:
        groepen = {g.naam: g for g in db.scalars(select(Lampgroep))}
    assert len(groepen) == 5
    assert all(g.geldig_vanaf is None and g.geldig_tot is None for g in groepen.values())  # vraag 1
    led2 = groepen["LED fase 2"]
    assert led2.intensiteit_umol == 120 and led2.dimbaar and (led2.module_van, led2.module_tot) == (101, 140)
    assert groepen["SON-T volledig"].status == "indicatief" and groepen["SON-T volledig"].intensiteit_umol == 120
    assert (
        groepen["SON-T resterend (50%)"].intensiteit_umol == 60 and groepen["LED fase 1 (2023–2024)"].kw_per_kap == 20
    )
    pagina = beheerder.get("/beheer/tabel/lampgroepen").text
    assert "LED fase 2" in pagina and "onbekend" in pagina
    # Beheer: periode invullen via CSV
    csv = beheerder.get("/beheer/tabel/lampgroepen/export.csv").text.lstrip("﻿")
    regels = csv.strip().splitlines()
    nieuw = [regels[0]] + [r.replace(";;;", ";;;") for r in regels[1:]]
    led2_regel = next(i for i, r in enumerate(nieuw) if r.startswith("LED fase 2;"))
    velden = nieuw[led2_regel].split(";")
    velden[9] = "2024-07-01"
    nieuw[led2_regel] = ";".join(velden)
    antwoord = beheerder.post(
        "/beheer/tabel/lampgroepen/csv", data={"csv_tekst": "\n".join(nieuw)}, follow_redirects=False
    )
    assert "1%20regel" in antwoord.headers["location"]
    with SessionLocal() as db:
        assert db.scalar(select(Lampgroep).where(Lampgroep.naam == "LED fase 2")).geldig_vanaf == date(2024, 7, 1)
    fout = beheerder.post("/beheer/tabel/lampgroepen/csv", data={"csv_tekst": "\n".join(nieuw).replace("2024-07-01", "1-7-2024")},
                          follow_redirects=False)  # fmt: skip
    assert "JJJJ-MM-DD" in fout.headers["location"].replace("%20", " ")


def test_import_belichting_voorbeeld_meldingen_en_bevestigen(beheerder, synthetische_werkboeken):
    pad = synthetische_werkboeken["belichting"]
    batch = upload(beheerder, pad)
    pagina = beheerder.get(f"/import/belichting/{batch}").text
    # Zonder antwoord op "begin of eind van het interval" wordt het tabblad data niet gelezen.
    assert "begin of het eind van het interval" in pagina and "22 kappen" in pagina
    beheerder.post(f"/import/belichting/{batch}/tijdstempel", data={"waarde": "begin"})
    belichting_dienst.wacht(batch)
    with SessionLocal() as db:
        from teeltplanning.db.modellen import Importbatch

        v = db.get(Importbatch, batch).voorbeeld
    data = v["data"]
    assert data["modules"] == list(range(101, 141)) and data["dagen"] == 7
    assert data["eerste"] == "2024-07-01" and data["laatste"] == "2024-10-28"
    assert data["dubbel"] == 1 and data["buiten_tijdvolgorde"] >= 1
    assert "2024-10-27" in data["zomer_wintertijd"]
    perioden = data["ontbrekend"]["perioden"]
    assert {"van": "2024-07-05 00:00:00", "tot": "2024-10-26 00:00:00"} in perioden  # lange ontbrekende periode
    assert {"van": "2024-10-28 03:00:00", "tot": "2024-10-28 04:00:00"} in perioden  # gat van een uur
    assert [o["datum"] for o in data["onvolledige_dagen"]] == ["2024-10-28"]
    assert v["weekuren"]["buiten_grenzen"] == ["D3"] and "tekst" in v["weekuren"]["soorten"]
    pagina = beheerder.get(f"/import/belichting/{batch}").text
    assert "2024-10-28 03:00:00 – 2024-10-28 04:00:00" in pagina
    # Bevestigen zonder jaren: weekuren en teeltschema worden overgeslagen
    antwoord = beheerder.post(f"/import/belichting/{batch}/bevestig", follow_redirects=False)
    assert "Overgeslagen" in antwoord.headers["location"].replace("%20", " ")
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(BelichtingDag)) == 40 * 7
        assert db.scalar(select(func.count()).select_from(BelichtingWeek)) == 0
        assert db.scalar(select(func.count()).select_from(LbeIjkpunt)) == 22
    # Tweede import van hetzelfde bestand, nu met jaren: niets dubbel
    importeer_belichting(beheerder, pad)
    importeer_belichting(beheerder, pad)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(BelichtingDag)) == 40 * 7
        assert db.scalar(select(func.count()).select_from(LbeIjkpunt)) == 22
        assert db.scalar(select(func.count()).select_from(TeeltschemaRegel)) == 860
        weken = db.scalar(select(func.count()).select_from(BelichtingWeek))
        assert weken == 80 * 27 - 2  # 180 uur (> 168) en de lege cel niet
        b3 = db.scalar(select(BelichtingWeek).where(BelichtingWeek.module == 1, BelichtingWeek.week == 36))
        assert b3.iso_jaar == 2023 and b3.uren == pytest.approx(82 + 35 / 60)
        assert db.scalar(select(BelichtingWeek).where(BelichtingWeek.week == 10).limit(1)).iso_jaar == 2024
        ts = db.scalar(select(TeeltschemaRegel).where(TeeltschemaRegel.rij == 2))
        assert ts.plantdatum == date.fromisocalendar(2023, 1, 1) and ts.waarden["datums"]["licht_uit"] is not None


def _met_de_hand(pad, modules, dagen) -> dict:
    """Onafhankelijke nagerekende waarden: het hele blad met openpyxl, elke tijdstempel één keer."""
    ws = load_workbook(pad, read_only=True)["data"]
    kolommen = {}
    for i, kop in enumerate(next(ws.iter_rows(min_row=2, max_row=2, values_only=True))):
        if kop and str(kop).startswith("PCU 1:MOD. LICHT "):
            kolommen[int(str(kop).rsplit(" ", 1)[1])] = i
    gezien, som = set(), {}
    for rij in ws.iter_rows(min_row=4, values_only=True):
        tijd = rij[0]
        if not isinstance(tijd, datetime) or tijd in gezien:
            continue
        gezien.add(tijd)
        if tijd.date() in dagen:
            for m in modules:
                som[(m, tijd.date())] = som.get((m, tijd.date()), 0) + rij[kolommen[m]] / 100 * 5 / 60
    return som


def test_dimgewogen_uren_steekproef_vijf_dagen(beheerder, synthetische_werkboeken):
    """Vijf volledige dagen, met de hand nagerekend: Σ dimstand/100 × 5 minuten."""
    pad = synthetische_werkboeken["belichting"]
    importeer_belichting(beheerder, pad)
    dagen = [date(2024, 7, 1), date(2024, 7, 2), date(2024, 7, 3), date(2024, 7, 4), date(2024, 10, 26)]
    modules = [101, 120, 140]
    hand = _met_de_hand(pad, modules, set(dagen))
    with SessionLocal() as db:
        app = {(r.module, r.datum): r for r in db.scalars(select(BelichtingDag))}
    regels = []
    for d in dagen:
        for m in modules:
            r = app[(m, d)]
            assert r.volledig and r.dimgewogen_uren == pytest.approx(hand[(m, d)], abs=1e-9)
            regels.append(f"{d} module {m}: {r.dimgewogen_uren:.4f} uur (hand {hand[(m, d)]:.4f})")
    assert not app[(101, date(2024, 10, 28))].volledig and "onvolledig" in app[(101, date(2024, 10, 28))].kwaliteit
    assert "zomer_wintertijd" in app[(101, date(2024, 10, 27))].kwaliteit
    print("\n" + "\n".join(regels))


# --- Lichtsom in het plan --------------------------------------------------------------------------------------
def _scenario_met_teelt(db, plant: date, ld=7, rt=0, verl=0, ras="a") -> int:
    scenario = Scenario(naam=f"test {plant}", status="vastgesteld")
    db.add(scenario)
    db.flush()
    ronde = Ronde(scenario_id=scenario.id, nummer=1, iso_jaar_eerste_planting=plant.year)
    db.add(ronde)
    db.flush()
    teelt = Teelt(ronde_id=ronde.id, kap=1, volgnummer=1, schemacode=ras)
    teelt.rassen.append(TeeltRas(code=ras, bedden=None))
    db.add(teelt)
    db.flush()
    for veld, waarde in (
        ("plantdatum", plant.isoformat()),
        ("ld_dagen", ld),
        ("reactietijd", rt),
        ("verlenging", verl),
    ):
        db.add(Veldwaarde(teelt_id=teelt.id, veld=veld, handmatig=waarde, handmatig_bron="gebruiker"))
    db.commit()
    return teelt.id


def test_lichtsom_in_het_plan_en_ontbrekende_perioden(beheerder, synthetische_werkboeken):
    from teeltplanning import instellingen, planning

    importeer_belichting(beheerder, synthetische_werkboeken["belichting"])
    with SessionLocal() as db:
        led2 = db.scalar(select(Lampgroep).where(Lampgroep.naam == "LED fase 2"))
        led2.geldig_vanaf = date(2024, 7, 1)
        for g in db.scalars(select(Lampgroep).where(Lampgroep.naam != "LED fase 2")):
            g.actief = False
        for i in range(20):
            db.add(
                Weerdag(
                    datum=date(2024, 7, 1) + timedelta(days=i),
                    soort="historie",
                    bron="open_meteo",
                    straling_j_cm2=2000.0,
                )
            )
        db.commit()
        teelt_id = _scenario_met_teelt(db, date(2024, 7, 1), ld=4)  # 1 t/m 4 juli: alles bekend, behalve transmissie
    beheerder.post(
        "/beheer/tabel/rassen/csv",
        data={"csv_tekst": "code;naam;doelgewicht_g;temperatuurgevoeligheid_dagen_per_graad;actief\na;Antonov;90;;ja"},
    )
    with SessionLocal() as db:
        _, plan = planning.bereken(db, db.scalar(select(Scenario.id).where(Scenario.status == "vastgesteld")))
        u = plan.teelten[teelt_id]
        assert u.lichtsom.totaal is None and "kastransmissie onbekend" in u.lichtsom.ontbrekend[0][2]  # vraag 6
        instellingen.wijzig(db, None, "licht.transmissie", 0.7, "test")
        db.commit()
        _, plan = planning.bereken(db, db.scalar(select(Scenario.id).where(Scenario.status == "vastgesteld")))
        u = plan.teelten[teelt_id]
        uren = sum(r.dimgewogen_uren for r in db.scalars(select(BelichtingDag).where(BelichtingDag.module == 101,
                                                                                      BelichtingDag.datum < date(2024, 7, 5))))  # fmt: skip
        assert u.lichtsom.dagen == 4 + 0 and u.lichtsom.zon == pytest.approx(4 * 2000 * 0.023 * 0.7)
        assert u.lichtsom.lamp == pytest.approx(120 * uren * 0.0036)
        assert (
            u.energie.kwh == pytest.approx(27.5 * uren) and u.energie.kosten is None
        )  # stroomprijs onbekend (vraag 10)
        # Langer telen: na 4 juli ontbreekt de belichtingsdata → geen stilzwijgend ingevulde lichtsom
        teelt2 = _scenario_met_teelt_kap(db, date(2024, 7, 1), ld=10)
        _, plan = planning.bereken(db, db.scalar(select(Scenario.id).where(Scenario.status == "vastgesteld")))
        u2 = plan.teelten[teelt2]
        assert u2.lichtsom.totaal is None
        assert u2.lichtsom.ontbrekend == ((date(2024, 7, 5), date(2024, 7, 10), "geen branduren voor module 102"),)
        assert any(
            m.code == "lichtsom_onbekend" and "5 juli" not in m.tekst and "2024-07-05" in m.tekst for m in u2.meldingen
        )
    pagina = beheerder.get(f"/teelt/{teelt2}").text
    assert "ontbrekend licht" in pagina and "geen branduren voor module 102" in pagina


def _scenario_met_teelt_kap(db, plant, ld):
    scenario_id = db.scalar(select(Scenario.id).where(Scenario.status == "vastgesteld"))
    ronde = db.scalar(select(Ronde).where(Ronde.scenario_id == scenario_id))
    teelt = Teelt(ronde_id=ronde.id, kap=2, volgnummer=2, schemacode="a")
    db.add(teelt)
    db.flush()
    for veld, waarde in (("plantdatum", plant.isoformat()), ("ld_dagen", ld), ("reactietijd", 0), ("verlenging", 0)):
        db.add(Veldwaarde(teelt_id=teelt.id, veld=veld, handmatig=waarde, handmatig_bron="gebruiker"))
    db.commit()
    return teelt.id


def test_ijkpunten_kalibratie_energie_en_vergelijking(beheerder, synthetische_werkboeken):
    importeer_belichting(beheerder, synthetische_werkboeken["belichting"])
    pagina = beheerder.get("/beheer/lbe").text
    assert "IJkpunten belichtingsblad (22 kappen)" in pagina and "nog niet afgesproken" in pagina
    assert "geen teelt in het plan" in pagina  # er is nog geen plan met deze kappen en plantdatums
    assert beheerder.post("/beheer/lbe/kalibreren", follow_redirects=False).status_code == 303
    energie = beheerder.get("/energie?jaar=2024")
    assert energie.status_code == 200 and "geldigheidsperiode onbekend" in energie.text
    uit = beheerder.get("/energie/vergelijking", params={
        "rekenen": 1, "a_umol": 150, "b_umol": 200, "a_uren": 1200, "b_uren": 1200, "a_kw": 40, "b_kw": 55,
        "oppervlak": 726, "zon": 500, "lbe": 6, "doelgewicht": 80, "prijs": 80, "opbrengst": "0,25"})  # fmt: skip
    assert uit.status_code == 200 and '<th>meeropbrengst</th><td colspan="3"><strong>' in uit.text
    assert "<strong>onbekend" not in uit.text
    assert "uren per teelt" in uit.text  # startwaarden uit het werkboek (AK–AM) zichtbaar


# --- Bronnen: standaard uit, nep-HTTP ----------------------------------------------------------------------------
def test_bronnen_staan_na_schone_installatie_uit(beheerder):
    from teeltplanning import instellingen
    from teeltplanning.bronnen import dienst

    with SessionLocal() as db:
        snap = instellingen.laad(db)
        assert not any(snap[s] for s, _ in dienst.BRONNEN.values())
    dienst.ververs_als_nodig()  # mag niets ophalen (de netwerkblokkade in conftest zou anders falen)
    assert "Nu ophalen" in beheerder.get("/beheer/bronnen").text


def test_open_meteo_knmi_energyzero_met_nep_http(beheerder, monkeypatch):
    from teeltplanning.bronnen import http

    verzoeken = []

    def nep(url, parameters=None, koppen=None, tijdslimiet=30):
        verzoeken.append((url, parameters, koppen, tijdslimiet))
        if "archive" in url:
            van, tot = date.fromisoformat(parameters["start_date"]), date.fromisoformat(parameters["end_date"])
            dagen = [(van + timedelta(days=i)) for i in range((tot - van).days + 1)]
            return json.dumps({"daily": {"time": [d.isoformat() for d in dagen],
                                         "shortwave_radiation_sum": [5.0 + d.year % 7 for d in dagen],
                                         "temperature_2m_mean": [10.0] * len(dagen)}}).encode()  # fmt: skip
        if "forecast" in url:
            dagen = [date.today() + timedelta(days=i) for i in range(parameters["forecast_days"])]
            return json.dumps(
                {"daily": {"time": [d.isoformat() for d in dagen], "shortwave_radiation_sum": [12.5] * len(dagen)}}
            ).encode()
        if "dataplatform" in url:
            assert koppen["Authorization"] == "geheime-sleutel"
            return json.dumps({"files": [{"filename": "a.nc"}]}).encode()
        if "energyzero" in url:
            return json.dumps({"Prices": [{"price": 0.1, "readingDate": f"{date.today().isoformat()}T10:00:00Z"},
                                          {"price": 0.3, "readingDate": f"{date.today().isoformat()}T11:00:00Z"}]}).encode()  # fmt: skip
        raise AssertionError(url)

    monkeypatch.setattr(http, "haal", nep)
    for sleutel, waarde in (("bronnen.open_meteo_aan", "aan"), ("bronnen.knmi_aan", "aan"), ("bronnen.energyzero_aan", "aan"),
                            ("bronnen.normalen_jaren", "3"), ("bronnen.normalen_min_jaren", "2")):  # fmt: skip
        assert beheerder.post(f"/beheer/instellingen/{sleutel}", data={"waarde": waarde}).status_code == 200
    # Zonder coördinaten: nette melding, geen crash
    antwoord = beheerder.post("/beheer/bronnen/open_meteo/ophalen", follow_redirects=False)
    assert "Breedte-%20en%20lengtegraad" in antwoord.headers["location"]
    beheerder.post("/beheer/instellingen/bedrijf.breedtegraad", data={"waarde": "52"})
    beheerder.post("/beheer/instellingen/bedrijf.lengtegraad", data={"waarde": "4,5"})
    antwoord = beheerder.post("/beheer/bronnen/open_meteo/ophalen", follow_redirects=False)
    assert "bijgewerkt" in antwoord.headers["location"]
    with SessionLocal() as db:
        historie = db.scalar(select(func.count()).select_from(Weerdag).where(Weerdag.soort == "historie"))
        eerste = db.scalar(select(Weerdag).where(Weerdag.soort == "historie").order_by(Weerdag.datum))
        assert historie > 3 * 365 and eerste.straling_j_cm2 == pytest.approx(
            (5.0 + eerste.datum.year % 7) * 100
        )  # MJ/m² → J/cm²
        assert db.scalar(select(func.count()).select_from(Weerdag).where(Weerdag.soort == "verwachting")) == 14
    assert all(v[3] == 30 for v in verzoeken)  # tijdslimiet uit de instellingen
    pagina = beheerder.get("/beheer/bronnen").text
    assert "Weeknormalen" in pagina and "nooit" in pagina  # KNMI en EnergyZero nog nooit gelukt
    # Tweede keer: alleen de ontbrekende dagen (incrementeel)
    aantal = len(verzoeken)
    beheerder.post("/beheer/bronnen/open_meteo/ophalen")
    assert len([v for v in verzoeken[aantal:] if "archive" in v[0]]) == 0
    # KNMI: sleutel versleuteld; verbinding werkt, maar inlezen wacht op verificatie (vraag B23)
    beheerder.post("/beheer/bronnen/knmi/sleutel", data={"sleutel": "geheime-sleutel"})
    beheerder.post("/beheer/instellingen/bronnen.knmi_versie", data={"waarde": "1.0"})
    antwoord = beheerder.post("/beheer/bronnen/knmi/ophalen", follow_redirects=False)
    assert "niet%20geverifieerd" in antwoord.headers["location"]
    with SessionLocal() as db:
        from teeltplanning.db.modellen import Audit, BronGeheim, Energieprijs

        assert b"geheime-sleutel" not in db.get(BronGeheim, "knmi").versleuteld
        assert not any("geheime-sleutel" in json.dumps(a.details or {}) for a in db.scalars(select(Audit)))
    # EnergyZero: €/kWh → €/MWh, gemiddelde per dag
    beheerder.post("/beheer/bronnen/energyzero/ophalen")
    with SessionLocal() as db:
        assert db.get(Energieprijs, date.today()).eur_per_mwh == pytest.approx(200)
    # Bron valt weg: melding, app werkt door
    monkeypatch.setattr(
        http, "haal", lambda *a, **k: (_ for _ in ()).throw(http.BronFout("Geen verbinding met de bron: time-out."))
    )
    antwoord = beheerder.post("/beheer/bronnen/energyzero/ophalen", follow_redirects=False)
    assert "Geen%20verbinding" in antwoord.headers["location"]
    assert beheerder.get("/energie").status_code == 200


def test_ras_doelgewicht_onbekend_geen_dichtheidsvoorstel(beheerder):
    with SessionLocal() as db:
        assert all(r.doelgewicht_g is None for r in db.scalars(select(Ras)))  # vraag 7: niet verzonnen


# --- Met het echte bestand (slaat over zonder TEELT_ECHT_BELICHTING) -------------------------------------------
ECHT = __import__("os").environ.get("TEELT_ECHT_BELICHTING")
ECHT_JAAR = __import__("os").environ.get("TEELT_ECHT_BELICHTING_JAAR")  # ISO-jaar van week 36 (vraag 2)


@pytest.mark.skipif(not ECHT or not __import__("pathlib").Path(ECHT).is_file(),
                    reason="TEELT_ECHT_BELICHTING niet gezet of bestand ontbreekt")  # fmt: skip
def test_echt_belichting_steekproef_vijf_dagen(beheerder):
    """Echt bestand: importeren, kwaliteitsrapport tonen en vijf volledige dagen met de hand narekenen."""
    import random
    from pathlib import Path

    pad = Path(ECHT)
    jaar = int(ECHT_JAAR) if ECHT_JAAR else None
    batch = upload(beheerder, pad)
    beheerder.post(f"/import/belichting/{batch}/tijdstempel", data={"waarde": "begin"})
    belichting_dienst.wacht(batch, 3600)
    beheerder.post(f"/import/belichting/{batch}/keuzes", data={"jaar_weekuren": jaar or ""})
    beheerder.post(f"/import/belichting/{batch}/bevestig")
    with SessionLocal() as db:
        from teeltplanning.db.modellen import Importbatch

        rapport = db.get(Importbatch, batch).voorbeeld["data"]
        volledig = sorted({r.datum for r in db.scalars(select(BelichtingDag).where(BelichtingDag.volledig.is_(True)))})
        dagen = random.Random(1).sample(volledig, 5)
        app = {
            (r.module, r.datum): r.dimgewogen_uren
            for r in db.scalars(select(BelichtingDag).where(BelichtingDag.datum.in_(dagen)))
        }
    print(f"\nperiode {rapport['eerste']} t/m {rapport['laatste']}, {rapport['dagen']} dagen, "
          f"{rapport['ontbrekend']['aantal']} ontbrekende perioden, {rapport['dubbel']} dubbele tijdstempels, "
          f"{rapport['buiten_tijdvolgorde']} regels buiten volgorde, zomer-/wintertijd: {rapport['zomer_wintertijd']}")  # fmt: skip
    hand = _met_de_hand(pad, [101, 120, 140], set(dagen))
    for d in sorted(dagen):
        for m in (101, 120, 140):
            print(f"{d} module {m}: app {app[(m, d)]:.4f} uur, met de hand {hand[(m, d)]:.4f} uur")
            assert app[(m, d)] == pytest.approx(hand[(m, d)], abs=1e-6)
