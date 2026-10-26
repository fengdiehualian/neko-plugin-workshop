"""Validate game events and keep only announcements that are still relevant."""
from __future__ import annotations

import asyncio
import math
from collections import deque
from time import monotonic
from typing import cast

from .kitchen import KITCHEN_EVENTS, MIXING_EVENTS, KitchenFeedback, valid_object
from .orders import ORDER_EVENTS, OrderFeedback


class GameEvents:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[tuple[float, dict]] = asyncio.Queue(maxsize=16)
        self._seen: deque[str] = deque(maxlen=256)
        self._retired: deque[str] = deque(maxlen=64)
        self._scene = ""
        self._round = ""
        self._stages: set[tuple[str, int | None]] = set()
        self._finished = False
        self._orders = OrderFeedback()
        self._kitchen = KitchenFeedback()

    def configure(self, enabled: dict[str, bool]) -> None:
        self._orders.configure(enabled)
        self._kitchen.configure(enabled)

    def kitchen_pending(self) -> bool:
        return self._kitchen.has_pending()

    def clear(self) -> None:
        self._orders.reset()
        self._kitchen.clear()
        while not self.queue.empty():
            self.queue.get_nowait()

    def update_state(self, state: dict) -> None:
        self._kitchen.update_state(state)
        if state.get("in_round") is True and state.get("phase") == "playing":
            return
        self._orders.clear_pending()
        keep = []
        while not self.queue.empty():
            item = self.queue.get_nowait()
            event = item[1]
            if event["event"] == "round_finished" and event.get("round_id") == state.get("round_id"):
                keep.append(item)
        for item in keep:
            self.queue.put_nowait(item)

    @staticmethod
    def valid(payload: dict) -> bool:
        name = payload.get("event")
        if not isinstance(name, str) or name not in {"scene_ready", "round_started", "time_warning", "round_finished", *ORDER_EVENTS, *KITCHEN_EVENTS}:
            return False
        for key in ("event_id", "scene_name"):
            if not isinstance(payload.get(key), str) or not 0 < len(payload[key]) <= 256:
                return False
        if payload.get("round_id") is not None and not isinstance(payload["round_id"], str):
            return False
        if name != "scene_ready" and (not isinstance(payload.get("round_id"), str)
                                      or not 0 < len(payload["round_id"]) <= 128):
            return False
        for key in ("duration_seconds", "remaining_seconds", "elapsed_seconds"):
            value = payload.get(key)
            if value is None:
                continue
            if type(value) not in (int, float):
                return False
            number = cast(int | float, value)
            if not 0 <= number <= 1e9 or not math.isfinite(number):
                return False
        for key in ("score", "other_score", "recipe_count", "stars"):
            value = payload.get(key)
            if value is not None and type(value) is not int:
                return False
        if payload.get("recipe_count") is not None and payload["recipe_count"] < 0:
            return False
        if payload.get("stars") is not None and not 0 <= payload["stars"] <= 4:
            return False
        for key in ("in_round", "timer_suppressed"):
            if key in payload and type(payload[key]) is not bool:
                return False
        if name in ORDER_EVENTS:
            if payload.get("in_round") is not True or payload.get("phase") != "playing":
                return False
            if not isinstance(payload.get("team"), str) or payload["team"] not in {"one", "two"}:
                return False
            for key in ("successful_deliveries", "delivery_streak"):
                if type(payload.get(key)) is not int or payload[key] < 0:
                    return False
            for key in ("order_id", "recipe_id", "tip", "team_score", "score_delta", "combo", "multiplier",
                        "total_expiry_deductions", "deducted_points", "previous_combo", "previous_multiplier"):
                if payload.get(key) is not None and type(payload[key]) is not int:
                    return False
            if payload.get("was_combo") is not None and type(payload["was_combo"]) is not bool:
                return False
            if payload.get("combo_broken") is not None and type(payload["combo_broken"]) is not bool:
                return False
        if name in KITCHEN_EVENTS:
            if payload.get("in_round") is not True or payload.get("phase") != "playing":
                return False
            mixing = name in MIXING_EVENTS
            if not valid_object(payload, fire=name == "fire_started", mixing=mixing):
                return False
            if name == "fire_started" and payload["on_fire"] is not True:
                return False
            if name != "fire_started":
                expected = ({"ruined"} if name == "mixing_ruined" else {"completed", "overdoing"}
                            if name in {"food_ready", "mixing_ready"} else {"overdoing"})
                if payload["mixing_state" if mixing else "cooking_state"] not in expected:
                    return False
                if name != "mixing_ruined" and payload["on_mixer" if mixing else "on_heat"] is not True:
                    return False
        timer = payload.get("timer_kind")
        if timer is not None and (not isinstance(timer, str) or timer not in {"countdown", "modifiable", "unlimited"}):
            return False
        if name == "time_warning":
            threshold = payload.get("threshold_seconds")
            if type(threshold) is not int or threshold not in (10, 30):
                return False
            remaining_value = payload.get("remaining_seconds")
            if remaining_value is None:
                return False
            threshold = cast(int, threshold)
            remaining = cast(int | float, remaining_value)
            if not 0 < remaining <= threshold:
                return False
            if threshold == 30 and remaining <= 10:
                return False
            return payload.get("in_round") is True
        elif payload.get("threshold_seconds") is not None:
            return False
        return True

    def accept(self, payload: dict) -> bool:
        if not self.valid(payload) or payload["event_id"] in self._seen:
            return False
        self._seen.append(payload["event_id"])
        name = payload["event"]
        scene = payload["scene_name"]
        round_id = payload.get("round_id") or ""
        if round_id and round_id in self._retired:
            return False
        if scene != self._scene:
            self.clear()
            if self._round:
                self._retired.append(self._round)
            self._scene, self._round = scene, ""
            self._stages.clear()
            self._finished = False
        if round_id and round_id != self._round:
            self.clear()
            if self._round:
                self._retired.append(self._round)
            self._round = round_id
            self._stages.clear()
            self._finished = False
        stage = (name, payload.get("threshold_seconds"))
        if (name not in {*ORDER_EVENTS, *KITCHEN_EVENTS} and stage in self._stages) or (self._finished and name != "scene_ready"):
            return False
        if name in ORDER_EVENTS:
            self._orders.add(payload)
            return True
        if name in KITCHEN_EVENTS:
            self._kitchen.add(payload)
            return True
        if stage == ("time_warning", 30) and ("time_warning", 10) in self._stages:
            return False
        self._stages.add(stage)
        if name == "time_warning":
            self._orders.clear_pending(keep_required=True)
        if name == "round_finished":
            self._finished = True
            self.clear()
        if self.queue.full():
            self.queue.get_nowait()
        self.queue.put_nowait((monotonic(), dict(payload)))
        return True

    async def next(self) -> dict:
        result: dict | None = None
        while result is None:
            if self.queue.empty():
                feedback = self._kitchen.pop(urgent=True)
                if feedback:
                    result = feedback
                    continue
                if not self._kitchen.has_urgent():
                    order_feedback = self._orders.pop() or self._kitchen.pop(urgent=False)
                    if order_feedback:
                        result = order_feedback
                        continue
            try:
                received, event = await asyncio.wait_for(self.queue.get(), .25)
            except TimeoutError:
                continue
            lifetime = 2 if event["event"] == "time_warning" else 5
            if monotonic() - received <= lifetime:
                result = event
        return result
