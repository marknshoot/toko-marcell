"""Alembic environment for Toko Marcell.

The app talks to Postgres with raw psycopg; SQLAlchemy is used here only as the
connection layer Alembic requires. There are no ORM models, so autogenerate is
not used — every migration is hand-written SQL via ``op.execute``.

URL resolution order:
1. ``config.attributes["database_url"]`` (set programmatically, e.g. by tests)
2. ``DATABASE_URL`` from ``config.get_settings()`` (env / .env, same as the API)
"""

from logging.config import fileConfig

import psycopg
from alembic import context
from config import get_settings
from sqlalchemy import create_engine, pool

config = context.config

# Only configure logging when run from the CLI; when the API runs migrations on
# startup it passes configure_logger=False so its own logging setup is kept.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = None


def _database_url() -> str:
    return config.attributes.get("database_url") or get_settings().DATABASE_URL


def run_migrations_offline() -> None:
    """Emit SQL to stdout (``alembic upgrade head --sql``) without a DB connection."""
    context.configure(url="postgresql+psycopg://", literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Hand the DSN to psycopg as-is, so Alembic accepts exactly what the app
    # accepts (postgresql:// URLs *and* libpq key=value strings), with no
    # URL rewriting or escaping of special characters in passwords.
    dsn = _database_url()
    engine = create_engine("postgresql+psycopg://", creator=lambda: psycopg.connect(dsn), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
