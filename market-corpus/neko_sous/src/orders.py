"""Pick a few delivery reactions instead of announcing every plate."""
from __future__ import annotations

from collections import deque
from time import monotonic

from .kitchen import KITCHEN_EVENTS

CATEGORIES = ("delivery", "expiry", "failure", "combo", *KITCHEN_EVENTS.values())


def category(event: dict) -> str | None:
    name = event["event"]
    if name in KITCHEN_EVENTS:
        return KITCHEN_EVENTS[name]
    if name in {"first_delivery", "delivery_streak"}:
        return "delivery"
    if name == "combo_broken":
        return "combo"
    if name == "order_problem":
        return "expiry" if event.get("expired_count") else "failure"
    return None


def milestone(count: int) -> bool:
    return count in (3, 6, 10) or (count > 10 and (count - 10) % 4 == 0)


ORDER_EVENTS = {"order_delivered", "delivery_failed", "order_expired"}


class OrderFeedback:
    _deliveries: deque[dict]
    _expired: deque[dict]
    _milestones: set[tuple[str, str | int]]
    _last_spoken: float
    _last_break: float
    _pending: dict | None
    _due: float
    _expires: float

    def __init__(self) -> None:
        self.enabled = dict.fromkeys(CATEGORIES, True)
        self.reset()

    def configure(self, enabled: dict[str, bool]) -> None:
        self.enabled = dict(enabled)
        if not self.enabled["expiry"]:
            self._expired.clear()
        if not self.enabled["delivery"]:
            self._deliveries.clear()
        if self._pending:
            kind = category(self._pending)
            if kind is not None and not self.enabled[kind]:
                self._pending = None

    def reset(self) -> None:
        self._deliveries = deque()
        self._expired = deque()
        self._milestones = set()
        self._last_spoken = -1000.0
        self._last_break = -1000.0
        self.clear_pending()

    def clear_pending(self, *, keep_required: bool = False) -> None:
        if not keep_required:
            self._expired.clear()
            self._deliveries.clear()
        self._pending = None
        self._due = 0.0
        self._expires = 0.0

    def add(self, event: dict) -> None:
        if event["event"] == "order_expired":
            team = event["team"]
            self._milestones = {item for item in self._milestones if item[0] != team or item[1] == "first"}
            if self.enabled["expiry"]:
                self._expired.append(dict(event, event="order_problem", failed_count=0, expired_count=1))
            return
        now = monotonic()
        team = event["team"]
        name = event["event"]
        cue = dict(event)
        if name == "order_delivered":
            count = event["delivery_streak"]
            if event["successful_deliveries"] == 1:
                marker = (team, "first")
                cue["event"] = "first_delivery"
            elif milestone(count):
                marker = (team, count)
                cue["event"] = "delivery_streak"
            else:
                marker = None
            if marker is not None and marker not in self._milestones:
                self._milestones.add(marker)
                if self.enabled["delivery"]:
                    self._deliveries.append(cue)
            if event.get("combo_broken") is not True or not self.enabled["combo"]:
                return
            if event.get("remaining_seconds") is not None and event["remaining_seconds"] <= 35:
                return
            if self._pending and self._pending["event"] == "order_problem" and now <= self._expires:
                return
            cue = dict(event, event="combo_broken")
            self._due = now
        else:
            self._milestones = {item for item in self._milestones if item[0] != team or item[1] == "first"}
            if not self.enabled["failure"]:
                return
            if event.get("remaining_seconds") is not None and event["remaining_seconds"] <= 35:
                return
            cue["event"] = "order_problem"
            cue["failed_count"] = 1 if name == "delivery_failed" else 0
            cue["expired_count"] = 1 if name == "order_expired" else 0
            if (self._pending and self._pending["event"] == "order_problem"
                    and self._pending["team"] == team and now <= self._due):
                cue["failed_count"] += self._pending["failed_count"]
                cue["expired_count"] += self._pending["expired_count"]
                self._pending = cue
                return
            self._due = now + 2
        self._pending = cue
        self._expires = now + 5

    def pop(self) -> dict | None:
        if self._expired:
            self._last_spoken = monotonic()
            return self._expired.popleft()
        if self._deliveries:
            self._last_spoken = monotonic()
            return self._deliveries.popleft()
        if self._pending is None:
            return None
        now = monotonic()
        if now > self._expires:
            self.clear_pending()
            return None
        last = self._last_break if self._pending["event"] == "combo_broken" else self._last_spoken
        if now < self._due or now - last < 20:
            return None
        event = self._pending
        self.clear_pending()
        self._last_spoken = now
        if event["event"] == "combo_broken":
            self._last_break = now
        return event
