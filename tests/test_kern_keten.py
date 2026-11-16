"""Datumketen per kap (BOUWPLAN §6.1) en aantallen (§6.2)."""

from dataclasses import replace
from datetime import date, timedelta

from teeltplanning.kern import KapInvoer, KernInstellingen, Parameters, PlanInvoer, TeeltInvoer, Veld, bereken_plan
from teeltplanning.kern.typen import Blokkade, Uitvalregel

INST = KernInstellingen(licht_aan_na_licht_uit_dagen=12, plantdagen=frozenset({1, 2, 3, 4, 5, 6}), wisseltijd_dagen=0,
                        plantdatum_volgt_keten=True)  # fmt: skip


def teelt(id_, ronde, kap=1, plant=None, ld=14, rt=50, verl=0, **extra):
    velden = {"ld_dagen": Veld(handmatig=ld), "reactietijd": Veld(handmatig=rt), "verlenging": Veld(handmatig=verl)}
    if plant:
        velden["plantdatum"] = Veld(handmatig=plant)
    for naam, waarde in extra.items():
        velden[naam] = Veld(handmatig=waarde)
    return TeeltInvoer(id=id_, kap=kap, ronde=ronde, velden=velden)


def plan(teelten, inst=INST, kappen=None):
    return bereken_plan(PlanInvoer(tuple(teelten)), Parameters(kappen=kappen or {1: KapInvoer(1)}, instellingen=inst))


def test_formules():
    p = plan([teelt(1, 1, plant=date(2026, 3, 2), ld=14, rt=50, verl=2, tussenlicht_dagen=5)]).teelten[1]
    assert p.licht_uit == date(2026, 3, 16)
    assert p.oogst == date(2026, 3, 16) + timedelta(days=52)
    assert p.teeltduur == 66
    assert p.licht_aan == date(2026, 3, 28)  # licht uit + 12
    assert p.licht_uit_2 == date(2026, 4, 2)


def test_teelt_over_jaarwisseling_2026_2027_kent_week_53():
    p = plan([teelt(1, 1, plant=date.fromisocalendar(2026, 50, 1), ld=14, rt=0)]).teelten[1]
    assert p.licht_uit == date(2026, 12, 21)  # week 52
    p = plan([teelt(1, 1, plant=date.fromisocalendar(2026, 51, 3), ld=7, rt=0)]).teelten[1]
    assert p.licht_uit.isocalendar()[:3] == (2026, 52, 3)
    p = plan([teelt(1, 1, plant=date.fromisocalendar(2026, 51, 3), ld=14, rt=7)]).teelten[1]
    assert p.licht_uit.isocalendar()[:3] == (2026, 53, 3)  # het werkboek zou hier week 1 zeggen
    assert p.oogst.isocalendar()[:3] == (2027, 1, 3)


def test_wijziging_schuift_door_tot_vastgezette_plantdatum():
    basis = [
        teelt(1, 1, plant=date(2026, 1, 5)),
        teelt(2, 2),  # volgt de keten
        teelt(3, 3),  # volgt de keten
        teelt(4, 4, plant=date(2026, 12, 1)),  # vastgezet
    ]
    voor = plan(basis)
    langer = [replace(basis[0], velden=basis[0].velden | {"ld_dagen": Veld(handmatig=21)}), *basis[1:]]
    na = plan(langer)
    assert na.teelten[2].effectief["plantdatum"] > voor.teelten[2].effectief["plantdatum"]
    assert na.teelten[3].effectief["plantdatum"] > voor.teelten[3].effectief["plantdatum"]
    assert na.teelten[4].effectief["plantdatum"] == voor.teelten[4].effectief["plantdatum"] == date(2026, 12, 1)
    assert na.teelten[2].bron["plantdatum"] == "voorstel" and na.teelten[4].bron["plantdatum"] == "handmatig"


