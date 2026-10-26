"""主动性：不等指令，自己找话。"""

from __future__ import annotations

import time
from datetime import date
from typing import Any, Dict

WAIT_URGE_RATE = 0.04
POST_RESULT_URGE = 0.25
SILENCE_WINDOW = 20.0
SAME_EVENT_DEDUP = 60.0
DAILY_INVITE_LIMIT = 3


class ProactiveEngine:
    """主动说话引擎。"""

    def __init__(self) -> None:
        self._urge = 0.0
        self._last_talk_ts = 0.0
        self._last_owner_ts = time.time()
        self._last_event_key = ""
        self._last_event_ts = 0.0
        self._invite_today = 0
        self._last_invite_date = ""

    def on_owner_speak(self) -> None:
        self._last_owner_ts = time.time()
        self._urge = 0.0

    def on_result(self, outcome: str) -> None:
        self._urge = min(1.0, self._urge + POST_RESULT_URGE)
        if outcome and outcome == self._last_event_key and time.time() - self._last_event_ts < SAME_EVENT_DEDUP:
            self._urge = max(0.0, self._urge - 0.2)
        else:
            self._last_event_key = outcome
            self._last_event_ts = time.time()

    def on_wait(self, seconds: float) -> None:
        self._urge = min(1.0, self._urge + WAIT_URGE_RATE * seconds)

    def tick(self) -> None:
        self._urge *= 0.98

    @property
    def ready_to_speak(self) -> bool:
        if self._urge < 0.5:
            return False
        if time.time() - self._last_owner_ts < SILENCE_WINDOW:
            return False
        if time.time() - self._last_talk_ts < 3.0:
            return False
        return True

    def mark_spoke(self) -> None:
        self._last_talk_ts = time.time()
        self._urge = 0.0

    async def should_invite(self) -> bool:
        today = date.today().isoformat()
        if self._last_invite_date != today:
            self._invite_today = 0
            self._last_invite_date = today
        if self._invite_today >= DAILY_INVITE_LIMIT:
            return False
        if self._last_talk_ts and time.time() - self._last_talk_ts < 7200:
            return False
        return True

    def mark_invited(self) -> None:
        self._invite_today += 1
        self._last_talk_ts = time.time()

    def snapshot(self) -> Dict[str, Any]:
        return {"urge": round(self._urge, 2), "ready": self.ready_to_speak}