"""Historie voor de voorstelladder (§6.7, §8.3): afgesloten teelten uit alle bronnen als `Waarneming`.

Bronnen, in volgorde van voorrang (een latere bron vult alleen ontbrekende velden aan):

1. **Gewichtsverwachting** (fase 6): gekoppelde teelten (kap + plantdatum ± tolerantie) met gerealiseerde datums,
   gewicht en uitval.
2. **Plan** (het vastgestelde scenario, of het eerste): teelten waarvan de oogst vóór de peildatum ligt. Alleen
   **handmatige** waarden tellen als waarneming (geïmporteerd uit het werkboek of ingevuld); een voorstel van de
   app zelf nooit, anders leert het model van zijn eigen voorstellen. Gewicht = kolom X (gemiddeld gewicht).
3. **Teeltschema** (belichtingswerkboek): LD-dagen = licht uit − plantdatum; reactietijd = teeltduur korte dag −
   eerste aftrek − tweede aftrek (instellingen, start 2 en 3, zoals het teeltschema hem afleidt); gewicht. Het
   teeltschema heeft geen raskolom: het ras komt van de teelt in het plan op dezelfde kap en plantdatum
   (± tolerantie). Zonder koppeling telt de regel niet mee voor een ras.

Teelten met meer dan één ras (zoals 3a3p) hebben `ras = None` en tellen niet mee in de seizoensbasis per ras.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import instellingen, planning
from ..db.modellen import Ronde, TeeltschemaRegel, Weerdag
from .model import Waarneming

TEMPERATUUR_VOORRANG = ("gemeten", "historie")


def dagtemperaturen(db: Session, verwachting: bool = False) -> dict[date, float]:
    """Etmaaltemperatuur per dag: gemeten gaat voor historie (en voor de verwachting als die gevraagd is)."""
    soorten = TEMPERATUUR_VOORRANG + (("verwachting",) if verwachting else ())
    uit: dict[date, tuple[int, float]] = {}
    for datum, soort, t in db.execute(select(Weerdag.datum, Weerdag.soort, Weerdag.etmaaltemperatuur)):
        if t is None or soort not in soorten:
            continue
        rang = soorten.index(soort)
        if datum not in uit or rang < uit[datum][0]:
            uit[datum] = (rang, t)
    return {d: t for d, (_, t) in uit.items()}


class Temperaturen:
    """Verwachte etmaaltemperatuur per dag: gemeten/historie/verwachting, anders het weekgemiddelde over de jaren
    (voor de toekomst). Geen gegevens voor een week = onbekend."""

    def __init__(self, dagen: dict[date, float]):
        self.dagen = dagen
        per_week: dict[int, list[float]] = {}
        for d, t in dagen.items():
            per_week.setdefault(d.isocalendar().week, []).append(t)
        self.normaal = {w: sum(v) / len(v) for w, v in per_week.items()}

    def __bool__(self) -> bool:
        return bool(self.dagen)

    def get(self, d: date) -> float | None:
        waarde = self.dagen.get(d)
        return waarde if waarde is not None else self.normaal.get(d.isocalendar().week)


def _gemiddeld(temperaturen: dict[date, float], van: date | None, tot: date | None) -> float | None:
    if van is None or tot is None or tot <= van:
        return None
    dagen = [temperaturen.get(van + timedelta(days=i)) for i in range((tot - van).days)]
    if any(t is None for t in dagen):
        return None
    return sum(dagen) / len(dagen)


def _getal(waarde) -> float | None:
    return float(waarde) if isinstance(waarde, (int, float)) and not isinstance(waarde, bool) else None


def verzamel(db: Session, peildatum: date | None = None, extra: list[Waarneming] | None = None):
    """(waarnemingen, herkomst). `extra` = waarnemingen met voorrang (gekoppelde teelten uit Gewichtsverwachting)."""
    peildatum = peildatum or date.today()
    snap = instellingen.laad(db)
    tolerantie = snap["import.koppel_tolerantie_dagen"] or 0
    aftrek = (snap["gewas.reactietijd_aftrek_1"] or 0) + (snap["gewas.reactietijd_aftrek_2"] or 0)
    temperaturen = dagtemperaturen(db)
    herkomst = {"gewichtsverwachting": 0, "plan": 0, "teeltschema": 0, "teeltschema_aangevuld": 0,
                "teeltschema_zonder_ras": 0, "gemengd": 0}  # fmt: skip
    obs: list[Waarneming] = []
    per_kap: dict[int, list[Waarneming]] = {}

    def koppel(kap: int | None, plant: date | None) -> list[Waarneming]:
        if kap is None or plant is None:
            return []
        return [w for w in per_kap.get(kap, []) if abs((w.plantdatum - plant).days) <= tolerantie]

    def voeg_toe(w: Waarneming, sleutel_bron: str) -> None:
        obs.append(w)
        per_kap.setdefault(w.kap, []).append(w)
        herkomst[sleutel_bron] += 1

    for w in extra or []:
        voeg_toe(w, "gewichtsverwachting")

    scenario = planning.vastgesteld_of_eerste(db)
    if scenario is not None:
        geladen, plan = planning.bereken(db, scenario.id)
        rondes = {r.id: r for r in db.scalars(select(Ronde).where(Ronde.scenario_id == scenario.id))}
        for teelt in sorted(geladen.teelten.values(), key=lambda t: t.id):
            u = plan.teelten[teelt.id]
            plant = u.effectief["plantdatum"]
            if plant is None or u.oogst is None or u.oogst >= peildatum:
                continue

            def hand(veld, u=u):
                return u.effectief[veld] if u.bron[veld] == "handmatig" else None

            codes = [r.code for r in teelt.rassen]
            if len(codes) > 1:
                herkomst["gemengd"] += 1
            nieuw = Waarneming(
                sleutel=f"teelt:{teelt.id}", bron=f"{scenario.naam}, ronde {rondes[teelt.ronde_id].nummer}",
                kap=teelt.kap, ras=codes[0] if len(codes) == 1 else None, plantdatum=plant,
                ld_dagen=hand("ld_dagen"), verlenging=hand("verlenging"), reactietijd=hand("reactietijd"),
                gewicht=teelt.gemiddeld_gewicht_g, dichtheid=hand("dichtheid"),
                lichtsom=u.lichtsom.totaal if u.lichtsom else None,
                temperatuur=_gemiddeld(temperaturen, u.licht_uit, u.oogst),
            )  # fmt: skip
            bestaand = koppel(teelt.kap, plant)
            if len(bestaand) == 1:
                _vul_aan(bestaand[0], nieuw)
            else:
                voeg_toe(nieuw, "plan")

    for regel in db.scalars(select(TeeltschemaRegel).order_by(TeeltschemaRegel.rij)):
        w = regel.waarden or {}
        datums = w.get("datums") or {}
        plant = regel.plantdatum
        licht_uit = date.fromisoformat(datums["licht_uit"]) if datums.get("licht_uit") else None
        if plant is None:
            continue
        kd = _getal(w.get("teeltduur_kd"))
        reactietijd = kd - aftrek if kd is not None else _getal(w.get("reactietijd"))
        nieuw = Waarneming(
            sleutel=f"teeltschema:{regel.id}", bron=f"teeltschema rij {regel.rij}", kap=regel.kap, ras=None,
            plantdatum=plant, ld_dagen=(licht_uit - plant).days if licht_uit else None, reactietijd=reactietijd,
            gewicht=_getal(w.get("gewicht")), uitval=_getal(w.get("uitval")),
            temperatuur=_gemiddeld(temperaturen, licht_uit, licht_uit + timedelta(days=int(kd)) if licht_uit and kd else None),
        )  # fmt: skip
        bestaand = koppel(regel.kap, plant)
        if len(bestaand) == 1:
            _vul_aan(bestaand[0], nieuw)
            herkomst["teeltschema_aangevuld"] += 1
        else:
            herkomst["teeltschema_zonder_ras"] += 1
            voeg_toe(nieuw, "teeltschema")
    return obs, herkomst


def _vul_aan(doel: Waarneming, bron: Waarneming) -> None:
    for veld in ("ras", "ld_dagen", "verlenging", "reactietijd", "gewicht", "dichtheid", "lichtsom", "temperatuur",
                 "uitval"):  # fmt: skip
        if getattr(doel, veld) is None and getattr(bron, veld) is not None:
            setattr(doel, veld, getattr(bron, veld))
