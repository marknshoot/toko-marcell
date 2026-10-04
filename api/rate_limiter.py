"""
In-memory sliding-window rate limiter for the copilot endpoint.

Two windows per client IP: per-minute and per-day, configurable via env.
No external dependencies — uses only stdlib collections and time.
"""

import os
import threading
import time
from collections import defaultdict


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key, "")
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


class SlidingWindowLimiter:
    """Thread-safe sliding-window rate limiter.

    Stores timestamps per key; expired entries are pruned on each check.
    Suitable for a single-process deployment (Render free tier = 1 worker).
    """

    def __init__(self, window_seconds: int, max_requests: int):
        self.window = window_seconds
        self.limit = max_requests
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def is_allowed(self, key: str) -> tuple[bool, int, int]:
        """Check if a request from `key` is allowed.

        Returns (allowed, remaining, retry_after_seconds).
        """
        now = time.time()
        cutoff = now - self.window
        with self._lock:
            timestamps = self._buckets[key]
            # Prune expired
            timestamps[:] = [t for t in timestamps if t > cutoff]
            if len(timestamps) >= self.limit:
                oldest = timestamps[0] if timestamps else now
                retry_after = int(oldest + self.window - now) + 1
                return False, 0, max(retry_after, 1)
            timestamps.append(now)
            remaining = self.limit - len(timestamps)
            return True, remaining, 0


# Global limiters — created once at import time, reading env.
_minute_limiter: SlidingWindowLimiter | None = None
_day_limiter: SlidingWindowLimiter | None = None
_init_lock = threading.Lock()


def _ensure_limiters() -> tuple[SlidingWindowLimiter, SlidingWindowLimiter]:
    global _minute_limiter, _day_limiter
    if _minute_limiter is not None and _day_limiter is not None:
        return _minute_limiter, _day_limiter
    with _init_lock:
        if _minute_limiter is None:
            _minute_limiter = SlidingWindowLimiter(
                window_seconds=60,
                max_requests=_env_int("COPILOT_RATE_LIMIT_PER_MIN", 10),
            )
        if _day_limiter is None:
            _day_limiter = SlidingWindowLimiter(
                window_seconds=86400,
                max_requests=_env_int("COPILOT_RATE_LIMIT_PER_DAY", 100),
            )
        return _minute_limiter, _day_limiter


def reset_limiters() -> None:
    """Reset global limiters (for testing)."""
    global _minute_limiter, _day_limiter
    with _init_lock:
        _minute_limiter = None
        _day_limiter = None


def check_copilot_rate_limit(client_ip: str) -> tuple[bool, int, str]:
    """Check both per-minute and per-day limits for the copilot.

    Returns (allowed, retry_after_seconds, which_limit_hit).
    """
    minute_lim, day_lim = _ensure_limiters()

    allowed_min, remaining_min, retry_min = minute_lim.is_allowed(client_ip)
    if not allowed_min:
        return False, retry_min, "per_minute"

    allowed_day, remaining_day, retry_day = day_lim.is_allowed(client_ip)
    if not allowed_day:
        # Roll back the minute counter since we're rejecting
        now = time.time()
        with minute_lim._lock:
            ts = minute_lim._buckets[client_ip]
            if ts and abs(ts[-1] - now) < 0.1:
                ts.pop()
        return False, retry_day, "per_day"

    return True, 0, ""
