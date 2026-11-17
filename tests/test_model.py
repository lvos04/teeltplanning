"""Voorstelladder en voortschrijdende backtest (BOUWPLAN §6.7, §8.3) op synthetische historie."""

import random
from datetime import date, timedelta

import pytest

from teeltplanning.kern.typen import Voorspelling
from teeltplanning.voorstel import model

LBE = {"a": 5.6, "p": 6.1, "t": 5.0}


def instellingen(**over) -> model.ModelInstellingen:
    basis = dict(venster=2, terugkijk=None, min_waarnemingen=1, band=(10.0, 90.0), treden=("lbe", "ridge", "bomen", "temperatuur"),
                 kenmerken_ridge=("plantweek", "lichtsom", "dichtheid", "ld_dagen", "ras"),
                 kenmerken_bomen=("plantweek", "lichtsom", "dichtheid", "ld_dagen", "ras"),
                 monotoon={"lichtsom": 1, "dichtheid": -1}, drempel_pct=None, backtest_jaren=None, min_toets=20,
                 ridge_alpha=1.0, bomen_aantal=40, bomen_leersnelheid=0.1, bomen_min_blad=5, lbe_min_waarnemingen=None,
                 temperatuur_aan=False, referentietemperatuur=None, vergelijkbaar=3, gevoeligheid={})  # fmt: skip
    return model.ModelInstellingen(**(basis | over))


def historie(zaad=7, jaren=range(2019, 2025), per_jaar=45, signaal=True) -> list[model.Waarneming]:
    """Synthetisch: gewicht = LBE(ras) × lichtsom / dichtheid × ruis. De lichtsom hangt sterk af van de (willekeurige)
    lampuren, niet alleen van het seizoen, zodat de LBE-identiteit de seizoensbasis moet verslaan. Zonder signaal is
    het gewicht pure ruis: dan mag geen trede de seizoensbasis verslaan."""
    rnd = random.Random(zaad)
    uit = []
    for jaar in jaren:
        for i in range(per_jaar):
            plant = date.fromisocalendar(jaar, 1 + (i * 7) % 50, 1 + i % 6)
            ras = "apt"[i % 3]
            ld = rnd.randint(8, 20)
            dichtheid = rnd.uniform(42, 58)
            lichtsom = rnd.uniform(500, 1100)
            if signaal:
                gewicht = LBE[ras] * lichtsom / dichtheid * rnd.uniform(0.97, 1.03)
            else:
                gewicht = rnd.uniform(70, 120)
            uit.append(model.Waarneming(sleutel=f"s:{jaar}:{i}", bron="synthetisch", kap=i % 40 + 1, ras=ras,
                                        plantdatum=plant, ld_dagen=ld, verlenging=rnd.choice([0, 0, 1, 2]),
                                        reactietijd=rnd.randint(49, 56), gewicht=gewicht, dichtheid=dichtheid,
                                        lichtsom=lichtsom))  # fmt: skip
    return uit


