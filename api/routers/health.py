"""Health check endpoint."""

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}
