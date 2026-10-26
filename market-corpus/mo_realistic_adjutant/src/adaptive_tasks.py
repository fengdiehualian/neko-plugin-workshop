from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import random
import tempfile
import threading
import time
from typing import Any, Callable, Iterable
import uuid

from .adaptive_control import distance_sq, valid_cell
from .building_command import is_valid_attack_target


CommandSender = Callable[..., dict[str, Any]]


MOVE_STRATEGIES: tuple[tuple[str, int, int], ...] = (
    ("west_near", -3, 0),
    ("east_near", 3, 0),
    ("north_near", 0, -3),
    ("south_near", 0, 3),
    ("northwest_near", -3, -3),
    ("northeast_near", 3, -3),
    ("southwest_near", -3, 3),
    ("southeast_near", 3, 3),
    ("west_medium", -6, 0),
    ("east_medium", 6, 0),
    ("north_medium", 0, -6),
    ("south_medium", 0, 6),
    ("northwest_medium", -6, -6),
    ("northeast_medium", 6, -6),
    ("southwest_medium", -6, 6),
    ("southeast_medium", 6, 6),
    ("west_wide", -10, 0),
    ("east_wide", 10, 0),
    ("north_wide", 0, -10),
    ("south_wide", 0, 10),
)


@dataclass(frozen=True)
class StrategyScore:
    strategy: str
    score: float
    attempts: int
    successes: int
    failures: int
    progress_events: int
    reward_ema: float


