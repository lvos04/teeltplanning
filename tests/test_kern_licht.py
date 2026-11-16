"""Lichtsom (§6.3), LBE/dichtheid/gewicht (§6.5) en energie (§6.6) in de rekenkern."""

import time
from datetime import date, timedelta

import pytest

from teeltplanning.kern import (
    KapInvoer,
    KernInstellingen,
    Parameters,
    PlanInvoer,
    TeeltInvoer,
    Veld,
    bereken_plan,
    gewas,
)
from teeltplanning.kern import energie as en
from teeltplanning.kern.licht import Lampgroep, LichtInvoer, Rekenaar, Zondag, percentiel, weeknormalen

# --- §6.5: gepubliceerde proefcijfers (binnen 0,2) -----------------------------------------------------------


def test_proefcijfers_takgewicht():
    assert gewas.takgewicht(gewas.productie(7.3, 938), 63.8) == pytest.approx(107.3, abs=0.2)
    assert gewas.takgewicht(gewas.productie(7.5, 640), 48.8) == pytest.approx(98.4, abs=0.2)


def test_proefcijfers_dichtheid():
    assert gewas.dichtheid_voorstel(6.8, 834, 98) == pytest.approx(57.9, abs=0.2)


def test_lbe_gerealiseerd_is_de_omkering():
    assert gewas.lbe_gerealiseerd(107.3, 63.8, 938) == pytest.approx(7.3, abs=0.01)
    assert gewas.lbe_gerealiseerd(None, 63.8, 938) is None and gewas.lbe_gerealiseerd(100, 50, 0) is None


def test_afronden_op_praktijkdichtheden():
    reeks = (41.1, 45.0, 50.0, 55.0, 58.6)
    assert gewas.afronden(57.87, reeks) == 58.6 and gewas.afronden(47.4, reeks) == 45.0
    assert gewas.afronden(57.87, ()) == 57.87  # lege reeks: niet afronden


def test_lbe_terugval_op_seizoensgemiddelde():
    inv = gewas.GewasInvoer(lbe={("a", 10, ""): gewas.LbeCel(6.0, 2), ("a", 11, ""): gewas.LbeCel(7.0, 2),
                                 ("a", 30, ""): gewas.LbeCel(9.0, 50)}, min_waarnemingen=5, venster_weken=2)  # fmt: skip
    keuze = gewas.kies_lbe(inv, "a", 10, "", 52)
    assert keuze.status == "indicatief" and keuze.waarde == pytest.approx(6.5) and keuze.aantal == 4
    assert gewas.kies_lbe(inv, "a", 30, "", 52).status == "gevalideerd"
    assert gewas.kies_lbe(inv, "p", 10, "", 52).waarde is None
    # Jaarwisseling: week 52 ligt binnen twee weken van week 1
    inv = gewas.GewasInvoer(lbe={("a", 52, ""): gewas.LbeCel(5.0, 1)}, min_waarnemingen=5, venster_weken=2)
    assert gewas.kies_lbe(inv, "a", 1, "", 52).waarde == 5.0
    # Minimum onbekend: nooit "gevalideerd"
    inv = gewas.GewasInvoer(lbe={("a", 10, ""): gewas.LbeCel(6.0, 99)}, min_waarnemingen=None)
    assert gewas.kies_lbe(inv, "a", 10, "", 52).status == "indicatief"


def test_gemengde_kap_gewogen_naar_bedden():
    inv = gewas.GewasInvoer(lbe={("a", 10, ""): gewas.LbeCel(6.0, 9), ("p", 10, ""): gewas.LbeCel(9.0, 9)},
                            doelgewicht={"a": 100.0, "p": 70.0}, min_waarnemingen=5)  # fmt: skip
    lbe, doel, _ = gewas.lbe_teelt(inv, (("a", 4), ("p", 2)), 10, "", 52)
    assert lbe == pytest.approx(7.0) and doel == pytest.approx(90.0)


