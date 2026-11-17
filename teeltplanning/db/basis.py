"""Engine, sessies en `DATA_DIR` (zoals Gewichtsverwachting: alles persistent onder /data)."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
engine = create_engine(
    os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR / 'app.db'}"),
    connect_args={"check_same_thread": False},
    future=True,
)
if engine.dialect.name == "sqlite":

    @event.listens_for(engine, "connect")
    def _sqlite_instellen(verbinding, _):
        cursor = verbinding.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


class Base(DeclarativeBase):
    pass


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db():
    with SessionLocal() as db:
        yield db
