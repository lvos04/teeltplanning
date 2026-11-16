"""Datumketen per kap (BOUWPLAN §6.1).

    licht_uit   = plantdatum + ld_dagen
    oogst       = licht_uit + reactietijd + verlenging
    teeltduur   = oogst − plantdatum                       (dagen)
    licht_aan   = licht_uit + <instelling, start 12>        (tussenlicht)
    licht_uit_2 = licht_aan + tussenlicht_dagen
    plant_volgende_teelt_in_kap = oogst + wisseltijd       (afgerond naar de eerstvolgende toegestane plantdag)

Elke kap is een ketting van teelten. Een wijziging schuift alle latere teelten in die kap mee, tot aan de
eerstvolgende teelt met een handmatig vastgezette plantdatum. Overlap (plantdatum vóór de oogst van de vorige
teelt in dezelfde kap) is een fout op de rij, maar blokkeert niets.
"""

from __future__ import annotations

from datetime import date, timedelta

from . import gewas
from .kalender import eerstvolgende_toegestane_dag, waarde_op, weergave
from .typen import VELDEN, KapInvoer, KernInstellingen, Melding, TeeltInvoer, TeeltUitkomst

LABELS = {
    "plantdatum": "plantdatum",
    "ld_dagen": "dagen vegetatief (LD)",
    "reactietijd": "reactietijd",
    "verlenging": "verlenging",
    "tussenlicht": "tussenlicht",
    "tussenlicht_dagen": "dagen tussenlicht",
    "dichtheid": "planten per m²",
    "gewicht": "gewichtsprognose",
    "uitval": "uitval",
}


def plus(d: date | None, *dagen: int | None) -> date | None:
    if d is None or any(x is None for x in dagen):
        return None
    return d + timedelta(days=int(sum(dagen)))


def licht_uit(plantdatum: date | None, ld_dagen: int | None) -> date | None:
    return plus(plantdatum, ld_dagen)


def oogst(licht_uit_datum: date | None, reactietijd: int | None, verlenging: int | None) -> date | None:
    return plus(licht_uit_datum, reactietijd, verlenging)


def teeltduur(plantdatum: date | None, oogstdatum: date | None) -> int | None:
    if plantdatum is None or oogstdatum is None:
        return None
    return (oogstdatum - plantdatum).days


def plantdatum_voorstel(vorige_oogst: date | None, kap: KapInvoer, inst: KernInstellingen) -> date | None:
    """Oogst vorige teelt + wisseltijd, afgerond naar de eerstvolgende toegestane plantdag."""
    wisseltijd = kap.wisseltijd_dagen if kap.wisseltijd_dagen is not None else inst.wisseltijd_dagen
    start = plus(vorige_oogst, wisseltijd)
    if start is None:
        return None
    return eerstvolgende_toegestane_dag(start, inst.plantdagen, inst.blokkades, kap.nummer)


def _grenscontrole(naam: str, waarde, inst: KernInstellingen) -> Melding | None:
    if waarde is None or naam not in inst.grenzen:
        return None
    laag, hoog = inst.grenzen[naam]
    if (laag is not None and waarde < laag) or (hoog is not None and waarde > hoog):
        return Melding(
            f"buiten_grenzen_{naam}",
            "fout",
            f"{LABELS[naam].capitalize()} {waarde} ligt buiten de ingestelde grenzen ({laag} t/m {hoog}).",
        )
    return None


