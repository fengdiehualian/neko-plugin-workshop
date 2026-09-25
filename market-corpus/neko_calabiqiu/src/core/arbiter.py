"""候选 CompanionEvent → 至多 1 条输出。"""

from __future__ import annotations

from typing import Any

from .contracts import CAT_VISION, CompanionEvent, category_allowed
from .safety_guard import SafetyGuard


class Arbiter:
    def __init__(self, safety: SafetyGuard) -> None:
        self.safety = safety
        self._last_fired: dict[str, tuple[float, str]] = {}
        self._window_best: CompanionEvent | None = None

    def reset(self) -> None:
        self._last_fired.clear()
        self._window_best = None

    def checkpoint(self) -> tuple[dict[str, tuple[float, str]], CompanionEvent | None]:
        """Capture mutable selection state before a two-stage delivery attempt."""
        return dict(self._last_fired), self._window_best

    def restore(
        self,
        checkpoint: tuple[dict[str, tuple[float, str]], CompanionEvent | None],
    ) -> None:
        """Restore selection state when the dispatcher declines an event."""
        last_fired, window_best = checkpoint
        self._last_fired = dict(last_fired)
        self._window_best = window_best

    def decide(
        self, candidates: list[CompanionEvent], scenario: str, now: float
    ) -> tuple[CompanionEvent | None, list[dict[str, Any]]]:
        chain: list[dict[str, Any]] = []
        if self.safety.stopped:
            for c in candidates:
                chain.append(_rec(c, "suppressed", self.safety.status()))
            return None, chain

        survivors: list[CompanionEvent] = []
        for c in candidates:
            if not category_allowed(scenario, c.category):
                chain.append(_rec(c, "dropped", f"scenario_gated({scenario})"))
                continue
            cd = c.spec.cooldown_seconds
            last_at, last_level = self._last_fired.get(c.event_id, (-1e9, ""))
            critical_upgrade = c.level == "critical" and last_level != "critical"
            if cd > 0 and (now - last_at) < cd and not critical_upgrade:
                chain.append(_rec(c, "dropped", "cooldown"))
                continue
            survivors.append(c)

        preempt = [c for c in survivors if c.preempt_eligible]
        normal = [c for c in survivors if not c.preempt_eligible]

        if preempt:
            best = _top(preempt)
            # critical 抢占冷却仅约束视觉警报；生命周期和赛果等可靠事件不应被
            # 抖动保护误伤。enemy_nearby 仍可越过普通全局限流，但不能连续刷屏。
            remaining = (
                0.0
                if best.category != CAT_VISION
                else self.safety.critical_cooldown_remaining(now)
            )
            if remaining > 0:
                chain.append(_rec(best, "suppressed", f"critical_cooldown({remaining:.1f}s)"))
                survivors = [c for c in survivors if c is not best]
                for c in survivors:
                    chain.append(_rec(c, "dropped", "lost_to_preempt_cooldown"))
                return None, chain
            self._fire(best, now, critical=best.category == CAT_VISION)
            self._window_best = None
            chain.append(_rec(best, "spoken", "preempt"))
            for c in survivors:
                if c is not best:
                    chain.append(_rec(c, "dropped", "lost_to_preempt"))
            return best, chain

        if self._window_best is not None and self._is_expired_vision_event(
            self._window_best, now
        ):
            chain.append(_rec(self._window_best, "dropped", "window_event_expired"))
            self._window_best = None

        if normal:
            best = _top(normal)
            if self._window_best is None or _rank(best) > _rank(self._window_best):
                self._window_best = best
            for c in normal:
                if c is not best:
                    chain.append(_rec(c, "dropped", "lost_in_window"))

        if self._window_best is None:
            return None, chain

        remaining = self.safety.rate_limit_remaining(now)
        if remaining > 0:
            chain.append(_rec(self._window_best, "suppressed", f"rate_limit({remaining:.1f}s)"))
            return None, chain

        chosen = self._window_best
        self._window_best = None
        self._fire(chosen, now, critical=False)
        chain.append(_rec(chosen, "spoken", "selected"))
        return chosen, chain

    def _fire(self, event: CompanionEvent, now: float, *, critical: bool) -> None:
        self._last_fired[event.event_id] = (now, event.level)
        self.safety.mark_fired(now, critical=critical)

    def _is_expired_vision_event(self, event: CompanionEvent, now: float) -> bool:
        if (
            event.category != CAT_VISION
            or event.created_at <= 0
            or now < event.created_at
        ):
            return False
        max_age = self.safety.config.vision_output_event_max_age_seconds
        return max_age > 0 and now - event.created_at > max_age


def _rank(event: CompanionEvent) -> tuple[int, int, float]:
    return (event.priority, event.spec.severity_for(event.level), event.created_at)


def _top(events: list[CompanionEvent]) -> CompanionEvent:
    return max(events, key=_rank)


def _rec(event: CompanionEvent, outcome: str, reason: str) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "level": event.level,
        "outcome": outcome,
        "reason": reason,
        "priority": event.priority,
    }
