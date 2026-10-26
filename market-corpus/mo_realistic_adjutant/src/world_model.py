"""Persistent, thread-safe battlefield model built from native bridge snapshots."""

from __future__ import annotations

import json
import math
import threading
import time
from collections import Counter, deque
from copy import deepcopy
from pathlib import Path
from typing import Any


TERMINAL_ENTITY_SECONDS = 15.0
DEFAULT_MEMORY_SECONDS = 120.0
MAX_LOG_BYTES = 8 * 1024 * 1024


class WorldModel:
    """Turn isolated bridge frames into explicit, queryable battlefield memory.

    Remembered entities are never presented as currently visible. Consumers can use
    ``observed`` and ``confidence`` to distinguish live telemetry from stale memory.
    """

    def __init__(
        self,
        *,
        history_size: int = 256,
        entity_memory_seconds: float = DEFAULT_MEMORY_SECONDS,
        memory_budget_bytes: int = 256 * 1024 * 1024,
        log_path: str | Path | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._history_limit = max(32, int(history_size))
        self._memory_budget_bytes = max(8 * 1024 * 1024, int(memory_budget_bytes))
        self._history: deque[dict[str, Any]] = deque()
        self._history_sizes: deque[int] = deque()
        self._history_bytes = 0
        self._entity_memory_seconds = max(10.0, float(entity_memory_seconds))
        self._log_path = Path(log_path) if log_path else None
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._revision = 0
            self._updated_at: float | None = None
            self._source_frame: int | None = None
            self._session_started_at: float | None = None
            self._running = False
            self._entities: dict[int, dict[str, Any]] = {}
            self._seen_alert_ids: set[int] = set()
            self._economy: dict[str, Any] = {}
            self._production: dict[str, Any] = {}
            self._markers: list[dict[str, Any]] = []
            self._map: dict[str, Any] = {}
            self._player: dict[str, Any] = {}
            self._groups: dict[int, dict[str, Any]] = {}
            self._threats: list[dict[str, Any]] = []
            self._telemetry: dict[str, Any] = {}
            self._history.clear()
            self._history_sizes.clear()
            self._history_bytes = 0

    def update(self, state: dict[str, Any], *, observed_at: float | None = None) -> dict[str, Any]:
        if not isinstance(state, dict):
            raise TypeError("world model state must be a dictionary")
        now = float(observed_at if observed_at is not None else time.time())
        game = state.get("game") if isinstance(state.get("game"), dict) else {}
        running = game.get("running") is True
        frame = game.get("frame") if isinstance(game.get("frame"), int) else None

        with self._lock:
            if self._is_new_session(running, frame):
                self._reset_for_new_session(now)
            self._revision += 1
            self._updated_at = now
            self._source_frame = frame
            self._running = running
            if running and self._session_started_at is None:
                self._session_started_at = now

            events: list[dict[str, Any]] = []
            if running:
                self._update_entities(state, now, events)
                self._update_economy(state, now, events)
                self._update_alerts(state, now, events)
                self._update_production(state, now, events)
                self._update_markers(state, now, events)
                self._map = self._safe_mapping(state.get("map"))
                self._player = self._safe_mapping(state.get("player"))
                self._groups = self._build_groups()
                self._threats = self._build_threats()
            else:
                self._mark_all_unobserved(now, events)

            feed = state.get("entity_feed") if isinstance(state.get("entity_feed"), dict) else {}
            production = state.get("production") if isinstance(state.get("production"), dict) else {}
            self._telemetry = {
                "snapshot_age_ms": state.get("snapshot_age_ms"),
                "entity_count": feed.get("count"),
                "entity_capacity": feed.get("capacity"),
                "entity_feed_truncated": feed.get("truncated") is True,
                "production_available": production.get("available") is True,
                "markers_available": state.get("marker_telemetry_available") is True,
            }
            self._record(events)
            return self.summary()

    def summary(self) -> dict[str, Any]:
        with self._lock:
            observed = [
                item for item in self._entities.values()
                if item.get("observed") is True and item.get("alive") is True
            ]
            force_counts: dict[str, dict[str, int]] = {}
            for relation in ("self", "ally", "enemy", "neutral", "unknown"):
                matching = [item for item in observed if item.get("relation") == relation]
                force_counts[relation] = dict(Counter(str(item.get("kind") or "unknown") for item in matching))
                force_counts[relation]["total"] = len(matching)
            return {
                "revision": self._revision,
                "updated_at": self._updated_at,
                "source_frame": self._source_frame,
                "running": self._running,
                "session_started_at": self._session_started_at,
                "telemetry": deepcopy(self._telemetry),
                "forces": force_counts,
                "economy": deepcopy(self._economy),
                "production": deepcopy(self._production),
                "groups": [deepcopy(self._groups[key]) for key in sorted(self._groups)],
                "markers": deepcopy(self._markers),
                "threats": deepcopy(self._threats),
                "remembered_entity_count": len(self._entities),
                "recent_events": list(deepcopy(self._history))[-20:],
                "buffer": {
                    "event_count": len(self._history),
                    "estimated_bytes": self._history_bytes,
                    "budget_bytes": self._memory_budget_bytes,
                },
            }

    def view(
        self,
        *,
        relation: str | None = None,
        kind: str | None = None,
        group_number: int | None = None,
        visible_only: bool = False,
        entity_limit: int = 120,
        event_limit: int = 30,
    ) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            entities = []
            for source in self._entities.values():
                if relation and source.get("relation") != relation:
                    continue
                if kind and source.get("kind") != kind:
                    continue
                if group_number is not None and source.get("group_number") != group_number:
                    continue
                if visible_only and source.get("observed") is not True:
                    continue
                item = deepcopy(source)
                stale = max(0.0, now - float(item.get("last_seen_at", now)))
                item["stale_seconds"] = round(stale, 2)
                item["confidence"] = self._confidence(item, stale)
                entities.append(item)
            entities.sort(key=lambda item: (item.get("observed") is not True, str(item.get("relation")), int(item.get("object_id", 0))))
            result = self.summary()
            result["entities"] = entities[: max(1, min(int(entity_limit), 512))]
            result["entity_result_count"] = len(entities)
            result["recent_events"] = list(deepcopy(self._history))[-max(0, min(int(event_limit), 256)):]
            return result

    def augment(self, state: dict[str, Any]) -> dict[str, Any]:
        """Attach a compact model view while preserving the native snapshot contract."""
        state["world_model"] = self.summary()
        return state

    def _is_new_session(self, running: bool, frame: int | None) -> bool:
        if running and not self._running:
            return self._revision > 0
        return bool(running and frame is not None and self._source_frame is not None and frame < self._source_frame)

    def _reset_for_new_session(self, now: float) -> None:
        self._entities.clear()
        self._seen_alert_ids.clear()
        self._economy.clear()
        self._production.clear()
        self._markers.clear()
        self._groups.clear()
        self._threats.clear()
        self._history.clear()
        self._history_sizes.clear()
        self._history_bytes = 0
        self._session_started_at = now

    def _update_entities(self, state: dict[str, Any], now: float, events: list[dict[str, Any]]) -> None:
        raw_entities = state.get("visible_entities")
        current: dict[int, dict[str, Any]] = {}
        if isinstance(raw_entities, list):
            for raw in raw_entities:
                if not isinstance(raw, dict) or not isinstance(raw.get("object_id"), int):
                    continue
                object_id = int(raw["object_id"])
                # Never refresh enemy memory from a hidden native record. This keeps
                # remembered positions useful for planning without granting map hacks.
                if raw.get("visible") is not True and raw.get("relation") not in {"self", "ally"}:
                    continue
                item = deepcopy(raw)
                item["observed"] = True
                item["first_seen_at"] = now
                item["last_seen_at"] = now
                current[object_id] = item

        previous_ids = set(self._entities)
        current_ids = set(current)
        for object_id, item in current.items():
            previous = self._entities.get(object_id)
            if previous is None:
                self._emit(events, now, "entity_spotted", object_id, item)
            else:
                item["first_seen_at"] = previous.get("first_seen_at", now)
                self._compare_entity(previous, item, now, events)
            self._entities[object_id] = item

        for object_id in previous_ids - current_ids:
            item = self._entities[object_id]
            if item.get("observed") is True:
                item["observed"] = False
                item["visible"] = False
                self._emit(events, now, "entity_lost_visibility", object_id, item)

        for object_id, item in list(self._entities.items()):
            stale = now - float(item.get("last_seen_at", now))
            ttl = TERMINAL_ENTITY_SECONDS if item.get("alive") is False else self._entity_memory_seconds
            if stale > ttl:
                self._entities.pop(object_id, None)

    def _compare_entity(
        self, previous: dict[str, Any], current: dict[str, Any], now: float,
        events: list[dict[str, Any]],
    ) -> None:
        object_id = int(current["object_id"])
        if previous.get("alive") is True and current.get("alive") is False:
            self._emit(events, now, "entity_destroyed", object_id, current)
        old_health = previous.get("health_ratio")
        new_health = current.get("health_ratio")
        if isinstance(old_health, (int, float)) and isinstance(new_health, (int, float)) and old_health - new_health >= 0.08:
            self._emit(events, now, "entity_damaged", object_id, current, health_delta=round(new_health - old_health, 4))
        if previous.get("group_number") != current.get("group_number"):
            self._emit(events, now, "group_changed", object_id, current, previous_group=previous.get("group_number"))
        old_cell, new_cell = previous.get("cell"), current.get("cell")
        moved_distance = self._cell_distance_sq(old_cell, new_cell)
        if math.isfinite(moved_distance) and moved_distance >= 16:
            self._emit(events, now, "entity_moved", object_id, current, previous_cell=old_cell)
        if previous.get("mission_id") != current.get("mission_id"):
            self._emit(events, now, "mission_changed", object_id, current, previous_mission=previous.get("mission_id"))

    def _update_economy(self, state: dict[str, Any], now: float, events: list[dict[str, Any]]) -> None:
        player = state.get("player") if isinstance(state.get("player"), dict) else {}
        economy = self._safe_mapping(state.get("economy"))
        if isinstance(player.get("credits"), int):
            economy["credits"] = player["credits"]
        old_credits = self._economy.get("credits")
        new_credits = economy.get("credits")
        if isinstance(old_credits, int) and isinstance(new_credits, int) and old_credits != new_credits:
            self._emit(events, now, "credits_changed", None, None, delta=new_credits - old_credits, credits=new_credits)
        for key in ("power_state", "low_power", "power_output", "power_drain"):
            if key in economy and key in self._economy and economy[key] != self._economy[key]:
                self._emit(events, now, "economy_state_changed", None, None, field=key, previous=self._economy[key], current=economy[key])
        self._economy = economy

    def _update_alerts(self, state: dict[str, Any], now: float, events: list[dict[str, Any]]) -> None:
        alerts = state.get("alerts")
        if not isinstance(alerts, list):
            return
        for alert in alerts:
            if not isinstance(alert, dict) or not isinstance(alert.get("event_id"), int):
                continue
            event_id = int(alert["event_id"])
            if event_id in self._seen_alert_ids:
                continue
            self._seen_alert_ids.add(event_id)
            self._emit(events, now, "game_alert", alert.get("object_id"), alert, alert=deepcopy(alert))
        if len(self._seen_alert_ids) > 4096:
            self._seen_alert_ids = set(sorted(self._seen_alert_ids)[-2048:])

    def _update_production(self, state: dict[str, Any], now: float, events: list[dict[str, Any]]) -> None:
        production = self._safe_mapping(state.get("production"))
        before = self._production_signature(self._production)
        after = self._production_signature(production)
        if self._production and before != after:
            self._emit(
                events, now, "production_changed", None, None,
                previous=before, current=after,
            )
        self._production = production

    def _update_markers(self, state: dict[str, Any], now: float, events: list[dict[str, Any]]) -> None:
        markers = self._safe_list(state.get("markers"))
        before = self._stable_signature(self._markers)
        after = self._stable_signature(markers)
        if self._markers and before != after:
            self._emit(events, now, "markers_changed", None, None, previous=before, current=after)
        self._markers = markers

    def _build_groups(self) -> dict[int, dict[str, Any]]:
        groups: dict[int, list[dict[str, Any]]] = {}
        for item in self._entities.values():
            number = item.get("group_number")
            if item.get("observed") is not True or item.get("relation") != "self" or not isinstance(number, int) or number < 0:
                continue
            groups.setdefault(number, []).append(item)
        return {
            number: {
                "group_number": number,
                "unit_count": len(items),
                "object_ids": [int(item["object_id"]) for item in items],
                "kinds": dict(Counter(str(item.get("kind") or "unknown") for item in items)),
                "average_health": round(sum(float(item.get("health_ratio", 0.0)) for item in items) / len(items), 3),
                "cell": self._centroid(items),
            }
            for number, items in groups.items()
        }

    def _build_threats(self) -> list[dict[str, Any]]:
        own = [item for item in self._entities.values() if item.get("observed") is True and item.get("relation") == "self" and item.get("alive") is True]
        enemies = [item for item in self._entities.values() if item.get("observed") is True and item.get("relation") == "enemy" and item.get("alive") is True]
        anchors = [
            item for item in own
            if item.get("kind") == "building" and isinstance(item.get("cell"), dict)
        ] or [item for item in own if isinstance(item.get("cell"), dict)]
        result = []
        for enemy in enemies:
            distance = None
            if anchors:
                distance = math.sqrt(min(self._cell_distance_sq(enemy.get("cell"), item.get("cell")) for item in anchors))
            result.append({
                "object_id": enemy.get("object_id"),
                "type_id": enemy.get("type_id"),
                "display_name": enemy.get("display_name"),
                "kind": enemy.get("kind"),
                "cell": deepcopy(enemy.get("cell")),
                "health_ratio": enemy.get("health_ratio"),
                "distance_to_owned_anchor_cells": round(distance, 1) if distance is not None else None,
            })
        result.sort(key=lambda item: (item["distance_to_owned_anchor_cells"] is None, item["distance_to_owned_anchor_cells"] or 0.0))
        return result[:32]

    def _mark_all_unobserved(self, now: float, events: list[dict[str, Any]]) -> None:
        for object_id, item in self._entities.items():
            if item.get("observed") is True:
                item["observed"] = False
                item["visible"] = False
                self._emit(events, now, "entity_lost_visibility", object_id, item)

    def _emit(
        self, events: list[dict[str, Any]], at: float, kind: str,
        object_id: Any, entity: dict[str, Any] | None, **details: Any,
    ) -> None:
        event = {
            "event_id": f"wm-{self._revision}-{len(events) + 1}",
            "revision": self._revision,
            "at": at,
            "kind": kind,
        }
        if isinstance(object_id, int):
            event["object_id"] = object_id
        if isinstance(entity, dict):
            event.update({
                "relation": entity.get("relation"),
                "entity_kind": entity.get("kind") or entity.get("object_kind"),
                "type_id": entity.get("type_id"),
                "display_name": entity.get("display_name"),
                "cell": deepcopy(entity.get("cell")),
            })
        event.update(details)
        events.append(event)

    def _record(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            encoded_size = len(json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            estimated_size = max(128, encoded_size * 4)
            self._history.append(event)
            self._history_sizes.append(estimated_size)
            self._history_bytes += estimated_size
            while (
                len(self._history) > self._history_limit
                or self._history_bytes > self._memory_budget_bytes
            ):
                self._history.popleft()
                self._history_bytes -= self._history_sizes.popleft()
        if not events or self._log_path is None:
            return
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            if self._log_path.exists() and self._log_path.stat().st_size >= MAX_LOG_BYTES:
                rotated = self._log_path.with_suffix(self._log_path.suffix + ".1")
                if rotated.exists():
                    rotated.unlink()
                self._log_path.replace(rotated)
            with self._log_path.open("a", encoding="utf-8", newline="\n") as handle:
                for event in events:
                    handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError:
            # The world model remains usable when an install directory is read-only.
            pass

    @staticmethod
    def _safe_mapping(value: Any) -> dict[str, Any]:
        return deepcopy(value) if isinstance(value, dict) else {}

    @staticmethod
    def _safe_list(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [deepcopy(item) for item in value if isinstance(item, dict)]

    @staticmethod
    def _stable_signature(value: Any) -> Any:
        """Return a compact, deterministic value suitable for change events."""
        if isinstance(value, dict):
            ignored = {"snapshot_age_ms", "updated_at", "frame"}
            return {
                str(key): WorldModel._stable_signature(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
                if key not in ignored
            }
        if isinstance(value, list):
            return [WorldModel._stable_signature(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    @staticmethod
    def _production_signature(production: dict[str, Any]) -> dict[str, Any]:
        queue = []
        for item in production.get("queue", []):
            if not isinstance(item, dict):
                continue
            progress = item.get("progress_ratio")
            queue.append({
                "type_id": item.get("type_id"),
                "kind": item.get("kind"),
                "queued_count": item.get("queued_count"),
                "progress_quarter": (
                    min(4, max(0, int(float(progress) * 4)))
                    if isinstance(progress, (int, float)) else None
                ),
                "on_hold": item.get("on_hold") is True,
            })
        sidebar = [
            item.get("type_id") for item in production.get("sidebar_available", [])
            if isinstance(item, dict) and item.get("type_id")
        ]
        owned = production.get("owned_counts")
        return {
            "available": production.get("available") is True,
            "queue": queue,
            "buildable_type_ids": sidebar,
            "owned_counts": deepcopy(owned) if isinstance(owned, dict) else {},
        }

    @staticmethod
    def _cell_distance_sq(left: Any, right: Any) -> float:
        if not isinstance(left, dict) or not isinstance(right, dict):
            return math.inf
        try:
            return float(left["x"] - right["x"]) ** 2 + float(left["y"] - right["y"]) ** 2
        except (KeyError, TypeError, ValueError):
            return math.inf

    @staticmethod
    def _centroid(items: list[dict[str, Any]]) -> dict[str, int] | None:
        cells = [item.get("cell") for item in items if isinstance(item.get("cell"), dict)]
        if not cells:
            return None
        return {
            "x": round(sum(int(cell.get("x", 0)) for cell in cells) / len(cells)),
            "y": round(sum(int(cell.get("y", 0)) for cell in cells) / len(cells)),
        }

    def _confidence(self, item: dict[str, Any], stale: float) -> float:
        if item.get("observed") is True:
            return 1.0
        return round(max(0.0, 1.0 - stale / self._entity_memory_seconds), 3)