def test_seizoensbasis_mediaan_eerdere_jaren_en_venster():
    obs = [model.Waarneming(f"x{i}", "s", 1, "a", date.fromisocalendar(2020 + i % 3, 10 + i % 5, 1), ld_dagen=10 + i)
           for i in range(15)]  # fmt: skip
    basis = model.Seizoensbasis(obs, "ld_dagen", instellingen())
    plant = date.fromisocalendar(2022, 12, 3)
    venster = basis.venster("a", plant)
    assert venster and all(w.jaar < 2022 and abs(w.week - 12) <= 2 for w in venster)
    s = basis.voorspel((("a", None),), plant)
    waarde, laag, hoog, n = s.waarde, s.laag, s.hoog, s.aantal
    assert waarde == pytest.approx(sorted(w.ld_dagen for w in venster)[len(venster) // 2] if len(venster) % 2 else
                                   (sorted(w.ld_dagen for w in venster)[len(venster) // 2 - 1] + sorted(w.ld_dagen for w in venster)[len(venster) // 2]) / 2)  # fmt: skip
    assert laag <= waarde <= hoog and n == len(venster)
    # Eerste jaar: geen eerdere jaren → geen voorstel (onbekend), niets verzonnen.
    assert basis.voorspel((("a", None),), date.fromisocalendar(2020, 12, 1)) is None
    # Ras zonder historie → geen voorstel
    assert basis.voorspel((("z", None),), plant) is None
    # Terugkijkperiode: alleen het laatste jaar
    kort = model.Seizoensbasis(obs, "ld_dagen", instellingen(terugkijk=1))
    assert {w.jaar for w in kort.venster("a", plant)} == {2021}


def test_seizoensbasis_over_de_jaarwisseling():
    obs = [model.Waarneming("x1", "s", 1, "a", date.fromisocalendar(2020, 52, 1), ld_dagen=12),
           model.Waarneming("x2", "s", 1, "a", date.fromisocalendar(2021, 1, 1), ld_dagen=14)]  # fmt: skip
    basis = model.Seizoensbasis(obs, "ld_dagen", instellingen())
    assert {w.sleutel for w in basis.venster("a", date.fromisocalendar(2022, 1, 1))} == {"x1", "x2"}


def test_ridge_vindt_lineair_verband():
    rnd = random.Random(1)
    x = [[rnd.uniform(0, 10), rnd.uniform(-5, 5)] for _ in range(200)]
    y = [3 * a - 2 * b + 7 for a, b in x]
    r = model.Ridge.fit(x, y, 0.001)
    assert r.voorspel([4, 1]) == pytest.approx(3 * 4 - 2 + 7, abs=0.01)


def test_bomen_zijn_monotoon():
    rnd = random.Random(2)
    x = [[rnd.uniform(0, 10), rnd.uniform(0, 10)] for _ in range(300)]
    # Ruis die zonder beperking ook dalende stukken zou geven
    y = [a + rnd.uniform(-3, 3) - 0.5 * b for a, b in x]
    bomen = model.Bomen.fit(x, y, [1, -1], 60, 0.2, 5)
    vast = 5.0
    reeks = [bomen.voorspel([a / 10, vast]) for a in range(0, 101)]
    assert all(b >= a - 1e-12 for a, b in zip(reeks, reeks[1:], strict=False))
    reeks = [bomen.voorspel([vast, b / 10]) for b in range(0, 101)]
    assert all(b <= a + 1e-12 for a, b in zip(reeks, reeks[1:], strict=False))
    assert bomen.voorspel([9, 5]) > bomen.voorspel([1, 5])


def test_backtest_voortschrijdend_trainen_tm_jaar_n_toetsen_op_n_plus_1(monkeypatch):
    obs = historie()
    gezien = []
    echte_ladder = model.Ladder

    def ladder(doel, training, inst, fit=True):
        gezien.append((doel, max(w.jaar for w in training)))
        return echte_ladder(doel, training, inst, fit)

    monkeypatch.setattr(model, "Ladder", ladder)
    uitkomst = model.backtest(obs, "ld_dagen", instellingen())
    assert uitkomst["toetsjaren"] == [2020, 2021, 2022, 2023, 2024]
    assert [j for _, j in gezien] == [2019, 2020, 2021, 2022, 2023]  # train t/m N, toets op N+1
    assert set(uitkomst["treden"]["seizoensbasis"]["jaren"]) == {"2020", "2021", "2022", "2023", "2024"}


def test_model_alleen_gevalideerd_als_het_de_seizoensbasis_verslaat():
    met = model.backtest(historie(signaal=True), "gewicht", instellingen())
    assert met["treden"]["lbe"]["status"] == "gevalideerd"
    assert met["treden"]["lbe"]["mae"] < met["treden"]["lbe"]["mae_basis"]
    assert met["gekozen"] != "seizoensbasis"
    zonder = model.backtest(historie(signaal=False), "gewicht", instellingen())
    for trede in ("lbe", "ridge", "bomen"):
        r = zonder["treden"][trede]
        assert r["status"] != "gevalideerd", (trede, r["mae"], r["mae_basis"])
    assert zonder["gekozen"] == "seizoensbasis"


def test_drempel_en_minimum_toetsteelten():
    obs = historie(signaal=True)
    assert model.backtest(obs, "gewicht", instellingen(drempel_pct=99))["treden"]["lbe"]["status"] == "indicatief"
    r = model.backtest(obs, "gewicht", instellingen(min_toets=100000))["treden"]["lbe"]
    assert r["status"] == "indicatief" and "te weinig toetsteelten" in r["reden"]


def test_temperatuurcorrectie_alleen_als_ze_wint():
    rnd = random.Random(3)
    obs = []
    for jaar in range(2019, 2025):
        for i in range(40):
            t = rnd.uniform(14, 24)
            obs.append(model.Waarneming(f"t{jaar}{i}", "s", 1, "a", date.fromisocalendar(jaar, 1 + i % 50, 1),
                                        reactietijd=52 + 1.5 * (19 - t), temperatuur=t))  # fmt: skip
    goed = instellingen(temperatuur_aan=True, referentietemperatuur=19.0, gevoeligheid={"a": 1.5})
    assert model.backtest(obs, "reactietijd", goed)["gekozen"] == "temperatuur"
    fout = instellingen(temperatuur_aan=True, referentietemperatuur=19.0, gevoeligheid={"a": -3.0})
    assert model.backtest(obs, "reactietijd", fout)["treden"]["temperatuur"]["status"] != "gevalideerd"
    uit = instellingen(temperatuur_aan=False, referentietemperatuur=19.0, gevoeligheid={"a": 1.5})
    assert model.backtest(obs, "reactietijd", uit)["gekozen"] == "seizoensbasis"


def test_train_en_voorspeller_leveren_bron_band_modelversie_en_drie_vergelijkbare():
    obs = historie(signaal=True)
    artefact = model.train([w.naar_dict() for w in obs], instellingen().naar_dict(), "m1-test")
    v = model.Voorspeller(artefact, {d: True for d in model.DOELEN})
    plant = date.fromisocalendar(2025, 20, 2)
    ld = v.veld("ld_dagen", (("a", None),), plant, None)
    assert isinstance(ld, Voorspelling) and isinstance(ld.waarde, int)
    assert ld.modelversie == "m1-test" and ld.status == "indicatief" and ld.trede == "seizoensbasis"
    assert ld.ondergrens <= ld.waarde <= ld.bovengrens and "Seizoensbasis" in ld.bron
    assert len(ld.vergelijkbaar) == 3 and all(c.plantdatum.isocalendar().year < 2025 for c in ld.vergelijkbaar)
    gew = v.gewicht((("a", None),), plant, {"lichtsom": 800.0, "dichtheid": 50.0, "ld_dagen": 12})
    assert gew.status == "gevalideerd" and gew.trede in ("lbe", "ridge", "bomen")
    assert gew.waarde == pytest.approx(LBE["a"] * 800 / 50, rel=0.08)
    assert gew.ondergrens < gew.waarde < gew.bovengrens and "backtest" in gew.bron
    assert len(gew.vergelijkbaar) == 3
    # Zonder lichtsom terug naar de seizoensbasis (indicatief)
    zonder = v.gewicht((("a", None),), plant, {"lichtsom": None, "dichtheid": 50.0, "ld_dagen": 12})
    assert zonder.trede == "seizoensbasis" and zonder.status == "indicatief"
    # Schakelaar per veld
    assert v.met({"ld_dagen": False}, None).veld("ld_dagen", (("a", None),), plant, None) is None
    # Gemengde kap: gewogen naar bedden
    gemengd = v.veld("reactietijd", (("a", 3), ("p", 3)), plant, plant + timedelta(days=12))
    assert gemengd is not None


def test_training_zonder_historie_geeft_geen_voorstellen():
    artefact = model.train([], instellingen().naar_dict(), "leeg")
    v = model.Voorspeller(artefact, {d: True for d in model.DOELEN})
    assert v.veld("ld_dagen", (("a", None),), date(2026, 3, 2), None) is None
    assert all(r["gekozen"] == "seizoensbasis" for r in artefact["backtest"].values())