# --- §6.3: lichtsom -------------------------------------------------------------------------------------------
VANDAAG = date(2026, 11, 16)
LED = Lampgroep("LED fase 2", 120.0, 27.5, True, 101, date(2024, 7, 1), None)


def licht(**over) -> LichtInvoer:
    basis = dict(
        zon={date(2026, 3, 1) + timedelta(days=i): Zondag(1000.0, "gemeten") for i in range(10)},
        dag_uren={(101 + k - 1, date(2026, 3, 1) + timedelta(days=i)): (10.0, True) for i in range(10) for k in (1, 2)},
        week_uren={}, normaal_uren={}, lampgroepen=(LED,), peildatum=VANDAAG, par_factor=0.023,
        transmissie=((None, 0.7),), prijs=((None, 100.0),), opslagen=((None, 20.0),),
    )  # fmt: skip
    return LichtInvoer(**(basis | over))


def test_lichtsom_zon_en_lamp():
    r = Rekenaar(licht())
    som = r.lichtsom(1, date(2026, 3, 1), date(2026, 3, 11))
    zon_dag = 1000 * 0.023 * 0.7  # 16,1 mol/m²/dag
    lamp_dag = 120 * 10 * 0.0036  # 4,32 mol/m²/dag
    assert som.dagen == 10 and som.zon == pytest.approx(10 * zon_dag) and som.lamp == pytest.approx(10 * lamp_dag)
    assert som.totaal == pytest.approx(10 * (zon_dag + lamp_dag)) and som.soorten == {"gemeten": 10}
    # kap 2 gebruikt module 102
    assert r.lichtsom(2, date(2026, 3, 1), date(2026, 3, 11)).totaal == pytest.approx(som.totaal)


def test_ontbrekende_dag_geeft_geen_stilzwijgende_lichtsom():
    inv = licht()
    uren = dict(inv.dag_uren)
    del uren[(101, date(2026, 3, 5))]
    som = Rekenaar(licht(dag_uren=uren)).lichtsom(1, date(2026, 3, 1), date(2026, 3, 11))
    assert som.totaal is None and som.zon is None
    assert som.ontbrekend == ((date(2026, 3, 5), date(2026, 3, 5), "geen branduren voor module 101"),)
    # onvolledige dag telt standaard als ontbrekend
    uren[(101, date(2026, 3, 5))] = (9.0, False)
    som = Rekenaar(licht(dag_uren=uren)).lichtsom(1, date(2026, 3, 1), date(2026, 3, 11))
    assert som.totaal is None and "onvolledige belichtingsdag" in som.ontbrekend[0][2]
    som = Rekenaar(licht(dag_uren=uren, onvolledig_is_ontbrekend=False)).lichtsom(
        1, date(2026, 3, 1), date(2026, 3, 11)
    )
    assert som.totaal is not None and som.notities


@pytest.mark.parametrize(
    ("over", "reden"),
    [
        ({"transmissie": ((None, None),)}, "kastransmissie onbekend"),
        ({"lampgroepen": (Lampgroep("SON-T", 60.0, 25.0, False, 101, None, None),)}, "geldigheidsperiode onbekend"),
        ({"zon": {}}, "geen straling"),
        ({"lampgroepen": (Lampgroep("x", None, 1.0, True, 101, date(2020, 1, 1), None),)}, "intensiteit onbekend"),
    ],
)
def test_onbekende_invoer_geeft_reden(over, reden):
    som = Rekenaar(licht(**over)).lichtsom(1, date(2026, 3, 1), date(2026, 3, 3))
    assert som.totaal is None and reden in som.ontbrekend[0][2]


def test_lampgroep_buiten_periode_telt_niet_mee():
    oud = Lampgroep("SON-T", 60.0, 25.0, False, 101, date(2020, 1, 1), date(2024, 6, 30))
    som = Rekenaar(licht(lampgroepen=(oud, LED))).lichtsom(1, date(2026, 3, 1), date(2026, 3, 3))
    assert som.lamp == pytest.approx(2 * 120 * 10 * 0.0036)


