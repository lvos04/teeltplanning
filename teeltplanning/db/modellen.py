"""Tabellen (BOUWPLAN §5), fase 1-deel. Lampgroepen, parameters, weer, belichting, afzet en koppeling volgen
in latere fasen met eigen migraties."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .basis import Base


def utcnu() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# --- Gebruikers en beveiliging (zoals Gewichtsverwachting) -------------------------------------------------
class Gebruiker(Base):
    __tablename__ = "gebruiker"
    id: Mapped[int] = mapped_column(primary_key=True)
    gebruikersnaam: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    wachtwoord_hash: Mapped[str] = mapped_column(String(300))
    rol: Mapped[str] = mapped_column(String(20))  # beheerder | medewerker | lezer
    actief: Mapped[bool] = mapped_column(Boolean, default=True)
    aangemaakt_op: Mapped[datetime] = mapped_column(DateTime, default=utcnu)
    mfa_geheim: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mfa_aan: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_laatste_stap: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Sessie(Base):
    __tablename__ = "sessie"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    gebruiker_id: Mapped[int] = mapped_column(ForeignKey("gebruiker.id"))
    verloopt_op: Mapped[datetime] = mapped_column(DateTime, index=True)


class MfaStap(Base):
    """Kortlevende stap tussen wachtwoord en verificatiecode; alleen de hash wordt bewaard."""

    __tablename__ = "mfa_stap"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    gebruiker_id: Mapped[int] = mapped_column(ForeignKey("gebruiker.id"))
    verloopt_op: Mapped[datetime] = mapped_column(DateTime, index=True)
    pogingen: Mapped[int] = mapped_column(Integer, default=0)


class Aanmeldpoging(Base):
    __tablename__ = "aanmeldpoging"
    id: Mapped[int] = mapped_column(primary_key=True)
    sleutel: Mapped[str] = mapped_column(String(200), index=True)
    op: Mapped[datetime] = mapped_column(DateTime, default=utcnu, index=True)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    gebruiker_id: Mapped[int | None] = mapped_column(ForeignKey("gebruiker.id"), nullable=True)
    actie: Mapped[str] = mapped_column(String(80), index=True)
    onderwerp: Mapped[str] = mapped_column(String(200), index=True)
    details: Mapped[Any] = mapped_column(JSON, nullable=True)
    op: Mapped[datetime] = mapped_column(DateTime, default=utcnu, index=True)


class Instelling(Base):
    """Eén regel per wijziging; de nieuwste (per ingangsdatum) geldt. Zie teeltplanning/instellingen.py."""

    __tablename__ = "instelling"
    id: Mapped[int] = mapped_column(primary_key=True)
    sleutel: Mapped[str] = mapped_column(String(100), index=True)
    waarde: Mapped[Any] = mapped_column(JSON, nullable=True)
    is_startwaarde: Mapped[bool] = mapped_column(Boolean, default=False)
    geldig_vanaf: Mapped[date | None] = mapped_column(Date, nullable=True)
    aangemaakt_op: Mapped[datetime] = mapped_column(DateTime, default=utcnu)
    gewijzigd_door: Mapped[int | None] = mapped_column(ForeignKey("gebruiker.id"), nullable=True)
    reden: Mapped[str | None] = mapped_column(Text, nullable=True)


# --- Bedrijfstabellen --------------------------------------------------------------------------------------
class Kap(Base):
    __tablename__ = "kap"
    nummer: Mapped[int] = mapped_column(Integer, primary_key=True)
    vakgroep: Mapped[str | None] = mapped_column(String(20), nullable=True)
    oppervlak_m2: Mapped[float | None] = mapped_column(Float, nullable=True)  # None = bedrijfsinstelling
    plantvolgorde: Mapped[int | None] = mapped_column(Integer, nullable=True)  # volgnummer in het rondeblad
    wisseltijd_dagen: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = bedrijfsinstelling
    actief: Mapped[bool] = mapped_column(Boolean, default=True)
    bijgewerkt_op: Mapped[datetime] = mapped_column(DateTime, default=utcnu, onupdate=utcnu)


class Ras(Base):
    __tablename__ = "ras"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    naam: Mapped[str] = mapped_column(String(100))
    doelgewicht_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperatuurgevoeligheid_dagen_per_graad: Mapped[float | None] = mapped_column(Float, nullable=True)
    actief: Mapped[bool] = mapped_column(Boolean, default=True)


class Scenario(Base):
    __tablename__ = "scenario"
    id: Mapped[int] = mapped_column(primary_key=True)
    naam: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="concept")  # concept | vastgesteld
    afgeleid_van: Mapped[int | None] = mapped_column(ForeignKey("scenario.id"), nullable=True)
    aangemaakt_op: Mapped[datetime] = mapped_column(DateTime, default=utcnu)
    __table_args__ = (
        # Er is hooguit één vastgesteld plan.
        Index("ix_scenario_een_vastgesteld", "status", unique=True, sqlite_where=text("status = 'vastgesteld'")),
    )


class Ronde(Base):
    __tablename__ = "ronde"
    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenario.id"), index=True)
    nummer: Mapped[int] = mapped_column(Integer)
    iso_jaar_eerste_planting: Mapped[int | None] = mapped_column(Integer, nullable=True)
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    __table_args__ = (UniqueConstraint("scenario_id", "nummer"),)


class Teelt(Base):
    __tablename__ = "teelt"
    id: Mapped[int] = mapped_column(primary_key=True)
    ronde_id: Mapped[int] = mapped_column(ForeignKey("ronde.id"), index=True)
    kap: Mapped[int] = mapped_column(ForeignKey("kap.nummer"), index=True)
    volgnummer: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schemacode: Mapped[str | None] = mapped_column(String(50), nullable=True)
    opmerking: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Realisatie zoals in het werkboek ingevuld (kolom V en X); vanaf fase 6 uit Gewichtsverwachting.
    geoogst_op: Mapped[date | None] = mapped_column(Date, nullable=True)
    gemiddeld_gewicht_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    bijgewerkt_op: Mapped[datetime] = mapped_column(DateTime, default=utcnu, onupdate=utcnu)
    rassen: Mapped[list[TeeltRas]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    __table_args__ = (UniqueConstraint("ronde_id", "kap"),)


class TeeltRas(Base):
    __tablename__ = "teelt_ras"
    id: Mapped[int] = mapped_column(primary_key=True)
    teelt_id: Mapped[int] = mapped_column(ForeignKey("teelt.id"), index=True)
    ras_id: Mapped[int | None] = mapped_column(ForeignKey("ras.id"), nullable=True)
    code: Mapped[str] = mapped_column(String(20))
    bedden: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Veldwaarde(Base):
    """Drie lagen per veld (§4.2): voorstel (met bron/modelversie/band) en handmatig. Effectief wordt berekend."""

    __tablename__ = "veldwaarde"
    id: Mapped[int] = mapped_column(primary_key=True)
    teelt_id: Mapped[int] = mapped_column(ForeignKey("teelt.id"), index=True)
    veld: Mapped[str] = mapped_column(String(40))
    voorstel: Mapped[Any] = mapped_column(JSON, nullable=True)
    ondergrens: Mapped[float | None] = mapped_column(Float, nullable=True)
    bovengrens: Mapped[float | None] = mapped_column(Float, nullable=True)
    bron: Mapped[str | None] = mapped_column(String(100), nullable=True)
    modelversie: Mapped[str | None] = mapped_column(String(100), nullable=True)
    handmatig: Mapped[Any] = mapped_column(JSON, nullable=True)
    handmatig_bron: Mapped[str | None] = mapped_column(String(40), nullable=True)  # gebruiker | import:<id>
    gewijzigd_door: Mapped[int | None] = mapped_column(ForeignKey("gebruiker.id"), nullable=True)
    gewijzigd_op: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reden: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (UniqueConstraint("teelt_id", "veld"),)


class Importbatch(Base):
    __tablename__ = "importbatch"
    id: Mapped[int] = mapped_column(primary_key=True)
    soort: Mapped[str] = mapped_column(String(40))  # programma
    bestandsnaam: Mapped[str] = mapped_column(String(300))
    pad: Mapped[str] = mapped_column(String(500))  # relatief onder DATA_DIR/bron/
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="voorbeeld")  # voorbeeld | bevestigd | mislukt
    meldingen: Mapped[Any] = mapped_column(JSON, nullable=True)
    voorbeeld: Mapped[Any] = mapped_column(JSON, nullable=True)
    keuzes: Mapped[Any] = mapped_column(JSON, nullable=True)  # o.a. ISO-jaar per ronde
    scenario_id: Mapped[int | None] = mapped_column(ForeignKey("scenario.id"), nullable=True)
    aangemaakt_door: Mapped[int | None] = mapped_column(ForeignKey("gebruiker.id"), nullable=True)
    aangemaakt_op: Mapped[datetime] = mapped_column(DateTime, default=utcnu)
    bevestigd_door: Mapped[int | None] = mapped_column(ForeignKey("gebruiker.id"), nullable=True)
    bevestigd_op: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Werkboekregel(Base):
    """Bewijs per geïmporteerde regel: broncellen met formule én gecachte uitkomst."""

    __tablename__ = "werkboekregel"
    id: Mapped[int] = mapped_column(primary_key=True)
    importbatch_id: Mapped[int] = mapped_column(ForeignKey("importbatch.id"), index=True)
    teelt_id: Mapped[int | None] = mapped_column(ForeignKey("teelt.id"), nullable=True, index=True)
    blad: Mapped[str] = mapped_column(String(100))
    rij: Mapped[int] = mapped_column(Integer)
    broncellen: Mapped[Any] = mapped_column(JSON)
