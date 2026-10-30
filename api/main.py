"""
Toko Marcell API — thin application factory.

Creates the FastAPI app, wires middleware/CORS/lifespan, and includes routers.
All business logic lives in the ``routers/`` package, database access in ``db.py``,
and configuration in ``config.py``.

Startup: ``uvicorn main:app --host 0.0.0.0 --port 8000``
"""

import logging
import secrets
import time
from contextlib import asynccontextmanager

from config import get_settings
from db import close_pool, open_pool
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from logging_config import setup_logging
from logging_context import ctx_request_id, ctx_span_id, ctx_trace_id
from migrate import run_migrations

logger = logging.getLogger(__name__)

# Structured logging is installed at import time so startup, migration and
# request logs share the same JSON shape.  ``setup_logging`` is idempotent.
setup_logging()

# ── Database migration ───────────────────────────────────────────────────────

def init_db():
    """Migrate the schema to head, then seed the RAG knowledge base if empty."""
    settings = get_settings()
    run_migrations(settings.DATABASE_URL)

    try:
        from knowledge_seed import seed_knowledge
        seed_knowledge(settings.DATABASE_URL)
    except Exception as e:
        logger.warning("[init_db] knowledge seed error: %s", e)


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)
    init_db()
    open_pool()

    # Copilot thread TTL cleanup: once on startup, then daily in the background.
    cleanup_task = None
    try:
        import asyncio

        from copilot_memory import cleanup_expired_threads, ensure_activity_table

        ensure_activity_table()
        await asyncio.to_thread(cleanup_expired_threads)

        async def _periodic_cleanup():
            while True:
                await asyncio.sleep(24 * 60 * 60)
                try:
                    await asyncio.to_thread(cleanup_expired_threads)
                except Exception as e:
                    logger.warning("[copilot] periodic TTL cleanup error: %s", e)

        cleanup_task = asyncio.create_task(_periodic_cleanup())
    except Exception as e:
        logger.warning("[copilot] TTL cleanup not started: %s", e)

    yield

    if cleanup_task is not None:
        cleanup_task.cancel()
    close_pool()


# ── App creation ─────────────────────────────────────────────────────────────

app = FastAPI(title="Toko Marcell API", lifespan=lifespan)

settings = get_settings()
cors_origins_raw = settings.CORS_ORIGINS
origins = [
    origin.strip()
    for origin in cors_origins_raw.split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    # Credentialed requests (the HttpOnly refresh cookie) may not use a literal
    # "*" origin. Mirror the caller's Origin instead when CORS is wide open.
    allow_origins=[] if "*" in origins else origins,
    allow_origin_regex=(
        r".*" if "*" in origins else r"^https:\/\/.*\.vercel\.app$"
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request context / access logging ────────────────────────────────────────

def _trace_context(request: Request) -> tuple[str, str]:
    """Return ``(trace_id, span_id)`` from a W3C ``traceparent`` or a new pair.

    Valid format: ``00-<32 hex>-<16 hex>-01``.  Anything malformed is replaced
    with a fresh random context rather than rejecting the request.
    """
    traceparent = request.headers.get("traceparent", "")
    parts = traceparent.split("-")
    if len(parts) == 4:
        version, trace_id, span_id, _flags = parts
        if (
            len(version) == 2
            and len(trace_id) == 32
            and len(span_id) == 16
            and _is_hex(trace_id)
            and _is_hex(span_id)
        ):
            return trace_id.lower(), span_id.lower()
    return secrets.token_hex(16), secrets.token_hex(8)


def _is_hex(value: str) -> bool:
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    """Bind request/trace context, log the access line, and echo X-Request-ID."""
    request_id = request.headers.get("X-Request-ID") or f"req_{secrets.token_hex(4)}"
    trace_id, span_id = _trace_context(request)

    request_token = ctx_request_id.set(request_id)
    trace_token = ctx_trace_id.set(trace_id)
    span_token = ctx_span_id.set(span_id)
    started = time.perf_counter()
    status_code = 500

    try:
        response = await call_next(request)
        status_code = response.status_code
    finally:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.info(
            "request.completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "request_id": request_id,
                "trace_id": trace_id,
                "span_id": span_id,
            },
        )
        ctx_span_id.reset(span_token)
        ctx_trace_id.reset(trace_token)
        ctx_request_id.reset(request_token)

    response.headers["X-Request-ID"] = request_id
    return response

# ── Include routers ──────────────────────────────────────────────────────────

from routers import admin, auth, catalog, checkout, copilot, events, health, recs, reviews, search, sizing  # noqa: E402

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(catalog.router)
app.include_router(search.router)
app.include_router(sizing.router)
app.include_router(recs.router)
app.include_router(reviews.router)
app.include_router(events.router)
app.include_router(checkout.router)
app.include_router(copilot.router)
