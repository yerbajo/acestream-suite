"""Alembic's entry point: the schema of `app/db/models.py`, reached two ways.

A caller that names a URL in the Alembic configuration gets that database — the tests and the
launcher both do — and otherwise the app's own `DATABASE_URL` is used. Batch rendering is on
everywhere: SQLite cannot alter a table in place, so Alembic rebuilds it and copies the rows, and
that is the only way it can add a constraint to a table that already holds data.
"""

from alembic import context
from sqlalchemy import create_engine

from app.config import DATA_DIR, DATABASE_URL
from app.db import models  # noqa: F401  (importing it is what fills Base.metadata)
from app.db.base import Base

config = context.config
target_metadata = Base.metadata


def _migration_url() -> str:
    """The database to migrate: the one the configuration names, else the app's own."""
    return config.get_main_option("sqlalchemy.url") or DATABASE_URL


def run_migrations_offline() -> None:
    """Write the SQL out instead of applying it: no connection, every value spelled out."""
    context.configure(
        url=_migration_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply the migrations to the database the URL names."""
    # On a first run the folder holding an SQLite file may not exist yet.
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    engine = create_engine(_migration_url())
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    # The engine is this module's, so nothing else is going to close it.
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
