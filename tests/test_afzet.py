"""Afzetmomenten (BOUWPLAN §8.1): elke datumregel getest tegen minstens drie bekende jaren (hier 2024–2027)."""

from datetime import date

import pytest

from teeltplanning.bronnen import feestdagen
from teeltplanning.kern import afzet

REGELS = dict((naam, regel) for naam, regel, _ in feestdagen.START_AFZETMOMENTEN)

# Bekende datums (kalenders; Pasen volgens de westerse kerk).
BEKEND = {
    "Valentijnsdag": [date(2024, 2, 14), date(2025, 2, 14), date(2026, 2, 14), date(2027, 2, 14)],
    "Vrouwendag": [date(2024, 3, 8), date(2025, 3, 8), date(2026, 3, 8), date(2027, 3, 8)],
    # Mothering Sunday: vierde zondag van de vasten = drie weken vóór Pasen
    "Britse moederdag": [date(2024, 3, 10), date(2025, 3, 30), date(2026, 3, 15), date(2027, 3, 7)],
    "Pasen": [date(2024, 3, 31), date(2025, 4, 20), date(2026, 4, 5), date(2027, 3, 28)],
    # Nederland: tweede zondag van mei
    "Moederdag": [date(2024, 5, 12), date(2025, 5, 11), date(2026, 5, 10), date(2027, 5, 9)],
    # Frankrijk: laatste zondag van mei, behalve als dat Pinksteren is: dan de eerste zondag van juni
    "Franse moederdag": [date(2024, 5, 26), date(2025, 5, 25), date(2026, 5, 31), date(2027, 5, 30)],
    "Allerheiligen": [date(2024, 11, 1), date(2025, 11, 1), date(2026, 11, 1), date(2027, 11, 1)],
    # Totensonntag: zondag vóór de eerste advent
    "Totensonntag": [date(2024, 11, 24), date(2025, 11, 23), date(2026, 11, 22), date(2027, 11, 21)],
    "Kerst": [date(2024, 12, 25), date(2025, 12, 25), date(2026, 12, 25), date(2027, 12, 25)],
}


def test_alle_startregels_hebben_bekende_jaren():
    assert set(BEKEND) == set(REGELS)
    assert all(len(d) >= 3 for d in BEKEND.values())


@pytest.mark.parametrize("naam", sorted(BEKEND))
def test_datumregel_tegen_bekende_jaren(naam):
    for verwacht in BEKEND[naam]:
        assert afzet.datum_van(REGELS[naam], verwacht.year, feestdagen.pasen) == verwacht, (naam, verwacht.year)


def test_franse_moederdag_uitzondering_pinksteren():
    # 2004: Pinksteren viel op 30 mei (laatste zondag van mei) → moederdag 6 juni
    assert afzet.datum_van(REGELS["Franse moederdag"], 2004, feestdagen.pasen) == date(2004, 6, 6)
    assert afzet.datum_van("pasen 49", 2004, feestdagen.pasen) == date(2004, 5, 30)


def test_pasen_gelijk_aan_holidays_pakket():
    nl = {naam: d for d, land, naam in feestdagen.officiele_feestdagen(["NL"], [2024, 2025, 2026, 2027])}
    paasdagen = sorted(d for d, land, naam in feestdagen.officiele_feestdagen(["NL"], [2024, 2025, 2026, 2027])
                       if "aas" in naam and d.isoweekday() == 7)  # fmt: skip
    assert paasdagen == BEKEND["Pasen"], nl


def test_officiele_feestdagen_per_land():
    dagen = feestdagen.officiele_feestdagen(["NL", "DE", "XX"], [2025])
    assert (date(2025, 4, 26), "NL") in {(d, land) for d, land, _ in dagen}  # Koningsdag op zaterdag (27/4 = zondag)
    assert any(land == "DE" and d == date(2025, 10, 3) for d, land, _ in dagen)  # Tag der Deutschen Einheit
    assert feestdagen.onbekende_landen(["NL", "XX"]) == ["XX"]


@pytest.mark.parametrize(
    ("regel", "melding"),
    [("vast 13-01", "month"), ("weekdag 02 7 5", "geen 5e"), ("weekdag 05 8 1", "weekdag loopt"), ("zomaar", "onbekende regel"),
     ("vast 02-14 behalve vast 02-14", "behalve"), ("weekdag 05 7 0", "volgnummer 0"), ("voor 12-25 7 0", "1 of meer")],
)  # fmt: skip
def test_ongeldige_regel_geeft_uitleg(regel, melding):
    fout = afzet.controleer(regel, feestdagen.pasen, 2026)
    assert fout and melding in fout


def test_venster_terugrekenen_en_dekking():
    m = afzet.Afzetmoment(1, "Moederdag", "weekdag 05 7 2", venster_dagen_voor=5, gewicht=8)
    md = afzet.momenten_in([m], [2026], feestdagen.pasen)[0]
    assert md.datum == date(2026, 5, 10) and md.venster == (date(2026, 5, 5), date(2026, 5, 9))
    assert afzet.terugrekenen(date(2026, 5, 8), 52, 1, 14) == date(2026, 5, 8) - __import__("datetime").timedelta(
        days=67
    )
    assert afzet.terugrekenen(date(2026, 5, 8), None, 1, 14) is None
    zonder = afzet.momenten_in([afzet.Afzetmoment(2, "Kerst", "vast 12-25")], [2026], feestdagen.pasen)[0]
    assert zonder.venster is None  # venster onbekend (vraag 9): geen dekking verzinnen


def test_startgegevens_onbevestigd_venster_en_belang_onbekend(beheerder):
    from sqlalchemy import select

    from teeltplanning.db.basis import SessionLocal
    from teeltplanning.db.modellen import Afzetmoment

    with SessionLocal() as db:
        rijen = db.scalars(select(Afzetmoment)).all()
    assert {r.naam for r in rijen} == set(REGELS)
    assert all(not r.bevestigd and r.venster_dagen_voor is None and r.gewicht is None for r in rijen)
    pagina = beheerder.get("/afzet?jaar=2026").text
    assert "nog niet door de teler bevestigd" in pagina and "2026-05-10" in pagina and "2026-11-22" in pagina
    # Een ongeldige regel wordt in Beheer geweigerd
    csv = "naam;datumregel;venster_dagen_voor;gewicht;markt;actief;bevestigd;opmerking\nTest;weekdag 13 7 1;;;;ja;nee;"
    antwoord = beheerder.post("/beheer/tabel/afzetmomenten/csv", data={"csv_tekst": csv}, follow_redirects=True)
    assert "Niet opgeslagen" in antwoord.text
    csv = "naam;datumregel;venster_dagen_voor;gewicht;markt;actief;bevestigd;opmerking\nMoederdag;weekdag 05 7 2;5;11;;ja;nee;"
    assert (
        "kies 0 t/m 10"
        in beheerder.post("/beheer/tabel/afzetmomenten/csv", data={"csv_tekst": csv}, follow_redirects=True).text
    )
