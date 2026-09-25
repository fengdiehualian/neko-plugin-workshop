"""安全门：手动急停/自动急停/限流时钟/失败计数。"""

from __future__ import annotations

import time
from typing import Any


class SafetyGuard:
    """输出安全阀：防刷屏、防故障风暴。"""

    def __init__(self, config: object) -> None:
        self.config = config
        self.manual_paused = False
        self.auto_paused = False
        self._last_output_at = 0.0
        self._last_critical_at = 0.0
        self._failures: list[float] = []

    def pause(self) -> None:
        self.manual_paused = True

    def resume(self) -> None:
        self.manual_paused = False
        self.auto_paused = False
        self._failures.clear()
        self._last_output_at = 0.0
        self._last_critical_at = 0.0

    @property
    def stopped(self) -> bool:
        return self.manual_paused or self.auto_paused

    def status(self) -> str:
        if self.auto_paused:
            return "tripped"
        if self.manual_paused:
            return "paused"
        return "running"

    def rate_limit_remaining(self, now: float | None = None) -> float:
        """非抢占输出：距下次允许还剩多少秒。"""
        if getattr(self.config, "global_rate_limit_s", 12.0) <= 0:
            return 0.0
        cur = time.time() if now is None else now
        limit = getattr(self.config, "global_rate_limit_s", 12.0)
        remaining = limit - (cur - self._last_output_at)
        return remaining if remaining > 0 else 0.0

    def critical_cooldown_remaining(self, now: float | None = None) -> float:
        """抢占输出之间的最小间隔。"""
        if getattr(self.config, "critical_cooldown_s", 5.0) <= 0:
            return 0.0
        cur = time.time() if now is None else now
        remaining = getattr(self.config, "critical_cooldown_s", 5.0) - (cur - self._last_critical_at)
        return remaining if remaining > 0 else 0.0

    def mark_output(self, *, critical: bool, now: float | None = None) -> None:
        cur = time.time() if now is None else now
        self._last_output_at = cur
        if critical:
            self._last_critical_at = cur

    def record_failure(self, now: float | None = None) -> None:
        """记录一次输出失败；窗口内达上限自动急停。"""
        cur = time.time() if now is None else now
        self._failures.append(cur)
        window = getattr(self.config, "safety_window_s", 60.0)
        self._failures = [t for t in self._failures if cur - t <= window]
        limit = getattr(self.config, "safety_failure_limit", 5)
        if getattr(self.config, "safety_auto_stop", True) and len(self._failures) >= limit:
            self.auto_paused = True

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status(),
            "manual_paused": self.manual_paused,
            "auto_paused": self.auto_paused,
            "failures": len(self._failures),
            "rate_limit_remaining": round(self.rate_limit_remaining(), 1),
        }
