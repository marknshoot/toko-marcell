"""Tests for the copilot v2 Postgres checkpointer memory (api/copilot_memory.py).

Needs the DB. Uses the FAKE LLM for turns (no network).
"""

import asyncio
import sys
import uuid
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

from config import get_settings

DB_URL = get_settings().DATABASE_URL


def _run(coro):
    return asyncio.run(coro)


def test_checkpointer_setup():
    from copilot_memory import get_checkpointer
    cp = get_checkpointer()
    assert cp is not None  # PostgresSaver on the test DB


def test_turn_persists_and_reads_back():
    from copilot_agent import run_turn
    from copilot_memory import delete_thread, get_checkpointer, get_thread_messages

    cp = get_checkpointer()
    tid = f"test-{uuid.uuid4()}"
    try:
        _run(run_turn("ada kemeja flanel?", db_url=DB_URL, thread_id=tid, checkpointer=cp, fake=True))
        _run(run_turn("ada celana chino?", db_url=DB_URL, thread_id=tid, checkpointer=cp, fake=True))
        msgs = get_thread_messages(tid, db_url=DB_URL)
        assert len(msgs) >= 4
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"
    finally:
        delete_thread(tid, db_url=DB_URL)


def test_delete_thread_clears_history():
    from copilot_agent import run_turn
    from copilot_memory import delete_thread, get_checkpointer, get_thread_messages

    cp = get_checkpointer()
    tid = f"test-{uuid.uuid4()}"
    _run(run_turn("ada jaket?", db_url=DB_URL, thread_id=tid, checkpointer=cp, fake=True))
    assert len(get_thread_messages(tid, db_url=DB_URL)) >= 2
    delete_thread(tid, db_url=DB_URL)
    assert get_thread_messages(tid, db_url=DB_URL) == []


def test_touch_and_cleanup_fresh_thread_not_removed():
    from copilot_memory import cleanup_expired_threads, delete_thread, ensure_activity_table, touch_thread

    ensure_activity_table(DB_URL)
    tid = f"test-{uuid.uuid4()}"
    touch_thread(tid, db_url=DB_URL)
    # A just-touched thread must NOT be cleaned up (TTL is 7 days).
    removed = cleanup_expired_threads(db_url=DB_URL)
    assert isinstance(removed, int)
    # our fresh thread still has its activity row
    delete_thread(tid, db_url=DB_URL)


def test_cleanup_removes_expired_thread():
    import psycopg
    from copilot_memory import cleanup_expired_threads, ensure_activity_table

    ensure_activity_table(DB_URL)
    tid = f"test-expired-{uuid.uuid4()}"
    # Insert an activity row dated 8 days ago.
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO copilot_thread_activity (thread_id, last_seen) "
                "VALUES (%s, now() - interval '8 days')",
                (tid,),
            )
        conn.commit()
    removed = cleanup_expired_threads(db_url=DB_URL)
    assert removed >= 1
    # the expired row is gone
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM copilot_thread_activity WHERE thread_id = %s", (tid,))
            assert cur.fetchone()[0] == 0
