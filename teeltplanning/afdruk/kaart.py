"""Celwaarden voor de teeltkaart (§9.2) en het rondeblad (§9.3) uit het berekende plan.

Alle celposities komen uit het instellingenregister: de teeltkaart uit *Afdrukken → Teeltkaart: …*, het
rondeblad uit dezelfde kolomtoewijzing als de importer (*Import → Rondeblad: …*), zodat afdrukken en
importeren altijd dezelfde cellen gebruiken.
"""

from __future__ import annotations

from datetime import date

from openpyxl.utils import column_index_from_string, get_column_letter

from ..kern.kalender import week_dag, weergave
from ..kern.teeltkaart import BEHANDELINGEN, KaartInstellingen, Seizoen, Teeltkaart
from ..xlsxpakket import Datum, kolom_verschuif

SEIZOENEN = ("winter", "voorjaar", "zomer", "najaar")


def kaart_instellingen(snap) -> KaartInstellingen:
    streef = {}
    for rij in snap["teeltkaart.streeflengte"] or []:
        streef[rij["ld_dag"]] = {s: rij.get(s) for s in SEIZOENEN}
    seizoenen = []
    for s in SEIZOENEN:
        van, tot = (int(x) for x in snap[f"teeltkaart.seizoen_{s}"].split("-"))
        seizoenen.append(Seizoen(s, van, tot))
    return KaartInstellingen(
        streeflengte=streef,
        seizoenen=tuple(seizoenen),
        meetdagen=frozenset(snap["teeltkaart.meetdagen"] or ()),
        aantal_momenten=snap["teeltkaart.aantal_momenten"],
        offsets={n: snap[f"teeltkaart.offset_{n}"] for n in BEHANDELINGEN},
    )


def _datumwaarden(naam: str, d: date | None) -> dict[str, object]:
    if d is None:
        return {naam: None, f"{naam}_week": None, f"{naam}_dag": None}
    _, week, dag = week_dag(d)
    return {naam: Datum(d, weergave(d)), f"{naam}_week": week, f"{naam}_dag": dag}


def kaartvelden(kaart: Teeltkaart | None, kap: int | None, ronde: int | None, opmerking: str | None) -> dict:
    """Alle velden die een cel kunnen krijgen (zie instellingen.KAART_VELDEN)."""
    if kaart is None:
        velden: dict[str, object] = {"kap": kap, "ronde": ronde}
        return velden
    velden = {
        "kap": kaart.kap,
        "ronde": kaart.ronde,
        "soort": kaart.soort,
        "dichtheid": kaart.dichtheid,
        "ld_dagen": kaart.ld_dagen,
        "tussenlicht_dagen": kaart.tussenlicht_dagen,
        "reactietijd": kaart.reactietijd,
        "verlenging": kaart.verlenging,
        "opmerking": opmerking,
    }
    for naam, d in (("plantdatum", kaart.plantdatum), ("licht_uit", kaart.licht_uit), ("licht_aan", kaart.licht_aan),
                    ("licht_uit_2", kaart.licht_uit_2), ("oogst", kaart.oogst), *kaart.behandelingen.items()):  # fmt: skip
        velden |= _datumwaarden(naam, d)
    return velden


def celwaarden_kaart(kaart: Teeltkaart | None, kap: int | None, ronde: int | None, opmerking: str | None, snap,
                     verschuiving: int = 0) -> dict[str, object]:  # fmt: skip
    """Cel → waarde voor één kaart. Cellen zonder waarde worden leeg geschreven (geen restanten uit het sjabloon)."""
    velden = kaartvelden(kaart, kap, ronde, opmerking)
    uit: dict[str, object] = {}
    for veld, cel in snap["afdrukken.teeltkaart_cellen"].items():
        uit[kolom_verschuif(cel, verschuiving)] = velden.get(veld)
    kolommen = snap["afdrukken.teeltkaart_momentkolommen"]
    eerste = snap["afdrukken.teeltkaart_eerste_momentrij"]
    momenten = kaart.momenten if kaart else ()
    for i in range(snap["teeltkaart.aantal_momenten"]):
        m = momenten[i] if i < len(momenten) else None
        waarden = {"datum": None, "datum_week": None, "datum_dag": None, "ld_dag": None, "streeflengte": None}
        if m is not None:
            _, week, dag = week_dag(m.datum)
            waarden = {"datum": Datum(m.datum, weergave(m.datum)), "datum_week": week, "datum_dag": dag,
                       "ld_dag": m.ld_dag, "streeflengte": m.streeflengte}  # fmt: skip
        for onderdeel, kolom in kolommen.items():
            nieuwe_kolom = get_column_letter(column_index_from_string(kolom) + verschuiving)
            uit[f"{nieuwe_kolom}{eerste + i}"] = waarden[onderdeel]
    return uit


def celwaarden_rondeblad(regels: list[dict], snap, titel: str) -> dict[str, object]:
    """Cel → waarde voor het rondeblad. `regels`: per teelt de velden met dezelfde namen als de kolomtoewijzing
    van de importer. Elke regel van eerste t/m laatste rij wordt geschreven (lege regels worden leeg)."""
    kolommen = snap["import.rondeblad_kolommen"]
    eerste, laatste = snap["import.rondeblad_eerste_rij"], snap["import.rondeblad_laatste_rij"]
    uit: dict[str, object] = {snap["import.rondeblad_titelcel"]: titel}
    per_rij = {}
    for index, regel in enumerate(regels):
        volgnummer = regel.get("volgnummer")
        rij = eerste + (volgnummer - 1 if isinstance(volgnummer, int) and volgnummer >= 1 else index)
        if rij <= laatste and rij not in per_rij:
            per_rij[rij] = regel
    for rij in range(eerste, laatste + 1):
        regel = per_rij.get(rij, {})
        for veld, kolom in kolommen.items():
            uit[f"{kolom}{rij}"] = regel.get(veld)
    return uit
