"""急停 / 失败熔断 / 全局限流。"""

from __future__ import annotations

import time
from collections import deque
from typing import Any

from .contracts import CbqConfig


class SafetyGuard:
    def __init__(self, config: CbqConfig) -> None:
        self.config = config
        self._manual_stop = False
        self._failures: deque[float] = deque()
        self._last_fire_at = 0.0
        self._last_critical_at = 0.0

    def update(self, config: CbqConfig) -> None:
        self.config = config

    def pause(self) -> None:
        self._manual_stop = True

    def resume(self) -> None:
        self._manual_stop = False
        self._failures.clear()

    @property
    def stopped(self) -> bool:
        if self._manual_stop:
            return True
        if not self.config.safety_auto_stop_enabled:
            return False
        return len(self._failures) >= self.config.safety_failure_limit

    def status(self) -> str:
        if self._manual_stop:
            return "paused"
        if self.stopped:
            return "auto_stopped"
        return "ok"

    def record_failure(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._failures.append(now)
        window = self.config.safety_window_seconds
        while self._failures and now - self._failures[0] > window:
            self._failures.popleft()

    def mark_fired(self, now: float, *, critical: bool = False) -> None:
        self._last_fire_at = now
        if critical:
            self._last_critical_at = now

    def output_clock_checkpoint(self) -> tuple[float, float]:
        """Capture clocks before the arbiter and dispatcher attempt output."""
        return self._last_fire_at, self._last_critical_at

    def restore_output_clock(self, checkpoint: tuple[float, float]) -> None:
        """A suppressed/failed delivery must not consume the global output slot."""
        self._last_fire_at, self._last_critical_at = checkpoint

    def rate_limit_remaining(self, now: float) -> float:
        gap = self.config.global_rate_limit_seconds
        if gap <= 0:
            return 0.0
        return max(0.0, gap - (now - self._last_fire_at))

    def critical_cooldown_remaining(self, now: float) -> float:
        gap = self.config.critical_preempt_cooldown_seconds
        if gap <= 0:
            return 0.0
        return max(0.0, gap - (now - self._last_critical_at))

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status(),
            "stopped": self.stopped,
            "failure_count": len(self._failures),
            "manual_stop": self._manual_stop,
        }
