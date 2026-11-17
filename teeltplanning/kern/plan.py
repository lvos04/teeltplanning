"""Eén rekenfunctie voor het hele plan (BOUWPLAN §4.1).

Deterministisch en zonder neveneffecten. Scherm, afdruk, koppeling, zoekfunctie en tests gebruiken allemaal
deze ene functie. Volgorde per teelt: datumketen (§6.1) → lichtsom (§6.3) → LBE en dichtheidsvoorstel (§6.5)
→ aantallen (§6.2) → gewichtsvoorstel (§6.5) → energie en kosten (§6.6).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from . import gewas
from .energie import energie_teelt
from .kalender import weken_in_jaar
from .keten import bereken_keten
from .licht import Rekenaar
from .typen import KapInvoer, Melding, Parameters, Plan, PlanInvoer, TeeltInvoer, TeeltUitkomst


def _licht_en_gewas(u: TeeltUitkomst, teelt: TeeltInvoer, rekenaar: Rekenaar | None, inv: gewas.GewasInvoer | None,
                    voorspeller=None):  # fmt: skip
    plant = u.effectief["plantdatum"]
    meldingen = list(u.meldingen)
    effectief, voorstel, bron = dict(u.effectief), dict(u.voorstel), dict(u.bron)
    toelichting = dict(u.toelichting)
    lichtsom = rekenaar.lichtsom(u.kap, plant, u.oogst) if rekenaar else None
    if lichtsom is not None and not lichtsom.bekend:
        van, tot, reden = lichtsom.ontbrekend[0]
        dagen = sum((b - a).days + 1 for a, b, _ in lichtsom.ontbrekend)
        meldingen.append(Melding("lichtsom_onbekend", "info",
                                 f"Lichtsom onbekend: {dagen} dag(en) zonder licht, eerste {van.isoformat()} t/m "
                                 f"{tot.isoformat()} ({reden})."))  # fmt: skip
    keuze = dichtheid_ruw = None
    band = (None, None)
    if inv is not None and plant is not None:
        umol = None
        if rekenaar is not None:
            groepen = [g for g, t in rekenaar.groepen_op(plant) if t == "ja" and (g.kap is None or g.kap == u.kap)]
            umol = sum(g.intensiteit_umol or 0 for g in groepen) if groepen else None
        regime = gewas.lichtregime(inv, umol)
        iso = plant.isocalendar()
        lbe, doel, keuzes = gewas.lbe_teelt(inv, teelt.rassen, iso.week, regime, weken_in_jaar(iso.year))
        status = (
            None if lbe is None else ("indicatief" if any(k.status != "gevalideerd" for k in keuzes) else "gevalideerd")
        )
        keuze = gewas.LbeKeuze(lbe, status, "; ".join(k.uitleg for k in keuzes), sum(k.aantal for k in keuzes))
        totaal = lichtsom.totaal if lichtsom else None
        # Dichtheid: voorstel alleen als er geen opgeslagen voorstel is; handmatig wint altijd.
        dichtheid_ruw = gewas.dichtheid_voorstel(lbe, totaal, doel)
        if voorstel.get("dichtheid") is None and dichtheid_ruw is not None:
            voorstel["dichtheid"] = gewas.afronden(dichtheid_ruw, inv.dichtheid_reeks)
            if bron["dichtheid"] != "handmatig":
                effectief["dichtheid"], bron["dichtheid"] = voorstel["dichtheid"], "voorstel"
        dichtheid = effectief["dichtheid"]
        if voorstel.get("gewicht") is None and voorspeller is not None:
            # Voorstelladder (§8.3): seizoensbasis → LBE-identiteit → ridge → bomen; een hogere trede alleen als
            # hij in de backtest de lagere versloeg. Geen voorstel uit de ladder → de eigen LBE-berekening hieronder.
            v = voorspeller.gewicht(teelt.rassen, plant, {"lichtsom": totaal, "dichtheid": dichtheid,
                                                          "ld_dagen": effectief["ld_dagen"], "lbe": lbe})  # fmt: skip
            if v is not None:
                voorstel["gewicht"] = v.waarde
                toelichting["gewicht"] = v
                band = (v.ondergrens, v.bovengrens)
                if bron["gewicht"] != "handmatig":
                    effectief["gewicht"], bron["gewicht"] = v.waarde, "voorstel"
        if voorstel.get("gewicht") is None:
            voorstel["gewicht"] = gewas.takgewicht(gewas.productie(lbe, totaal), dichtheid)
            if voorstel["gewicht"] is not None and bron["gewicht"] != "handmatig":
                effectief["gewicht"], bron["gewicht"] = voorstel["gewicht"], "voorstel"
            if voorstel["gewicht"] is not None and lichtsom is not None:
                band = (gewas.takgewicht(gewas.productie(lbe, lichtsom.laag), dichtheid),
                        gewas.takgewicht(gewas.productie(lbe, lichtsom.hoog), dichtheid))  # fmt: skip
        if lbe is None:
            meldingen.append(Melding("lbe_onbekend", "info", f"LBE onbekend: {keuze.uitleg}."))
        elif doel is None and effectief["dichtheid"] is None:
            meldingen.append(
                Melding(
                    "doelgewicht_onbekend", "info", "Dichtheidsvoorstel onbekend: doelgewicht van het ras onbekend."
                )
            )
    geplant = gewas.geplant(effectief["dichtheid"], u.oppervlak_m2)
    oogstaantal = gewas.oogstaantal(geplant, effectief["uitval"])
    energie = energie_teelt(rekenaar, u.kap, plant, u.oogst, oogstaantal) if rekenaar else None
    return replace(u, effectief=effectief, voorstel=voorstel, bron=bron, geplant=geplant, oogstaantal=oogstaantal,
                   meldingen=tuple(meldingen), lichtsom=lichtsom, energie=energie, lbe=keuze,
                   dichtheid_onafgerond=dichtheid_ruw, gewicht_band=band, toelichting=toelichting)  # fmt: skip


def bereken_plan(invoer: PlanInvoer, parameters: Parameters) -> Plan:
    per_kap: dict[int, list[TeeltInvoer]] = defaultdict(list)
    for teelt in invoer.teelten:
        per_kap[teelt.kap].append(teelt)
    rekenaar = Rekenaar(parameters.licht) if parameters.licht is not None else None
    ketens = {}
    for kapnummer, teelten in per_kap.items():
        kap = parameters.kappen.get(kapnummer, KapInvoer(nummer=kapnummer))
        ketens[kapnummer] = bereken_keten(teelten, kap, parameters.instellingen, parameters.voorspeller)
    if rekenaar is not None:
        datums = [d for keten in ketens.values() for u in keten for d in (u.effectief["plantdatum"], u.oogst) if d]
        if datums:
            rekenaar.bereid_voor(min(datums), max(datums))
    uitkomsten = {}
    for kapnummer, keten in ketens.items():
        per_id = {t.id: t for t in per_kap[kapnummer]}
        for uitkomst in keten:
            if rekenaar is not None or parameters.gewas is not None or parameters.voorspeller is not None:
                uitkomst = _licht_en_gewas(uitkomst, per_id[uitkomst.id], rekenaar, parameters.gewas,
                                           parameters.voorspeller)  # fmt: skip
            uitkomsten[uitkomst.id] = uitkomst
    return Plan(teelten=uitkomsten)
