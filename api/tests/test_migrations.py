"""Alembic migration tests.

Each test runs against a throwaway database created next to DATABASE_URL, so the
real (seeded) database is never touched. Skips cleanly when Postgres is
unreachable or the role cannot CREATE DATABASE.
"""

import sys
import uuid
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

import psycopg
from config import get_settings
from psycopg import sql

ADMIN_URL = get_settings().DATABASE_URL

APP_TABLES = {
    "products",
    "events",
    "orders",
    "order_items",
    "item_recommendations",
    "reviews",
    "store_knowledge",
    "store_knowledge_questions",
    "copilot_turns",
    "copilot_feedback",
    "admin_users",
    "admin_refresh_tokens",
}


def _url_for(dbname: str) -> str:
    return psycopg.conninfo.make_conninfo(ADMIN_URL, dbname=dbname)


@pytest.fixture
def temp_db_url():
    name = f"toko_migr_{uuid.uuid4().hex[:10]}"
    try:
        with psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=3) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    except psycopg.Error as e:
        pytest.skip(f"cannot create a temp database: {e}")
    try:
        yield _url_for(name)
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def _alembic_cfg(url: str):
    from migrate import alembic_config

    return alembic_config(url)


def _head() -> str:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(_alembic_cfg(ADMIN_URL)).get_current_head()


def _tables(url: str) -> set[str]:
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchall()
    return {r[0] for r in rows}


def _columns(url: str, table: str) -> set[str]:
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,)
        ).fetchall()
    return {r[0] for r in rows}


def _indexes(url: str) -> set[str]:
    with psycopg.connect(url) as conn:
        rows = conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'").fetchall()
    return {r[0] for r in rows}


def _version(url: str) -> str:
    with psycopg.connect(url) as conn:
        return conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]


def test_single_linear_head():
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_cfg(ADMIN_URL))
    assert len(script.get_heads()) == 1


def test_upgrade_empty_database_to_head(temp_db_url):
    from migrate import run_migrations

    run_migrations(temp_db_url)

    assert APP_TABLES <= _tables(temp_db_url)
    assert {"embedding", "embedding_ml"} <= _columns(temp_db_url, "products")
    assert "image_embedding" not in _columns(temp_db_url, "products")
    assert {
        "thread_id",
        "session_id",
        "guardrail",
        "outcome",
        "tool_names",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "model",
        "cost_usd",
        "unanswered_question",
    } <= _columns(temp_db_url, "copilot_turns")
    assert {"thread_id", "session_id", "rating"} <= _columns(temp_db_url, "copilot_feedback")
    assert {
        "products_embedding_idx",
        "products_embedding_ml_idx",
        "store_knowledge_embedding_idx",
        "store_knowledge_q_embedding_idx",
        "copilot_turns_created_idx",
        "copilot_turns_thread_idx",
        "copilot_feedback_created_idx",
        "admin_refresh_tokens_token_hash_idx",
    } <= _indexes(temp_db_url)
    assert "products_image_embedding_idx" not in _indexes(temp_db_url)
    assert _version(temp_db_url) == _head()

    # The migration seeds a working Argon2id demo admin.
    with psycopg.connect(temp_db_url) as conn:
        row = conn.execute(
            "SELECT username, password_hash, role FROM admin_users WHERE username = 'admin'"
        ).fetchone()
    assert row is not None
    assert row[0] == "admin"
    assert row[1].startswith("$argon2id$")
    assert row[2] == "admin"

    # Re-running at head is a no-op.
    run_migrations(temp_db_url)
    assert _version(temp_db_url) == _head()


def test_upgrade_adopts_pre_alembic_database(temp_db_url):
    """A DB built before Alembic (seed dump / Neon) has the schema but no
    alembic_version; upgrade must succeed without a manual `alembic stamp`."""
    from migrate import run_migrations

    run_migrations(temp_db_url)
    with psycopg.connect(temp_db_url) as conn:
        conn.execute("INSERT INTO orders (token, total_idr, item_count) VALUES ('tk_keep', 1000, 1)")
        conn.execute("DROP TABLE alembic_version")

    run_migrations(temp_db_url)

    assert _version(temp_db_url) == _head()
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute("SELECT count(*) FROM orders WHERE token = 'tk_keep'").fetchone()[0] == 1


def test_downgrade_roundtrip(temp_db_url):
    from alembic import command

    cfg = _alembic_cfg(temp_db_url)
    command.upgrade(cfg, "head")

    command.downgrade(cfg, "0003")
    assert "unanswered_question" not in _columns(temp_db_url, "copilot_turns")
    assert "admin_users" not in _tables(temp_db_url)
    assert "admin_refresh_tokens" not in _tables(temp_db_url)
    # Revision 0005's downgrade restores the legacy visual-search column.
    assert "image_embedding" in _columns(temp_db_url, "products")
    assert "products_image_embedding_idx" in _indexes(temp_db_url)

    command.downgrade(cfg, "0001")
    assert "embedding_ml" not in _columns(temp_db_url, "products")

    command.downgrade(cfg, "base")
    assert not (APP_TABLES & _tables(temp_db_url))

    command.upgrade(cfg, "head")
    assert APP_TABLES <= _tables(temp_db_url)