def test_plantdatumvoorstel_oogst_plus_wisseltijd_naar_plantdag():
    eerste = teelt(1, 1, plant=date(2026, 1, 5), ld=13, rt=0)  # oogst zondag 18 januari
    p = plan([eerste, teelt(2, 2)]).teelten[2]
    assert p.voorstel["plantdatum"] == date(2026, 1, 19)  # maandag
    p = plan([eerste, teelt(2, 2)], kappen={1: KapInvoer(1, wisseltijd_dagen=3)}).teelten[2]
    assert p.voorstel["plantdatum"] == date(2026, 1, 21)
    stomen = replace(INST, blokkades=(Blokkade(date(2026, 1, 19), date(2026, 1, 24)),))
    assert plan([eerste, teelt(2, 2)], inst=stomen).teelten[2].voorstel["plantdatum"] == date(2026, 1, 26)
    zonder_keten = replace(INST, plantdatum_volgt_keten=False)
    assert plan([eerste, teelt(2, 2)], inst=zonder_keten).teelten[2].effectief["plantdatum"] is None


def test_overlap_is_fout_maar_blokkeert_niet():
    p = plan([teelt(1, 1, plant=date(2026, 1, 5)), teelt(2, 2, plant=date(2026, 2, 1))])
    assert p.teelten[2].heeft_fout
    assert any(m.code == "overlap" for m in p.teelten[2].meldingen)
    assert p.teelten[2].licht_uit is not None  # er wordt gewoon verder gerekend


def test_onbekende_waarden_geven_null_en_melding():
    p = plan([TeeltInvoer(id=1, kap=1, ronde=1, velden={"plantdatum": Veld(handmatig=date(2026, 1, 5))})]).teelten[1]
    assert p.licht_uit is None and p.oogst is None and p.teeltduur is None
    assert {m.code for m in p.meldingen} >= {"onbekend_ld_dagen", "onbekend_reactietijd", "onbekend_verlenging"}


def test_aantallen_en_oppervlak_onbekend():
    t = teelt(1, 1, plant=date(2026, 3, 2), dichtheid=50.0, uitval=0.08)
    p = plan([t]).teelten[1]
    assert p.geplant is None and p.oogstaantal is None
    assert any(m.code == "onbekend_oppervlak" for m in p.meldingen)
    met = replace(INST, oppervlak_per_kap=((None, None), (date(2020, 1, 1), 726.0), (date(2027, 1, 1), 750.0)))
    p = plan([t], inst=met).teelten[1]
    assert p.geplant == 50.0 * 726 and p.oogstaantal == 50.0 * 726 * (1 - 0.08)
    p = plan([t], kappen={1: KapInvoer(1, oppervlak_m2=700.0)}, inst=met).teelten[1]
    assert p.geplant == 50.0 * 700


def test_uitvalvoorstel_uit_tabel_en_handmatig_wint():
    regels = (Uitvalregel(1, 11, 0.08), Uitvalregel(12, 53, 0.05))
    inst = replace(INST, uitval_per_oogstweek=regels)
    p = plan([teelt(1, 1, plant=date(2026, 1, 5), ld=7, rt=7)], inst=inst).teelten[1]  # oogst week 4
    assert p.effectief["uitval"] == 0.08 and p.bron["uitval"] == "voorstel"
    p = plan([teelt(1, 1, plant=date(2026, 5, 4), ld=7, rt=7)], inst=inst).teelten[1]
    assert p.effectief["uitval"] == 0.05
    p = plan([teelt(1, 1, plant=date(2026, 1, 5), ld=7, rt=7, uitval=0.1)], inst=inst).teelten[1]
    assert p.effectief["uitval"] == 0.1 and p.bron["uitval"] == "handmatig"


def test_grenzen_geven_fout():
    inst = replace(INST, grenzen={"verlenging": (0, 2)})
    p = plan([teelt(1, 1, plant=date(2026, 1, 5), verl=3)], inst=inst).teelten[1]
    assert any(m.code == "buiten_grenzen_verlenging" and m.niveau == "fout" for m in p.meldingen)


def test_deterministisch():
    teelten = [
        teelt(i, r, kap=k, plant=date(2026, 1, 5) if r == 1 else None)
        for i, (r, k) in enumerate(((1, 1), (2, 1), (1, 2)), 1)
    ]
    kappen = {1: KapInvoer(1), 2: KapInvoer(2)}
    assert plan(teelten, kappen=kappen) == plan(list(reversed(teelten)), kappen=kappen)
