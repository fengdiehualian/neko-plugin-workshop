from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any


def should_report_production_completion(enabled: bool, status: str) -> bool:
    """Only completed production edges are muted by the default quiet mode."""
    return bool(enabled) or str(status) != "completed"


class ReportPolicy:
    """Single configurable gate for every proactive battlefield report."""

    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._enabled = True
        self._allowed_kinds: set[str] | None = None
        self._minimum_severity = 0
        self._cooldown_seconds = 8.0
        self._max_per_minute: int | None = 12
        self._last_by_key: dict[str, float] = {}
        self._recent: deque[float] = deque()
        self._lock = threading.RLock()

    def configure(
        self, *, enabled: bool | None = None,
        allowed_kinds: list[str] | None = None,
        minimum_severity: int | None = None,
        cooldown_seconds: float | None = None,
        max_per_minute: int | None | object = ...,
    ) -> dict[str, Any]:
        with self._lock:
            if enabled is not None:
                self._enabled = bool(enabled)
            if allowed_kinds is not None:
                self._allowed_kinds = {
                    str(item).strip() for item in allowed_kinds if str(item).strip()
                } or set()
            if minimum_severity is not None:
                self._minimum_severity = int(minimum_severity)
            if cooldown_seconds is not None:
                self._cooldown_seconds = max(0.0, float(cooldown_seconds))
            if max_per_minute is not ...:
                self._max_per_minute = (
                    None if max_per_minute is None else max(1, int(max_per_minute))
                )
            return self.snapshot()

    def allow(self, kind: str, *, key: str | None = None, severity: int = 0) -> bool:
        now = self._clock()
        kind = str(kind)
        dedupe_key = str(key or kind)
        with self._lock:
            if not self._enabled:
                return False
            if self._allowed_kinds is not None and kind not in self._allowed_kinds:
                return False
            if int(severity) < self._minimum_severity:
                return False
            previous = self._last_by_key.get(dedupe_key)
            if previous is not None and now - previous < self._cooldown_seconds:
                return False
            while self._recent and now - self._recent[0] >= 60.0:
                self._recent.popleft()
            if self._max_per_minute is not None and len(self._recent) >= self._max_per_minute:
                return False
            self._last_by_key[dedupe_key] = now
            self._recent.append(now)
            return True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self._enabled,
                "allowed_kinds": (
                    sorted(self._allowed_kinds)
                    if self._allowed_kinds is not None else None
                ),
                "minimum_severity": self._minimum_severity,
                "cooldown_seconds": self._cooldown_seconds,
                "max_per_minute": self._max_per_minute,
                "reports_in_current_minute": len(self._recent),
            }
