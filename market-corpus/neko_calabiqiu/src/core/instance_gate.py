"""Process-local gate preventing overlapping plugin runtimes during hot reload."""

from __future__ import annotations

import threading

_LOCK = threading.RLock()
_ACTIVE: dict[str, object] = {}


def try_claim(key: str, token: object) -> bool:
    normalized = str(key).casefold()
    with _LOCK:
        if normalized in _ACTIVE:
            return False
        _ACTIVE[normalized] = token
        return True


def release(key: str, token: object) -> None:
    normalized = str(key).casefold()
    with _LOCK:
        if _ACTIVE.get(normalized) is token:
            _ACTIVE.pop(normalized, None)
