from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable


SUPPORT_POWER_RULES = {
    "HUMMINGBIRD": {"mode": "enemy_cluster", "min_score": 8.0, "radius": 6.0},
    "HUMMING": {"mode": "enemy_cluster", "min_score": 8.0, "radius": 6.0},
    "LIGHTNING": {"mode": "enemy_cluster", "min_score": 14.0, "radius": 7.0},
    "STORM": {"mode": "enemy_cluster", "min_score": 14.0, "radius": 7.0},
    "PARADROP": {"mode": "enemy_edge", "min_score": 6.0, "radius": 5.0},
    "SPYPLANE": {"mode": "enemy_edge", "min_score": 4.0, "radius": 5.0},
    "RECON": {"mode": "enemy_edge", "min_score": 4.0, "radius": 5.0},
    "FORCEFIELD": {"mode": "friendly_cluster", "min_score": 8.0, "radius": 5.0},
    "SHIELD": {"mode": "friendly_cluster", "min_score": 8.0, "radius": 5.0},
}

EXCLUDED_TARGET_CLASSES = {"civilian", "scenery", "decoration", "dummy", "neutral"}


class SupportPowerController:
    def __init__(
        self,
        submit: Callable[[dict[str, Any]], dict[str, Any]],
        log_path: str | Path,
        *,
        retry_seconds: float = 8.0,
        verification_seconds: float = 3.0,
    ) -> None:
        self._submit = submit
        self._log_path = Path(log_path)
        self._retry_seconds = max(2.0, float(retry_seconds))
        self._verification_seconds = max(0.1, float(verification_seconds))
        self._last_attempt: dict[str, float] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._last_result: dict[str, Any] | None = None

    @staticmethod
    def available(state: dict[str, Any]) -> list[dict[str, Any]]:
        production = state.get("production") if isinstance(state, dict) else None
        powers = production.get("support_powers", []) if isinstance(production, dict) else []
        return [dict(item) for item in powers if isinstance(item, dict)]

    @staticmethod
    def rule_for(type_id: str) -> dict[str, Any] | None:
        upper = str(type_id or "").upper()
        for keyword, rule in SUPPORT_POWER_RULES.items():
            if keyword in upper:
                return dict(rule)
        return None

    @staticmethod
    def _is_valid_entity(item: Any, relation: str) -> bool:
        if not isinstance(item, dict):
            return False
        if item.get("relation") != relation or item.get("alive") is not True:
            return False
        if item.get("visible") is False or not isinstance(item.get("cell"), dict):
            return False
        labels = {
            str(item.get("kind") or "").casefold(),
            str(item.get("role") or "").casefold(),
            str(item.get("building_class") or "").casefold(),
        }
        return not bool(labels & EXCLUDED_TARGET_CLASSES)

    @staticmethod
    def _weight(item: dict[str, Any]) -> float:
        kind = str(item.get("kind") or "")
        building_class = str(item.get("building_class") or "")
        if building_class in {"superweapon", "technology", "production"}:
            return 5.0
        if kind == "building":
            return 3.0
        if kind in {"vehicle", "aircraft"}:
            return 2.0
        return 1.0

    @classmethod
    def score_target(
        cls, entities: list[dict[str, Any]], relation: str, radius: float
    ) -> dict[str, Any] | None:
        candidates = [item for item in entities if cls._is_valid_entity(item, relation)]
        if not candidates:
            return None
        radius_sq = radius * radius
        best: dict[str, Any] | None = None
        for anchor in candidates:
            cell = anchor["cell"]
            score = 0.0
            matched = 0
            for item in candidates:
                other = item["cell"]
                distance_sq = (float(cell["x"]) - float(other["x"])) ** 2 + (
                    float(cell["y"]) - float(other["y"])
                ) ** 2
                if distance_sq <= radius_sq:
                    score += cls._weight(item)
                    matched += 1
            result = {
                "cell": {"x": int(cell["x"]), "y": int(cell["y"])},
                "score": round(score, 2),
                "matched": matched,
                "anchor_object_id": anchor.get("object_id"),
            }
            if best is None or (result["score"], result["matched"]) > (
                best["score"], best["matched"]
            ):
                best = result
        return best

    def observe(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        now = time.monotonic()
        ready = {
            str(item.get("type_id") or "").upper(): item
            for item in self.available(state)
        }
        results = []
        for type_id, pending in list(self._pending.items()):
            if now < float(pending["verify_at"]):
                continue
            current = ready.get(type_id)
            verified = not (current and current.get("ready") is True)
            result = {**pending, "verification": "confirmed" if verified else "failed"}
            self._record(result)
            results.append(result)
            self._pending.pop(type_id, None)
        return results

    def run_once(self, state: dict[str, Any]) -> dict[str, Any] | None:
        self.observe(state)
        now = time.monotonic()
        entities = state.get("visible_entities", [])
        if not isinstance(entities, list):
            return None
        for power in self.available(state):
            type_id = str(power.get("type_id") or "").upper()
            if not type_id or power.get("ready") is not True or power.get("available") is not True:
                continue
            if type_id in self._pending:
                continue
            if now - self._last_attempt.get(type_id, 0.0) < self._retry_seconds:
                continue
            rule = self.rule_for(type_id)
            if rule is None:
                continue
            relation = "self" if rule["mode"] == "friendly_cluster" else "enemy"
            target = self.score_target(entities, relation, float(rule["radius"]))
            if not target or float(target["score"]) < float(rule["min_score"]):
                continue
            request = {
                "request_id": f"support-{int(time.time() * 1000)}"[-39:],
                "type_id": type_id,
                "target_cell": target["cell"],
            }
            self._last_attempt[type_id] = now
            response = self._submit(request)
            result = {
                "timestamp": time.time(),
                "type_id": type_id,
                "target_cell": target["cell"],
                "score": target["score"],
                "matched": target["matched"],
                "accepted": response.get("accepted") is True,
                "native": response,
            }
            self._last_result = result
            self._record(result)
            if result["accepted"]:
                self._pending[type_id] = {
                    **result,
                    "verify_at": now + self._verification_seconds,
                }
            return result
        return None

    def manual(self, type_id: str, target_cell: dict[str, int]) -> dict[str, Any]:
        request = {
            "request_id": f"support-{int(time.time() * 1000)}"[-39:],
            "type_id": str(type_id).upper(),
            "target_cell": target_cell,
        }
        response = self._submit(request)
        result = {
            "timestamp": time.time(),
            "type_id": request["type_id"],
            "target_cell": target_cell,
            "accepted": response.get("accepted") is True,
            "manual": True,
            "native": response,
        }
        self._record(result)
        return result

    def status(self) -> dict[str, Any]:
        return {
            "pending_verification": list(self._pending.values()),
            "last_result": self._last_result,
            "known_keywords": sorted(SUPPORT_POWER_RULES),
        }

    def _record(self, record: dict[str, Any]) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
