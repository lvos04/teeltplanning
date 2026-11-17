"""Noodbeheer zonder webinterface (zoals Gewichtsverwachting).

python -m teeltplanning.beheer_cli mfa-uit <gebruikersnaam>   # tweestapsverificatie resetten (telefoon kwijt)
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
    args = parser.parse_args(argv)
    migreer()
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
