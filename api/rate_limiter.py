"""
In-memory sliding-window rate limiter for the copilot and write endpoints.

Two windows per client IP for copilot: per-minute and per-day, configurable via
env / Settings. Includes automatic bucket pruning so memory stays bounded.
"""

import threading
import time

from config import get_settings

MAX_TRACKED_KEYS = 1000


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
        allowed, remaining, retry_after, _ = self.admit(key)
        return allowed, remaining, retry_after

    def admit(self, key: str) -> tuple[bool, int, int, float | None]:
        """Like ``is_allowed``, plus the timestamp recorded for an admitted request.

        The timestamp lets a caller undo *its own* admission when a second
        limiter rejects the same request (see ``release``), without reading or
        locking this limiter's internals.
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
                return False, 0, max(retry_after, 1), None

            if self.limit <= 0:
                return False, 0, self.window, None

            if timestamps is None:
                timestamps = [now]
                self._buckets[key] = timestamps
            else:
                timestamps.append(now)
            remaining = self.limit - len(timestamps)
            return True, remaining, 0, now

    def release(self, key: str, admitted_at: float | None) -> None:
        """Undo one admission made at ``admitted_at`` (no-op if it is already gone)."""
        if admitted_at is None:
            return
        with self._lock:
            timestamps = self._buckets.get(key)
            if not timestamps or timestamps[-1] != admitted_at:
                return
            timestamps.pop()
            if not timestamps:
                del self._buckets[key]


# Global limiters — created lazily, reading env / Settings.
_minute_limiter: SlidingWindowLimiter | None = None
_day_limiter: SlidingWindowLimiter | None = None
_write_limiter: SlidingWindowLimiter | None = None
_init_lock = threading.Lock()


def _ensure_limiters() -> tuple[SlidingWindowLimiter, SlidingWindowLimiter]:
    global _minute_limiter, _day_limiter
    if _minute_limiter is not None and _day_limiter is not None:
        return _minute_limiter, _day_limiter
    settings = get_settings()
    with _init_lock:
        if _minute_limiter is None:
            _minute_limiter = SlidingWindowLimiter(
                window_seconds=60,
                max_requests=settings.COPILOT_RATE_LIMIT_PER_MIN,
            )
        if _day_limiter is None:
            _day_limiter = SlidingWindowLimiter(
                window_seconds=86400,
                max_requests=settings.COPILOT_RATE_LIMIT_PER_DAY,
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
                max_requests=get_settings().WRITE_RATE_LIMIT_PER_MIN,
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

    allowed_minute, _, retry_minute, admitted_at = minute_lim.admit(client_ip)
    if not allowed_minute:
        return False, retry_minute, "per_minute"

    allowed_day, _, retry_day = day_lim.is_allowed(client_ip)
    if not allowed_day:
        # The day window rejected the request: give back the minute slot it used.
        minute_lim.release(client_ip, admitted_at)
        return False, retry_day, "per_day"

    return True, 0, ""


def check_write_rate_limit(client_ip: str) -> tuple[bool, int]:
    """Check per-minute write rate limit for /checkout/confirm and /events."""
    lim = _ensure_write_limiter()
    allowed, _, retry_after = lim.is_allowed(client_ip)
    return allowed, retry_after
