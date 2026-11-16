"""Herberekening van het hele plan (34 rondes × 40 kappen) blijft ruim onder 200 ms (BOUWPLAN §4.1)."""

import time
from datetime import date

from teeltplanning.kern import KapInvoer, KernInstellingen, Parameters, PlanInvoer, TeeltInvoer, Veld, bereken_plan
from teeltplanning.kern.typen import Uitvalregel


def test_hele_plan_onder_200_ms():
    inst = KernInstellingen(licht_aan_na_licht_uit_dagen=12, plantdagen=frozenset(range(1, 7)), wisseltijd_dagen=0,
                            plantdatum_volgt_keten=True, oppervlak_per_kap=((None, 726.0),),
                            uitval_per_oogstweek=(Uitvalregel(1, 53, 0.05),),
                            grenzen={"ld_dagen": (0, 365), "reactietijd": (0, 365), "verlenging": (0, 2)})  # fmt: skip
    teelten = []
    for ronde in range(1, 35):
        for kap in range(1, 41):
            velden = {"ld_dagen": Veld(handmatig=14), "reactietijd": Veld(handmatig=52), "verlenging": Veld(handmatig=1),
                      "dichtheid": Veld(handmatig=50.0)}  # fmt: skip
            if ronde == 1:
                velden["plantdatum"] = Veld(handmatig=date(2018, 1, 1))
            teelten.append(TeeltInvoer(id=ronde * 100 + kap, kap=kap, ronde=ronde, velden=velden))
    invoer = PlanInvoer(tuple(teelten))
    parameters = Parameters(kappen={k: KapInvoer(k) for k in range(1, 41)}, instellingen=inst)
    tijden = []
    for _ in range(5):
        start = time.perf_counter()
        plan = bereken_plan(invoer, parameters)
        tijden.append(time.perf_counter() - start)
    assert len(plan.teelten) == 1360
    assert all(u.oogst is not None for u in plan.teelten.values())
    print(f"\nrekentijd hele plan: {min(tijden) * 1000:.1f} ms (beste van 5)")
    assert min(tijden) < 0.2


def test_hele_plan_met_modelvoorstellen_onder_200_ms():
    """Zelfde plan, maar LD-dagen, reactietijd, verlenging en gewicht komen uit de voorstelladder (fase 4)."""
    from teeltplanning.voorstel import model
    from tests.test_model import historie, instellingen

    obs = historie(jaren=range(2010, 2018), per_jaar=60)
    artefact = model.train([w.naar_dict() for w in obs], instellingen().naar_dict(), "m-prestatie")
    voorspeller = model.Voorspeller(artefact, {d: True for d in model.DOELEN})
    inst = KernInstellingen(licht_aan_na_licht_uit_dagen=12, plantdagen=frozenset(range(1, 7)), wisseltijd_dagen=0,
                            plantdatum_volgt_keten=True, oppervlak_per_kap=((None, 726.0),))  # fmt: skip
    teelten = []
    for ronde in range(1, 35):
        for kap in range(1, 41):
            velden = {"dichtheid": Veld(handmatig=50.0)}
            if ronde == 1:
                velden["plantdatum"] = Veld(handmatig=date(2018, 1, 1))
            teelten.append(TeeltInvoer(id=ronde * 100 + kap, kap=kap, ronde=ronde, velden=velden,
                                       rassen=((("a", "p", "t")[kap % 3], None),)))  # fmt: skip
    invoer = PlanInvoer(tuple(teelten))
    tijden = []
    for _ in range(5):
        parameters = Parameters(kappen={k: KapInvoer(k) for k in range(1, 41)}, instellingen=inst,
                                voorspeller=voorspeller.met({d: True for d in model.DOELEN}, None))  # fmt: skip
        start = time.perf_counter()
        plan = bereken_plan(invoer, parameters)
        tijden.append(time.perf_counter() - start)
    assert all(u.oogst is not None and u.bron["ld_dagen"] == "voorstel" for u in plan.teelten.values())
    print(f"\nrekentijd hele plan met modelvoorstellen: {min(tijden) * 1000:.1f} ms (beste van 5)")
    assert min(tijden) < 0.2
