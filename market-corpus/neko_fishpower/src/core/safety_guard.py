"""安全门：全局限流 + 抢占冷却 + 暂停/恢复。"""

from __future__ import annotations

import time
from typing import Any, Dict


class SafetyGuard:
    """输出保护：限流窗口 + 抢占冷却 + 手动暂停。"""

    def __init__(self, config: object) -> None:
        self.config = config
        self._rate_window: list[float] = []
        self._last_critical_at = 0.0
        self._paused = False
        self._failures = 0

    def mark_output(self, critical: bool = False, now: float | None = None) -> None:
        now = now or time.time()
        self._rate_window.append(now)
        # 只保留 safety_window_s 内的记录
        window = self.config.safety_window_s
        self._rate_window = [t for t in self._rate_window if now - t < window]
        if critical:
            self._last_critical_at = now

    def rate_limit_remaining(self, now: float | None = None) -> float:
        now = now or time.time()
        window = self.config.safety_window_s
        limit = self.config.global_rate_limit_s
        recent = [t for t in self._rate_window if now - t < window]
        if len(recent) >= 2:
            # 窗口内最后一条距今 < limit → 剩余等待
            last = max(recent)
            remain = limit - (now - last)
            if remain > 0:
                return remain
        return 0.0

    def critical_cooldown_remaining(self, now: float | None = None) -> float:
        now = now or time.time()
        cd = self.config.critical_cooldown_s
        remain = cd - (now - self._last_critical_at)
        return remain if remain > 0 else 0.0

    @property
    def stopped(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False
        self._failures = 0
        self._rate_window = []

    def note_failure(self) -> None:
        self._failures += 1

    def status(self) -> str:
        if self._paused:
            return "paused"
        return "running"

    def snapshot(self) -> Dict[str, Any]:
        return {"status": self.status(),
                "rate_window": len(self._rate_window),
                "failures": self._failures}
