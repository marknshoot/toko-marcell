"""Programmatic Alembic entry point.

The API calls ``run_migrations()`` on startup so ``docker compose up`` and Render
stay one-step. From a shell, the equivalent is ``cd api && alembic upgrade head``
(or ``python migrate.py``).
"""

from pathlib import Path

from alembic import command
from alembic.config import Config

API_DIR = Path(__file__).resolve().parent


def alembic_config(database_url: str | None = None) -> Config:
    """Alembic config that works from any working directory.

    ``database_url`` overrides DATABASE_URL; it is passed via ``attributes``
    rather than the ini so ``%`` in passwords needs no configparser escaping.
    """
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    cfg.attributes["configure_logger"] = False  # keep the caller's logging setup
    if database_url:
        cfg.attributes["database_url"] = database_url
    return cfg


def run_migrations(database_url: str | None = None) -> None:
    """``alembic upgrade head``. Revisions are idempotent, so this is safe on
    databases created before Alembic (seed dump, Neon) as well as empty ones."""
    command.upgrade(alembic_config(database_url), "head")


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run_migrations()
