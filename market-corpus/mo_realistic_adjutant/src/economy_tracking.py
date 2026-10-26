from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import math
import threading
import time
from typing import Any, Iterable


@dataclass(frozen=True)
class EconomySample:
    timestamp: float
    frame: int
    house_id: int
    credits: int
    power_available: bool
    power_output: int | None
    power_drain: int | None
    power_surplus: int | None


@dataclass(frozen=True)
class CreditEvent:
    timestamp: float
    frame: int
    delta: int

    @property
    def kind(self) -> str:
        return "income" if self.delta > 0 else "spending"


@dataclass(frozen=True)
class FlowWindow:
    requested_seconds: int
    observed_seconds: float
    sample_count: int
    starting_credits: int | None
    ending_credits: int | None
    net_change: int
    observed_income: int
    observed_spending: int
    net_per_minute: float | None
    income_per_minute: float | None
    spending_per_minute: float | None
    confidence: str


class EconomyTracker:
    """Build useful economy flow from the game's point-in-time telemetry.

    Credits are authoritative snapshots, but the game does not label each change.
    Positive deltas are therefore reported as observed income and negative deltas as
    observed spending. This wording deliberately avoids claiming a source that is not
    present in the native protocol.
    """

    def __init__(
        self,
        *,
        retention_seconds: int = 15 * 60,
        minimum_sample_interval: float = 0.2,
        clock=time.monotonic,
    ) -> None:
        self._retention_seconds = max(60, int(retention_seconds))
        self._minimum_sample_interval = max(0.0, float(minimum_sample_interval))
        self._clock = clock
        self._samples: deque[EconomySample] = deque()
        self._events: deque[CreditEvent] = deque()
        self._lock = threading.RLock()
        self._session_key: tuple[int, int] | None = None
        self._last_frame: int | None = None
        self._last_observed_at: float | None = None
        self._power_transition: str | None = None
        self._generation = 0

    def observe(
        self, state: dict[str, Any], *, now: float | None = None
    ) -> dict[str, Any] | None:
        now = self._clock() if now is None else float(now)
        parsed = self._parse_state(state, now)
        if parsed is None:
            return None

        with self._lock:
            scenario_reset = self._is_new_session(parsed)
            if scenario_reset:
                self._clear_locked()
            if (
                self._last_observed_at is not None
                and now - self._last_observed_at < self._minimum_sample_interval
            ):
                return None

            previous = self._samples[-1] if self._samples else None
            self._samples.append(parsed)
            self._last_frame = parsed.frame
            self._last_observed_at = now
            self._session_key = (parsed.house_id, self._generation)
            if previous is not None and previous.house_id == parsed.house_id:
                delta = parsed.credits - previous.credits
                if delta:
                    self._events.append(CreditEvent(now, parsed.frame, delta))

            transition = self._detect_power_transition(previous, parsed)
            self._power_transition = transition
            self._trim_locked(now)
            return {
                "reset": scenario_reset,
                "power_transition": transition,
                "credits_delta": (
                    parsed.credits - previous.credits if previous is not None else 0
                ),
            }

    def reset(self) -> None:
        with self._lock:
            self._clear_locked()

    def report(
        self,
        *,
        windows: Iterable[int] = (15, 60, 180),
        now: float | None = None,
    ) -> dict[str, Any]:
        now = self._clock() if now is None else float(now)
        with self._lock:
            if not self._samples:
                return {
                    "available": False,
                    "summary": "尚未收到有效的资金遥测",
                    "credits": None,
                    "flow_status": "unknown",
                    "windows": [],
                    "power": self._unknown_power(),
                }

            latest = self._samples[-1]
            reports = [self._window_locked(int(value), now) for value in windows]
            primary = self._choose_primary_window(reports)
            status = self._flow_status(primary)
            power = self._power_report(latest)
            power.update(self._power_stability_locked())
            return {
                "available": True,
                "summary": self._summary(latest, status, primary, power),
                "credits": latest.credits,
                "flow_status": status,
                "net_per_minute": primary.net_per_minute if primary else None,
                "income_per_minute": primary.income_per_minute if primary else None,
                "spending_per_minute": primary.spending_per_minute if primary else None,
                "flow_confidence": primary.confidence if primary else "low",
                "observed_income": primary.observed_income if primary else 0,
                "observed_spending": primary.observed_spending if primary else 0,
                "observation_seconds": (
                    round(primary.observed_seconds, 1) if primary else 0.0
                ),
                "windows": [asdict(item) for item in reports],
                "power": power,
                "sample_count": len(self._samples),
                "last_frame": latest.frame,
                "measurement_note": (
                    "资金流根据连续余额变化计算；正变化记为可观察收入，"
                    "负变化记为可观察支出，不猜测具体来源。"
                ),
            }

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            selected = list(self._events)[-max(0, min(int(limit), 200)) :]
            return [
                {
                    "timestamp": item.timestamp,
                    "frame": item.frame,
                    "kind": item.kind,
                    "delta": item.delta,
                    "amount": abs(item.delta),
                }
                for item in reversed(selected)
            ]

    @property
    def sample_count(self) -> int:
        with self._lock:
            return len(self._samples)

    def power_stability(self) -> dict[str, Any]:
        """Return the latest consecutive power stability assessment."""
        with self._lock:
            return self._power_stability_locked()

    def _parse_state(
        self, state: dict[str, Any], now: float
    ) -> EconomySample | None:
        game = state.get("game")
        player = state.get("player")
        economy = state.get("economy")
        if (
            not isinstance(game, dict)
            or game.get("running") is not True
            or not isinstance(player, dict)
        ):
            return None
        frame = self._integer(game.get("frame"))
        house_id = self._integer(player.get("house_id"))
        credits = self._integer(player.get("credits"))
        if frame is None or house_id is None or credits is None or house_id < 0:
            return None
        economy = economy if isinstance(economy, dict) else {}
        power_available = economy.get("power_available") is True
        output = self._integer(economy.get("power_output")) if power_available else None
        drain = self._integer(economy.get("power_drain")) if power_available else None
        surplus = self._integer(economy.get("power_surplus")) if power_available else None
        if power_available and (output is None or drain is None):
            power_available = False
            output = drain = surplus = None
        elif power_available and surplus is None:
            surplus = int(output) - int(drain)
        return EconomySample(
            timestamp=now,
            frame=frame,
            house_id=house_id,
            credits=credits,
            power_available=power_available,
            power_output=output,
            power_drain=drain,
            power_surplus=surplus,
        )

    def _is_new_session(self, current: EconomySample) -> bool:
        if not self._samples:
            return False
        previous = self._samples[-1]
        if current.house_id != previous.house_id:
            return True
        if self._last_frame is not None and current.frame + 30 < self._last_frame:
            return True
        return False

    def _clear_locked(self) -> None:
        self._samples.clear()
        self._events.clear()
        self._last_frame = None
        self._last_observed_at = None
        self._power_transition = None
        self._generation += 1
        self._session_key = None

    def _trim_locked(self, now: float) -> None:
        cutoff = now - self._retention_seconds
        while len(self._samples) > 1 and self._samples[1].timestamp < cutoff:
            self._samples.popleft()
        while self._events and self._events[0].timestamp < cutoff:
            self._events.popleft()

    def _power_stability_locked(
        self, *, required_samples: int = 3, max_variation: int = 100
    ) -> dict[str, Any]:
        """Assess recent contiguous power samples without guessing hidden data."""
        required = max(1, int(required_samples))
        recent: list[EconomySample] = []
        for sample in reversed(self._samples):
            if not sample.power_available or sample.power_surplus is None:
                break
            recent.append(sample)
            if len(recent) >= required:
                break
        surpluses = [int(item.power_surplus) for item in recent]
        stable = bool(
            len(surpluses) >= required
            and max(surpluses) - min(surpluses) <= max(0, int(max_variation))
            and min(surpluses) >= 0
        )
        return {
            "stable": stable,
            "stable_samples": len(surpluses),
            "surplus_min": min(surpluses) if surpluses else None,
            "surplus_max": max(surpluses) if surpluses else None,
            "stability_note": (
                "连续电力遥测稳定"
                if stable else "需要至少 3 个连续且未断电的电力样本"
            ),
        }

    def _window_locked(self, seconds: int, now: float) -> FlowWindow:
        requested = max(1, seconds)
        cutoff = now - requested
        candidates = [item for item in self._samples if item.timestamp >= cutoff]
        if not candidates:
            candidates = [self._samples[-1]]
        first = candidates[0]
        last = candidates[-1]
        duration = max(0.0, last.timestamp - first.timestamp)
        events = [
            item
            for item in self._events
            if first.timestamp < item.timestamp <= last.timestamp
        ]
        income = sum(item.delta for item in events if item.delta > 0)
        spending = -sum(item.delta for item in events if item.delta < 0)
        net = last.credits - first.credits
        if duration < 1.0:
            net_rate = income_rate = spending_rate = None
        else:
            scale = 60.0 / duration
            net_rate = round(net * scale, 1)
            income_rate = round(income * scale, 1)
            spending_rate = round(spending * scale, 1)
        coverage = duration / requested
        confidence = "high" if coverage >= 0.8 and len(candidates) >= 5 else (
            "medium" if coverage >= 0.35 and len(candidates) >= 3 else "low"
        )
        return FlowWindow(
            requested_seconds=requested,
            observed_seconds=duration,
            sample_count=len(candidates),
            starting_credits=first.credits,
            ending_credits=last.credits,
            net_change=net,
            observed_income=income,
            observed_spending=spending,
            net_per_minute=net_rate,
            income_per_minute=income_rate,
            spending_per_minute=spending_rate,
            confidence=confidence,
        )

    @staticmethod
    def _choose_primary_window(windows: list[FlowWindow]) -> FlowWindow | None:
        if not windows:
            return None
        useful = [item for item in windows if item.net_per_minute is not None]
        if not useful:
            return windows[0]
        return max(
            useful,
            key=lambda item: (
                {"high": 3, "medium": 2, "low": 1}.get(item.confidence, 0),
                item.observed_seconds,
            ),
        )

    @staticmethod
    def _flow_status(window: FlowWindow | None) -> str:
        if window is None or window.net_per_minute is None:
            return "collecting"
        rate = window.net_per_minute
        activity = window.observed_income + window.observed_spending
        if rate >= 60:
            return "growing"
        if rate <= -60:
            return "draining"
        if activity == 0 and window.observed_seconds >= 10:
            return "stalled"
        return "stable"

    @staticmethod
    def _detect_power_transition(
        previous: EconomySample | None, current: EconomySample
    ) -> str | None:
        if not current.power_available or current.power_surplus is None:
            return None
        current_low = current.power_surplus < 0
        if previous is None or not previous.power_available or previous.power_surplus is None:
            return "low_power" if current_low else None
        previous_low = previous.power_surplus < 0
        if current_low != previous_low:
            return "low_power" if current_low else "power_recovered"
        return None

    @staticmethod
    def _power_report(sample: EconomySample) -> dict[str, Any]:
        if not sample.power_available:
            return EconomyTracker._unknown_power()
        output = int(sample.power_output or 0)
        drain = int(sample.power_drain or 0)
        surplus = int(sample.power_surplus or 0)
        load_ratio = None if output <= 0 else round(drain / output, 3)
        if surplus < 0:
            status = "low"
            summary = f"电力不足：产出 {output}，消耗 {drain}，缺口 {abs(surplus)}"
        elif output == 0 and drain == 0:
            status = "offline"
            summary = "当前没有电力产出或消耗"
        elif surplus == 0:
            status = "balanced"
            summary = f"电力刚好平衡：产出和消耗均为 {output}"
        else:
            status = "healthy"
            summary = f"电力正常：产出 {output}，消耗 {drain}，余量 {surplus}"
        return {
            "available": True,
            "status": status,
            "output": output,
            "drain": drain,
            "surplus": surplus,
            "load_ratio": load_ratio,
            "summary": summary,
        }

    @staticmethod
    def _unknown_power() -> dict[str, Any]:
        return {
            "available": False,
            "status": "unavailable",
            "output": None,
            "drain": None,
            "surplus": None,
            "load_ratio": None,
            "summary": "当前 DLL 未提供电力遥测；更新 MO 端覆盖文件后即可读取",
        }

    @staticmethod
    def _summary(
        sample: EconomySample,
        status: str,
        window: FlowWindow | None,
        power: dict[str, Any],
    ) -> str:
        names = {
            "collecting": "正在积累资金流样本",
            "growing": "资金净流入",
            "draining": "资金净流出",
            "stable": "资金基本稳定",
            "stalled": "资金暂无变化",
        }
        flow = names.get(status, "资金状态未知")
        if window and window.net_per_minute is not None:
            flow += f"（约 {window.net_per_minute:+.0f}/分钟）"
        return f"当前资金 {sample.credits}，{flow}；{power['summary']}"

    @staticmethod
    def _integer(value: Any) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        if not math.isfinite(float(value)):
            return None
        return value
