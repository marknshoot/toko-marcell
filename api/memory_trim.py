"""Return freed heap pages to the OS so RSS does not creep on a 512 MB instance.

glibc keeps freed ``malloc`` arenas inside the process, so a long-lived async API
that churns through JSON payloads and ONNX buffers grows monotonically even when
every object has been collected.  ``malloc_trim(0)`` walks the arenas and hands
fully-free pages back to the kernel, which keeps the footprint flat between
turns instead of drifting into Render's OOM killer.

The helper is deliberately defensive.  Import happens on every host, including
platforms where the symbol does not exist (macOS, musl, Windows); in that case
:func:`trim_memory` degrades to a garbage collection and reports ``False``
rather than raising into a response path.
"""

import ctypes
import ctypes.util
import gc
import logging

logger = logging.getLogger(__name__)

# Linux x86/arm glibc and musl images name the library differently, and a
# container built from scratch can defeat find_library(); candidates are tried
# in order until one loads.
_LIBC_CANDIDATES = ("libc.so.6", "libc.so", "libc.dylib", "libc.so.7")


def _load_libc() -> ctypes.CDLL | None:
    """Load the platform C library, or ``None`` when no candidate resolves."""
    candidates = []
    soname = ctypes.util.find_library("c")
    if soname:
        candidates.append(soname)
    candidates.extend(_LIBC_CANDIDATES)

    for name in candidates:
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue

    # Last resort: the interpreter is already linked against the process C
    # library, so the global symbol namespace exposes it even when the path
    # lookup above fails.
    try:
        return ctypes.CDLL(None)
    except OSError:
        return None


def _resolve_malloc_trim() -> tuple[ctypes.CDLL | None, object | None]:
    """Return ``(libc, malloc_trim)``; the function is ``None`` when absent.

    ``libc`` is returned alongside the symbol because ``ctypes`` does not own
    the loaded library: keeping a reference alive guarantees the resolved
    address stays valid for the lifetime of the process.
    """
    libc = _load_libc()
    if libc is None:
        return None, None

    # Not every libc implements the glibc extension, so this is an attribute
    # probe rather than a guaranteed symbol.
    func = getattr(libc, "malloc_trim", None)
    if func is None:
        return libc, None

    func.restype = ctypes.c_int
    func.argtypes = [ctypes.c_size_t]
    return libc, func


_LIBC, _MALLOC_TRIM = _resolve_malloc_trim()


def trim_memory() -> bool:
    """Collect garbage, then hand fully-free heap pages back to the OS.

    Returns ``True`` only when ``malloc_trim(0)`` was called and reported
    success, and ``False`` when the platform has no ``malloc_trim`` or the call
    failed.  This runs in request and stream teardown, so it never raises: a
    memory optimization must not mask the response a shopper is waiting for.
    """
    try:
        gc.collect()
    except Exception:  # noqa: BLE001 — teardown must never raise
        logger.debug("[memory] gc.collect() failed", exc_info=True)

    if _MALLOC_TRIM is None:
        return False

    try:
        return bool(_MALLOC_TRIM(0))
    except Exception:  # noqa: BLE001 — teardown must never raise
        logger.debug("[memory] malloc_trim(0) failed", exc_info=True)
        return False
