"""输出安全阀：全局限流 / 抢占冷却 / 断路器。

取自同域成熟参照 ``neko_pawpilot/core/safety_guard.py``（欧卡2 副驾，长期实机验证），
按本插件口径重写，阈值全部来自 ``core/constants.py``。

职责边界：本模块只管**输出节流与失效保护**，不管"该不该报"（那是 arbiter）。
"""

from __future__ import annotations

import time
from typing import Any

from .constants import (
    CRITICAL_COOLDOWN_S,
    GLOBAL_RATE_LIMIT_S,
    SAFETY_FAILURE_LIMIT,
    SAFETY_FAILURE_WINDOW_S,
)


class SafetyGuard:
    """插件侧安全阀：防刷屏、防故障风暴。

    两道时钟：
      * ``global_rate_limit_s``  —— 任意两条**非抢占**播报之间的最小间隔
      * ``critical_cooldown_s``  —— 两条**抢占**播报之间的最小间隔

    以及一道断路器：``safety_window_s`` 内输出失败达到 ``safety_failure_limit``
    即自动急停。这条针对的是"宿主通道持续报错时插件还在猛发"的风暴场景——
    失败本身就是信号，不该继续加码。
    """

    def __init__(
        self,
        *,
        global_rate_limit_s: float = GLOBAL_RATE_LIMIT_S,
        critical_cooldown_s: float = CRITICAL_COOLDOWN_S,
        failure_window_s: float = SAFETY_FAILURE_WINDOW_S,
        failure_limit: int = SAFETY_FAILURE_LIMIT,
    ) -> None:
        self.global_rate_limit_s = float(global_rate_limit_s)
        self.critical_cooldown_s = float(critical_cooldown_s)
        self.failure_window_s = float(failure_window_s)
        self.failure_limit = int(failure_limit)

        self.manual_paused = False
        self.auto_paused = False
        self._last_output_at = 0.0
        self._last_critical_at = 0.0
        self._failures: list[float] = []

    # ── 急停 / 恢复 ────────────────────────────────────────────────────
    @property
    def stopped(self) -> bool:
        return self.manual_paused or self.auto_paused

    def status(self) -> str:
        if self.auto_paused:
            return "tripped"      # 断路器跳闸
        if self.manual_paused:
            return "paused"       # 用户手动停
        return "running"

    def pause(self) -> None:
        self.manual_paused = True

    def resume(self) -> None:
        self.manual_paused = False
        self.auto_paused = False
        self._failures.clear()
        self._last_output_at = 0.0
        self._last_critical_at = 0.0

    # ── 两道时钟 ───────────────────────────────────────────────────────
    def rate_limit_remaining(self, now: float | None = None) -> float:
        """非抢占输出距下次允许还剩多少秒。"""
        if self.global_rate_limit_s <= 0:
            return 0.0
        cur = time.time() if now is None else now
        remaining = self.global_rate_limit_s - (cur - self._last_output_at)
        return remaining if remaining > 0 else 0.0

    def critical_cooldown_remaining(self, now: float | None = None) -> float:
        """抢占输出距下次允许还剩多少秒。"""
        if self.critical_cooldown_s <= 0:
            return 0.0
        cur = time.time() if now is None else now
        remaining = self.critical_cooldown_s - (cur - self._last_critical_at)
        return remaining if remaining > 0 else 0.0

    def mark_output(self, *, critical: bool, now: float | None = None) -> None:
        """登记一次**已经发生**的输出。两条时钟都从"真的说出口"起算。"""
        cur = time.time() if now is None else now
        self._last_output_at = cur
        if critical:
            self._last_critical_at = cur

    # ── 断路器 ─────────────────────────────────────────────────────────
    def record_failure(self, now: float | None = None) -> None:
        """记一次输出失败；窗口内达上限自动急停。"""
        cur = time.time() if now is None else now
        self._failures.append(cur)
        self._failures = [t for t in self._failures if cur - t <= self.failure_window_s]
        if self.failure_limit > 0 and len(self._failures) >= self.failure_limit:
            self.auto_paused = True

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status(),
            "manual_paused": self.manual_paused,
            "auto_paused": self.auto_paused,
            "failures_in_window": len(self._failures),
            "rate_limit_remaining": round(self.rate_limit_remaining(), 1),
            "critical_cooldown_remaining": round(self.critical_cooldown_remaining(), 1),
        }
