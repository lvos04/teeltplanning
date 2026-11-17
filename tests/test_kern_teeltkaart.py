"""Teeltkaart (BOUWPLAN §9.2): meetmomenten, LD-dag, streeflengte en seizoen."""

from datetime import date, timedelta

import pytest

from teeltplanning import instellingen
from teeltplanning.afdruk.kaart import kaart_instellingen
from teeltplanning.kern.teeltkaart import KaartInstellingen, Seizoen, eerstvolgende_meetdag, maak_kaart, seizoen_van
from teeltplanning.kern.typen import TeeltUitkomst


class Snap(dict):
    def __getitem__(self, sleutel):
        return self.get(sleutel, instellingen.REGISTER[sleutel].startwaarde)


INST = kaart_instellingen(Snap())


def uitkomst(plant: date, ld: int = 14, rt: int = 50, verl: int = 0, tussen: int | None = None) -> TeeltUitkomst:
    lu = plant + timedelta(days=ld)
    oogst = lu + timedelta(days=rt + verl)
    eff = {"plantdatum": plant, "ld_dagen": ld, "reactietijd": rt, "verlenging": verl, "dichtheid": 50.0,
           "tussenlicht_dagen": tussen}  # fmt: skip
    return TeeltUitkomst(id=1, kap=7, ronde=3, effectief=eff, bron={}, voorstel={}, licht_uit=lu,
                         licht_aan=lu + timedelta(days=12), licht_uit_2=None if tussen is None else lu + timedelta(days=12 + tussen),
                         oogst=oogst, teeltduur=(oogst - plant).days, oppervlak_m2=None, geplant=None, oogstaantal=None,
                         meldingen=())  # fmt: skip


@pytest.mark.parametrize(
    ("weekdag", "verwacht_weekdag", "verwacht_ld"),
    [(1, 1, 0), (2, 3, 1), (3, 3, 0), (4, 5, 1), (5, 5, 0), (6, 1, 2), (7, 1, 1)],
)
def test_eerste_moment_zoals_werkboek(weekdag, verwacht_weekdag, verwacht_ld):
    """Werkboek: weekdag licht uit 1…7 → moment op weekdag 1,3,3,5,5,1,1 en LD-dag 0,1,0,1,0,2,1."""
    licht_uit = date.fromisocalendar(2026, 10, weekdag)
    eerste = eerstvolgende_meetdag(licht_uit, INST.meetdagen)
    assert eerste.isoweekday() == verwacht_weekdag
    assert (eerste - licht_uit).days == verwacht_ld


def test_zestien_momenten_ma_wo_vr():
    kaart = maak_kaart(uitkomst(date(2026, 3, 2), ld=9), "a", INST)  # licht uit wo 11 maart
    assert len(kaart.momenten) == 16
    stappen = [(b.datum - a.datum).days for a, b in zip(kaart.momenten, kaart.momenten[1:], strict=False)]
    assert stappen[:6] == [2, 3, 2, 2, 3, 2]  # wo→vr, vr→ma, ma→wo, …
    assert {m.datum.isoweekday() for m in kaart.momenten} == {1, 3, 5}
    assert kaart.momenten[0].ld_dag == 0 and kaart.momenten[-1].ld_dag == 35


def test_streeflengte_volgens_seizoen_van_plantweek():
    # plantweek 30 = zomer: LD-dag 4 → 30 cm, LD-dag 8 → 40 cm, LD-dag 6 → leeg
    kaart = maak_kaart(uitkomst(date.fromisocalendar(2026, 30, 1), ld=10), "p", INST)  # licht uit do
    per_dag = {m.ld_dag: m.streeflengte for m in kaart.momenten}
    assert kaart.seizoen == "zomer"
    assert per_dag[1] is None and per_dag[4] == 30 and per_dag[6] is None and per_dag[8] == 40
    # plantweek 45 = winter: LD-dag 7 → 45 cm; LD-dag 4 heeft in de winter geen waarde
    kaart = maak_kaart(uitkomst(date.fromisocalendar(2026, 45, 1), ld=10), "p", INST)
    per_dag = {m.ld_dag: m.streeflengte for m in kaart.momenten}
    assert kaart.seizoen == "winter" and per_dag[4] is None and per_dag[8] == 48


def test_seizoensgrenzen_en_week_53():
    assert seizoen_van(43, INST.seizoenen) == "winter" and seizoen_van(52, INST.seizoenen) == "winter"
    assert seizoen_van(6, INST.seizoenen) == "voorjaar" and seizoen_van(36, INST.seizoenen) == "zomer"
    assert seizoen_van(42, INST.seizoenen) == "najaar"
    # Het werkboek kent geen week 53; de app meldt het in plaats van te gokken.
    kaart = maak_kaart(uitkomst(date.fromisocalendar(2026, 53, 1)), "a", INST)
    assert kaart.seizoen is None and any(m.code == "geen_seizoen" for m in kaart.meldingen)
    assert all(m.streeflengte is None for m in kaart.momenten)
    assert Seizoen("winter", 43, 6).bevat(53) and Seizoen("winter", 43, 6).bevat(2)


def test_behandelingen_en_onbekende_offsets():
    kaart = maak_kaart(uitkomst(date(2026, 3, 2), tussen=5), "a", INST)
    assert kaart.behandelingen == {"slak": None, "remstof": None, "pluizen": None, "netten": None}  # start onbekend
    inst = KaartInstellingen(INST.streeflengte, INST.seizoenen, INST.meetdagen, INST.aantal_momenten,
                             {"slak": 7, "remstof": 14, "pluizen": None, "netten": None})  # fmt: skip
    kaart = maak_kaart(uitkomst(date(2026, 3, 2), tussen=5), "a", inst)
    assert kaart.behandelingen["slak"] == kaart.licht_uit + timedelta(days=7)
    assert kaart.licht_uit_2 == kaart.licht_aan + timedelta(days=5)


def test_zonder_licht_uit_geen_momenten():
    u = uitkomst(date(2026, 3, 2))
    u = TeeltUitkomst(**{**u.__dict__, "licht_uit": None})
    kaart = maak_kaart(u, "a", INST)
    assert kaart.momenten == () and any(m.code == "geen_licht_uit" for m in kaart.meldingen)
