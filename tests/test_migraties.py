"""Migratie 0001 komt overeen met de modellen (geen verschil tussen schema en code)."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from teeltplanning.db.basis import Base, engine


def test_schema_gelijk_aan_modellen(client):
    with engine.connect() as verbinding:
        verschil = compare_metadata(MigrationContext.configure(verbinding), Base.metadata)
    assert verschil == []
