"""Per-request logging context (W3C TraceContext-friendly).

The values live in :mod:`contextvars` so every log record emitted while a
request is being handled can be correlated without threading an explicit
request object through the call stack.
"""

import contextvars

ctx_request_id = contextvars.ContextVar("request_id", default=None)
ctx_trace_id = contextvars.ContextVar("trace_id", default=None)
ctx_span_id = contextvars.ContextVar("span_id", default=None)
