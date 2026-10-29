"""
In-memory sliding-window rate limiter for the copilot and write endpoints.

Two windows per client IP for copilot: per-minute and per-day, configurable via
env / Settings. Includes automatic bucket pruning so memory stays bounded.
"""

import os
import threading
import time

MAX_TRACKED_KEYS = 1000


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key, "")
    if raw:
        try:
            return int(raw)
        except (ValueError, TypeError):
            pass
    try:
        from config import get_settings
        val = getattr(get_settings(), key, None)
        if val is not None:
            return int(val)
    except Exception:
        pass
    return default


class SlidingWindowLimiter:
    """Thread-safe sliding-window rate limiter with bounded bucket cleanup.

    Stores timestamps per key; expired entries and empty keys are pruned on
    check so unique IP scans cannot leak memory over time.
    """

    def __init__(self, window_seconds: int, max_requests: int):
        self.window = window_seconds
        self.limit = max_requests
        self._buckets: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _sweep_expired_locked(self, cutoff: float) -> None:
        stale_keys = []
        for k, ts in self._buckets.items():
            ts[:] = [t for t in ts if t > cutoff]
            if not ts:
                stale_keys.append(k)
        for k in stale_keys:
            del self._buckets[k]

    def is_allowed(self, key: str) -> tuple[bool, int, int]:
        """Check if a request from `key` is allowed.

        Returns (allowed, remaining, retry_after_seconds).
        """
        now = time.time()
        cutoff = now - self.window
        with self._lock:
            if len(self._buckets) >= MAX_TRACKED_KEYS:
                self._sweep_expired_locked(cutoff)

            timestamps = self._buckets.get(key)
            if timestamps is not None:
                timestamps[:] = [t for t in timestamps if t > cutoff]
                if not timestamps:
                    del self._buckets[key]
                    timestamps = None

            if timestamps is not None and len(timestamps) >= self.limit:
                oldest = timestamps[0] if timestamps else now
                retry_after = int(oldest + self.window - now) + 1
                return False, 0, max(retry_after, 1)

            if self.limit <= 0:
                return False, 0, self.window

            if timestamps is None:
                timestamps = [now]
                self._buckets[key] = timestamps
            else:
                timestamps.append(now)
            remaining = self.limit - len(timestamps)
            return True, remaining, 0


# Global limiters — created lazily, reading env / Settings.
_minute_limiter: SlidingWindowLimiter | None = None
_day_limiter: SlidingWindowLimiter | None = None
_write_limiter: SlidingWindowLimiter | None = None
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


def _ensure_write_limiter() -> SlidingWindowLimiter:
    global _write_limiter
    if _write_limiter is not None:
        return _write_limiter
    with _init_lock:
        if _write_limiter is None:
            _write_limiter = SlidingWindowLimiter(
                window_seconds=60,
                max_requests=_env_int("WRITE_RATE_LIMIT_PER_MIN", 120),
            )
        return _write_limiter


def reset_limiters() -> None:
    """Reset global limiters (for testing)."""
    global _minute_limiter, _day_limiter, _write_limiter
    with _init_lock:
        _minute_limiter = None
        _day_limiter = None
        _write_limiter = None


def check_copilot_rate_limit(client_ip: str) -> tuple[bool, int, str]:
    """Check both per-minute and per-day limits for the copilot.

    Returns (allowed, retry_after_seconds, which_limit_hit).
    """
    minute_lim, day_lim = _ensure_limiters()

    allowed_min, _, retry_min = minute_lim.is_allowed(client_ip)
    if not allowed_min:
        return False, retry_min, "per_minute"

    allowed_day, _, retry_day = day_lim.is_allowed(client_ip)
    if not allowed_day:
        now = time.time()
        with minute_lim._lock:
            ts = minute_lim._buckets.get(client_ip)
            if ts and abs(ts[-1] - now) < 0.1:
                ts.pop()
                if not ts:
                    del minute_lim._buckets[client_ip]
        return False, retry_day, "per_day"

    return True, 0, ""


def check_write_rate_limit(client_ip: str) -> tuple[bool, int]:
    """Check per-minute write rate limit for /checkout/confirm and /events."""
    lim = _ensure_write_limiter()
    allowed, _, retry_after = lim.is_allowed(client_ip)
    return allowed, retry_after
