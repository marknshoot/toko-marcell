"""Health check endpoint.

Reports liveness of the API itself plus the database.  Render (API host) and
Neon (Postgres host) are separate platforms: when Neon has scaled its compute
to zero the API is perfectly alive while the database needs a moment to wake
up, so the database probe is reported separately instead of failing the whole
endpoint.
"""

import logging

from db import get_conn
from fastapi import APIRouter, Response, status

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
def health(response: Response):
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1;")
    except Exception as e:
        logger.warning("[health] database check failed: %s", e)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "waking",
            "services": {
                "api": "ok",
                "database": "waking",
            },
            "error": "Database is waking up or unreachable",
        }

    return {
        "status": "ok",
        "services": {
            "api": "ok",
            "database": "ok",
        },
    }
