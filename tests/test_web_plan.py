"""Import, raster, keten, drie lagen, herimport en instellingen via de webapp (fase 1-acceptatie)."""

import re

from sqlalchemy import func, select

from teeltplanning.db.basis import SessionLocal
from teeltplanning.db.modellen import Audit, Ronde, Teelt, Veldwaarde
from tests.conftest import importeer, maak_gebruiker


def _teelt(ronde: int, kap: int) -> Teelt:
    with SessionLocal() as db:
        return db.scalar(select(Teelt).join(Ronde).where(Ronde.nummer == ronde, Teelt.kap == kap))


def _plan(ronde: int, kap: int):
    from teeltplanning import planning

    with SessionLocal() as db:
        t = _teelt(ronde, kap)
        return planning.bereken(db, db.get(Ronde, t.ronde_id).scenario_id)[1].teelten[t.id]


def test_import_raster_keten_herimport_en_instellingen(beheerder, synthetische_werkboeken):
    c = beheerder
    jaren = synthetische_werkboeken["meta"]["programma"]["jaren"]
    batch = importeer(c, synthetische_werkboeken["programma"], jaren)
    pagina = c.get(f"/import/{batch}").text
    assert "52_weken" in pagina and "afwijking</strong>" not in pagina
    assert c.get(f"/import/{batch}/vergelijking.csv").text.lstrip("﻿").startswith("blad;rij")
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Teelt)) == 1360

    # Tweede identieke bevestiging maakt niets dubbel aan.
    assert c.post(f"/import/{batch}/bevestig", follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Teelt)) == 1360

    # Raster: 40 regels, kolommen in de volgorde van het rondeblad, bewerkbare cellen.
    raster = c.get("/ronde?nummer=2").text
    assert raster.count('<tr id="teelt-') == 40
    kop = raster[raster.index("<thead>") : raster.index("</thead>")]
    assert kop.index("pl wk") < kop.index("l.uit wk") < kop.index("bloei wk") < kop.index("cumulatief")
    assert 'data-veld="ld_dagen"' in raster
    assert "Oppervlak per kap is onbekend" in raster

    # Een wijziging in ronde 2 schuift door naar ronde 3 in dezelfde kap (die volgt de keten) ...
    kap = next(
        k
        for k in range(1, 41)
        if _plan(3, k).bron["plantdatum"] == "voorstel" and _plan(2, k).effectief["ld_dagen"] is not None
    )
    t2 = _teelt(2, kap)
    voor = _plan(3, kap).effectief["plantdatum"]
    ld = _plan(2, kap).effectief["ld_dagen"]
    antwoord = c.post(f"/teelt/{t2.id}/cel", data={"veld": "ld_dagen", "waarde": str(ld + 7)})
    assert antwoord.status_code == 200 and f'id="teelt-{t2.id}"' in antwoord.text
    assert re.search(r'data-elders="(\d+)"', antwoord.text).group(1) != "0"
    na = _plan(3, kap)
    assert (na.effectief["plantdatum"] - voor).days >= 7
    assert _plan(2, kap).bron["ld_dagen"] == "handmatig"

    # ... maar niet voorbij een vastgezette plantdatum.
    t3 = _teelt(3, kap)
    vast = na.effectief["plantdatum"]
    assert c.post(f"/teelt/{t3.id}/veld/plantdatum", data={"waarde": f"{vast.isocalendar()[1]}-{vast.isocalendar()[2]}"},
                  follow_redirects=False).status_code == 303  # fmt: skip
    c.post(f"/teelt/{t2.id}/cel", data={"veld": "ld_dagen", "waarde": str(ld + 14)})
    assert _plan(3, kap).effectief["plantdatum"] == vast

    # Ongeldige invoer geeft een begrijpelijke melding.
    fout = c.post(f"/teelt/{t2.id}/cel", data={"veld": "ld_dagen", "waarde": "abc"})
    assert fout.status_code == 422 and "getal" in fout.text

    # Handmatige waarde overleeft een herimport; "terug naar voorstel" werkt daarna.
    c.post(f"/teelt/{t2.id}/cel", data={"veld": "dichtheid", "waarde": "61,5", "reden": "proef"})
    batch2 = importeer(c, synthetische_werkboeken["programma"], jaren)
    assert batch2 != batch
    assert _plan(2, kap).effectief["dichtheid"] == 61.5
    assert _plan(2, kap).effectief["ld_dagen"] == ld + 14
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Teelt)) == 1360
        rij = db.scalar(select(Veldwaarde).where(Veldwaarde.teelt_id == t2.id, Veldwaarde.veld == "dichtheid"))
        assert rij.handmatig_bron == "gebruiker" and rij.reden == "proef"
    c.post(f"/teelt/{t2.id}/cel", data={"veld": "dichtheid", "waarde": ""})
    assert _plan(2, kap).bron["dichtheid"] is None  # geen voorstel in fase 1: onbekend
    with SessionLocal() as db:
        acties = [a.actie for a in db.scalars(select(Audit).where(Audit.onderwerp == f"teelt:{t2.id}:dichtheid"))]
    assert acties == ["veld_gewijzigd", "veld_terug_naar_voorstel"]

    # Teeltpagina toont de drie lagen en het werkboekbewijs.
    teeltpagina = c.get(f"/teelt/{t2.id}").text
    assert "voorstel" in teeltpagina and "handmatig" in teeltpagina and "Werkboekbewijs" in teeltpagina
    assert "=MOD(" in teeltpagina

    # Kapscherm: ketting met tijdbalk.
    kappagina = c.get(f"/kap/{kap}").text
    assert kappagina.count('class="ld"') >= 30

    # Een gewijzigde instelling rekent het plan direct door en staat in het auditlog.
    licht_aan_voor = _plan(2, kap).licht_aan
    antwoord = c.post("/beheer/instellingen/keten.licht_aan_na_licht_uit_dagen", data={"waarde": "14", "reden": "proef"},
                      follow_redirects=False)  # fmt: skip
    assert antwoord.status_code == 303 and "opgeslagen" in antwoord.headers["location"]
    assert (_plan(2, kap).licht_aan - licht_aan_voor).days == 2
    with SessionLocal() as db:
        regel = db.scalar(select(Audit).where(Audit.onderwerp == "instelling:keten.licht_aan_na_licht_uit_dagen"))
        assert regel.details["oud"] == 12 and regel.details["nieuw"] == 14

    # Oppervlak met ingangsdatum invullen → aantallen worden berekend.
    c.post(
        "/beheer/instellingen/bedrijf.oppervlak_per_kap_m2",
        data={"waarde": "726", "geldig_vanaf": "2000-01-01", "reden": "vraag 4"},
    )
    assert _plan(4, kap).geplant == _plan(4, kap).effectief["dichtheid"] * 726
    assert "Oppervlak per kap is onbekend" not in c.get("/ronde?nummer=2").text

    # Overlap blokkeren (instelling): opslaan wordt geweigerd.
    c.post("/beheer/instellingen/keten.overlap", data={"waarde": "blokkeren"})
    oogst2 = _plan(2, kap).oogst
    from datetime import timedelta

    te_vroeg = oogst2 - timedelta(days=10)
    iso = te_vroeg.isocalendar()
    geblokkeerd = c.post(
        f"/teelt/{t3.id}/veld/plantdatum", data={"waarde": f"{iso[1]}-{iso[2]}-{iso[0]}"}, follow_redirects=False
    )
    assert geblokkeerd.status_code == 422 and "geblokkeerd" in geblokkeerd.text
    assert _plan(3, kap).effectief["plantdatum"] == vast
    c.post("/beheer/instellingen/keten.overlap", data={"waarde": "waarschuwen"})
    toegestaan = c.post(
        f"/teelt/{t3.id}/veld/plantdatum", data={"waarde": f"{iso[1]}-{iso[2]}-{iso[0]}"}, follow_redirects=False
    )
    assert toegestaan.status_code == 303
    assert any(m.code == "overlap" for m in _plan(3, kap).meldingen)