def test_toekomst_weeknormaal_en_historisch_gemiddelde():
    toekomst = date(2027, 3, 1)
    zon = {toekomst + timedelta(days=i): Zondag(700.0, "normaal", 500.0, 900.0) for i in range(7)}
    inv = licht(zon=zon, normaal_uren={(101, toekomst.isocalendar().week): 8.0})
    som = Rekenaar(inv).lichtsom(1, toekomst, toekomst + timedelta(days=7))
    assert som.soorten == {"normaal": 7}
    assert som.laag < som.totaal < som.hoog
    assert som.lamp == pytest.approx(7 * 120 * 8 * 0.0036)
    som = Rekenaar(licht(zon=zon, toekomst_branduren="regel")).lichtsom(1, toekomst, toekomst + timedelta(days=2))
    assert som.totaal is None and "nog niet beschikbaar" in som.ontbrekend[0][2]


def test_weekuren_gelijk_verdeeld():
    week = date(2024, 1, 1)  # maandag week 1
    son = Lampgroep("SON-T rest", 60.0, 25.0, False, 101, date(2023, 7, 1), date(2024, 6, 30))
    inv = licht(zon={week + timedelta(days=i): Zondag(500.0, "gemeten") for i in range(7)}, dag_uren={},
                week_uren={(101, 2024, 1): 70.0}, lampgroepen=(son,))  # fmt: skip
    som = Rekenaar(inv).lichtsom(1, week, week + timedelta(days=7))
    assert som.lamp == pytest.approx(60 * 70 * 0.0036) and "weekuren gelijk over de dagen verdeeld" in som.notities
    assert (
        Rekenaar(licht(**{**inv.__dict__, "weekuren_verdelen": False}))
        .lichtsom(1, week, week + timedelta(days=7))
        .totaal
        is None
    )


def test_weeknormalen_percentielen():
    assert percentiel([1, 2, 3, 4, 5], 50) == 3 and percentiel([1, 2, 3, 4, 5], 10) == pytest.approx(1.4)
    dagen = {}
    for jaar in range(2000, 2025):
        start = date.fromisocalendar(jaar, 10, 1)
        for i in range(7):
            dagen[start + timedelta(days=i)] = float(jaar - 1999)
    normaal = weeknormalen(dagen, (10, 50, 90), 30, 20, date(2026, 1, 1))
    assert normaal[10][3] == 25 and normaal[10][1] == pytest.approx(7 * 13)
    assert weeknormalen(dagen, (10, 50, 90), 30, 26, date(2026, 1, 1))[10][:3] == (None, None, None)
    assert weeknormalen(dagen, (10, 50, 90), 5, 1, date(2026, 1, 1))[10][3] == 5  # alleen de laatste jaren


# --- §6.6: energie -----------------------------------------------------------------------------------------
def test_energie_en_kosten():
    r = Rekenaar(licht())
    e = en.energie_teelt(r, 1, date(2026, 3, 1), date(2026, 3, 11), 30000)
    assert e.kwh == pytest.approx(10 * 27.5 * 10)  # kW × uren
    assert e.kosten == pytest.approx(e.kwh * 120 / 1000)  # (100 + 20) €/MWh
    assert e.kosten_per_tak == pytest.approx(e.kosten / 30000)
    zonder_prijs = en.energie_teelt(Rekenaar(licht(prijs=((None, None),))), 1, date(2026, 3, 1), date(2026, 3, 3), 1)
    assert zonder_prijs.kwh is not None and zonder_prijs.kosten is None and zonder_prijs.dagen_zonder_prijs == 2
    weken = en.per_kap_per_week(r, [1, 2], date(2026, 3, 2), date(2026, 3, 9))
    assert len(weken) == 2 and weken[0].kwh == pytest.approx(7 * 275) and weken[0].soort == "gerealiseerd"
    bedrijf = en.bedrijf_per_week(weken)
    assert bedrijf[0]["kwh"] == pytest.approx(2 * 7 * 275)
    assert en.vermogen_w_per_m2(120, 3.0) == 40 and en.vermogen_w_per_m2(120, None) is None


