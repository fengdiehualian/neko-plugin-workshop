from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

from .config import RecommendationConfig


@dataclass(frozen=True, slots=True)
class GateDecision:
    allowed: bool
    reason: str


def _minutes(value: str) -> int | None:
    try:
        hour, minute = value.split(":", 1)
        return int(hour) * 60 + int(minute)
    except (AttributeError, TypeError, ValueError):
        return None


def in_quiet_hours(now: datetime, start: str, end: str) -> bool:
    start_min, end_min = _minutes(start), _minutes(end)
    if start_min is None or end_min is None or start_min == end_min:
        return False
    current = now.hour * 60 + now.minute
    if start_min < end_min:
        return start_min <= current < end_min
    return current >= start_min or current < end_min


def evaluate_gate(
    *,
    config: RecommendationConfig,
    now: datetime,
    history: Iterable[Mapping[str, Any]],
    proactive_enabled: bool,
    privacy_state: str = "unavailable",
    idle_seconds: float | None = None,
    last_user_message_at: float = 0.0,
) -> GateDecision:
    if not config.enabled:
        return GateDecision(False, "plugin_disabled")
    if not proactive_enabled:
        return GateDecision(False, "global_proactive_disabled")
    if in_quiet_hours(now, config.quiet_start, config.quiet_end):
        return GateDecision(False, "quiet_hours")
    if privacy_state == "private":
        return GateDecision(False, "private_foreground")
    if (
        idle_seconds is not None
        and config.max_idle_seconds
        and idle_seconds > config.max_idle_seconds
    ):
        return GateDecision(False, "user_away")
    if (
        last_user_message_at > 0
        and now.timestamp() - last_user_message_at
        < config.min_user_silence_minutes * 60
    ):
        return GateDecision(False, "recent_user_activity")

    rows = [dict(item) for item in history]
    delivered = [
        item for item in rows if item.get("mode") == "live" and item.get("submitted")
    ]
    today = now.astimezone().date().isoformat()
    if (
        sum(str(item.get("local_date")) == today for item in delivered)
        >= config.daily_limit
    ):
        return GateDecision(False, "daily_limit")
    if delivered:
        elapsed = now.timestamp() - float(delivered[-1].get("timestamp", 0.0))
        if elapsed < config.min_interval_minutes * 60:
            return GateDecision(False, "minimum_interval")

    # Consecutive ignores trigger a one-day cooling-off period, not a permanent
    # lockout that could only be cleared by a recommendation we refuse to send.
    if (
        delivered
        and now.timestamp() - float(delivered[-1].get("timestamp", 0.0)) < 86400
    ):
        ignored = 0
        for item in reversed(delivered):
            if item.get("outcome") not in {"ignored", "rejected"}:
                break
            ignored += 1
        if ignored >= config.max_consecutive_ignored:
            return GateDecision(False, "ignored_streak")
    return GateDecision(True, "allowed")
