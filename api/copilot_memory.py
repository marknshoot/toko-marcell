"""Copilot v2 conversation memory (design §2).

LangGraph Postgres checkpointer (``PostgresSaver``) keyed by a browser-generated
``thread_id``. Because the checkpoint tables carry no plain "last activity"
timestamp, we keep a tiny sidecar table ``copilot_thread_activity(thread_id,
last_seen)`` that we touch on every turn; the 7-day TTL cleanup deletes threads
whose ``last_seen`` is older than 7 days (both the sidecar row and the
checkpointer's per-thread rows).

A module-level singleton saver is created lazily from ``DATABASE_URL`` and
``setup()`` is run once (idempotent). History is trimmed by a token budget via
``trim_messages`` in the agent's ``before_model`` middleware (wired in
copilot_agent when a checkpointer is present).
"""

import logging
import threading

import psycopg
from config import get_settings

logger = logging.getLogger(__name__)

TTL_DAYS = 7

_SAVER = None
_SAVER_CM = None
_LOCK = threading.Lock()

_ACTIVITY_DDL = """
CREATE TABLE IF NOT EXISTS copilot_thread_activity (
    thread_id TEXT PRIMARY KEY,
    last_seen TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def _db_url() -> str:
    return get_settings().DATABASE_URL


def ensure_activity_table(db_url: str | None = None) -> None:
    with psycopg.connect(db_url or _db_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(_ACTIVITY_DDL)
        conn.commit()


def get_checkpointer():
    """Lazily create and setup the PostgresSaver singleton. Returns None on failure."""
    global _SAVER, _SAVER_CM
    if _SAVER is not None:
        return _SAVER
    with _LOCK:
        if _SAVER is not None:
            return _SAVER
        try:
            from langgraph.checkpoint.postgres import PostgresSaver

            # from_conn_string is a context manager; keep it open for process life.
            _SAVER_CM = PostgresSaver.from_conn_string(_db_url())
            saver = _SAVER_CM.__enter__()
            saver.setup()
            ensure_activity_table()
            _SAVER = saver
            logger.info("[copilot memory] PostgresSaver ready")
        except Exception as e:
            logger.warning("[copilot memory] checkpointer unavailable: %s", e)
            _SAVER = None
    return _SAVER


def touch_thread(thread_id: str, db_url: str | None = None) -> None:
    """Record activity for TTL purposes (upsert last_seen = now())."""
    if not thread_id:
        return
    try:
        with psycopg.connect(db_url or _db_url()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO copilot_thread_activity (thread_id, last_seen)
                    VALUES (%s, now())
                    ON CONFLICT (thread_id) DO UPDATE SET last_seen = now()
                    """,
                    (thread_id,),
                )
            conn.commit()
    except Exception as e:
        logger.info("[copilot memory] touch_thread note: %s", e)


def cleanup_expired_threads(db_url: str | None = None) -> int:
    """Delete threads idle > TTL_DAYS. Returns the number of threads removed.

    Cheap SQL: find expired thread_ids from the activity table, delete their
    checkpointer rows and the activity rows.
    """
    url = db_url or _db_url()
    removed = 0
    try:
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT thread_id FROM copilot_thread_activity "
                    "WHERE last_seen < now() - make_interval(days => %s)",
                    (int(TTL_DAYS),),
                )
                expired = [r[0] for r in cur.fetchall()]
                for tid in expired:
                    for tbl in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
                        try:
                            cur.execute(f"DELETE FROM {tbl} WHERE thread_id = %s", (tid,))
                        except Exception:
                            pass
                    cur.execute("DELETE FROM copilot_thread_activity WHERE thread_id = %s", (tid,))
                    removed += 1
            conn.commit()
        if removed:
            logger.info("[copilot memory] TTL cleanup removed %d expired thread(s)", removed)
    except Exception as e:
        logger.info("[copilot memory] cleanup note: %s", e)
    return removed


def get_thread_messages(thread_id: str, db_url: str | None = None) -> list[dict]:
    """Return the display messages (role/content) for a thread, oldest first."""
    saver = get_checkpointer()
    if saver is None:
        return []
    try:
        from langchain_core.messages import AIMessage, HumanMessage

        cfg = {"configurable": {"thread_id": thread_id}}
        tup = saver.get_tuple(cfg)
        if not tup:
            return []
        channel_values = (tup.checkpoint or {}).get("channel_values", {})
        messages = channel_values.get("messages", []) or []
        out = []
        for m in messages:
            if isinstance(m, HumanMessage):
                role = "user"
            elif isinstance(m, AIMessage):
                role = "assistant"
            else:
                continue
            content = m.content if isinstance(m.content, str) else str(m.content)
            if content.strip():
                out.append({"role": role, "content": content})
        return out
    except Exception as e:
        logger.info("[copilot memory] get_thread_messages note: %s", e)
        return []


def delete_thread(thread_id: str, db_url: str | None = None) -> None:
    """Delete all persisted state for a thread (checkpointer rows + activity)."""
    saver = get_checkpointer()
    if saver is not None:
        try:
            saver.delete_thread(thread_id)
        except Exception as e:
            logger.info("[copilot memory] delete_thread (saver) note: %s", e)
    try:
        with psycopg.connect(db_url or _db_url()) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM copilot_thread_activity WHERE thread_id = %s", (thread_id,))
            conn.commit()
    except Exception as e:
        logger.info("[copilot memory] delete_thread (activity) note: %s", e)