def test_scenariovergelijking_150_tegenover_200():
    g = en.Gemeenschappelijk(oppervlak_m2=726, lichtsom_zon=500, lbe=6.0, doelgewicht_g=80, prijs_eur_per_mwh=80,
                             opbrengst_per_tak=0.25)  # fmt: skip
    a = en.Lichtscenario("150 µmol", 150, 1200, 40)
    b = en.Lichtscenario("200 µmol", 200, 1200, 55)
    uit = en.vergelijk(a, b, g)
    assert uit["a"]["lichtsom_lamp"] == pytest.approx(150 * 1200 * 0.0036)
    assert uit["verschil"]["mwh_per_kap"] == pytest.approx(15 * 1200 / 1000)
    assert uit["verschil"]["takken_per_m2"] == pytest.approx(6.0 * 50 * 1200 * 0.0036 / 80)
    assert uit["meeropbrengst"] == pytest.approx(uit["verschil"]["opbrengst"] - uit["verschil"]["kosten"])
    leeg = en.vergelijk(a, b, en.Gemeenschappelijk(726, 500, 6.0, None, 80, None))
    assert leeg["a"]["takken_per_m2"] is None and leeg["meeropbrengst"] is None


# --- Alles samen in bereken_plan ---------------------------------------------------------------------------
INST = KernInstellingen(licht_aan_na_licht_uit_dagen=12, plantdagen=frozenset(range(1, 7)), wisseltijd_dagen=0,
                        plantdatum_volgt_keten=True, oppervlak_per_kap=((None, 726.0),))  # fmt: skip


def _teelt(i, kap, ronde, plant=None, **hand):
    velden = {"ld_dagen": Veld(handmatig=14), "reactietijd": Veld(handmatig=50), "verlenging": Veld(handmatig=0)}
    if plant:
        velden["plantdatum"] = Veld(handmatig=plant)
    velden |= {k: Veld(handmatig=v) for k, v in hand.items()}
    return TeeltInvoer(i, kap, ronde, velden=velden, rassen=(("a", None),))


def test_plan_dichtheid_en_gewichtvoorstel():
    start = date(2026, 1, 5)
    zon = {start + timedelta(days=i): Zondag(1000.0, "gemeten") for i in range(200)}
    uren = {(101, start + timedelta(days=i)): (10.0, True) for i in range(200)}
    inv = licht(zon=zon, dag_uren=uren)
    g = gewas.GewasInvoer(lbe={("a", 2, ""): gewas.LbeCel(6.0, 10)}, doelgewicht={"a": 90.0}, min_waarnemingen=5,
                          dichtheid_reeks=(41.1, 50.0, 58.6, 64.0))  # fmt: skip
    p = bereken_plan(PlanInvoer((_teelt(1, 1, 1, start), _teelt(2, 1, 2, dichtheid=50.0))),
                     Parameters({1: KapInvoer(1)}, INST, licht=inv, gewas=g))  # fmt: skip
    t = p.teelten[1]
    som = 64 * (1000 * 0.023 * 0.7 + 120 * 10 * 0.0036)
    assert t.lichtsom.totaal == pytest.approx(som) and t.lbe.status == "gevalideerd"
    assert t.dichtheid_onafgerond == pytest.approx(6.0 * som / 90)
    assert t.voorstel["dichtheid"] == 64.0 and t.bron["dichtheid"] == "voorstel"
    assert t.effectief["gewicht"] == pytest.approx(6.0 * som / 64.0)
    assert t.geplant == pytest.approx(64.0 * 726) and t.energie.kwh == pytest.approx(64 * 275)
    # tweede teelt: handmatige dichtheid wint, gewichtsvoorstel rekent met de handmatige dichtheid
    t2 = p.teelten[2]
    assert t2.bron["dichtheid"] == "handmatig" and t2.effectief["dichtheid"] == 50.0
    # plantweek 11 staat niet in de LBE-tabel (venster 0): geen LBE, dus geen gewichtsvoorstel, wel een melding
    assert (
        t2.lbe.waarde is None and t2.voorstel["gewicht"] is None and any(m.code == "lbe_onbekend" for m in t2.meldingen)
    )