def test_rollen_lezer_kan_niet_bewerken(beheerder, synthetische_werkboeken):
    c = beheerder
    importeer(c, synthetische_werkboeken["programma"], {1: synthetische_werkboeken["meta"]["programma"]["jaren"][1]})
    maak_gebruiker(c, "lezer1", "lezer")
    maak_gebruiker(c, "mw1", "medewerker")
    t = _teelt(1, 1)
    c.post("/afmelden")
    from tests.conftest import WACHTWOORD

    c.post("/aanmelden", data={"gebruikersnaam": "lezer1", "wachtwoord": WACHTWOORD})
    raster = c.get("/ronde?nummer=1").text
    assert "data-url=" not in raster
    assert c.post(f"/teelt/{t.id}/cel", data={"veld": "ld_dagen", "waarde": "9"}).status_code == 403
    assert c.get("/beheer/instellingen").status_code == 403
    assert c.post("/import/upload", files={"bestand": ("x.xlsx", b"x")}).status_code == 403
    c.post("/afmelden")
    c.post("/aanmelden", data={"gebruikersnaam": "mw1", "wachtwoord": WACHTWOORD})
    assert c.post(f"/teelt/{t.id}/cel", data={"veld": "ld_dagen", "waarde": "9"}).status_code == 200
    assert c.get("/beheer/instellingen").status_code == 403
    assert c.get("/beheer/tabel/kappen").status_code == 200


