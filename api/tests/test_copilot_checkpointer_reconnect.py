"""The checkpointer must recover when its database connection is killed server-side.

Neon suspends compute after 5 minutes and drops connections while Render stays awake,
so a checkpointer holding one cached connection fails for good. This test simulates
that by terminating the backends and then using the saver again.

Needs the local Postgres (docker compose). It terminates every other session of the
test database, so it must never point at a shared database.
"""

import os
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

import copilot_memory
import psycopg
import pytest

DSN = os.environ.get("DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko")


@pytest.fixture
def saver():
    copilot_memory.close_checkpointer()
    saver = copilot_memory.get_checkpointer()
    if saver is None:
        pytest.skip("checkpointer unavailable (is the database running?)")
    yield saver
    copilot_memory.close_checkpointer()


def _kill_other_sessions() -> None:
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid()"
        )


def test_checkpointer_recovers_after_connections_are_killed(saver):
    cfg = {"configurable": {"thread_id": "t-reconnect-test"}}
    saver.get_tuple(cfg)  # opens a pooled connection

    _kill_other_sessions()

    # The old single-connection saver raised OperationalError here, and on every later call.
    saver.get_tuple(cfg)
    saver.get_tuple(cfg)


def test_close_checkpointer_is_idempotent_and_allows_reopen():
    copilot_memory.close_checkpointer()
    copilot_memory.close_checkpointer()
    reopened = copilot_memory.get_checkpointer()
    if reopened is None:
        pytest.skip("checkpointer unavailable (is the database running?)")
    assert copilot_memory._POOL is not None
    copilot_memory.close_checkpointer()
    assert copilot_memory._POOL is None
    assert copilot_memory._SAVER is None
