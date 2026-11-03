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
from logging_config import SERVICE_VERSION, JSONLogFormatter, setup_logging
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
    assert payload["version"] == SERVICE_VERSION
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


def test_environment_defaults_to_development(monkeypatch):
    # The old default reported "production" whenever ENVIRONMENT was unset, so a
    # laptop claimed to be production. Production now opts in via render.yaml.
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert _formatted(_record())["environment"] == "development"

    monkeypatch.setenv("ENVIRONMENT", "production")
    assert _formatted(_record())["environment"] == "production"


def test_setup_logging_configures_root_and_uvicorn():
    setup_logging("INFO")
    root = logging.getLogger()
    assert any(
        isinstance(handler.formatter, JSONLogFormatter) for handler in root.handlers
    )
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        assert len(logger.handlers) == 1
        assert isinstance(logger.handlers[0].formatter, JSONLogFormatter)
        assert logger.propagate is False
    # request.completed replaces uvicorn's uncorrelated access line.
    assert logging.getLogger("uvicorn.access").disabled is True


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
    assert access.route == "/health"
    assert access.kind == "buffered"
    assert access.status_code == 200
    assert isinstance(access.duration_ms, int | float)


def _access_records(caplog):
    return [r for r in caplog.records if r.getMessage() == "request.completed"]


def test_access_log_uses_route_template_not_order_token(caplog):
    # /orders/{token}: the token is the only credential for reading an order.
    from routers import checkout as checkout_module

    order_credential = "tk_abc123SECRET"  # noqa: S105

    @contextmanager
    def _no_order_db():
        class _Cur:
            def execute(self, *a, **k):
                return None

            def fetchone(self):
                return None

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class _Conn:
            def cursor(self):
                return _Cur()

        yield _Conn()

    original = checkout_module.get_conn
    checkout_module.get_conn = _no_order_db
    try:
        caplog.clear()
        with caplog.at_level(logging.INFO):
            response = TestClient(app).get(f"/orders/{order_credential}")
    finally:
        checkout_module.get_conn = original

    assert response.status_code == 404
    access = _access_records(caplog)[-1]
    assert access.route == "/orders/{token}"
    assert not hasattr(access, "path")
    rendered = JSONLogFormatter().format(access)
    assert order_credential not in rendered


def test_access_log_includes_raw_path_only_at_debug(caplog, monkeypatch):
    monkeypatch.setattr(health_module, "get_conn", _fake_db_conn)
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="main"):
        TestClient(app).get("/health")
    assert _access_records(caplog)[-1].path == "/health"


def test_unmatched_route_is_labelled(caplog):
    caplog.clear()
    with caplog.at_level(logging.INFO):
        response = TestClient(app).get("/definitely/not/a/route/tk_secret")
    assert response.status_code == 404
    access = _access_records(caplog)[-1]
    assert access.route == "unmatched"
    assert "tk_secret" not in JSONLogFormatter().format(access)


def test_event_stream_responses_are_labelled_stream(caplog):
    from fastapi.responses import StreamingResponse

    async def _events():
        yield "data: hi\n\n"

    @app.get("/__test_sse")
    async def _sse():
        return StreamingResponse(_events(), media_type="text/event-stream")

    try:
        caplog.clear()
        with caplog.at_level(logging.INFO):
            TestClient(app).get("/__test_sse")
    finally:
        app.router.routes = [r for r in app.router.routes if getattr(r, "path", "") != "/__test_sse"]

    access = _access_records(caplog)[-1]
    assert access.kind == "stream"
    assert access.route == "/__test_sse"


def test_unhandled_error_logs_one_record_with_traceback(caplog):
    @app.get("/__test_boom")
    async def _boom():
        raise RuntimeError("kaboom")

    try:
        caplog.clear()
        with caplog.at_level(logging.INFO):
            response = TestClient(app, raise_server_exceptions=False).get("/__test_boom")
    finally:
        app.router.routes = [r for r in app.router.routes if getattr(r, "path", "") != "/__test_boom"]

    assert response.status_code == 500
    records = _access_records(caplog)
    assert len(records) == 1
    assert records[0].status_code == 500
    assert records[0].levelno == logging.ERROR
    assert "kaboom" in JSONLogFormatter().format(records[0])


def test_successful_request_has_no_exception_field(caplog, monkeypatch):
    monkeypatch.setattr(health_module, "get_conn", _fake_db_conn)
    caplog.clear()
    with caplog.at_level(logging.INFO):
        TestClient(app).get("/health")
    payload = json.loads(JSONLogFormatter().format(_access_records(caplog)[-1]))
    assert "exception" not in payload


def test_cors_exposes_request_id_header():
    response = TestClient(app).get(
        "/__nope", headers={"Origin": "https://toko-marcell.vercel.app"}
    )
    assert "x-request-id" in response.headers.get("access-control-expose-headers", "").lower()


def test_credential_like_extras_are_redacted():
    payload = _formatted(
        _record(
            "login",
            password="hunter2",
            access_token="eyJabc",
            refresh_token="r-123",
            Authorization="Bearer x",
            user_id=7,
        )
    )
    for key in ("password", "access_token", "refresh_token", "Authorization"):
        assert payload[key] == "***"
    assert payload["user_id"] == 7
    assert "hunter2" not in json.dumps(payload)


def test_long_values_are_truncated_on_one_line():
    big = "x" * 10_000
    line = JSONLogFormatter().format(_record(big, note=big))
    assert "\n" not in line
    payload = json.loads(line)
    assert payload["message"].endswith("…[truncated]")
    assert payload["note"].endswith("…[truncated]")
    assert len(payload["message"]) < 2100
    assert len(payload["note"]) < 2100


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
