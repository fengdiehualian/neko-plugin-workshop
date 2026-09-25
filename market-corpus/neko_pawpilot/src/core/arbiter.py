"""提示仲裁器：场景门控 → 类别开关 → 冷却 → 抢占/单槽窗口 → 全局限流。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from .event_catalog import BROADCAST_FREQUENCY_MULTIPLIERS, spec
from .safety_guard import SafetyGuard
from .scenario import ScenarioMachine


@dataclass(frozen=True)
class ArbiterCheckpoint:
    last_fired: dict[str, tuple[float, bool]]


class Arbiter:
    """候选事件 → 至多 1 条输出。"""

    def __init__(self, config: object, safety: SafetyGuard) -> None:
        self.config = config
        self.safety = safety
        self.scenario = ScenarioMachine()
        self._last_fired: dict[str, tuple[float, bool]] = {}
        self._window_best: Optional[tuple[str, int, int, float]] = None
        self._player_silence_until = 0.0
        self.broadcast_categories: dict[str, bool] = {}
        self.broadcast_frequency = "standard"
        self._decision_log: list[dict[str, Any]] = []

    def reset(self) -> None:
        self._last_fired.clear()
        self._window_best = None

    def checkpoint(self) -> ArbiterCheckpoint:
        return ArbiterCheckpoint(last_fired=dict(self._last_fired))

    def restore(self, cp: ArbiterCheckpoint) -> None:
        self._last_fired = dict(cp.last_fired)

    def on_player_speak(self, silence_s: float = 60.0) -> None:
        self._player_silence_until = time.time() + silence_s

    def update_scenario(self, snap) -> str:
        return self.scenario.update(snap)

    def decide(self, event_name: str, snap, now: float | None = None) -> tuple[bool, str]:
        """判定是否输出；返回 (是否, 理由)。"""
        now = now or time.time()
        self._decision_log = []
        if self.safety.stopped:
            self._log(event_name, "suppressed", self.safety.status())
            return False, self.safety.status()
        if now < self._player_silence_until:
            self._log(event_name, "suppressed", "player_quiet_window")
            return False, "player_quiet_window"

        es = spec(event_name)
        # 场景门控
        if not self.scenario.allow(es.category):
            self._log(event_name, "suppressed", f"scenario_gated({self.scenario.current})")
            return False, f"scenario_gated({self.scenario.current})"
        # 类别开关
        if self.broadcast_categories.get(es.category, True) is False:
            self._log(event_name, "suppressed", "category_disabled")
            return False, "category_disabled"

        cd = es.cooldown_seconds
        if cd > 0:
            cd *= BROADCAST_FREQUENCY_MULTIPLIERS.get(self.broadcast_frequency, 1.0)
        last_at, last_critical = self._last_fired.get(event_name, (-1e9, False))
        critical = es.preempt
        critical_upgrade = critical and not last_critical
        if cd > 0 and (now - last_at) < cd and not critical_upgrade:
            self._log(event_name, "suppressed", "cooldown")
            return False, "cooldown"

        # 抢占通道
        if critical:
            crit_remaining = self.safety.critical_cooldown_remaining(now)
            if crit_remaining > 0:
                self._log(event_name, "suppressed", f"critical_cooldown({crit_remaining:.1f}s)")
                return False, "critical_cooldown"
            self._fire(event_name, critical, now)
            self._window_best = None
            self._log(event_name, "spoken", "preempt")
            return True, "preempt"

        # 限流通道：单槽窗口择优
        rate_remaining = self.safety.rate_limit_remaining(now)
        if rate_remaining > 0:
            rank = (es.priority, 0, now)
            if self._window_best is None or rank > self._window_best:
                self._window_best = (event_name, es.priority, 0, now)
            self._log(event_name, "buffered", f"rate_limited({rate_remaining:.1f}s)")
            return False, "rate_limited"

        if self._window_best is not None:
            chosen = self._window_best[0]
            self._window_best = None
            if chosen == event_name or now - self._last_fired.get(chosen, (-1e9, False))[0] >= cd:
                self._fire(chosen, False, now)
                self._log(chosen, "spoken", "window_flush")
                return chosen == event_name, "window_flush" if chosen == event_name else "window_flush_other"

        self._fire(event_name, False, now)
        self._log(event_name, "spoken", "rate_ok")
        return True, "rate_ok"

    def _fire(self, event_name: str, critical: bool, now: float) -> None:
        self._last_fired[event_name] = (now, critical)
        self.safety.mark_output(critical=critical, now=now)

    def _log(self, event_name: str, result: str, reason: str) -> None:
        self._decision_log.append({"event_id": event_name, "result": result, "reason": reason})

    def decision_snapshot(self) -> list[dict[str, Any]]:
        return list(self._decision_log)

    def snapshot(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario.current,
            "broadcast_frequency": self.broadcast_frequency,
            "broadcast_categories": dict(self.broadcast_categories),
            "safety": self.safety.snapshot(),
        }
