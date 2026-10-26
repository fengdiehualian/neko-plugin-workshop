from __future__ import annotations

import time as _real_time


class _ScopedTime:
    def __init__(self, **overrides):
        self._overrides = overrides

    def __getattr__(self, name):
        try:
            return self._overrides[name]
        except KeyError:
            return getattr(_real_time, name)


def patch_module_clock(monkeypatch, module, **overrides) -> None:
    """Patch one module's clock without mutating the process-wide time module."""

    monkeypatch.setattr(module, "time", _ScopedTime(**overrides))
