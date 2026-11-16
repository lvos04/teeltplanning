"""Plan genereren (BOUWPLAN §8.2, punt 1): per kap de ketting vooruit vullen met voorstellen.

Per kap, vanaf de oogst van de laatste teelt + wisseltijd (afgerond naar een toegestane plantdag), of voor een kap
zonder teelt vanaf de startdatum (eventueel gespreid: *Genereren → Kappen per plantdag*):

1. ras volgens de rasverdeling (grootste-restmethode over de actieve kappen in plantvolgorde; zonder verdeling het
   ras van de vorige teelt in de kap);
2. LD-dagen, verlenging en reactietijd uit de voorstelladder (fase 4), anders de terugvalwaarde uit de instellingen;
   binnen de grenzen van de datumketen;
3. valt de oogst niet op een toegestane oogstdag, dan de kleinste verlenging binnen de grenzen die dat wel doet;
4. volgende plantdatum = oogst + wisseltijd → toegestane plantdag (zelfde regel als de keten, dus nooit overlap).

Stopt bij het einde van het ISO-jaar (de laatste teelt plant nog in dat jaar) of na het gevraagde aantal rondes.
Ontbreekt een waarde, dan stopt de ketting van die kap met een melding; er wordt niets verzonnen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .kalender import eerstvolgende_toegestane_dag
from .keten import plantdatum_voorstel
from .typen import KapInvoer, KernInstellingen

VELDEN = ("ld_dagen", "verlenging", "reactietijd")


@dataclass(frozen=True)
class GenOpties:
    jaar: int | None
    rondes: int | None
    rasverdeling: tuple[tuple[str, float], ...]
    terugval: dict[str, int | None]
    start: date
    kappen_per_plantdag: int | None = None


@dataclass(frozen=True)
class NieuweTeelt:
    kap: int
    index: int
    rassen: tuple[tuple[str, int | None], ...]
    plantdatum: date
    eerste_in_kap: bool
    oogst: date
    waarden: dict[str, int]
    toelichting: dict[str, Any] = field(default_factory=dict)  # veld → (onder, boven, bron, modelversie)


def rasreeks(verdeling: tuple[tuple[str, float], ...], aantal: int) -> list[str | None]:
    """Grootste-restmethode: per kap (in plantvolgorde) een ras, in de verhouding van de verdeling."""
    totaal = sum(a for _, a in verdeling if a)
    if not verdeling or not totaal or not aantal:
        return [None] * aantal
    quota = [(code, a / totaal * aantal) for code, a in verdeling if a]
    tellingen = {code: int(q) for code, q in quota}
    over = aantal - sum(tellingen.values())
    for code, _ in sorted(quota, key=lambda x: (-(x[1] - int(x[1])), x[0]))[:over]:
        tellingen[code] += 1
    uit: list[str | None] = []
    for code, _ in quota:
        uit += [code] * tellingen[code]
    return uit


def _binnen(waarde: int, grens: tuple[float | None, float | None] | None) -> int:
    if grens is None:
        return waarde
    laag, hoog = grens
    if laag is not None and waarde < laag:
        return int(laag)
    if hoog is not None and waarde > hoog:
        return int(hoog)
    return waarde


def genereer(kappen: list[KapInvoer], laatste: dict[int, tuple[date | None, tuple]], inst: KernInstellingen,
             voorspeller, opties: GenOpties) -> tuple[list[NieuweTeelt], list[str]]:  # fmt: skip
    """`laatste`: per kap (oogst van de laatste teelt, rassen) voor kappen die al een teelt hebben."""
    if not inst.plantdagen:
        return [], ["Geen toegestane plantdagen ingesteld (Instellingen → Kalender); er is niets gegenereerd."]
    actief = [k for k in kappen if k.actief]
    rassen_per_positie = rasreeks(opties.rasverdeling, len(actief))
    eind = date.fromisocalendar(opties.jaar + 1, 1, 1) if opties.jaar else None
    startdagen: list[date] = []
    d = opties.start - timedelta(days=1)
    nieuw: list[NieuweTeelt] = []
    meldingen: list[str] = []
    zonder = 0
    for positie, kap in enumerate(actief):
        if kap.nummer in laatste:
            oogst, vorige_rassen = laatste[kap.nummer]
            if oogst is None:
                meldingen.append(f"Kap {kap.nummer}: de laatste teelt heeft geen oogstdatum; deze kap is niet gevuld.")
                continue
            plant = plantdatum_voorstel(oogst, kap, inst)
            eerste = False
        else:
            vorige_rassen = ()
            stap = zonder // opties.kappen_per_plantdag if opties.kappen_per_plantdag else 0
            while len(startdagen) <= stap:
                d = eerstvolgende_toegestane_dag(d + timedelta(days=1), inst.plantdagen, inst.blokkades, None)
                startdagen.append(d)
            plant = eerstvolgende_toegestane_dag(startdagen[stap], inst.plantdagen, inst.blokkades, kap.nummer)
            zonder += 1
            eerste = True
        index = 0
        while plant is not None and (eind is None or plant < eind) and (opties.rondes is None or index < opties.rondes):
            code = rassen_per_positie[positie] if opties.rasverdeling else None
            rassen = ((code, None),) if code else tuple(vorige_rassen)
            waarden: dict[str, int] = {}
            toelichting: dict[str, Any] = {}
            ontbreekt = []
            licht_uit = None
            for veld in VELDEN:
                v = voorspeller.veld(veld, rassen, plant, licht_uit) if voorspeller is not None and rassen else None
                if v is not None:
                    waarde = int(v.waarde)
                    toelichting[veld] = (v.ondergrens, v.bovengrens, v.bron, v.modelversie)
                elif opties.terugval.get(veld) is not None:
                    waarde = int(opties.terugval[veld])
                    toelichting[veld] = (None, None, "terugvalwaarde uit de instellingen (Genereren)", None)
                else:
                    ontbreekt.append(veld)
                    continue
                waarden[veld] = _binnen(waarde, inst.grenzen.get(veld))
                if veld == "ld_dagen":
                    licht_uit = plant + timedelta(days=waarden[veld])
            if ontbreekt:
                meldingen.append(f"Kap {kap.nummer}: {', '.join(ontbreekt)} onbekend vanaf {plant.isoformat()} (geen "
                                 "voorstel uit het model en geen terugvalwaarde); de ketting van deze kap stopt hier.")  # fmt: skip
                break
            oogst = plant + timedelta(days=waarden["ld_dagen"] + waarden["reactietijd"] + waarden["verlenging"])
            if inst.oogstdagen and oogst.isoweekday() not in inst.oogstdagen:
                laag, hoog = inst.grenzen.get("verlenging", (None, None))
                basis = oogst - timedelta(days=waarden["verlenging"])
                for verlenging in range(int(laag if laag is not None else 0), int(hoog if hoog is not None else 0) + 1):
                    if (basis + timedelta(days=verlenging)).isoweekday() in inst.oogstdagen:
                        waarden["verlenging"], oogst = verlenging, basis + timedelta(days=verlenging)
                        toelichting["verlenging"] = (
                            None,
                            None,
                            "gekozen zodat de oogst op een toegestane oogstdag valt",
                            None,
                        )
                        break
            if oogst <= plant:
                meldingen.append(
                    f"Kap {kap.nummer}: teeltduur 0 dagen vanaf {plant.isoformat()}; de ketting stopt hier."
                )
                break
            nieuw.append(NieuweTeelt(kap.nummer, index, rassen, plant, eerste, oogst, waarden, toelichting))
            eerste = False
            vorige_rassen = rassen
            index += 1
            plant = plantdatum_voorstel(oogst, kap, inst)
    return nieuw, meldingen
