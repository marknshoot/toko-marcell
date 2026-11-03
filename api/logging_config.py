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
from typing import Any

from logging_context import ctx_request_id, ctx_span_id, ctx_trace_id

SERVICE_NAME = "toko-marcell-api"
# Build identity: Render injects RENDER_GIT_COMMIT; GIT_SHA covers local Docker
# or CI builds.  "dev" marks a process that was not built from a known commit.
SERVICE_VERSION = (os.environ.get("RENDER_GIT_COMMIT") or os.environ.get("GIT_SHA") or "dev")[:12]

# Values longer than this are cut so a single oversized field cannot bloat a line.
_MAX_VALUE_CHARS = 2048
# Any key containing one of these fragments (case-insensitive) is masked, so
# ``access_token``, ``refresh_token`` and ``X-Api-Key`` are covered as well as ``token``.
_REDACT_KEY_FRAGMENTS = ("authorization", "password", "token", "secret", "api_key", "apikey", "cookie")
_REDACTED = "***"
_TRUNCATED_SUFFIX = "…[truncated]"


def _truncate(value: str) -> str:
    if len(value) > _MAX_VALUE_CHARS:
        return value[:_MAX_VALUE_CHARS] + _TRUNCATED_SUFFIX
    return value


def _safe(key: str, value: Any) -> Any:
    """Mask credential-like keys and cap string length for one log field."""
    lowered = key.lower()
    if any(fragment in lowered for fragment in _REDACT_KEY_FRAGMENTS):
        return _REDACTED
    if isinstance(value, str):
        return _truncate(value)
    return value

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
            "message": _truncate(record.getMessage()),
            "trace_id": ctx_trace_id.get(),
            "span_id": ctx_span_id.get(),
            "request_id": ctx_request_id.get(),
            # Defaults to development so a laptop never claims to be production;
            # render.yaml sets ENVIRONMENT=production explicitly.
            "environment": os.environ.get("ENVIRONMENT") or "development",
        }

        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and key not in payload:
                payload[key] = _safe(key, value)

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

    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = [handler]
        uvicorn_logger.propagate = False
        uvicorn_logger.setLevel(resolved)

    # The ``request.completed`` line from the request middleware already carries
    # method, route, status and duration with trace correlation.  Uvicorn's own
    # access line would be a second, uncorrelated line per request.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers = []
    access_logger.propagate = False
    access_logger.disabled = True
