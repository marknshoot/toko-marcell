"""
Database connection pool and helpers for Toko Marcell API.

``open_pool()`` / ``close_pool()`` are called from the FastAPI lifespan.
``get_conn()`` is a context-manager that checks out a connection from the pool;
it also works as a FastAPI ``Depends`` via ``get_conn_dep()``.

When the pool has not been opened (e.g. pipeline scripts, standalone tests),
``get_conn()`` falls back to a one-shot ``psycopg.connect()``, so
``agent_tools`` and other modules keep working outside of the server process.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Generator
from typing import TYPE_CHECKING

import psycopg
from config import get_settings

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None


def open_pool() -> None:
    """Create the global connection pool (call once from lifespan startup)."""
    global _pool
    if _pool is not None:
        return
    # Imported lazily so modules that only need offline helpers (e.g. the
    # ``agent_tools`` unit tests) don't require psycopg_pool to be installed.
    from psycopg_pool import ConnectionPool

    s = get_settings()
    _pool = ConnectionPool(
        conninfo=s.DATABASE_URL,
        min_size=s.DB_POOL_MIN,
        max_size=s.DB_POOL_MAX,
        open=True,
    )
    logger.info(
        "Connection pool opened (min=%d, max=%d)", s.DB_POOL_MIN, s.DB_POOL_MAX
    )


def close_pool() -> None:
    """Close the global pool (call from lifespan shutdown)."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
        logger.info("Connection pool closed")


@contextlib.contextmanager
def get_conn() -> Generator[psycopg.Connection, None, None]:
    """Yield a connection.  Pool when available, direct connect otherwise."""
    if _pool is not None:
        with _pool.connection() as conn:
            yield conn
    else:
        s = get_settings()
        with psycopg.connect(s.DATABASE_URL) as conn:
            yield conn


def default_db_url() -> str:
    """The URL the process is configured with (pool connection string)."""
    return get_settings().DATABASE_URL


@contextlib.contextmanager
def connect(db_url: str | None = None) -> Generator[psycopg.Connection, None, None]:
    """Yield a connection for an explicit URL, or the pooled one when it matches.

    Services accept an optional ``db_url`` so they can run standalone (pipeline
    scripts, tests) outside the server process.  A URL that differs from the
    configured one cannot come from the pool, so it gets a one-shot connection.
    """
    if not db_url or db_url == default_db_url():
        with get_conn() as conn:
            yield conn
    else:
        with psycopg.connect(db_url) as conn:
            yield conn


def get_conn_dep() -> Generator[psycopg.Connection, None, None]:
    """FastAPI ``Depends``-compatible wrapper around ``get_conn()``."""
    with get_conn() as conn:
        yield conn
