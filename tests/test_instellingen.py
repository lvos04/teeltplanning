"""Het instellingenregister (BOUWPLAN §4.3)."""

import json
from datetime import date

import pytest

from teeltplanning import instellingen as reg
from teeltplanning.instellingen import REGISTER, OngeldigeWaarde, valideer
from tests.synthetisch import STREEFLENGTE_CONTROLE


def test_elke_instelling_is_volledig_en_startwaarde_geldig():
    for inst in REGISTER.values():
        assert inst.groep in reg.GROEPEN, inst.sleutel
        assert inst.label and inst.uitleg, inst.sleutel
        if inst.startwaarde is None:
            assert inst.null, f"{inst.sleutel}: startwaarde onbekend maar null niet toegestaan"
        else:
            assert valideer(inst, inst.startwaarde) == inst.startwaarde, inst.sleutel


def test_alle_groepen_uit_het_bouwplan_hebben_instellingen():
    groepen = {i.groep for i in REGISTER.values()}
    assert groepen >= set(reg.GROEPEN) - {"Lampen"}  # lampgroepen worden in fase 3 een beheertabel


def test_startwaarden_uit_het_bouwplan_en_onbekend_waar_niets_bekend_is():
    s = {k: i.startwaarde for k, i in REGISTER.items()}
    assert s["keten.licht_aan_na_licht_uit_dagen"] == 12
    assert s["kalender.plantdagen"] == [1, 2, 3, 4, 5, 6]
    assert s["kalender.wisseltijd_dagen"] == 0
    assert s["licht.straling_naar_par"] == 0.023
    assert s["bedrijf.aantal_kappen"] == 40
    assert s["bedrijf.oppervlak_per_kap_m2"] is None  # open vraag 4
    assert s["licht.transmissie"] is None and s["energie.stroomprijs_eur_per_mwh"] is None
    assert (s["teeltkaart.seizoen_winter"], s["teeltkaart.seizoen_voorjaar"], s["teeltkaart.seizoen_zomer"],
            s["teeltkaart.seizoen_najaar"]) == ("43-52", "1-6", "7-36", "37-42")  # fmt: skip
    assert s["teeltkaart.meetdagen"] == [1, 3, 5] and s["teeltkaart.aantal_momenten"] == 16
    tabel = {r["ld_dag"]: (r["winter"], r["voorjaar"], r["zomer"], r["najaar"]) for r in s["teeltkaart.streeflengte"]}
    assert tabel == STREEFLENGTE_CONTROLE
    assert s["gewas.uitval_per_oogstweek"] == []


def test_externe_bronnen_en_koppeling_standaard_uit():
    for sleutel, inst in REGISTER.items():
        if sleutel.startswith("bronnen.") and inst.type == "bool":
            assert inst.startwaarde is False, sleutel
    assert REGISTER["koppeling.ingeschakeld"].startwaarde is False
    assert REGISTER["beveiliging.mfa_verplicht_beheerders"].startwaarde is False
    assert REGISTER["beveiliging.strikte_oorsprong"].startwaarde is True


@pytest.mark.parametrize(
    ("sleutel", "waarde", "melding"),
    [
        ("keten.licht_aan_na_licht_uit_dagen", "abc", "geen getal"),
        ("keten.licht_aan_na_licht_uit_dagen", "1.5", "geheel getal"),
        ("keten.licht_aan_na_licht_uit_dagen", -1, "minimaal"),
        ("keten.licht_aan_na_licht_uit_dagen", None, "verplicht"),
        ("keten.overlap", "misschien", "kies uit"),
        ("kalender.plantdagen", [], "minstens één"),
        ("kalender.plantdagen", [8], "maximaal"),
        ("teeltkaart.seizoen_winter", "43", "week van-tot"),
        ("gewas.uitval_per_oogstweek", "week_van;week_tot;fractie\n1;11;1,5", "maximaal"),
        ("gewas.uitval_per_oogstweek", "week;fractie\n1;0,08", "kopregel"),
        ("import.rondeblad_kolommen", '{"vakgroep": "B"}', "alle velden"),
    ],
)
def test_ongeldige_waarden_worden_geweigerd(sleutel, waarde, melding):
    with pytest.raises(OngeldigeWaarde, match=melding):
        valideer(REGISTER[sleutel], waarde)


def test_geldige_omzettingen():
    assert valideer(REGISTER["bedrijf.oppervlak_per_kap_m2"], "726,5") == 726.5
    assert valideer(REGISTER["bedrijf.oppervlak_per_kap_m2"], "") is None
    assert valideer(REGISTER["keten.plantdatum_volgt_keten"], "uit") is False
    rijen = valideer(REGISTER["gewas.uitval_per_oogstweek"], "week_van;week_tot;fractie\n1;11;0,08\n12;53;0.05")
    assert rijen == [
        {"week_van": 1, "week_tot": 11, "fractie": 0.08},
        {"week_van": 12, "week_tot": 53, "fractie": 0.05},
    ]
    assert reg.csv_naar_tabel(
        REGISTER["gewas.uitval_per_oogstweek"], reg.tabel_naar_csv(REGISTER["gewas.uitval_per_oogstweek"], rijen)
    )


def test_opslaan_ingangsdatum_audit_en_export_import(client):
    from teeltplanning.db.basis import SessionLocal
    from teeltplanning.db.modellen import Audit

    with SessionLocal() as db:
        reg.wijzig(db, None, "bedrijf.oppervlak_per_kap_m2", 726, "werkboek rondeblad", date(2018, 1, 1))
        reg.wijzig(db, None, "bedrijf.oppervlak_per_kap_m2", "750", "nieuwe meting", date(2025, 1, 1))
        reg.wijzig(db, None, "keten.licht_aan_na_licht_uit_dagen", 11, "proef")
        db.commit()
        snap = reg.laad(db)
        assert snap.op("bedrijf.oppervlak_per_kap_m2", date(2017, 1, 1)) is None
        assert snap.op("bedrijf.oppervlak_per_kap_m2", date(2024, 6, 1)) == 726
        assert snap.op("bedrijf.oppervlak_per_kap_m2", date(2026, 1, 1)) == 750
        assert snap["keten.licht_aan_na_licht_uit_dagen"] == 11
        acties = [a.details for a in db.query(Audit).filter(Audit.actie == "instelling_gewijzigd")]
        assert {"oud": 12, "nieuw": 11}.items() <= acties[-1].items()
        document = reg.exporteer(db)
        assert json.dumps(document)  # JSON-serialiseerbaar
        reg.wijzig(db, None, "keten.licht_aan_na_licht_uit_dagen", None, "terug", naar_start=True)
        db.commit()
        assert reg.laad(db)["keten.licht_aan_na_licht_uit_dagen"] == 12
        aantal = reg.importeer(db, None, document, "terugzetten")
        db.commit()
        assert aantal >= 1 and reg.laad(db)["keten.licht_aan_na_licht_uit_dagen"] == 11
        with pytest.raises(OngeldigeWaarde):
            reg.importeer(
                db,
                None,
                {"formaat": "teeltplanning-instellingen", "instellingen": {"bestaat.niet": {"waarde": 1}}},
                "x",
            )
        with pytest.raises(OngeldigeWaarde):
            reg.wijzig(db, None, "keten.licht_aan_na_licht_uit_dagen", 5, "x", date(2020, 1, 1))  # geen ingangsdatum
