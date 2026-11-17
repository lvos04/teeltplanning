"""Fase 5: genereren (leeg jaar → volledig, conflictvrij plan), zoeken (past niets toe, raakt geen handmatige
waarden), scenario's vergelijken en jaaroverzicht."""

from datetime import date, timedelta

from sqlalchemy import select

from teeltplanning.db.basis import SessionLocal
from teeltplanning.db.modellen import Ronde, Scenario, Teelt, Veldwaarde
from teeltplanning.kern import (
    KapInvoer,
    KernInstellingen,
    Parameters,
    PlanInvoer,
    TeeltInvoer,
    Veld,
    bereken_plan,
    genereren,
    zoeken,
)
from teeltplanning.kern import afzet as kern_afzet
from teeltplanning.voorstel import model

from .conftest import importeer
from .test_model import historie, instellingen

CONFLICT = ("overlap", "geen_plantdag", "geen_oogstdag")


def _inst(**over):
    basis = dict(licht_aan_na_licht_uit_dagen=12, plantdagen=frozenset(range(1, 7)), wisseltijd_dagen=0, plantdatum_volgt_keten=True,
                 oppervlak_per_kap=((None, 726.0),), grenzen={"ld_dagen": (0, 365), "reactietijd": (0, 365), "verlenging": (0, 2)})  # fmt: skip
    return KernInstellingen(**(basis | over))


def _voorspeller():
    artefact = model.train(
        [w.naar_dict() for w in historie(jaren=range(2018, 2025))], instellingen().naar_dict(), "m-gen"
    )
    return model.Voorspeller(artefact, {d: True for d in model.DOELEN})


def _invoer_uit(nieuw):
    teelten = []
    for i, nt in enumerate(nieuw):
        velden = {v: Veld(voorstel=w) for v, w in nt.waarden.items()}
        if nt.eerste_in_kap:
            velden["plantdatum"] = Veld(voorstel=nt.plantdatum)
        teelten.append(TeeltInvoer(id=i + 1, kap=nt.kap, ronde=nt.index + 1, velden=velden, rassen=nt.rassen))
    return PlanInvoer(tuple(teelten))


def test_genereren_leeg_jaar_volledig_en_conflictvrij():
    inst = _inst(oogstdagen=frozenset({1, 2, 3, 4, 5}))
    kappen = [KapInvoer(k) for k in range(1, 41)]
    opties = genereren.GenOpties(jaar=2025, rondes=None, rasverdeling=(("a", 5), ("p", 3), ("t", 2)), terugval={},
                                 start=date(2025, 1, 1), kappen_per_plantdag=4)  # fmt: skip
    nieuw, meldingen = genereren.genereer(kappen, {}, inst, _voorspeller(), opties)
    assert not meldingen, meldingen
    plan = bereken_plan(_invoer_uit(nieuw), Parameters(kappen={k.nummer: k for k in kappen}, instellingen=inst))
    assert {u.kap for u in plan.teelten.values()} == set(range(1, 41))
    for u in plan.teelten.values():
        assert all(u.effectief[v] is not None for v in ("plantdatum", "ld_dagen", "reactietijd", "verlenging"))
        assert u.oogst is not None and u.effectief["plantdatum"].isocalendar().year == 2025
        assert not u.heeft_fout and not any(m.code in CONFLICT for m in u.meldingen), u.meldingen
        assert u.oogst.isoweekday() in {1, 2, 3, 4, 5}
    for kap in range(1, 41):
        ketting = plan.per_kap(kap)
        assert ketting[0].effectief["plantdatum"] < date(2025, 2, 1)
        assert plantdatum_na(ketting[-1], inst) >= date(2025, 12, 29)  # ISO-jaar 2026 begint op 29-12-2025
    # Rasverdeling 5:3:2 over 40 kappen per ronde
    eerste = [nt for nt in nieuw if nt.index == 0]
    assert [sum(1 for nt in eerste if nt.rassen[0][0] == r) for r in "apt"] == [20, 12, 8]
    # Spreiding: vier kappen per plantdag
    assert len({nt.plantdatum for nt in eerste}) == 10


def plantdatum_na(u, inst):
    from teeltplanning.kern.keten import plantdatum_voorstel

    return plantdatum_voorstel(u.oogst, KapInvoer(u.kap), inst)


