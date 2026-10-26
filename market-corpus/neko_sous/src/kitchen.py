"""Keep short kitchen reminders relevant to the objects that triggered them."""
from __future__ import annotations

import math
from time import monotonic
from typing import Any, NamedTuple, cast

EventData = dict[str, Any]
EventKey = tuple[str, int, int]


class PendingCue(NamedTuple):
    received: float
    event: EventData

MIXING_EVENTS = {"mixing_ready": "mixing_ready", "mixing_warning": "mixing_warning", "mixing_ruined": "mixing_ruined"}
KITCHEN_EVENTS = {"fire_started": "fire", "food_ready": "ready", "food_overcooking": "overcooking", **MIXING_EVENTS}
READY_EVENTS = {"food_ready", "mixing_ready"}


def valid_object(item: EventData, *, fire: bool = False, mixing: bool = False) -> bool:
    if not isinstance(item, dict) or type(item.get("object_id")) is not int:
        return False
    if type(item.get("cycle")) is not int or item["cycle"] < 0:
        return False
    if fire:
        return type(item.get("on_fire")) is bool
    if type(item.get("on_mixer" if mixing else "on_heat")) is not bool:
        return False
    if mixing and type(item.get("mixer_running")) is not bool:
        return False
    prefix = "mixing" if mixing else "cooking"
    state = item.get(f"{prefix}_state")
    if not isinstance(state, str) or state not in {"idle", "progressing", "completed", "overdoing", "ruined"}:
        return False
    for key in (f"{prefix}_progress", f"{prefix}_time", "progress_ratio"):
        value = item.get(key)
        if type(value) not in (int, float):
            return False
        number = cast(int | float, value)
        if not math.isfinite(number) or number < 0:
            return False
    return item[f"{prefix}_time"] > 0


class KitchenFeedback:
    _pending: dict[EventKey, PendingCue]

    def __init__(self) -> None:
        self.enabled = dict.fromkeys(KITCHEN_EVENTS.values(), True)
        self.clear()

    def clear(self) -> None:
        self._pending = {}

    def configure(self, enabled: dict[str, bool]) -> None:
        self.enabled = dict(enabled)
        self._pending = {key: item for key, item in self._pending.items()
                         if self.enabled[KITCHEN_EVENTS[item.event["event"]]]}

    def add(self, event: EventData) -> None:
        name = event["event"]
        superseded = {"food_overcooking": ("food_ready",), "mixing_warning": ("mixing_ready",),
                      "mixing_ruined": ("mixing_ready", "mixing_warning")}
        for previous in superseded.get(name, ()):
            self._pending.pop((previous, event["object_id"], event["cycle"]), None)
        if not self.enabled[KITCHEN_EVENTS[name]]:
            return
        key = (name, event["object_id"], event["cycle"])
        self._pending.setdefault(key, PendingCue(monotonic(), dict(event)))

    def update_state(self, state: EventData) -> None:
        for key, cue in list(self._pending.items()):
            event = cue.event
            if state.get("round_id") != event["round_id"] or state.get("in_round") is not True:
                self._pending.pop(key, None)
                continue
            fire = event["event"] == "fire_started"
            mixing = event["event"] in MIXING_EVENTS
            prefix = "mixing" if mixing else "cooking"
            collection = "fires" if fire else prefix
            if collection not in state:
                continue
            objects = state[collection]
            if not isinstance(objects, list) or any(not valid_object(item, fire=fire, mixing=mixing) for item in objects):
                continue
            current: EventData | None = None
            for item in objects:
                if item["object_id"] == event["object_id"]:
                    current = item
                    break
            if current is None:
                self._pending.pop(key, None)
                continue
            if current["cycle"] < event["cycle"]:
                continue
            if (current["cycle"] == event["cycle"] and not fire
                    and current[f"{prefix}_progress"] + .01 < event[f"{prefix}_progress"]):
                continue  # A snapshot queued before this event is not a cancellation.
            relevant = current["cycle"] == event["cycle"]
            if fire:
                relevant = relevant and current["on_fire"]
            else:
                expected = ("ruined" if event["event"] == "mixing_ruined" else
                            "overdoing" if event["event"] in {"food_overcooking", "mixing_warning"} else "completed")
                relevant = relevant and current[f"{prefix}_state"] == expected
                if event["event"] != "mixing_ruined":
                    relevant = relevant and current["on_mixer" if mixing else "on_heat"]
                if event["event"] == "mixing_warning":
                    relevant = relevant and current["mixer_running"]
            if not relevant:
                self._pending.pop(key, None)

    def has_urgent(self) -> bool:
        self._prune()
        return any(cue.event["event"] not in READY_EVENTS for cue in self._pending.values())

    def has_pending(self) -> bool:
        self._prune()
        return bool(self._pending)

    def _prune(self) -> None:
        now = monotonic()
        self._pending = {key: item for key, item in self._pending.items()
                         if now - item.received <= (5 if item.event["event"] in {*READY_EVENTS, "mixing_ruined"} else 2)}

    def pop(self, *, urgent: bool) -> EventData | None:
        self._prune()
        now = monotonic()
        for name in (("fire_started", "food_overcooking", "mixing_warning", "mixing_ruined")
                     if urgent else ("food_ready", "mixing_ready")):
            group: list[tuple[EventKey, PendingCue]] = [
                (key, cue) for key, cue in self._pending.items() if cue.event["event"] == name
            ]
            if not group or not any(now - cue.received >= (.3 if urgent else .8) for _, cue in group):
                continue
            event = group[0][1].event
            for key, _ in group:
                self._pending.pop(key)
            return dict(event, kitchen_count=len(group))
        return None
