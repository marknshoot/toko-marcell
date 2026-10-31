"""Unit tests for the anti-creep heap trimming helper (api/memory_trim.py).

``trim_memory()`` sits in the teardown path of every copilot turn and search
request, so the contract under test is narrow and absolute: it always returns a
bool, always collects garbage, and never raises — including on platforms where
``malloc_trim`` does not exist at all.
"""

import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

import memory_trim


class _FakeMallocTrim:
    """Stand-in for the resolved ``malloc_trim`` ctypes symbol."""

    def __init__(self, result: int = 1, exc: Exception | None = None):
        self.result = result
        self.exc = exc
        self.calls: list[int] = []

    def __call__(self, pad: int) -> int:
        self.calls.append(pad)
        if self.exc is not None:
            raise self.exc
        return self.result


def test_trim_memory_returns_bool_and_does_not_raise():
    result = memory_trim.trim_memory()

    assert isinstance(result, bool)


def test_trim_memory_calls_gc_collect(monkeypatch):
    collected: list[int] = []
    monkeypatch.setattr(memory_trim.gc, "collect", lambda: collected.append(1))

    memory_trim.trim_memory()

    assert collected == [1]


def test_trim_memory_returns_true_when_malloc_trim_succeeds(monkeypatch):
    fake = _FakeMallocTrim(result=1)
    monkeypatch.setattr(memory_trim, "_MALLOC_TRIM", fake)

    assert memory_trim.trim_memory() is True
    assert fake.calls == [0]


def test_trim_memory_returns_false_when_malloc_trim_reports_no_release(monkeypatch):
    fake = _FakeMallocTrim(result=0)
    monkeypatch.setattr(memory_trim, "_MALLOC_TRIM", fake)

    assert memory_trim.trim_memory() is False
    assert fake.calls == [0]


def test_trim_memory_fallback_without_malloc_trim(monkeypatch):
    """A libc without the glibc extension (macOS, musl) still collects and returns False."""
    collected: list[int] = []
    monkeypatch.setattr(memory_trim, "_MALLOC_TRIM", None)
    monkeypatch.setattr(memory_trim.gc, "collect", lambda: collected.append(1))

    assert memory_trim.trim_memory() is False
    assert collected == [1]


def test_trim_memory_swallows_malloc_trim_errors(monkeypatch):
    fake = _FakeMallocTrim(exc=RuntimeError("corrupt heap"))
    monkeypatch.setattr(memory_trim, "_MALLOC_TRIM", fake)

    assert memory_trim.trim_memory() is False
    assert fake.calls == [0]


def test_trim_memory_swallows_gc_errors(monkeypatch):
    fake = _FakeMallocTrim(result=1)
    monkeypatch.setattr(memory_trim, "_MALLOC_TRIM", fake)

    def _boom():
        raise RuntimeError("gc is wedged")

    monkeypatch.setattr(memory_trim.gc, "collect", _boom)

    assert memory_trim.trim_memory() is True
    assert fake.calls == [0]
