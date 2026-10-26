from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class CombatExperimentLog:
    """Evidence log for bounded force-composition trials using visible information only."""

    def __init__(self, path: Path, *, disengage_seconds: float = 15.0):
        self.path = path
        self.disengage_seconds = disengage_seconds
        self.active: dict[str, Any] | None = None
        self.last_enemy_seen_at: float | None = None
        self.completed = 0

    @property
    def variation_index(self) -> int:
        return self.completed % 4

    def reset(self) -> None:
        self.active = None
        self.last_enemy_seen_at = None

    def observe(self, state: dict[str, Any], *, now: float | None = None) -> None:
        now = time.time() if now is None else float(now)
        game = state.get("game") if isinstance(state.get("game"), dict) else {}
        if game.get("running") is not True:
            self.reset()
            return
        own = self._counts(state, "self")
        enemies = self._counts(state, "enemy")
        if enemies:
            self.last_enemy_seen_at = now
            if self.active is None:
                self.active = {
                    "started_at": now,
                    "source_frame": game.get("frame"),
                    "variation_index": self.variation_index,
                    "own_start": own,
                    "visible_enemy_start": enemies,
                }
                self._append("engagement_started", self.active)
            return
        if self.active is None or self.last_enemy_seen_at is None:
            return
        if now - self.last_enemy_seen_at < self.disengage_seconds:
            return
        own_start = self.active.get("own_start", {})
        payload = {
            **self.active,
            "ended_at": now,
            "own_end": own,
            "own_losses": {
                type_id: max(0, int(count) - int(own.get(type_id, 0)))
                for type_id, count in own_start.items()
                if int(count) > int(own.get(type_id, 0))
            },
            "result": "enemy_no_longer_visible",
            "result_limit": "visibility loss is not proof of destruction or victory",
            "next_variation_index": (self.variation_index + 1) % 4,
        }
        self._append("engagement_ended", payload)
        self.completed += 1
        self.active = None
        self.last_enemy_seen_at = None

    def record_decision(self, decision: dict[str, Any]) -> None:
        self._append("production_decision", decision)

    def record_strategy(self, snapshot: dict[str, Any]) -> None:
        self._append("strategic_phase", snapshot)

    def record_contact_decision(self, decision: dict[str, Any]) -> None:
        self._append("contact_defense_decision", decision)

    @staticmethod
    def _counts(state: dict[str, Any], relation: str) -> dict[str, int]:
        result: dict[str, int] = {}
        for item in state.get("visible_entities", []):
            if not isinstance(item, dict) or item.get("visible") is not True:
                continue
            if item.get("relation") != relation or item.get("alive") is not True:
                continue
            if item.get("kind") not in {"infantry", "vehicle", "aircraft"}:
                continue
            type_id = str(item.get("type_id", "")).upper()
            if type_id:
                result[type_id] = result.get(type_id, 0) + 1
        return result

    def _append(self, event: str, payload: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            row = {"event": event, "logged_at": time.time(), **payload}
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        except (OSError, TypeError, ValueError):
            pass