def test_genereren_zonder_voorstel_en_terugval_stopt_met_melding():
    nieuw, meldingen = genereren.genereer([KapInvoer(1)], {}, _inst(), None, genereren.GenOpties(
        jaar=2025, rondes=None, rasverdeling=(), terugval={}, start=date(2025, 1, 1)))  # fmt: skip
    assert nieuw == [] and "onbekend" in meldingen[0]
    nieuw, meldingen = genereren.genereer([KapInvoer(1)], {}, _inst(), None, genereren.GenOpties(
        jaar=None, rondes=2, rasverdeling=(), terugval={"ld_dagen": 14, "reactietijd": 52, "verlenging": 0},
        start=date(2025, 1, 1)))  # fmt: skip
    assert (
        len(nieuw) == 2
        and nieuw[1].plantdatum == nieuw[0].oogst + timedelta(days=0)
        or nieuw[1].plantdatum > nieuw[0].oogst
    )


def _klein_plan():
    teelten = []
    for kap in range(1, 9):
        for ronde in range(1, 6):
            velden = {"ld_dagen": Veld(voorstel=12), "reactietijd": Veld(voorstel=52), "verlenging": Veld(voorstel=0),
                      "dichtheid": Veld(handmatig=50.0)}  # fmt: skip
            if ronde == 1:
                # kap 5: de tweede teelt oogst op moederdag zelf (12 mei 2030), één dag na het venster
                velden["plantdatum"] = Veld(
                    handmatig=date(2030, 1, 4) if kap == 5 else date(2030, 1, 7) + timedelta(days=kap % 4)
                )
            if kap == 3 and ronde == 2:
                velden["ld_dagen"] = Veld(voorstel=12, handmatig=15)  # vast
            if kap == 4 and ronde == 2:
                velden["plantdatum"] = Veld(handmatig=date(2030, 3, 25))  # vast
            teelten.append(
                TeeltInvoer(id=kap * 10 + ronde, kap=kap, ronde=ronde, velden=velden, rassen=(("ap"[kap % 2], None),))
            )
    return PlanInvoer(tuple(teelten))


def test_zoeken_past_niets_toe_en_raakt_geen_handmatige_waarden():
    invoer = _klein_plan()
    parameters = Parameters(kappen={k: KapInvoer(k) for k in range(1, 9)}, instellingen=_inst())
    voor = repr(invoer)
    plan_voor = bereken_plan(invoer, parameters)
    moederdag = kern_afzet.Afzetmoment(1, "Moederdag", "weekdag 05 7 2", venster_dagen_voor=7, gewicht=8)
    from teeltplanning.bronnen.feestdagen import pasen

    md = kern_afzet.momenten_in([moederdag], [2030], pasen)
    opties = zoeken.ZoekOpties(jaar=2030, peildatum=date(2029, 1, 1), gewichten={"afzet": 1.0, "gelijkmatig": 1.0, "doelgewicht": None, "energie": None},
                               zetten=zoeken.ZETTEN, stap_plantdag=1, stap_ld_dagen=1, max_voorstellen=8, max_rekentijd_s=60,
                               doelgewicht={})  # fmt: skip
    uitkomst = zoeken.zoek(invoer, parameters, md, opties)
    assert uitkomst["voorstellen"], uitkomst
    assert "in het venster van Moederdag" in uitkomst["voorstellen"][0]["tekst"], uitkomst["voorstellen"][0]
    assert repr(invoer) == voor  # de invoer is niet gewijzigd: niets is toegepast
    assert bereken_plan(invoer, parameters) == plan_voor
    handmatig = {(t.id, n) for t in invoer.teelten for n, v in t.velden.items() if v.handmatig is not None}
    for v in uitkomst["voorstellen"]:
        assert v["veld"] is None or (v["teelt_id"], v["veld"]) not in handmatig, v
        assert v["score_na"] < v["score_voor"] and v["tekst"].startswith(("Kap", "Ronde"))
    assert uitkomst["onderdelen"]["afzet"]["na"] <= uitkomst["onderdelen"]["afzet"]["voor"]
    assert uitkomst["onderdelen"]["doelgewicht"]["reden"] == "gewicht niet ingesteld"
    # Zonder gewichten: niets te doen, met uitleg
    leeg = zoeken.zoek(invoer, parameters, md, opties.__class__(**(opties.__dict__ | {"gewichten": {}})))
    assert leeg["voorstellen"] == [] and "geen onderdeel" in leeg["gestopt"]


