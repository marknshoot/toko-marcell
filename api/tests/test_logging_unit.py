"""Unit tests for the OpenTelemetry-aligned JSON logger and request context."""

import io
import json
import logging
import re
import sys
from contextlib import contextmanager
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

from fastapi.testclient import TestClient
from logging_config import JSONLogFormatter, setup_logging
from logging_context import ctx_request_id, ctx_span_id, ctx_trace_id
from main import app
from routers import health as health_module

REQUIRED_FIELDS = {
    "timestamp",
    "level",
    "service",
    "version",
    "message",
    "trace_id",
    "span_id",
    "request_id",
    "environment",
}

TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


@contextmanager
def _fake_db_conn():
    """Stand-in for ``routers.health.get_conn``.

    The middleware assertions are about request/trace context, not the database,
    and the suite must pass on a machine where local Postgres is not running.
    """

    class _Conn:
        def execute(self, query, *args, **kwargs):
            return None

    yield _Conn()


def _formatted(record: logging.LogRecord) -> dict:
    return json.loads(JSONLogFormatter().format(record))


def _record(message: str = "hello", **extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="toko.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_formatter_emits_all_nine_standard_fields(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    payload = _formatted(_record("hello %s", user_id=7))

    assert REQUIRED_FIELDS <= payload.keys()
    assert payload["level"] == "info"
    assert payload["service"] == "toko-marcell-api"
    assert payload["version"] == "1.0.0"
    assert payload["message"] == "hello %s"
    assert payload["environment"] == "test"
    assert TIMESTAMP_RE.match(payload["timestamp"])
    assert payload["user_id"] == 7


def test_formatter_reads_contextvars(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    request_token = ctx_request_id.set("req_12345678")
    trace_token = ctx_trace_id.set("a" * 32)
    span_token = ctx_span_id.set("b" * 16)
    try:
        payload = _formatted(_record("context"))
    finally:
        ctx_span_id.reset(span_token)
        ctx_trace_id.reset(trace_token)
        ctx_request_id.reset(request_token)

    assert payload["request_id"] == "req_12345678"
    assert payload["trace_id"] == "a" * 32
    assert payload["span_id"] == "b" * 16


def test_environment_falls_back_to_debug_flag(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("DEBUG", "1")
    assert _formatted(_record())["environment"] == "development"

    monkeypatch.delenv("DEBUG")
    assert _formatted(_record())["environment"] == "production"


def test_setup_logging_configures_root_and_uvicorn():
    setup_logging("INFO")
    root = logging.getLogger()
    assert any(
        isinstance(handler.formatter, JSONLogFormatter) for handler in root.handlers
    )
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        assert len(logger.handlers) == 1
        assert isinstance(logger.handlers[0].formatter, JSONLogFormatter)
        assert logger.propagate is False


def test_middleware_sets_request_and_trace_context(caplog, monkeypatch):
    # ``/health`` must answer 200 without a live database, so the probe is faked.
    monkeypatch.setattr(health_module, "get_conn", _fake_db_conn)

    client = TestClient(app)
    trace_id = "c" * 32
    span_id = "d" * 16

    caplog.clear()
    with caplog.at_level(logging.INFO):
        response = client.get(
            "/health",
            headers={
                "X-Request-ID": "req_deadbeef",
                "traceparent": f"00-{trace_id}-{span_id}-01",
            },
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req_deadbeef"

    records = [r for r in caplog.records if r.getMessage() == "request.completed"]
    assert records
    access = records[-1]
    assert access.request_id == "req_deadbeef"
    assert access.trace_id == trace_id
    assert access.span_id == span_id
    assert access.method == "GET"
    assert access.path == "/health"
    assert access.status_code == 200
    assert isinstance(access.duration_ms, int | float)


def test_middleware_generates_request_and_trace_ids():
    client = TestClient(app)
    response = client.get("/health")

    request_id = response.headers["X-Request-ID"]
    assert re.fullmatch(r"req_[0-9a-f]{8}", request_id)
    assert ctx_request_id.get() is None
    assert ctx_trace_id.get() is None
    assert ctx_span_id.get() is None


def test_formatter_output_is_one_line():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONLogFormatter())
    logger = logging.getLogger("toko.test.one-line")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info("single line", extra={"status_code": 200})
    lines = [line for line in stream.getvalue().splitlines() if line]
    assert len(lines) == 1
    assert json.loads(lines[0])["status_code"] == 200
