"""Conservative precondition and battlefield utility checks for high-level intents."""
from __future__ import annotations
from typing import Any

from .building_command import is_valid_attack_target
from .combat_doctrine import recommend_combat_doctrine


class StrategyEvaluator:
    """Evaluate whether an objective should be dispatched on this world snapshot."""

    def evaluate(self, objective: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        kind = str(objective.get("kind") or "")
        params = objective.get("params") if isinstance(objective.get("params"), dict) else {}
        game = state.get("game") if isinstance(state.get("game"), dict) else {}
        if game.get("running") is not True:
            return self._blocked("game is not running", "match_not_running")
        age = state.get("snapshot_age_ms")
        if isinstance(age, (int, float)) and age > 2500:
            return self._blocked("telemetry is stale", "stale_telemetry")
        if kind == "develop_base":
            reserve = max(0, int(params.get("credit_reserve", 0)))
            credits = self._credits(state)
            if credits is not None and credits < reserve:
                return self._blocked("credits below protected reserve", "insufficient_credits")
        elif kind == "deploy_mcv" and not self._owned(state, ("MCV", "建设车", "基地车")):
            return self._blocked("no visible deployable construction vehicle", "mcv_not_visible")
        elif kind == "produce_force":
            production = state.get("production") if isinstance(state.get("production"), dict) else {}
            available = production.get("sidebar_available")
            requested = str(params.get("type_id") or "").strip().upper()
            if isinstance(available, list) and requested and not any(
                isinstance(item, dict) and str(item.get("type_id") or "").upper() == requested
                for item in available
            ):
                return self._blocked("requested unit is not currently buildable", "unit_not_buildable")
        elif kind == "attack":
            enemies = [item for item in state.get("visible_entities", []) if isinstance(item, dict)
                       and item.get("relation") == "enemy" and item.get("alive") is True
                       and item.get("visible") is True and is_valid_attack_target(item)]
            if not enemies:
                return self._blocked(
                    "no live visible enemy combat target is targetable",
                    "no_visible_target",
                )
            group = params.get("group_number")
            if group is not None and not any(
                isinstance(item, dict) and item.get("relation") == "self"
                and item.get("alive") is True and item.get("group_number") == int(group)
                for item in state.get("visible_entities", [])
            ):
                return self._blocked("requested control group has no visible units", "group_empty")
        doctrine = recommend_combat_doctrine(state)
        rule = doctrine.get("rule") if isinstance(doctrine, dict) else None
        posture = str(rule.get("posture") or "") if isinstance(rule, dict) else ""
        score = 1.0
        reasons = ["preconditions satisfied"]
        if kind == "attack" and posture in {"defend", "delay"}:
            score = 0.45
            reasons.append("doctrine recommends delaying until counters or reinforcements arrive")
        elif kind == "attack" and posture in {"pressure", "attack", "decisive_attack"}:
            score = 1.25
            reasons.append(f"doctrine supports {posture}")
        return {
            "allowed": True, "score": score, "reasons": reasons, "blockers": [],
            "combat_doctrine": doctrine,
        }

    @staticmethod
    def _blocked(reason: str, code: str) -> dict[str, Any]:
        return {"allowed": False, "score": 0.0, "reasons": [], "blockers": [{"code": code, "reason": reason}]}

    @staticmethod
    def _credits(state: dict[str, Any]) -> int | None:
        player = state.get("player") if isinstance(state.get("player"), dict) else {}
        economy = state.get("economy") if isinstance(state.get("economy"), dict) else {}
        value = player.get("credits", economy.get("credits"))
        return int(value) if isinstance(value, (int, float)) else None

    @staticmethod
    def _owned(state: dict[str, Any], query: tuple[str, ...]) -> bool:
        for item in state.get("visible_entities", []):
            if not isinstance(item, dict) or item.get("relation") != "self" or item.get("alive") is not True:
                continue
            text = " ".join(str(item.get(key) or "") for key in ("type_id", "display_name", "name"))
            if any(token.casefold() in text.casefold() for token in query):
                return True
        return False
