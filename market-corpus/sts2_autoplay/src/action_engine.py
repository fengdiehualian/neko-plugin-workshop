from __future__ import annotations

import logging
from typing import Any

from .planner_interface import PlannedOperation

logger = logging.getLogger(__name__)


class STS2ActionEngine:
    def __init__(self, i18n: Any = None) -> None:
        self._i18n = i18n
        # 最近一次 _normalize_kwargs 是否修正过越界目标（诊断用，见 _repair_combat_target）。
        self._last_target_repair: dict[str, Any] = {}

    def t(self, key: str, *, default: str = "", **params: Any) -> str:
        if self._i18n is not None:
            return self._i18n.t(key, default=default, **params)
        return default.format(**params) if params and default else (default or key)

    def validate(self, snapshot: dict[str, Any], operation: dict[str, Any] | PlannedOperation | None) -> dict[str, Any] | None:
        if operation is None:
            return None
        planned = operation.as_dict() if hasattr(operation, "as_dict") else dict(operation)
        action_type = str(planned.get("action_type") or "").strip()
        if not action_type:
            return None

        registry = snapshot.get("action_registry") if isinstance(snapshot.get("action_registry"), list) else []
        action = self._find_action(registry, snapshot, action_type=action_type, action_id=planned.get("action_id"))
        if action is None:
            return None

        kwargs = planned.get("kwargs") if isinstance(planned.get("kwargs"), dict) else {}
        normalized_kwargs = self._normalize_kwargs(action, kwargs, snapshot)
        return {
            "action_id": action.get("id") or "",
            "action_type": action_type,
            "category": action.get("category") or "general",
            "kwargs": normalized_kwargs,
            "source": planned.get("source") or "unknown",
            "confidence": planned.get("confidence"),
            "reason": planned.get("reason") or "",
            "candidate_actions": list(planned.get("candidate_actions") or []),
            "action": action,
            "target_repair": dict(self._last_target_repair),
        }

    def validate_with_feedback(self, snapshot: dict[str, Any], operation: dict[str, Any] | PlannedOperation | None) -> tuple[dict[str, Any] | None, str]:
        if operation is None:
            return None, "missing operation"
        planned = operation.as_dict() if hasattr(operation, "as_dict") else dict(operation)
        action_type = str(planned.get("action_type") or "").strip()
        if not action_type:
            return None, "missing action_type"
        registry = snapshot.get("action_registry") if isinstance(snapshot.get("action_registry"), list) else []
        action = self._find_action(registry, snapshot, action_type=action_type, action_id=planned.get("action_id"))
        if action is None:
            return None, f"illegal action: {action_type}"
        kwargs = planned.get("kwargs") if isinstance(planned.get("kwargs"), dict) else {}
        normalized_kwargs = self._normalize_kwargs(action, kwargs, snapshot)
        validated = {
            "action_id": action.get("id") or "",
            "action_type": action_type,
            "category": action.get("category") or "general",
            "kwargs": normalized_kwargs,
            "source": planned.get("source") or "unknown",
            "confidence": planned.get("confidence"),
            "reason": planned.get("reason") or "",
            "candidate_actions": list(planned.get("candidate_actions") or []),
            "action": action,
            "target_repair": dict(self._last_target_repair),
        }
        return validated, ""

    async def execute(self, client: Any, snapshot: dict[str, Any], operation: dict[str, Any] | PlannedOperation | None) -> dict[str, Any]:
        validated = self.validate(snapshot, operation)
        if validated is None:
            message = self.t("action_engine.no_legal_action", default="当前没有可执行的合法动作。")
            return {
                "status": "idle",
                "message": message,
                "summary": message,
                "executed": False,
            }
        result = await client.execute_action(validated["action_type"], **validated["kwargs"])
        message = self.t("action_engine.executed_action", default="已执行动作: {action_type}", action_type=validated["action_type"])
        return {
            "status": "ok",
            "message": message,
            "summary": message,
            "executed": True,
            "operation": validated,
            "result": result,
        }

    def _find_action(self, registry: list[dict[str, Any]], snapshot: dict[str, Any], *, action_type: str, action_id: Any) -> dict[str, Any] | None:
        normalized_id = str(action_id or "").strip()
        if normalized_id:
            for action in registry:
                if str(action.get("id") or "") == normalized_id:
                    return action
        target = str(action_type or "").strip().lower()
        for action in registry:
            if str(action.get("type") or "").strip().lower() == target:
                return action

        actions = snapshot.get("available_actions") if isinstance(snapshot.get("available_actions"), list) else []
        for action in actions:
            if not isinstance(action, dict):
                continue
            raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
            candidate = str(action.get("type") or raw.get("name") or raw.get("action") or "").strip().lower()
            if candidate == target:
                return action
        return None

    def _normalize_kwargs(self, action: dict[str, Any], kwargs: dict[str, Any], snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        self._last_target_repair = {}
        raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
        default_kwargs = action.get("default_kwargs") if isinstance(action.get("default_kwargs"), dict) else {}
        action_type = str(action.get("type") or raw.get("name") or raw.get("action") or "").strip().lower()
        normalized = dict(default_kwargs)
        normalized.update(kwargs)
        if action_type in {"choose_event_option", "choose_map_node", "choose_rest_option", "choose_reward_card", "select_deck_card", "claim_reward", "buy_card", "buy_relic", "buy_potion", "use_potion", "discard_potion"}:
            if "option_index" not in normalized and "index" in raw:
                normalized["option_index"] = raw["index"]
        if action_type == "play_card" and "card_index" not in normalized and "card_index" in raw:
            normalized["card_index"] = raw["card_index"]
        if action_type == "play_card":
            target_index = normalized.get("target_index") if "target_index" in normalized else raw.get("target_index")
            if target_index is None:
                fallback_target = self._fallback_target_index(action, snapshot, normalized)
                if fallback_target is not None:
                    normalized["target_index"] = fallback_target
            elif "target_index" not in normalized:
                normalized["target_index"] = target_index
            self._repair_combat_target(action, normalized, snapshot)
        return normalized

    def _hand_card(self, snapshot: dict[str, Any] | None, card_index: Any) -> dict[str, Any]:
        """从**现场**（raw_state.combat.hand）取出即将打出的那张牌。

        目标合法集只有这里最权威：mod 的 /actions/available 对 play_card 只报
        requires_index，具体某张牌能不能指向某个敌人要看 hand[i].valid_target_indices。
        """
        if not isinstance(snapshot, dict):
            return {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        try:
            wanted = int(card_index)
        except (TypeError, ValueError):
            return {}
        for card in hand:
            if not isinstance(card, dict):
                continue
            try:
                if int(card.get("index")) == wanted:
                    return card
            except (TypeError, ValueError):
                continue
        return {}

    def _valid_target_indices(self, action: dict[str, Any], snapshot: dict[str, Any] | None, kwargs: dict[str, Any]) -> list[int]:
        raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
        card = raw.get("card") if isinstance(raw.get("card"), dict) else {}
        declared = card.get("valid_target_indices") if isinstance(card.get("valid_target_indices"), list) else []
        if not declared:
            live = self._hand_card(snapshot, kwargs.get("card_index"))
            declared = live.get("valid_target_indices") if isinstance(live.get("valid_target_indices"), list) else []
        indices: list[int] = []
        for item in declared:
            try:
                indices.append(int(item))
            except (TypeError, ValueError):
                continue
        return indices

    def _repair_combat_target(self, action: dict[str, Any], kwargs: dict[str, Any], snapshot: dict[str, Any] | None) -> None:
        """把越界的 target_index 就地改成现场合法值（最后一次兜底）。

        为什么需要这一层：战斗计划是**整回合缓存**的，而目标索引是**绝对下标**。回合内死掉一只敌人
        后，缓存里那一步的 target_index 就可能指向已不存在的位置，mod 会以
        ``invalid_target: target_index is out of range for combat.enemies[]`` 拒收——而一次被拒
        曾经足以让自动玩永久停工（见 service._mark_loop_error）。与其指望上游永远不发旧下标，
        不如在**即将上线的那一刻**用现场合法集校正一次。
        """
        valid = self._valid_target_indices(action, snapshot, kwargs)
        if not valid:
            # 拿不到合法集（旧 mod / 非战斗）就不猜，保持原样。
            return
        current = kwargs.get("target_index")
        if current is None:
            return
        try:
            current_int = int(current)
        except (TypeError, ValueError):
            current_int = None
        if current_int is not None and current_int in valid:
            return
        repaired = valid[0]
        self._last_target_repair = {
            "card_index": kwargs.get("card_index"),
            "from": current,
            "to": repaired,
            "valid": list(valid),
        }
        kwargs["target_index"] = repaired
        logger.info(
            "[sts2_target_repair] 越界目标已按现场合法集修正 card_index=%s %s -> %s (valid=%s)",
            kwargs.get("card_index"),
            current,
            repaired,
            valid,
        )

    def _fallback_target_index(self, action: dict[str, Any], snapshot: dict[str, Any] | None = None, kwargs: dict[str, Any] | None = None) -> int | None:
        raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
        card = raw.get("card") if isinstance(raw.get("card"), dict) else {}
        valid_target_indices = card.get("valid_target_indices") if isinstance(card.get("valid_target_indices"), list) else []
        if not valid_target_indices:
            valid_target_indices = self._valid_target_indices(action, snapshot, kwargs or {})
        if not valid_target_indices:
            return None
        try:
            return int(valid_target_indices[0])
        except Exception:
            return None


__all__ = ["STS2ActionEngine"]
