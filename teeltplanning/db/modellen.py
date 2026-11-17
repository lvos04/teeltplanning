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
    LargeBinary,
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


# --- Fase 2: afdrukken -------------------------------------------------------------------------------------
class Sjabloon(Base):
    """Het eigen werkboek van de gebruiker als afdruksjabloon (§9.3, route A). Het bestand staat onder
    DATA_DIR/sjabloon/ en nooit in de image; de nieuwste actieve regel geldt."""

    __tablename__ = "sjabloon"
    id: Mapped[int] = mapped_column(primary_key=True)
    bestandsnaam: Mapped[str] = mapped_column(String(300))
    pad: Mapped[str] = mapped_column(String(500))  # relatief onder DATA_DIR
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    grootte: Mapped[int] = mapped_column(Integer)
    actief: Mapped[bool] = mapped_column(Boolean, default=True)
    controle: Mapped[Any] = mapped_column(JSON, nullable=True)  # bladen, lettertypen, formulecellen
    geupload_door: Mapped[int | None] = mapped_column(ForeignKey("gebruiker.id"), nullable=True)
    geupload_op: Mapped[datetime] = mapped_column(DateTime, default=utcnu)


# --- Fase 3: licht, energie en dichtheid -------------------------------------------------------------------
class Lampgroep(Base):
    """Lampgroep (§6.4). `kap` leeg = alle kappen; module voor kap k = module_van + (k − 1). Een lege
    `geldig_vanaf` betekent "begin onbekend": de groep telt dan niet mee en de lichtsom wordt onbekend."""

    __tablename__ = "lampgroep"
    id: Mapped[int] = mapped_column(primary_key=True)
    naam: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(20))  # led | son_t
    kap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    intensiteit_umol: Mapped[float | None] = mapped_column(Float, nullable=True)
    rendement_umol_per_j: Mapped[float | None] = mapped_column(Float, nullable=True)
    kw_per_kap: Mapped[float | None] = mapped_column(Float, nullable=True)
    dimbaar: Mapped[bool] = mapped_column(Boolean, default=False)
    module_van: Mapped[int | None] = mapped_column(Integer, nullable=True)
    module_tot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    geldig_vanaf: Mapped[date | None] = mapped_column(Date, nullable=True)
    geldig_tot: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="bevestigd")  # bevestigd | indicatief
    opmerking: Mapped[str | None] = mapped_column(Text, nullable=True)
    actief: Mapped[bool] = mapped_column(Boolean, default=True)


class Weerdag(Base):
    __tablename__ = "weerdag"
    id: Mapped[int] = mapped_column(primary_key=True)
    datum: Mapped[date] = mapped_column(Date, index=True)
    soort: Mapped[str] = mapped_column(String(20))  # gemeten | historie | verwachting
    bron: Mapped[str] = mapped_column(String(30))  # knmi | open_meteo | handmatig
    straling_j_cm2: Mapped[float | None] = mapped_column(Float, nullable=True)
    etmaaltemperatuur: Mapped[float | None] = mapped_column(Float, nullable=True)
    opgehaald_op: Mapped[datetime] = mapped_column(DateTime, default=utcnu)
    __table_args__ = (UniqueConstraint("datum", "soort", "bron"),)


class Weeknormaal(Base):
    """P10/P50/P90 van de stralingssom per ISO-week (J/cm² per week) uit de daghistorie."""

    __tablename__ = "weeknormaal"
    week: Mapped[int] = mapped_column(Integer, primary_key=True)
    p10: Mapped[float | None] = mapped_column(Float, nullable=True)
    p50: Mapped[float | None] = mapped_column(Float, nullable=True)
    p90: Mapped[float | None] = mapped_column(Float, nullable=True)
    jaren: Mapped[int] = mapped_column(Integer, default=0)
    berekend_op: Mapped[datetime] = mapped_column(DateTime, default=utcnu)


class BelichtingDag(Base):
    """Dimgewogen uren per module per dag (uit de 5-minutendata). Kwaliteit: lijst met meldingen."""

    __tablename__ = "belichting_dag"
    id: Mapped[int] = mapped_column(primary_key=True)
    module: Mapped[int] = mapped_column(Integer)
    datum: Mapped[date] = mapped_column(Date, index=True)
    branduren: Mapped[float | None] = mapped_column(Float, nullable=True)
    dimgewogen_uren: Mapped[float | None] = mapped_column(Float, nullable=True)
    volledig: Mapped[bool] = mapped_column(Boolean, default=False)
    kwaliteit: Mapped[Any] = mapped_column(JSON, nullable=True)
    importbatch_id: Mapped[int | None] = mapped_column(ForeignKey("importbatch.id"), nullable=True)
    __table_args__ = (UniqueConstraint("module", "datum"),)


