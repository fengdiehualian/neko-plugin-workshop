"""提示仲裁器：场景门控 → 类别开关 → 冷却 → 抢占/单槽择优 → 全局限流。

分层与理由取自同域成熟参照 ``neko_pawpilot/core/arbiter.py``（欧卡2 副驾）。
核心不变量：**至多 1 条输出**。候选再多，同一时刻只放一条出去。

为什么需要"场景门控"
------------------
用户明确要求考虑铁路作业的复杂性："停站会保持制动"、"单机 / 连挂 / 换端作业"。
停站保压时，制动缸压力本来就在高位、速度本来就是 0 —— 若照常跑规则，
`GAUGE_BRAKE_NOT_RELEASED`（制动未缓解）、`SPD_*`（限速/超速）会持续误报。
所以按**作业场景**整体抑制无关类别，而不是靠逐条调阈值。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from .constants import PLAYER_QUIET_WINDOW_S
from .event_catalog import (
    CAT_EMERGENCY,
    CAT_PARKING,
    CAT_SHUNT,
    CAT_SIGNAL,
    CATEGORIES,
    CATEGORY_DEFAULTS,
    EventSpec,
    scaled_cooldown,
)
from .event_catalog import (
    spec as _spec_of,
)
from .safety_guard import SafetyGuard

# ── 作业场景 ────────────────────────────────────────────────────────────
SC_NORMAL = "normal"              # 正常运行
SC_STATION_STOP = "station_stop"  # 停站保压：车停、常用制动保持
SC_SHUNTING = "shunting"          # 调车作业：低速、道岔语境
SC_STOPPED = "stopped"            # 其他静止（被迫停车等）

#: 场景 → 允许的类别。``None`` 表示不额外限制，交给类别开关。
SCENARIO_ALLOW: dict[str, frozenset[str] | None] = {
    SC_NORMAL: None,
    # 停站保压：限速/超速与仪表都已无意义，只留信号、应急、防溜
    SC_STATION_STOP: frozenset({CAT_SIGNAL, CAT_EMERGENCY, CAT_PARKING}),
    # 调车：干线限速规则不适用，留调车与安全类
    SC_SHUNTING: frozenset({CAT_SHUNT, CAT_SIGNAL, CAT_EMERGENCY, CAT_PARKING}),
    SC_STOPPED: frozenset({CAT_SIGNAL, CAT_EMERGENCY, CAT_PARKING}),
}

SCENARIOS = tuple(SCENARIO_ALLOW)

#: logger 缺省值：仲裁器必须能在没有 logger 的情况下独立构造（便于单测）。
#: 挂 NullHandler 是为了避免 "no handler" 警告，也不往上冒到 root logger。
_NULL_LOGGER = logging.getLogger("openrails_copilot.arbiter")
_NULL_LOGGER.addHandler(logging.NullHandler())


@dataclass(frozen=True)
class Decision:
    event_id: str
    spoken: bool
    reason: str

    def __str__(self) -> str:
        return f"{self.event_id}:{'被报' if self.spoken else '未报'}({self.reason})"


class Arbiter:
    """候选事件 → 至多 1 条输出。"""

    def __init__(
        self,
        safety: SafetyGuard,
        *,
        frequency: str = "standard",
        categories: dict[str, bool] | None = None,
        logger: Any = None,
    ) -> None:
        self.safety = safety
        self.logger = logger if logger is not None else _NULL_LOGGER
        self.frequency = frequency
        self.categories: dict[str, bool] = dict(CATEGORY_DEFAULTS)
        if categories:
            self.categories.update(categories)
        self.scenario = SC_NORMAL

        self._last_fired: dict[str, float] = {}
        self._last_critical: dict[str, bool] = {}
        self._window_best: tuple[int, float, str] | None = None  # (-priority, seq, event_id)
        self._seq = 0
        self._player_silence_until = 0.0
        self._log: list[Decision] = []

    # ── 运行期配置 ─────────────────────────────────────────────────────
    def set_frequency(self, frequency: str) -> None:
        self.frequency = frequency

    def set_category(self, category: str, enabled: bool) -> None:
        self.categories[category] = bool(enabled)

    def update_scenario(self, scenario: str) -> str:
        if scenario in SCENARIO_ALLOW:
            if scenario != self.scenario:
                self.logger.info("作业场景切换: %s -> %s", self.scenario, scenario)
                # 场景切换时清掉限流窗口：上一个场景缓冲的候选在新场景可能已无关
                self._window_best = None
            self.scenario = scenario
        return self.scenario

    def on_player_speak(self, silence_s: float = PLAYER_QUIET_WINDOW_S) -> None:
        """用户刚说完话 → 静默窗。不抢刚开口的人的话。

        在 N.E.K.O 上这条另有一层好处：用户说话那一轮 ``had_user_input=True``，
        走的是**不受 2 次/会话上限约束**的分析路径。
        """
        self._player_silence_until = time.time() + silence_s

    def reset(self) -> None:
        self._last_fired.clear()
        self._last_critical.clear()
        self._window_best = None

    # ── 场景门控 ───────────────────────────────────────────────────────
    def scenario_allows(self, category: str) -> bool:
        allowed = SCENARIO_ALLOW.get(self.scenario)
        return True if allowed is None else category in allowed

    # ── 判定 ───────────────────────────────────────────────────────────
    def decide(self, event_id: str, now: float | None = None) -> tuple[bool, str]:
        """判定是否输出；返回 ``(是否, 理由)``。"""
        now = time.time() if now is None else now
        es = _spec_of(event_id)

        def deny(reason: str) -> tuple[bool, str]:
            self._log.append(Decision(event_id, False, reason))
            return False, reason

        if self.safety.stopped:
            return deny(self.safety.status())
        if now < self._player_silence_until:
            return deny("player_quiet_window")
        if not self.scenario_allows(es.category):
            return deny(f"scenario_gated({self.scenario})")
        if self.categories.get(es.category, True) is False:
            return deny("category_disabled")

        cd = scaled_cooldown(es, self.frequency)
        last_at = self._last_fired.get(event_id, -1e18)
        critical = es.preempt
        critical_upgrade = critical and not self._last_critical.get(event_id, False)
        if cd > 0 and (now - last_at) < cd and not critical_upgrade:
            return deny("cooldown")

        # ── 抢占通道：绕过限流，立即占用槽位 ──────────────────────────
        if critical:
            remaining = self.safety.critical_cooldown_remaining(now)
            if remaining > 0:
                return deny(f"critical_cooldown({remaining:.1f}s)")
            self._fire(event_id, True, now)
            self._window_best = None
            self._log.append(Decision(event_id, True, "preempt"))
            return True, "preempt"

        # ── 限流通道：单槽择优 ────────────────────────────────────────
        self._seq += 1
        if self.safety.rate_limit_remaining(now) > 0:
            rank = (-es.priority, self._seq, event_id)
            if self._window_best is None or rank < self._window_best:
                self._window_best = rank
            return deny(f"rate_limited(buffered={event_id})")

        if self._window_best is not None:
            # 窗口期内攒下的最高优先级候选优先出口
            _, _, best = self._window_best
            self._window_best = None
            if best != event_id:
                best_spec = _spec_of(best)
                best_cd = scaled_cooldown(best_spec, self.frequency)
                if (now - self._last_fired.get(best, -1e18)) >= best_cd:
                    self._fire(best, False, now)
                    self._log.append(Decision(best, True, "window_flush"))
                    self._log.append(Decision(event_id, False, f"window_flush_yielded_to({best})"))
                    return False, f"window_flush_yielded_to({best})"

        self._fire(event_id, False, now)
        self._log.append(Decision(event_id, True, "rate_ok"))
        return True, "rate_ok"

    def _fire(self, event_id: str, critical: bool, now: float) -> None:
        self._last_fired[event_id] = now
        self._last_critical[event_id] = critical
        self.safety.mark_output(critical=critical, now=now)

    # ── 观测 ───────────────────────────────────────────────────────────
    def decision_snapshot(self) -> list[Decision]:
        return list(self._log)

    def explain_last(self) -> list[str]:
        """最近一轮判定的可读解释（"这条为何没说"）。"""
        return [str(d) for d in self._log]

    def snapshot(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "frequency": self.frequency,
            "categories": dict(self.categories),
            "safety": self.safety.snapshot(),
            "cooling_down": {
                k: round(time.time() - v, 1) for k, v in self._last_fired.items()
            },
        }

    def begin_cycle(self) -> None:
        """每个采样周期开始时调用，清空上一轮的判定日志。"""
        self._log = []


__all__ = [
    "Arbiter", "Decision", "SCENARIO_ALLOW", "SCENARIOS",
    "SC_NORMAL", "SC_STATION_STOP", "SC_SHUNTING", "SC_STOPPED",
    "CATEGORIES", "EventSpec",
]