class AdaptiveLearningStore:
    """Persist bounded, explainable strategy statistics between matches."""

    FORMAT_VERSION = 1
    MAX_CONTEXTS = 256
    MAX_STRATEGIES_PER_CONTEXT = 64

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        clock=time.time,
    ) -> None:
        self.path = Path(path) if path is not None else self.default_path()
        self._clock = clock
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {
            "version": self.FORMAT_VERSION,
            "updated_at": None,
            "contexts": {},
        }
        self._dirty = False
        self._last_save = 0.0
        self.load()

    @staticmethod
    def default_path() -> Path:
        local = os.environ.get("LOCALAPPDATA")
        root = Path(local) if local else Path(tempfile.gettempdir())
        return root / "NekoMOCompanion" / "adaptive_learning.json"

    def load(self) -> None:
        with self._lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                return
            if not isinstance(raw, dict) or raw.get("version") != self.FORMAT_VERSION:
                return
            contexts = raw.get("contexts")
            if not isinstance(contexts, dict):
                return
            cleaned: dict[str, Any] = {}
            for context, entry in list(contexts.items())[-self.MAX_CONTEXTS :]:
                if not isinstance(context, str) or not isinstance(entry, dict):
                    continue
                strategies = entry.get("strategies")
                if not isinstance(strategies, dict):
                    continue
                clean_strategies = {}
                for name, stats in list(strategies.items())[
                    -self.MAX_STRATEGIES_PER_CONTEXT :
                ]:
                    cleaned_stats = self._clean_stats(stats)
                    if isinstance(name, str) and cleaned_stats is not None:
                        clean_strategies[name] = cleaned_stats
                cleaned[context] = {
                    "last_used": self._number(entry.get("last_used"), 0.0),
                    "strategies": clean_strategies,
                }
            self._data = {
                "version": self.FORMAT_VERSION,
                "updated_at": raw.get("updated_at"),
                "contexts": cleaned,
            }
            self._dirty = False

    def score(
        self,
        context: str,
        strategies: Iterable[str],
        *,
        exploration: float = 0.7,
    ) -> list[StrategyScore]:
        with self._lock:
            entry = self._context_locked(context)
            names = list(dict.fromkeys(str(value) for value in strategies))
            total_attempts = sum(
                max(0, int(stats.get("attempts", 0)))
                for stats in entry["strategies"].values()
                if isinstance(stats, dict)
            )
            result = []
            for index, name in enumerate(names):
                stats = entry["strategies"].get(name, self._empty_stats())
                attempts = max(0, int(stats.get("attempts", 0)))
                successes = max(0, int(stats.get("successes", 0)))
                failures = max(0, int(stats.get("failures", 0)))
                progress = max(0, int(stats.get("progress_events", 0)))
                ema = float(stats.get("reward_ema", 0.5))
                if attempts == 0:
                    # Untried directions remain attractive, with a deterministic
                    # tie-break that still rotates across units.
                    value = 2.0 - index * 0.0001
                else:
                    uncertainty = math.sqrt(
                        math.log(max(2, total_attempts + 1)) / attempts
                    )
                    completion_rate = successes / attempts
                    failure_rate = failures / attempts
                    value = (
                        ema
                        + completion_rate * 0.55
                        + min(0.25, progress / attempts * 0.12)
                        - failure_rate * 0.35
                        + float(exploration) * uncertainty
                    )
                result.append(
                    StrategyScore(
                        strategy=name,
                        score=round(value, 6),
                        attempts=attempts,
                        successes=successes,
                        failures=failures,
                        progress_events=progress,
                        reward_ema=round(ema, 6),
                    )
                )
            return sorted(result, key=lambda item: (-item.score, item.strategy))

    def record(
        self,
        context: str,
        strategy: str,
        outcome: str,
        *,
        progress_ratio: float = 0.0,
    ) -> None:
        if outcome not in {"issued", "progress", "success", "failure"}:
            raise ValueError(f"unsupported learning outcome: {outcome}")
        with self._lock:
            entry = self._context_locked(context)
            stats = entry["strategies"].setdefault(strategy, self._empty_stats())
            reward = 0.0
            if outcome == "issued":
                stats["attempts"] += 1
                reward = 0.15
            elif outcome == "progress":
                stats["progress_events"] += 1
                reward = min(0.8, 0.25 + max(0.0, float(progress_ratio)))
            elif outcome == "success":
                stats["successes"] += 1
                reward = 1.0
            else:
                stats["failures"] += 1
                reward = -0.6
            alpha = 0.22
            stats["reward_ema"] = round(
                (1.0 - alpha) * float(stats.get("reward_ema", 0.5))
                + alpha * reward,
                6,
            )
            stats["last_outcome"] = outcome
            stats["last_used"] = self._clock()
            entry["last_used"] = stats["last_used"]
            self._dirty = True

    def save(self, *, force: bool = False) -> bool:
        with self._lock:
            now = self._clock()
            if not self._dirty or (not force and now - self._last_save < 5.0):
                return False
            self._prune_locked()
            self._data["updated_at"] = now
            payload = json.dumps(
                self._data,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_name(
                    f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
                )
                temporary.write_text(payload, encoding="utf-8")
                os.replace(temporary, self.path)
            except OSError:
                return False
            self._dirty = False
            self._last_save = now
            return True

    def summary(self, limit: int = 10) -> dict[str, Any]:
        with self._lock:
            contexts = self._data.get("contexts", {})
            rows = []
            for context, entry in contexts.items():
                for name, stats in entry.get("strategies", {}).items():
                    rows.append(
                        {
                            "context": context,
                            "strategy": name,
                            **dict(stats),
                        }
                    )
            rows.sort(
                key=lambda item: (
                    -int(item.get("successes", 0)),
                    -int(item.get("progress_events", 0)),
                    -float(item.get("last_used", 0)),
                )
            )
            return {
                "path": str(self.path),
                "context_count": len(contexts),
                "strategy_count": len(rows),
                "top_strategies": rows[: max(0, min(int(limit), 100))],
            }

    def _context_locked(self, context: str) -> dict[str, Any]:
        key = str(context)[:240]
        contexts = self._data.setdefault("contexts", {})
        return contexts.setdefault(key, {"last_used": 0.0, "strategies": {}})

    def _prune_locked(self) -> None:
        contexts = self._data.get("contexts", {})
        if len(contexts) <= self.MAX_CONTEXTS:
            return
        ordered = sorted(
            contexts.items(),
            key=lambda pair: float(pair[1].get("last_used", 0.0)),
            reverse=True,
        )
        self._data["contexts"] = dict(ordered[: self.MAX_CONTEXTS])

    @staticmethod
    def _empty_stats() -> dict[str, Any]:
        return {
            "attempts": 0,
            "successes": 0,
            "failures": 0,
            "progress_events": 0,
            "reward_ema": 0.5,
            "last_outcome": None,
            "last_used": 0.0,
        }

    @classmethod
    def _clean_stats(cls, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        result = cls._empty_stats()
        for key in ("attempts", "successes", "failures", "progress_events"):
            raw = value.get(key, 0)
            result[key] = max(0, int(raw)) if isinstance(raw, (int, float)) else 0
        result["reward_ema"] = max(
            -1.0, min(1.0, cls._number(value.get("reward_ema"), 0.5))
        )
        outcome = value.get("last_outcome")
        result["last_outcome"] = outcome if isinstance(outcome, str) else None
        result["last_used"] = cls._number(value.get("last_used"), 0.0)
        return result

    @staticmethod
    def _number(value: Any, default: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return default
        result = float(value)
        return result if math.isfinite(result) else default


class AdaptiveTaskEngine:
    """Persistent closed-loop movement and exploration executor.

    The engine owns goal state, while the plugin remains responsible for policy
    validation and native command submission. `tick` is intentionally synchronous
    because it runs in the plugin's existing background watcher thread.
    """

    ACTIVE_STATUSES = {
        "queued",
        "moving",
        "engaging",
        "exploring",
        "stalled",
        "waiting_for_target",
        "waiting_for_frontier",
        "waiting_for_unit",
        "paused",
    }

    def __init__(
        self,
        command_sender: CommandSender,
        *,
        learning: AdaptiveLearningStore | None = None,
        clock=time.monotonic,
        wall_clock=time.time,
        random_source: random.Random | None = None,
        command_interval: float = 3.0,
        stall_seconds: float = 5.0,
    ) -> None:
        self._send = command_sender
        self.learning = learning or AdaptiveLearningStore()
        self._clock = clock
        self._wall_clock = wall_clock
        self._random = random_source or random.Random()
        self._command_interval = max(0.5, float(command_interval))
        self._stall_seconds = max(1.0, float(stall_seconds))
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._last_frame: int | None = None
        self._scenario_generation = 0

    def create_move_task(
        self,
        units: list[dict[str, Any]],
        target: dict[str, Any],
        *,
        query: str | None = None,
        destination: str | None = None,
        duration_seconds: int = 1800,
        arrival_radius: int = 5,
    ) -> dict[str, Any]:
        if not units:
            raise ValueError("move task requires at least one unit")
        if not valid_cell(target.get("cell")):
            raise ValueError("move task target has no valid cell")
        now = self._clock()
        task_id = uuid.uuid4().hex[:12]
        unit_states = {}
        for slot, unit in enumerate(units):
            object_id = self._object_id(unit)
            cell = unit.get("cell")
            unit_states[str(object_id)] = {
                "object_id": object_id,
                "label": self._label(unit),
                "type_id": str(unit.get("type_id", "")),
                "kind": str(unit.get("kind", "unknown")),
                "slot": slot,
                "status": "queued",
                "last_cell": dict(cell) if valid_cell(cell) else None,
                "last_distance": None,
                "best_distance": None,
                "last_progress_at": now,
                "last_command_at": 0.0,
                "last_seen_at": now,
                "attempts": 0,
                "accepted_orders": 0,
                "failed_orders": 0,
                "progress_events": 0,
                "strategy_cursor": slot,
                "active_strategy": None,
                "active_target_cell": None,
                "last_error": None,
            }
        task = {
            "task_id": task_id,
            "kind": "move_near",
            "query": query,
            "destination": destination or self._label(target),
            "created_at": now,
            "created_wall_time": self._wall_clock(),
            "expires_at": now + max(30, min(int(duration_seconds), 21600)),
            "status": "queued",
            "arrival_radius": max(2, min(int(arrival_radius), 20)),
            "target": self._target_descriptor(target),
            "target_cell": dict(target["cell"]),
            "target_last_seen_at": now,
            "unit_states": unit_states,
            "orders_issued": 0,
            "failed_orders": 0,
            "progress_events": 0,
            "arrived_count": 0,
            "scenario_generation": self._scenario_generation,
            "last_tick_at": None,
            "last_error": None,
        }
        with self._lock:
            self._tasks[task_id] = task
        return self.public_task(task)

    def create_explore_task(
        self,
        units: list[dict[str, Any]],
        *,
        query: str | None = None,
        duration_seconds: int = 3600,
    ) -> dict[str, Any]:
        if not units:
            raise ValueError("exploration task requires at least one unit")
        now = self._clock()
        task_id = uuid.uuid4().hex[:12]
        unit_states = {}
        for slot, unit in enumerate(units):
            object_id = self._object_id(unit)
            cell = unit.get("cell")
            unit_states[str(object_id)] = {
                "object_id": object_id,
                "label": self._label(unit),
                "type_id": str(unit.get("type_id", "")),
                "kind": str(unit.get("kind", "unknown")),
                "slot": slot,
                "status": "queued",
                "last_cell": dict(cell) if valid_cell(cell) else None,
                "leg_start_cell": dict(cell) if valid_cell(cell) else None,
                "last_progress_at": now,
                "last_command_at": 0.0,
                "last_seen_at": now,
                "next_retry_at": now,
                "attempts": 0,
                "accepted_orders": 0,
                "failed_orders": 0,
                "consecutive_failures": 0,
                "moved_cells": 0.0,
                "last_error": None,
            }
        task = {
            "task_id": task_id,
            "kind": "explore_shroud",
            "query": query,
            "destination": "当前黑幕/战争迷雾边界",
            "created_at": now,
            "created_wall_time": self._wall_clock(),
            "expires_at": now + max(30, min(int(duration_seconds), 21600)),
            "status": "queued",
            "unit_states": unit_states,
            "orders_issued": 0,
            "failed_orders": 0,
            "progress_events": 0,
            "scenario_generation": self._scenario_generation,
            "last_tick_at": None,
            "last_error": None,
        }
        with self._lock:
            self._tasks[task_id] = task
        return self.public_task(task)

    def create_tactical_advance_task(
        self,
        units: list[dict[str, Any]],
        target: dict[str, Any],
        *,
        query: str | None = None,
        destination: str | None = None,
        duration_seconds: int = 1800,
        arrival_radius: int = 5,
        engage_radius: int = 18,
        fire_mode: str = "distributed",
        regroup: bool = True,
    ) -> dict[str, Any]:
        if fire_mode not in {"distributed", "concentrated"}:
            raise ValueError("fire_mode must be distributed or concentrated")
        created = self.create_move_task(
            units,
            target,
            query=query,
            destination=destination,
            duration_seconds=duration_seconds,
            arrival_radius=arrival_radius,
        )
        with self._lock:
            task = self._tasks[created["task_id"]]
            task["kind"] = "tactical_advance"
            task["engage_radius"] = max(3, min(int(engage_radius), 64))
            task["fire_mode"] = fire_mode
            task["engagements"] = 0
            cells = [unit.get("cell") for unit in units if valid_cell(unit.get("cell"))]
            task["phase"] = "regrouping" if regroup and len(cells) > 1 else "advancing"
            task["regroup_cell"] = ({
                "x": round(sum(cell["x"] for cell in cells) / len(cells)),
                "y": round(sum(cell["y"] for cell in cells) / len(cells)),
            } if cells else None)
            task["regroup_radius"] = 6
            for unit_state in task["unit_states"].values():
                unit_state["combat_target_id"] = None
                unit_state["last_attack_at"] = 0.0
            return self.public_task(task)

    def tick(self, state: dict[str, Any]) -> dict[str, int]:
        now = self._clock()
        with self._lock:
            reset = self._observe_frame_locked(state)
            counts = {"ticked": 0, "commands": 0, "completed": 0, "expired": 0}
            for task in list(self._tasks.values()):
                if task.get("status") in {"cancelled", "completed", "expired"}:
                    continue
                if now >= float(task.get("expires_at", 0)):
                    task["status"] = "expired"
                    task["completed_at"] = now
                    counts["expired"] += 1
                    continue
                task["last_tick_at"] = now
                counts["ticked"] += 1
                if reset:
                    self._reset_task_observation_locked(task, now)
                before = int(task.get("orders_issued", 0))
                if task.get("kind") == "move_near":
                    self._tick_move_locked(task, state, now)
                elif task.get("kind") == "tactical_advance":
                    self._tick_tactical_advance_locked(task, state, now)
                elif task.get("kind") == "explore_shroud":
                    self._tick_explore_locked(task, state, now)
                counts["commands"] += int(task.get("orders_issued", 0)) - before
                if task.get("status") == "completed":
                    counts["completed"] += 1
            self.learning.save()
            return counts

    def cancel(self, task_id: str | None = None, *, kind: str | None = None) -> int:
        now = self._clock()
        count = 0
        with self._lock:
            for task in self._tasks.values():
                if task_id is not None and task.get("task_id") != task_id:
                    continue
                if kind is not None and task.get("kind") != kind:
                    continue
                if task.get("status") not in self.ACTIVE_STATUSES:
                    continue
                task["status"] = "cancelled"
                task["completed_at"] = now
                count += 1
            self.learning.save(force=True)
        return count

    def clear(self) -> int:
        return self.cancel()

    def tasks(
        self, *, include_finished: bool = False, task_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self._lock:
            selected = []
            for task in self._tasks.values():
                if task_id is not None and task.get("task_id") != task_id:
                    continue
                if not include_finished and task.get("status") not in self.ACTIVE_STATUSES:
                    continue
                selected.append(self.public_task(task))
            selected.sort(key=lambda item: item["created_at"], reverse=True)
            return selected

    @property
    def active_count(self) -> int:
        with self._lock:
            return sum(
                task.get("status") in self.ACTIVE_STATUSES
                for task in self._tasks.values()
            )

    def close(self) -> None:
        self.learning.save(force=True)

    def _tick_move_locked(
        self, task: dict[str, Any], state: dict[str, Any], now: float,
        skip_unit_ids: set[int] | None = None,
    ) -> None:
        skip_unit_ids = skip_unit_ids or set()
        target = self._resolve_target(state, task["target"])
        if target is None or not valid_cell(target.get("cell")):
            task["status"] = "waiting_for_target"
            task["last_error"] = "目标暂时不在当前可用遥测中，任务仍会继续等待"
            return
        target_cell = dict(target["cell"])
        previous_target = task.get("target_cell")
        target_moved = valid_cell(previous_target) and distance_sq(
            previous_target, target_cell
        ) > 4
        task["target_cell"] = target_cell
        task["target_last_seen_at"] = now
        task["target"] = self._target_descriptor(target, fallback=task["target"])
        radius = int(task.get("arrival_radius", 5))
        arrival_distance = radius * radius
        states = task["unit_states"]
        arrived = 0
        waiting = 0
        active = 0
        engaging = 0
        for unit_state in states.values():
            if unit_state.get("status") in {"lost", "arrived"}:
                if unit_state.get("status") == "arrived":
                    arrived += 1
                continue
            unit = self._find_entity(state, int(unit_state["object_id"]), own_only=True)
            if unit is None or not valid_cell(unit.get("cell")):
                if now - float(unit_state.get("last_seen_at", now)) >= 30.0:
                    unit_state["status"] = "lost"
                    unit_state["last_error"] = "单位已连续30秒不在可控遥测中"
                else:
                    unit_state["status"] = "waiting_for_unit"
                    waiting += 1
                continue
            active += 1
            unit_state["last_seen_at"] = now
            current_cell = dict(unit["cell"])
            current_distance = distance_sq(current_cell, target_cell)
            if current_distance <= arrival_distance:
                self._mark_arrived_locked(task, unit_state)
                arrived += 1
                continue
            if int(unit_state["object_id"]) in skip_unit_ids:
                unit_state["last_cell"] = current_cell
                unit_state["last_distance"] = current_distance
                unit_state["last_progress_at"] = now
                unit_state["status"] = "engaging"
                engaging += 1
                continue
            self._observe_move_progress_locked(
                task, unit_state, current_cell, current_distance, now
            )
            stalled = now - float(unit_state.get("last_progress_at", now)) >= self._stall_seconds
            due = now - float(unit_state.get("last_command_at", 0.0)) >= self._command_interval
            needs_first_order = int(unit_state.get("attempts", 0)) == 0
            retry_failed_order = unit_state.get("status") == "stalled" and due
            resume_after_combat = unit_state.pop("resume_move", False) is True
            if not (
                needs_first_order or stalled or retry_failed_order
                or target_moved or resume_after_combat
            ):
                unit_state["status"] = "moving"
                continue
            if stalled and unit_state.get("active_strategy"):
                self.learning.record(
                    self._learning_context(unit_state, target),
                    str(unit_state["active_strategy"]),
                    "failure",
                )
                unit_state["status"] = "stalled"
            strategy, command_cell = self._choose_move_cell(
                state, unit_state, target, current_cell
            )
            takeover = (
                bool(unit.get("in_tactical_team"))
                and int(unit_state.get("attempts", 0)) > 0
            )
            result = self._send(
                state,
                int(unit_state["object_id"]),
                "move",
                target_cell=command_cell,
                override_team=takeover,
            )
            unit_state["attempts"] += 1
            unit_state["last_command_at"] = now
            unit_state["active_strategy"] = strategy
            unit_state["active_target_cell"] = command_cell
            task["orders_issued"] += 1
            self.learning.record(
                self._learning_context(unit_state, target), strategy, "issued"
            )
            if result.get("accepted") is True:
                unit_state["accepted_orders"] += 1
                unit_state["status"] = "moving"
                unit_state["last_error"] = None
            else:
                unit_state["failed_orders"] += 1
                unit_state["status"] = "stalled"
                unit_state["last_error"] = str(
                    result.get("error") or "移动命令未被游戏接受"
                )
                task["failed_orders"] += 1
                self.learning.record(
                    self._learning_context(unit_state, target), strategy, "failure"
                )

        task["arrived_count"] = arrived
        total_viable = sum(
            item.get("status") != "lost" for item in states.values()
        )
        if total_viable > 0 and arrived >= total_viable:
            task["status"] = "completed"
            task["completed_at"] = now
            task["last_error"] = None
        elif engaging:
            task["status"] = "engaging"
        elif active:
            task["status"] = "moving"
        elif waiting:
            task["status"] = "waiting_for_unit"
        elif total_viable == 0:
            task["status"] = "completed"
            task["completed_at"] = now
            task["last_error"] = "所有受控单位均已失效或离开遥测"

    def _tick_tactical_advance_locked(
        self, task: dict[str, Any], state: dict[str, Any], now: float
    ) -> None:
        if task.get("phase") == "regrouping" and self._tick_regroup_locked(task, state, now):
            return
        entities = state.get("visible_entities")
        entities = entities if isinstance(entities, list) else []
        enemies = [
            item for item in entities
            if isinstance(item, dict)
            and item.get("relation") == "enemy"
            and item.get("visible") is True
            and item.get("alive", True) is True
            and valid_cell(item.get("cell"))
            and isinstance(item.get("object_id"), int)
            and is_valid_attack_target(item)
        ]
        engage_distance = int(task.get("engage_radius", 18)) ** 2
        engaging_ids: set[int] = set()
        concentrated_target = None
        own_positions = []
        for unit_state in task["unit_states"].values():
            unit = self._find_entity(state, int(unit_state["object_id"]), own_only=True)
            if unit is not None and valid_cell(unit.get("cell")):
                own_positions.append(unit["cell"])
        if enemies and own_positions and task.get("fire_mode") == "concentrated":
            concentrated_target = min(
                enemies,
                key=lambda enemy: sum(distance_sq(cell, enemy["cell"]) for cell in own_positions),
            )

        for unit_state in task["unit_states"].values():
            unit_id = int(unit_state["object_id"])
            unit = self._find_entity(state, unit_id, own_only=True)
            if unit is None or not valid_cell(unit.get("cell")):
                continue
            max_ammo = int(unit.get("max_ammo", 0) or 0)
            ammo = int(unit.get("ammo", 0) or 0)
            if unit.get("kind") == "aircraft" and max_ammo > 0 and ammo < max_ammo:
                unit_state["combat_target_id"] = None
                continue
            nearby = [
                enemy for enemy in enemies
                if distance_sq(unit["cell"], enemy["cell"]) <= engage_distance
            ]
            target = concentrated_target if concentrated_target in nearby else None
            if target is None and nearby:
                nearby.sort(key=lambda enemy: (
                    distance_sq(unit["cell"], enemy["cell"]),
                    int(enemy["object_id"]),
                ))
                if task.get("fire_mode") == "distributed":
                    target = nearby[int(unit_state.get("slot", 0)) % len(nearby)]
                else:
                    target = nearby[0]
            if target is None:
                if unit_state.get("combat_target_id") is not None:
                    unit_state["last_command_at"] = 0.0
                    unit_state["resume_move"] = True
                unit_state["combat_target_id"] = None
                continue
            engaging_ids.add(unit_id)
            target_id = int(target["object_id"])
            due = now - float(unit_state.get("last_attack_at", 0.0)) >= self._command_interval
            if unit_state.get("combat_target_id") == target_id and not due:
                continue
            result = self._send(
                state, unit_id, "attack", target_object_id=target_id
            )
            task["orders_issued"] += 1
            unit_state["last_attack_at"] = now
            if result.get("accepted") is True:
                if unit_state.get("combat_target_id") != target_id:
                    task["engagements"] += 1
                unit_state["combat_target_id"] = target_id
                unit_state["accepted_orders"] += 1
                unit_state["status"] = "engaging"
                unit_state["last_error"] = None
            else:
                engaging_ids.discard(unit_id)
                unit_state["failed_orders"] += 1
                task["failed_orders"] += 1
                unit_state["last_error"] = str(
                    result.get("error") or "攻击命令未被游戏接受"
                )
        self._tick_move_locked(task, state, now, engaging_ids)

    def _tick_regroup_locked(
        self, task: dict[str, Any], state: dict[str, Any], now: float
    ) -> bool:
        center = task.get("regroup_cell")
        if not valid_cell(center):
            task["phase"] = "advancing"
            return False
        radius_sq = int(task.get("regroup_radius", 6)) ** 2
        pending = []
        for unit_state in task["unit_states"].values():
            unit = self._find_entity(state, int(unit_state["object_id"]), own_only=True)
            if unit is None or not valid_cell(unit.get("cell")):
                continue
            if distance_sq(unit["cell"], center) > radius_sq:
                pending.append((unit_state, unit))
        if not pending:
            task["phase"] = "advancing"
            for unit_state in task["unit_states"].values():
                unit_state["last_command_at"] = 0.0
            return False
        task["status"] = "moving"
        for unit_state, unit in pending:
            due = now - float(unit_state.get("last_command_at", 0.0)) >= self._command_interval
            if int(unit_state.get("attempts", 0)) > 0 and not due:
                continue
            result = self._send(
                state, int(unit_state["object_id"]), "move", target_cell=dict(center)
            )
            task["orders_issued"] += 1
            unit_state["attempts"] += 1
            unit_state["last_command_at"] = now
            if result.get("accepted") is True:
                unit_state["accepted_orders"] += 1
                unit_state["status"] = "moving"
            else:
                unit_state["failed_orders"] += 1
                task["failed_orders"] += 1
                unit_state["last_error"] = str(result.get("error") or "集结命令未被游戏接受")
        return True

    def _tick_explore_locked(
        self, task: dict[str, Any], state: dict[str, Any], now: float
    ) -> None:
        active = 0
        waiting_frontier = 0
        for unit_state in task["unit_states"].values():
            if unit_state.get("status") == "lost":
                continue
            unit = self._find_entity(state, int(unit_state["object_id"]), own_only=True)
            if unit is None or not valid_cell(unit.get("cell")):
                if now - float(unit_state.get("last_seen_at", now)) >= 30.0:
                    unit_state["status"] = "lost"
                    unit_state["last_error"] = "单位已连续30秒不在可控遥测中"
                else:
                    unit_state["status"] = "waiting_for_unit"
                continue
            active += 1
            unit_state["last_seen_at"] = now
            cell = dict(unit["cell"])
            last_cell = unit_state.get("last_cell")
            if valid_cell(last_cell) and cell != last_cell:
                step = math.sqrt(float(distance_sq(last_cell, cell)))
                unit_state["moved_cells"] = round(
                    float(unit_state.get("moved_cells", 0.0)) + step, 2
                )
                unit_state["last_progress_at"] = now
                unit_state["last_cell"] = cell
                unit_state["status"] = "exploring"
                unit_state["consecutive_failures"] = 0
                task["progress_events"] += 1
            elif not valid_cell(last_cell):
                unit_state["last_cell"] = cell
                unit_state["last_progress_at"] = now

            stalled = now - float(unit_state.get("last_progress_at", now)) >= self._stall_seconds
            since_command = now - float(unit_state.get("last_command_at", 0.0))
            leg_start = unit_state.get("leg_start_cell")
            leg_distance = (
                distance_sq(leg_start, cell) if valid_cell(leg_start) else 0
            )
            leg_complete = leg_distance >= 20 * 20
            retry_due = now >= float(unit_state.get("next_retry_at", now))
            if not retry_due or (
                not stalled and not leg_complete and since_command < 12.0
            ):
                if unit_state.get("status") == "waiting_for_frontier":
                    waiting_frontier += 1
                continue

            takeover = (
                bool(unit.get("in_tactical_team"))
                and int(unit_state.get("attempts", 0)) > 0
            )
            result = self._send(
                state,
                int(unit_state["object_id"]),
                "explore",
                override_team=takeover,
            )
            unit_state["attempts"] += 1
            unit_state["last_command_at"] = now
            unit_state["leg_start_cell"] = cell
            task["orders_issued"] += 1
            if result.get("accepted") is True:
                unit_state["accepted_orders"] += 1
                # Native NavQueue waypoints let the unit keep exploring several
                # shroud frontiers without waiting for another dialogue/tool turn.
                queued = 0
                for _ in range(3):
                    waypoint = self._send(
                        state,
                        int(unit_state["object_id"]),
                        "explore",
                        append_waypoint=True,
                    )
                    task["orders_issued"] += 1
                    if waypoint.get("accepted") is not True:
                        task["failed_orders"] += 1
                        break
                    queued += 1
                    unit_state["accepted_orders"] += 1
                unit_state["queued_waypoints"] = queued
                unit_state["consecutive_failures"] = 0
                unit_state["next_retry_at"] = now + self._command_interval
                unit_state["status"] = "exploring"
                unit_state["last_error"] = None
            else:
                failures = int(unit_state.get("consecutive_failures", 0)) + 1
                unit_state["consecutive_failures"] = failures
                unit_state["failed_orders"] += 1
                unit_state["status"] = "waiting_for_frontier"
                unit_state["last_error"] = str(
                    result.get("error") or "当前没有可到达的黑幕边界"
                )
                unit_state["next_retry_at"] = now + min(30.0, 3.0 * (2 ** min(failures, 3)))
                task["failed_orders"] += 1
                waiting_frontier += 1

        viable = sum(
            item.get("status") != "lost" for item in task["unit_states"].values()
        )
        if viable == 0:
            task["status"] = "completed"
            task["completed_at"] = now
            task["last_error"] = "所有探索单位均已失效或离开遥测"
        elif waiting_frontier >= viable:
            task["status"] = "waiting_for_frontier"
        elif active:
            task["status"] = "exploring"
        else:
            task["status"] = "waiting_for_unit"

    def _observe_move_progress_locked(
        self,
        task: dict[str, Any],
        unit_state: dict[str, Any],
        cell: dict[str, int],
        current_distance: int,
        now: float,
    ) -> None:
        previous_distance = unit_state.get("last_distance")
        best_distance = unit_state.get("best_distance")
        moved = valid_cell(unit_state.get("last_cell")) and cell != unit_state["last_cell"]
        progressed = (
            isinstance(previous_distance, int)
            and current_distance + 1 < previous_distance
        )
        improved_best = not isinstance(best_distance, int) or current_distance < best_distance
        if moved and (progressed or improved_best):
            old = previous_distance if isinstance(previous_distance, int) else current_distance
            improvement = max(0, old - current_distance) / max(1, old)
            unit_state["last_progress_at"] = now
            unit_state["progress_events"] += 1
            task["progress_events"] += 1
            strategy = unit_state.get("active_strategy")
            if strategy:
                target = task.get("target", {})
                self.learning.record(
                    self._learning_context(unit_state, target),
                    str(strategy),
                    "progress",
                    progress_ratio=improvement,
                )
        if improved_best:
            unit_state["best_distance"] = current_distance
        unit_state["last_distance"] = current_distance
        unit_state["last_cell"] = cell

    def _mark_arrived_locked(
        self, task: dict[str, Any], unit_state: dict[str, Any]
    ) -> None:
        if unit_state.get("status") == "arrived":
            return
        unit_state["status"] = "arrived"
        strategy = unit_state.get("active_strategy")
        if strategy:
            self.learning.record(
                self._learning_context(unit_state, task.get("target", {})),
                str(strategy),
                "success",
            )

    def _choose_move_cell(
        self,
        state: dict[str, Any],
        unit_state: dict[str, Any],
        target: dict[str, Any],
        current_cell: dict[str, int],
    ) -> tuple[str, dict[str, int]]:
        context = self._learning_context(unit_state, target)
        names = [item[0] for item in MOVE_STRATEGIES]
        ranked = self.learning.score(context, names)
        attempted = int(unit_state.get("attempts", 0))
        # Rotate equal/near-equal candidates between units to reduce bunching.
        shortlist = ranked[: min(8, len(ranked))]
        cursor = (attempted + int(unit_state.get("slot", 0))) % max(1, len(shortlist))
        selected = shortlist[cursor] if shortlist else ranked[0]
        offsets = {name: (dx, dy) for name, dx, dy in MOVE_STRATEGIES}
        dx, dy = offsets[selected.strategy]
        anchor = target["cell"]
        candidate = {"x": int(anchor["x"]) + dx, "y": int(anchor["y"]) + dy}
        candidate = self._clamp_to_map(state, candidate)
        # A blocked target ring can be approached through an intermediate point.
        if attempted >= 4 and attempted % 3 == 1:
            candidate = self._intermediate_cell(state, current_cell, candidate, attempted)
            return selected.strategy, candidate
        return selected.strategy, candidate

    def _observe_frame_locked(self, state: dict[str, Any]) -> bool:
        frame = state.get("game", {}).get("frame")
        if isinstance(frame, bool) or not isinstance(frame, int):
            return False
        reset = self._last_frame is not None and frame + 30 < self._last_frame
        self._last_frame = frame
        if reset:
            self._scenario_generation += 1
        return reset

    @staticmethod
    def _reset_task_observation_locked(task: dict[str, Any], now: float) -> None:
        task["status"] = "paused"
        task["last_error"] = "检测到场景切换，正在重新确认单位和目标"
        for unit in task.get("unit_states", {}).values():
            if unit.get("status") in {"cancelled", "lost"}:
                continue
            unit["status"] = "waiting_for_unit"
            unit["last_cell"] = None
            unit["last_distance"] = None
            unit["best_distance"] = None
            unit["last_progress_at"] = now
            unit["last_command_at"] = 0.0

    @staticmethod
    def _find_entity(
        state: dict[str, Any], object_id: int, *, own_only: bool = False
    ) -> dict[str, Any] | None:
        for key in ("visible_entities", "landmarks"):
            items = state.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict) or item.get("object_id") != object_id:
                    continue
                if item.get("alive") is not True:
                    continue
                if own_only and item.get("relation") not in {None, "self"}:
                    continue
                if own_only and item.get("controllable") is not True:
                    continue
                return item
        return None

    def _resolve_target(
        self, state: dict[str, Any], descriptor: dict[str, Any]
    ) -> dict[str, Any] | None:
        if descriptor.get("kind") == "player_marker":
            markers = state.get("markers")
            if isinstance(markers, list):
                selected = next((
                    item for item in markers
                    if isinstance(item, dict)
                    and item.get("slot") == descriptor.get("slot")
                    and valid_cell(item.get("cell"))
                ), None)
                if selected is not None:
                    return {**selected, "kind": "player_marker", "alive": True}
            cell = descriptor.get("cell")
            return {"cell": dict(cell), "kind": "player_marker", "alive": True} if valid_cell(cell) else None
        identity_keys = {"object_id", "type_id", "display_name", "kind", "relation", "owner_house_id"}
        if not any(descriptor.get(key) is not None for key in identity_keys):
            cell = descriptor.get("cell")
            if valid_cell(cell):
                return {"cell": dict(cell), "alive": True}
        object_id = descriptor.get("object_id")
        if isinstance(object_id, int):
            exact = self._find_entity(state, object_id)
            if exact is not None:
                return exact
        candidates = []
        for key in ("visible_entities", "landmarks"):
            items = state.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict) or item.get("alive") is not True:
                    continue
                score = 0
                identity_match = False
                if descriptor.get("type_id") and str(item.get("type_id", "")).upper() == str(
                    descriptor["type_id"]
                ).upper():
                    score += 6
                    identity_match = True
                if descriptor.get("kind") and item.get("kind") == descriptor.get("kind"):
                    score += 2
                if descriptor.get("relation") and item.get("relation") == descriptor.get("relation"):
                    score += 2
                if descriptor.get("owner_house_id") is not None and item.get(
                    "owner_house_id"
                ) == descriptor.get("owner_house_id"):
                    score += 2
                if descriptor.get("display_name") and str(
                    item.get("display_name", "")
                ).casefold() == str(descriptor["display_name"]).casefold():
                    score += 4
                    identity_match = True
                if identity_match:
                    candidates.append((score, item))
        if not candidates:
            return None
        previous_cell = descriptor.get("cell")
        candidates.sort(
            key=lambda pair: (
                -pair[0],
                distance_sq(pair[1].get("cell", {}), previous_cell)
                if valid_cell(previous_cell)
                else 0,
                int(pair[1].get("object_id", 0)),
            )
        )
        return candidates[0][1]

    @staticmethod
    def _target_descriptor(
        target: dict[str, Any], *, fallback: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        fallback = fallback or {}
        result = {}
        for key in (
            "object_id",
            "type_id",
            "display_name",
            "kind",
            "relation",
            "owner_house_id",
            "faction",
            "slot",
            "marker_id",
        ):
            value = target.get(key, fallback.get(key))
            if value is not None:
                result[key] = value
        cell = target.get("cell", fallback.get("cell"))
        if valid_cell(cell):
            result["cell"] = dict(cell)
        return result

    @staticmethod
    def _learning_context(
        unit_state: dict[str, Any], target: dict[str, Any]
    ) -> str:
        source_kind = str(unit_state.get("kind") or "unknown").casefold()
        source_type = str(unit_state.get("type_id") or "*").upper()
        target_kind = str(target.get("kind") or "unknown").casefold()
        target_type = str(target.get("type_id") or "*").upper()
        return f"move_near|{source_kind}:{source_type}|{target_kind}:{target_type}"

    @staticmethod
    def _clamp_to_map(
        state: dict[str, Any], cell: dict[str, int]
    ) -> dict[str, int]:
        # RA2's isometric CellStruct coordinates are not bounded by
        # MapRect.X + Width / Y + Height independently. The native bridge calls
        # CoordinatesLegal and remains the authority for actual map validity.
        return {
            "x": max(-32768, min(32767, int(cell["x"]))),
            "y": max(-32768, min(32767, int(cell["y"]))),
        }

    def _intermediate_cell(
        self,
        state: dict[str, Any],
        start: dict[str, int],
        end: dict[str, int],
        attempt: int,
    ) -> dict[str, int]:
        ratio = 0.45 if attempt % 2 else 0.65
        x = round(start["x"] + (end["x"] - start["x"]) * ratio)
        y = round(start["y"] + (end["y"] - start["y"]) * ratio)
        # Small alternating lateral displacement prevents repeatedly selecting the
        # exact same blocked diagonal in narrow passages.
        lateral = 2 if attempt % 4 < 2 else -2
        if abs(end["x"] - start["x"]) >= abs(end["y"] - start["y"]):
            y += lateral
        else:
            x += lateral
        return self._clamp_to_map(state, {"x": x, "y": y})

    @staticmethod
    def _object_id(entity: dict[str, Any]) -> int:
        value = entity.get("object_id")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("unit object_id is invalid")
        return value

    @staticmethod
    def _label(entity: dict[str, Any]) -> str:
        return str(
            entity.get("display_name")
            or entity.get("type_id")
            or f"对象{entity.get('object_id', '?')}"
        )

    def public_task(self, task: dict[str, Any]) -> dict[str, Any]:
        now = self._clock()
        units = []
        for item in task.get("unit_states", {}).values():
            units.append(
                {
                    "object_id": item.get("object_id"),
                    "unit": item.get("label"),
                    "status": item.get("status"),
                    "attempts": item.get("attempts", 0),
                    "accepted_orders": item.get("accepted_orders", 0),
                    "failed_orders": item.get("failed_orders", 0),
                    "progress_events": item.get("progress_events", 0),
                    "moved_cells": item.get("moved_cells"),
                    "strategy": item.get("active_strategy"),
                    "last_error": item.get("last_error"),
                }
            )
        return {
            "task_id": task.get("task_id"),
            "kind": task.get("kind"),
            "query": task.get("query"),
            "destination": task.get("destination"),
            "status": task.get("status"),
            "created_at": task.get("created_at"),
            "created_wall_time": task.get("created_wall_time"),
            "remaining_seconds": max(
                0, round(float(task.get("expires_at", now)) - now)
            ),
            "orders_issued": task.get("orders_issued", 0),
            "failed_orders": task.get("failed_orders", 0),
            "progress_events": task.get("progress_events", 0),
            "arrived_count": task.get("arrived_count"),
            "engage_radius": task.get("engage_radius"),
            "fire_mode": task.get("fire_mode"),
            "engagements": task.get("engagements"),
            "phase": task.get("phase"),
            "unit_count": len(units),
            "units": units,
            "last_error": task.get("last_error"),
        }
