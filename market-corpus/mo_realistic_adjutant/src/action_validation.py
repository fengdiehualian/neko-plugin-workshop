"""Post-dispatch validation for native bridge actions."""
from __future__ import annotations
from typing import Any

from .building_command import is_valid_attack_target


class ActionValidator:
    """Separate bridge acceptance, observed progress, and terminal completion."""

    def validate(self, plan: dict[str, Any], order: dict[str, Any], state: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(result, dict):
            return {"accepted": False, "complete": False, "retryable": True, "reason": "invalid bridge result"}
        if result.get("accepted") is not True:
            return {"accepted": False, "complete": False, "retryable": result.get("retryable", True) is not False, "reason": str(result.get("error") or "bridge rejected action")}
        if result.get("verified") is False:
            return {"accepted": False, "complete": False, "retryable": True, "reason": "native state did not verify the action"}
        doctrine_constraints = order.get("doctrine_constraints")
        if isinstance(doctrine_constraints, dict):
            action = str(order.get("action") or "").casefold()
            if (
                doctrine_constraints.get("passenger_switching_allowed") is False
                and plan.get("combat_started") is True
                and action in {"load", "unload", "enter"}
            ):
                return {
                    "accepted": False, "complete": False, "retryable": False,
                    "reason": "combat doctrine forbids passenger switching after contact",
                }
            target_id = order.get("target_object_id")
            if action == "attack" and isinstance(target_id, int):
                target = next((
                    item for item in state.get("visible_entities", [])
                    if isinstance(item, dict) and item.get("object_id") == target_id
                ), None)
                if not isinstance(target, dict) or not is_valid_attack_target(target):
                    return {
                        "accepted": False, "complete": False, "retryable": True,
                        "reason": "doctrine target is no longer a visible valid combat target",
                    }
        if result.get("complete") is False:
            return {"accepted": True, "complete": False, "stage": "accepted_waiting_observation", "reason": str(result.get("status") or "awaiting observable progress")}
        return {"accepted": True, "complete": True, "stage": "verified_or_native_terminal", "reason": "native action reached its reported terminal state"}