def _laatste_jaar(db, scenario_id):
    from teeltplanning import planning

    _, plan = planning.bereken(db, scenario_id)
    return max(u.effectief["plantdatum"].isocalendar().year for u in plan.teelten.values() if u.effectief["plantdatum"])


def test_web_genereren_zoeken_vergelijken(beheerder, synthetische_werkboeken):
    from teeltplanning import instellingen, plannen, planning
    from teeltplanning.voorstel import dienst

    importeer(beheerder, synthetische_werkboeken["programma"], synthetische_werkboeken["meta"]["programma"]["jaren"])
    beheerder.post("/beheer/model/trainen")
    dienst.wacht(timeout=300)
    with SessionLocal() as db:
        for sleutel, waarde in (("genereren.rasverdeling", [{"ras": "a", "aandeel": 2}, {"ras": "p", "aandeel": 1}, {"ras": "t", "aandeel": 1}]),
                                ("zoeken.gewicht_gelijkmatig", 1), ("zoeken.gewicht_afzet", 1), ("zoeken.max_voorstellen", 3),
                                ("zoeken.max_rekentijd_s", 60)):  # fmt: skip
            instellingen.wijzig(db, None, sleutel, waarde, "test")
        db.commit()
        vast = db.scalar(select(Scenario).where(Scenario.status == "vastgesteld"))
        jaar = _laatste_jaar(db, vast.id) + 1
    csv = "naam;datumregel;venster_dagen_voor;gewicht;markt;actief;bevestigd;opmerking\nMoederdag;weekdag 05 7 2;6;8;;ja;nee;"
    beheerder.post("/beheer/tabel/afzetmomenten/csv", data={"csv_tekst": csv})
    # Genereren mag niet in het vastgestelde plan
    assert (
        "conceptscenario"
        in beheerder.post(f"/scenarios/{vast.id}/genereren", data={"jaar": jaar}, follow_redirects=True).text
    )
    beheerder.post(f"/scenarios/{vast.id}/kopie", data={"naam": "Plan volgend jaar"})
    with SessionLocal() as db:
        concept = db.scalar(select(Scenario).where(Scenario.naam == "Plan volgend jaar"))
        assert concept.status == "concept" and concept.afgeleid_van == vast.id
        aantal_voor = db.scalar(
            select(Teelt.id).join(Ronde).where(Ronde.scenario_id == concept.id).order_by(Teelt.id.desc())
        )
    pagina = beheerder.post(f"/scenarios/{concept.id}/genereren", data={"jaar": str(jaar)}, follow_redirects=True).text
    assert "gegenereerd als voorstel" in pagina and f"Jaaroverzicht {jaar}" in pagina
    with SessionLocal() as db:
        geladen, plan = planning.bereken(db, concept.id)
        nieuw = [u for u in plan.teelten.values() if u.id > aantal_voor]
        in_jaar = [u for u in nieuw if u.effectief["plantdatum"].isocalendar().year == jaar]
        assert in_jaar and {u.kap for u in in_jaar} == {k for k, kap in geladen.kappen.items() if kap.actief}
        for u in nieuw:
            assert all(u.effectief[v] is not None for v in ("plantdatum", "ld_dagen", "reactietijd", "verlenging")), u
            assert u.oogst is not None and not u.heeft_fout and not any(m.code in CONFLICT for m in u.meldingen), (
                u.meldingen
            )
            assert all(u.bron[v] == "voorstel" for v in ("ld_dagen", "reactietijd", "verlenging"))
            assert geladen.teelten[u.id].rassen
        # Genereren maakt nooit handmatige waarden
        ids = [u.id for u in nieuw]
        assert not db.scalars(
            select(Veldwaarde).where(Veldwaarde.teelt_id.in_(ids), Veldwaarde.handmatig.is_not(None))
        ).all()
        # Een tweede keer voor hetzelfde jaar: geweigerd (jaar is niet leeg)
        try:
            plannen.genereer(db, None, concept.id, jaar)
            raise AssertionError("tweede keer genereren had geweigerd moeten worden")
        except plannen.Fout as fout:
            assert "niet leeg" in str(fout) or "al teelten" in str(fout)
        veldwaarden_voor = {(v.teelt_id, v.veld): (v.handmatig, v.voorstel) for v in db.scalars(select(Veldwaarde))}

    # Zoeken: lijst met voorstellen, niets toegepast
    antwoord = beheerder.post(f"/scenarios/{concept.id}/zoeken", data={"jaar": str(jaar)}, follow_redirects=False)
    run_pad = antwoord.headers["location"].split("?")[0]
    plannen.wacht_zoeken(timeout=300)
    pagina = beheerder.get(run_pad).text
    assert "niets automatisch toegepast" in pagina
    with SessionLocal() as db:
        from teeltplanning.db.modellen import Zoekrun

        run = db.scalar(select(Zoekrun).order_by(Zoekrun.id.desc()))
        assert run.status == "klaar", run.melding
        assert {
            (v.teelt_id, v.veld): (v.handmatig, v.voorstel) for v in db.scalars(select(Veldwaarde))
        } == veldwaarden_voor
        voorstellen = run.uitkomst["voorstellen"]
    assert voorstellen, run.uitkomst
    # Eén voorstel toepassen → handmatige waarde met reden; een veld dat intussen handmatig is, wordt niet overschreven
    eerste = voorstellen[0]
    if eerste["veld"]:
        with SessionLocal() as db:
            teelt = db.get(Teelt, eerste["teelt_id"])
            waarde = date.fromisoformat(eerste["oud"]) if eerste["veld"] == "plantdatum" else eerste["oud"]
            planning.zet_handmatig(db, None, teelt, eerste["veld"], waarde, "intussen handmatig")
            db.commit()
        tekst = beheerder.post(f"{run_pad}/toepassen/1", follow_redirects=True).text
        assert "niet overschreven" in tekst
        with SessionLocal() as db:
            v = db.scalar(
                select(Veldwaarde).where(Veldwaarde.teelt_id == eerste["teelt_id"], Veldwaarde.veld == eerste["veld"])
            )
            assert v.reden == "intussen handmatig"
    if len(voorstellen) > 1 and voorstellen[1]["veld"]:
        assert "Toegepast" in beheerder.post(f"{run_pad}/toepassen/2", follow_redirects=True).text
        with SessionLocal() as db:
            v = db.scalar(
                select(Veldwaarde).where(
                    Veldwaarde.teelt_id == voorstellen[1]["teelt_id"], Veldwaarde.veld == voorstellen[1]["veld"]
                )
            )
            assert v.handmatig is not None and v.reden.startswith("zoekvoorstel")

    # Twee scenario's naast elkaar: oogst per week, afzetdekking en kWh
    pagina = beheerder.get(f"/scenarios/vergelijk?a={vast.id}&b={concept.id}&jaar={jaar}").text
    assert "Afzetdekking" in pagina and "Moederdag" in pagina and "Oogst en kWh per week" in pagina
    with SessionLocal() as db:
        v = plannen.vergelijk(db, vast.id, concept.id, jaar)
    assert v["totaal"]["oogst_a"] < v["totaal"]["oogst_b"]  # het vastgestelde plan heeft in dat jaar (bijna) niets
    assert len(v["weken"]) >= 52 and {"oogst_a", "oogst_b", "kwh_a", "kwh_b"} <= set(v["weken"][0])
    moederdag = next(d for d in v["dekking"] if d["naam"] == "Moederdag")
    assert moederdag["venster"] is not None and moederdag["aanvoer_b"] is not None
    assert all(d["aanvoer_a"] is None for d in v["dekking"] if d["naam"] == "Kerst")  # venster onbekend
    # Jaaroverzicht: kappen × weken, oogst, kWh, afzet, feestdagen
    pagina = beheerder.get(f"/jaar?scenario={concept.id}&jaar={jaar}").text
    assert "fase-KD" in pagina and "Moederdag" in pagina and "feestdagen" in pagina
    # Vaststellen: precies één vastgesteld
    beheerder.post(f"/scenarios/{concept.id}/vaststellen")
    with SessionLocal() as db:
        assert [s.id for s in db.scalars(select(Scenario).where(Scenario.status == "vastgesteld"))] == [concept.id]
