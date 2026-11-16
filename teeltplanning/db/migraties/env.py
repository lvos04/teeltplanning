from alembic import context

from teeltplanning.db import modellen  # noqa: F401 - registreert alle tabellen
from teeltplanning.db.basis import Base, engine

config = context.config
target_metadata = Base.metadata

if context.is_offline_mode():
    context.configure(url=str(engine.url), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with engine.connect() as verbinding:
        context.configure(connection=verbinding, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