def test_instellingenscherm_gegenereerd_uit_register(beheerder):
    from teeltplanning.instellingen import GROEPEN, REGISTER_LIJST

    c = beheerder
    for groep in GROEPEN:
        pagina = c.get(f"/beheer/instellingen?groep={groep}")
        assert pagina.status_code == 200
        for inst in (i for i in REGISTER_LIJST if i.groep == groep):
            assert inst.sleutel in pagina.text
    zoek = c.get("/beheer/instellingen?zoek=licht uit").text
    assert "keten.licht_aan_na_licht_uit_dagen" in zoek and "bedrijf.aantal_kappen" not in zoek
    fout = c.post("/beheer/instellingen/keten.verlenging_max", data={"waarde": "-3"}, follow_redirects=False)
    assert "Niet+opgeslagen" in fout.headers["location"] or "Niet%20opgeslagen" in fout.headers["location"]
    terug = c.post("/beheer/instellingen/keten.verlenging_max", data={"actie": "start"}, follow_redirects=False)
    assert terug.status_code == 303
    export = c.get("/beheer/instellingen/export.json").json()
    assert export["formaat"] == "teeltplanning-instellingen" and "koppeling.ingeschakeld" in export["instellingen"]


def test_kappen_en_rassen_beheer_csv(beheerder):
    c = beheerder
    csv = c.get("/beheer/tabel/kappen/export.csv").text.lstrip("﻿")
    regels = csv.splitlines()
    assert regels[0] == "nummer;vakgroep;oppervlak_m2;plantvolgorde;wisseltijd_dagen;actief" and len(regels) == 41
    regels[1] = "1;11;700;1;2;ja"
    assert (
        c.post("/beheer/tabel/kappen/csv", data={"csv_tekst": "\n".join(regels)}, follow_redirects=False).status_code
        == 303
    )
    rassen = c.get("/beheer/tabel/rassen").text
    assert "Antonov" in rassen and "Baltazar" in rassen and "onbekend" in rassen  # geen doelgewicht verzonnen
    fout = c.post("/beheer/tabel/kappen/csv", data={"csv_tekst": "nummer;x\n1;2"}, follow_redirects=False)
    assert "kopregel" in fout.headers["location"]
