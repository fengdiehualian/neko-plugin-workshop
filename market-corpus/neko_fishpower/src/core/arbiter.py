"""提示仲裁器：状态门控 → 类别开关 → 冷却 → 抢占通道 → 全局限流（照欧卡 §5）。"""

from __future__ import annotations

import time
from typing import Any, Tuple

from .event_catalog import BROADCAST_FREQUENCY_MULTIPLIERS, spec
from .safety_guard import SafetyGuard
from .state_machine import GameStateMachine


class Arbiter:
    """候选事件 → 至多 1 条输出。"""

    def __init__(self, config: object, safety: SafetyGuard) -> None:
        self.config = config
        self.safety = safety
        self.scenario = GameStateMachine()
        self._last_fired: dict[str, tuple[float, bool]] = {}
        self._player_silence_until = 0.0
        self.broadcast_categories: dict[str, bool] = {}
        self.broadcast_frequency = "standard"
        self._decision_log: list[dict[str, Any]] = []

    def on_player_speak(self, silence_s: float = 60.0) -> None:
        self._player_silence_until = time.time() + silence_s

    def decide(self, event_name: str, now: float | None = None) -> Tuple[bool, str]:
        """判定是否输出；返回 (是否, 理由)。"""
        now = now or time.time()
        self._decision_log = []
        if self.safety.stopped:
            return False, self.safety.status()
        if now < self._player_silence_until:
            return False, "player_quiet_window"

        es = spec(event_name)
        # 状态门控：游戏没开/主菜单时，非 lifecycle 事件全拒绝（修"没开游戏还提示换饵"）
        if not self.scenario.allow(es.category):
            return False, f"state_gated({self.scenario.current})"
        if self.broadcast_categories.get(es.category, True) is False:
            return False, "category_disabled"

        cd = es.cooldown_seconds
        if cd > 0:
            cd *= BROADCAST_FREQUENCY_MULTIPLIERS.get(self.broadcast_frequency, 1.0)
        last_at, last_critical = self._last_fired.get(event_name, (-1e9, False))
        critical = es.preempt
        critical_upgrade = critical and not last_critical
        if cd > 0 and (now - last_at) < cd and not critical_upgrade:
            return False, "cooldown"

        if critical:
            crit_remaining = self.safety.critical_cooldown_remaining(now)
            if crit_remaining > 0:
                return False, f"critical_cooldown({crit_remaining:.1f}s)"
            self._fire(event_name, True, now)
            return True, "preempt"

        rate_remaining = self.safety.rate_limit_remaining(now)
        if rate_remaining > 0:
            return False, f"rate_limited({rate_remaining:.1f}s)"

        self._fire(event_name, False, now)
        return True, "rate_ok"

    def update_state(self, st: Any) -> str:
        """由快照更新状态机，返回当前状态。"""
        return self.scenario.update(st)

    def _fire(self, event_name: str, critical: bool, now: float) -> None:
        self._last_fired[event_name] = (now, critical)
        self.safety.mark_output(critical=critical, now=now)

    def decision_snapshot(self) -> list[dict[str, Any]]:
        return list(self._decision_log)

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.scenario.snapshot(),
            "broadcast_frequency": self.broadcast_frequency,
            "broadcast_categories": dict(self.broadcast_categories),
            "safety": self.safety.snapshot(),
        }