class BelichtingWeek(Base):
    """Branduren per module per ISO-week (tabblad belichting, volle kracht)."""

    __tablename__ = "belichting_week"
    id: Mapped[int] = mapped_column(primary_key=True)
    module: Mapped[int] = mapped_column(Integer)
    iso_jaar: Mapped[int] = mapped_column(Integer)
    week: Mapped[int] = mapped_column(Integer)
    uren: Mapped[float | None] = mapped_column(Float, nullable=True)
    celsoort: Mapped[str | None] = mapped_column(String(20), nullable=True)
    importbatch_id: Mapped[int | None] = mapped_column(ForeignKey("importbatch.id"), nullable=True)
    __table_args__ = (UniqueConstraint("module", "iso_jaar", "week"),)


class ParameterLbe(Base):
    __tablename__ = "parameter_lbe"
    id: Mapped[int] = mapped_column(primary_key=True)
    ras: Mapped[str] = mapped_column(String(20))
    plantweek: Mapped[int] = mapped_column(Integer)
    lichtregime: Mapped[str] = mapped_column(String(50), default="")
    lbe_g_per_mol: Mapped[float | None] = mapped_column(Float, nullable=True)
    aantal_waarnemingen: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="onvoldoende")  # onvoldoende | indicatief | gevalideerd
    bron: Mapped[str | None] = mapped_column(String(50), nullable=True)  # kalibratie | handmatig
    bijgewerkt_op: Mapped[datetime] = mapped_column(DateTime, default=utcnu, onupdate=utcnu)
    __table_args__ = (UniqueConstraint("ras", "plantweek", "lichtregime"),)


class LbeIjkpunt(Base):
    """Kaptabel op het tabblad belichting vanaf rij 87: eerste ijkpunten voor de LBE (§6.5)."""

    __tablename__ = "lbe_ijkpunt"
    id: Mapped[int] = mapped_column(primary_key=True)
    importbatch_id: Mapped[int | None] = mapped_column(ForeignKey("importbatch.id"), nullable=True)
    rij: Mapped[int] = mapped_column(Integer)
    kap: Mapped[int] = mapped_column(Integer)
    plantdatum: Mapped[date | None] = mapped_column(Date, nullable=True)
    waarden: Mapped[Any] = mapped_column(JSON)  # alle kolommen zoals gelezen (uren al omgerekend)


class TeeltschemaRegel(Base):
    __tablename__ = "teeltschema_regel"
    id: Mapped[int] = mapped_column(primary_key=True)
    importbatch_id: Mapped[int | None] = mapped_column(ForeignKey("importbatch.id"), nullable=True)
    rij: Mapped[int] = mapped_column(Integer)
    kap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plantdatum: Mapped[date | None] = mapped_column(Date, nullable=True)
    waarden: Mapped[Any] = mapped_column(JSON)


class Bronstatus(Base):
    __tablename__ = "bronstatus"
    bron: Mapped[str] = mapped_column(String(30), primary_key=True)
    laatst_geprobeerd: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    laatst_gelukt: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    melding: Mapped[str | None] = mapped_column(Text, nullable=True)
    aantal: Mapped[int | None] = mapped_column(Integer, nullable=True)


class BronGeheim(Base):
    """Geheime sleutels van bronnen (zoals de KNMI-API-sleutel), versleuteld met DATA_DIR/geheim.key."""

    __tablename__ = "bron_geheim"
    bron: Mapped[str] = mapped_column(String(30), primary_key=True)
    versleuteld: Mapped[bytes] = mapped_column(LargeBinary)
    gewijzigd_op: Mapped[datetime] = mapped_column(DateTime, default=utcnu)


class Energieprijs(Base):
    """Gemiddelde dagvooruitprijs per dag (EnergyZero), alleen voor de komende dag en achteraf."""

    __tablename__ = "energieprijs"
    datum: Mapped[date] = mapped_column(Date, primary_key=True)
    eur_per_mwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    bron: Mapped[str] = mapped_column(String(30), default="energyzero")
    opgehaald_op: Mapped[datetime] = mapped_column(DateTime, default=utcnu)


# --- Fase 4: voorstellen met model -------------------------------------------------------------------------
class Modelversie(Base):
    """Eén training + voortschrijdende backtest (§8.3). Het artefact (waarnemingen, instellingen, backtest) is
    gewone JSON; de nieuwste versie met status `klaar` levert de voorstellen."""

    __tablename__ = "modelversie"
    id: Mapped[int] = mapped_column(primary_key=True)
    versie: Mapped[str] = mapped_column(String(40), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="bezig")  # bezig | klaar | mislukt
    gestart_op: Mapped[datetime] = mapped_column(DateTime, default=utcnu)
    klaar_op: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    gestart_door: Mapped[int | None] = mapped_column(ForeignKey("gebruiker.id"), nullable=True)
    melding: Mapped[str | None] = mapped_column(Text, nullable=True)
    aantal_waarnemingen: Mapped[int | None] = mapped_column(Integer, nullable=True)
    herkomst: Mapped[Any] = mapped_column(JSON, nullable=True)  # aantallen per bron, overgeslagen
    artefact: Mapped[Any] = mapped_column(JSON, nullable=True)