def test_snelle_en_trage_rekenweg_gelijk():
    """De prefixsommen van het hele plan geven hetzelfde als dag voor dag rekenen."""
    import random

    rnd = random.Random(7)
    start = date(2025, 1, 6)
    zon = {start + timedelta(days=i): Zondag(rnd.uniform(100, 2000), "gemeten") for i in range(300) if i != 150}
    uren = {
        (100 + k, start + timedelta(days=i)): (rnd.uniform(0, 16), rnd.random() > 0.02)
        for i in range(300)
        for k in (1, 2)
    }
    son = Lampgroep("SON-T", 60.0, None, False, 101, date(2024, 1, 1), date(2025, 4, 30))
    inv = licht(zon=zon, dag_uren=uren, lampgroepen=(son, LED), onvolledig_is_ontbrekend=rnd.random() > 0.5)
    snel, traag = Rekenaar(inv), Rekenaar(inv)
    snel.bereid_voor(start, start + timedelta(days=300))
    for _ in range(200):
        van = start + timedelta(days=rnd.randint(0, 250))
        tot = van + timedelta(days=rnd.randint(1, 49))
        kap = rnd.choice((1, 2))
        a, b = snel.lichtsom(kap, van, tot), traag._lichtsom_per_dag(kap, van, tot)
        assert (a.totaal is None) == (b.totaal is None) and a.ontbrekend == b.ontbrekend
        if a.totaal is not None:
            assert a.totaal == pytest.approx(b.totaal) and a.laag == pytest.approx(b.laag) and a.soorten == b.soorten
        sa, sb = snel.energie_som(kap, van, tot), traag.energie_som(kap, van, tot)
        assert sa[2:] == sb[2:] and sa[0] == pytest.approx(sb[0]) and sa[1] == pytest.approx(sb[1])


def test_hele_plan_met_licht_onder_200_ms():
    start = date(2019, 1, 7)
    dagen = 8 * 365
    zon = {start + timedelta(days=i): Zondag(800.0, "gemeten") for i in range(dagen)}
    uren = {(100 + k, start + timedelta(days=i)): (9.0, True) for i in range(dagen) for k in range(1, 41)}
    inv = licht(zon=zon, dag_uren=uren, peildatum=start + timedelta(days=dagen))
    g = gewas.GewasInvoer(lbe={("a", w, ""): gewas.LbeCel(6.0, 10) for w in range(1, 54)}, doelgewicht={"a": 90.0},
                          min_waarnemingen=5)  # fmt: skip
    teelten = [_teelt(r * 100 + k, k, r, start if r == 1 else None) for r in range(1, 35) for k in range(1, 41)]
    param = Parameters({k: KapInvoer(k) for k in range(1, 41)}, INST, licht=inv, gewas=g)
    tijden = []
    for _ in range(3):
        begin = time.perf_counter()
        plan = bereken_plan(PlanInvoer(tuple(teelten)), param)
        tijden.append(time.perf_counter() - begin)
    bekend = sum(1 for u in plan.teelten.values() if u.lichtsom and u.lichtsom.bekend)
    print(f"\nrekentijd hele plan met licht: {min(tijden) * 1000:.0f} ms; lichtsom bekend: {bekend}")
    assert bekend > 1000
    assert min(tijden) < 0.2
