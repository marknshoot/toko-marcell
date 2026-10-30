"""OpenTelemetry-aligned structured JSON logging.

Every line written by the application is a single JSON object with the nine
fields operators need to correlate a log line with a request and a trace:

``timestamp``, ``level``, ``service``, ``version``, ``message``, ``trace_id``,
``span_id``, ``request_id`` and ``environment``.

Values from ``logging_context`` are read at format time, so callers only need
to set the context vars once per request.  Arbitrary ``extra=`` attributes are
included verbatim (non-JSON values are serialised with ``str``).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime

from logging_context import ctx_request_id, ctx_span_id, ctx_trace_id

SERVICE_NAME = "toko-marcell-api"
SERVICE_VERSION = "1.0.0"

# LogRecord attributes that are part of the logging machinery, not user extras.
_STANDARD_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


class JSONLogFormatter(logging.Formatter):
    """Format one :class:`logging.LogRecord` as one line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": _utc_timestamp(record.created),
            # ``record.levelname.lower()`` gives stable machine-readable values
            # (info / warning / error / debug / critical).
            "level": record.levelname.lower(),
            "service": SERVICE_NAME,
            "version": SERVICE_VERSION,
            "message": record.getMessage(),
            "trace_id": ctx_trace_id.get(),
            "span_id": ctx_span_id.get(),
            "request_id": ctx_request_id.get(),
            "environment": os.environ.get(
                "ENVIRONMENT",
                "production" if not os.environ.get("DEBUG") else "development",
            ),
        }

        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and key not in payload:
                payload[key] = value

        if record.exc_info:
            payload.setdefault("exception", self.formatException(record.exc_info))

        return json.dumps(payload, default=str, ensure_ascii=False)


def _utc_timestamp(created: float) -> str:
    """RFC 3339 / ISO 8601 UTC timestamp with milliseconds and ``Z`` suffix."""
    return (
        datetime.fromtimestamp(created, tz=UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _log_level() -> int:
    name = os.environ.get("LOG_LEVEL")
    if not name:
        try:
            from config import get_settings

            name = get_settings().LOG_LEVEL
        except Exception:  # pragma: no cover - config is best-effort at import time
            name = "INFO"
    return getattr(logging, str(name).upper(), logging.INFO)


def setup_logging(level: int | str | None = None) -> None:
    """Route the root and uvicorn loggers through :class:`JSONLogFormatter`.

    Idempotent: repeated calls replace only the JSON handlers installed here,
    leaving pytest/caplog handlers on the root logger intact.
    """
    resolved = _log_level() if level is None else level
    if isinstance(resolved, str):
        resolved = getattr(logging, resolved.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONLogFormatter())
    handler._toko_json = True  # type: ignore[attr-defined]

    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, "_toko_json", False):
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(resolved)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = [handler]
        uvicorn_logger.propagate = False
        uvicorn_logger.setLevel(resolved)
