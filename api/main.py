"""
Toko Marcell API — thin application factory.

Creates the FastAPI app, wires middleware/CORS/lifespan, and includes routers.
All business logic lives in the ``routers/`` package, database access in ``db.py``,
and configuration in ``config.py``.

Startup: ``uvicorn main:app --host 0.0.0.0 --port 8000``
"""

import logging
from contextlib import asynccontextmanager

from config import get_settings
from db import close_pool, open_pool
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from migrate import run_migrations

logger = logging.getLogger(__name__)

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
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )
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
    allow_origins=["*"] if "*" in origins else origins,
    allow_origin_regex=r"^https:\/\/.*\.vercel\.app$",
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Include routers ──────────────────────────────────────────────────────────

from routers import catalog, checkout, copilot, events, health, recs, reviews, search, sizing  # noqa: E402

app.include_router(health.router)
app.include_router(catalog.router)
app.include_router(search.router)
app.include_router(sizing.router)
app.include_router(recs.router)
app.include_router(reviews.router)
app.include_router(events.router)
app.include_router(checkout.router)
app.include_router(copilot.router)
