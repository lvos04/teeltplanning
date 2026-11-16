"""Noodbeheer zonder webinterface (zoals Gewichtsverwachting).

python -m teeltplanning.beheer_cli mfa-uit <gebruikersnaam>   # tweestapsverificatie resetten (telefoon kwijt)
python -m teeltplanning.beheer_cli koppelsleutel <naam>        # koppelsleutel aanmaken (eenmalig getoond)
python -m teeltplanning.beheer_cli koppelsleutel-intrekken <id>
"""

import argparse

from sqlalchemy import func, select

from .db.basis import SessionLocal
from .db.modellen import Gebruiker
from .main import migreer
from .mfa import reset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m teeltplanning.beheer_cli", description="Noodbeheer voor accounts")
    sub = parser.add_subparsers(dest="actie", required=True)
    sub.add_parser("mfa-uit", help="tweestapsverificatie voor een account uitzetten").add_argument("gebruikersnaam")
    sub.add_parser("koppelsleutel", help="koppelsleutel aanmaken (Bedrijfskoppeling)").add_argument("naam")
    sub.add_parser("koppelsleutel-intrekken", help="koppelsleutel intrekken").add_argument("id", type=int)
    args = parser.parse_args(argv)
    migreer()
    if args.actie in ("koppelsleutel", "koppelsleutel-intrekken"):
        from . import koppeling

        with SessionLocal() as db:
            if args.actie == "koppelsleutel":
                rij, sleutel = koppeling.maak_sleutel(db, args.naam, None)
                print(f"Koppelsleutel {rij.id} ({rij.naam}): {sleutel}")
                print("Kopieer deze sleutel nu; hij wordt niet opnieuw getoond.")
            else:
                rij = koppeling.trek_in(db, args.id, None)
                print(f"Koppelsleutel {rij.id} ({rij.naam}) is ingetrokken.")
        return 0
    with SessionLocal() as db:
        gebruiker = db.scalar(
            select(Gebruiker).where(func.lower(Gebruiker.gebruikersnaam) == args.gebruikersnaam.strip().lower())
        )
        if gebruiker is None:
            print("Gebruiker niet gevonden.")
            return 1
        reset(db, gebruiker, None)
        db.commit()
        print(f"Tweestapsverificatie voor {gebruiker.gebruikersnaam} is uitgezet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