def bereken_teelt(
    teelt: TeeltInvoer, kap: KapInvoer, vorige_oogst: date | None, eerste_in_keten: bool, inst: KernInstellingen
) -> TeeltUitkomst:
    meldingen: list[Melding] = []
    voorstel = {naam: teelt.veld(naam).voorstel for naam in VELDEN}
    if inst.plantdatum_volgt_keten and not eerste_in_keten:
        voorstel["plantdatum"] = plantdatum_voorstel(vorige_oogst, kap, inst)
    effectief: dict = {}
    bron: dict = {}
    for naam in VELDEN:
        handmatig = teelt.veld(naam).handmatig
        if handmatig is not None:
            effectief[naam], bron[naam] = handmatig, "handmatig"
        elif voorstel[naam] is not None:
            effectief[naam], bron[naam] = voorstel[naam], "voorstel"
        else:
            effectief[naam], bron[naam] = None, None

    plant = effectief["plantdatum"]
    lu = licht_uit(plant, effectief["ld_dagen"])
    oogstdatum = oogst(lu, effectief["reactietijd"], effectief["verlenging"])
    # Uitvalvoorstel hangt af van de oogstweek; pas na de oogstdatum bekend.
    if bron["uitval"] is None or bron["uitval"] == "voorstel":
        voorstel["uitval"] = voorstel["uitval"] or gewas.uitval_voorstel(oogstdatum, inst.uitval_per_oogstweek)
        if voorstel["uitval"] is not None:
            effectief["uitval"], bron["uitval"] = voorstel["uitval"], "voorstel"
    licht_aan = plus(lu, inst.licht_aan_na_licht_uit_dagen)
    licht_uit_2 = plus(licht_aan, effectief["tussenlicht_dagen"])

    for naam in ("plantdatum", "ld_dagen", "reactietijd", "verlenging"):
        if effectief[naam] is None:
            reden = ""
            if naam == "plantdatum" and not eerste_in_keten and vorige_oogst is None:
                reden = " (de vorige teelt in deze kap heeft geen oogstdatum)"
            meldingen.append(
                Melding(f"onbekend_{naam}", "waarschuwing", f"{LABELS[naam].capitalize()} onbekend{reden}.")
            )
    for naam in ("ld_dagen", "reactietijd", "verlenging", "dichtheid", "uitval", "tussenlicht_dagen"):
        melding = _grenscontrole(naam, effectief[naam], inst)
        if melding:
            meldingen.append(melding)
    if plant is not None and vorige_oogst is not None and plant < vorige_oogst:
        meldingen.append(
            Melding(
                "overlap",
                "fout",
                f"Plantdatum {weergave(plant)} ligt vóór de oogst van de vorige teelt in kap {kap.nummer} "
                f"({weergave(vorige_oogst)}).",
            )
        )
    if plant is not None and inst.plantdagen and plant.isoweekday() not in inst.plantdagen:
        meldingen.append(
            Melding("geen_plantdag", "waarschuwing", "De plantdatum valt niet op een toegestane plantdag.")
        )

    oppervlak = kap.oppervlak_m2 if kap.oppervlak_m2 is not None else waarde_op(inst.oppervlak_per_kap, plant)
    geplant = gewas.geplant(effectief["dichtheid"], oppervlak)
    if oppervlak is None and effectief["dichtheid"] is not None:
        meldingen.append(
            Melding(
                "onbekend_oppervlak",
                "info",
                "Geplant aantal en oogstaantal onbekend: oppervlak per kap is niet ingesteld.",
            )
        )
    return TeeltUitkomst(
        id=teelt.id,
        kap=teelt.kap,
        ronde=teelt.ronde,
        effectief=effectief,
        bron=bron,
        voorstel=voorstel,
        licht_uit=lu,
        licht_aan=licht_aan,
        licht_uit_2=licht_uit_2,
        oogst=oogstdatum,
        teeltduur=teeltduur(plant, oogstdatum),
        oppervlak_m2=oppervlak,
        geplant=geplant,
        oogstaantal=gewas.oogstaantal(geplant, effectief["uitval"]),
        meldingen=tuple(meldingen),
    )


def bereken_keten(teelten: list[TeeltInvoer], kap: KapInvoer, inst: KernInstellingen) -> list[TeeltUitkomst]:
    """Reken de teelten van één kap door, in volgorde van ronde."""
    uitkomsten = []
    vorige_oogst: date | None = None
    for index, teelt in enumerate(sorted(teelten, key=lambda t: t.ronde)):
        uitkomst = bereken_teelt(teelt, kap, vorige_oogst, index == 0, inst)
        uitkomsten.append(uitkomst)
        vorige_oogst = uitkomst.oogst
    return uitkomsten
