from __future__ import annotations

import unittest

from mo_companion.action_validation import ActionValidator
from mo_companion.strategy_evaluation import StrategyEvaluator
from mo_companion.order_plans import OrderPlanEngine


def state(**overrides):
    value = {
        "game": {"running": True},
        "player": {"credits": 5000},
        "visible_entities": [],
        "production": {"sidebar_available": [{"type_id": "GI"}]},
    }
    value.update(overrides)
    return value


class StrategyValidationTests(unittest.TestCase):
    def test_attack_waits_for_visible_live_enemy(self):
        result = StrategyEvaluator().evaluate({"kind": "attack", "params": {}}, state())
        self.assertFalse(result["allowed"])
        self.assertEqual(result["blockers"][0]["code"], "no_visible_target")

    def test_attack_rejects_map_decoration_but_keeps_real_units_valid(self):
        decoration = {
            "object_id": 8, "type_id": "CITY01", "kind": "building",
            "relation": "enemy", "alive": True, "visible": True,
        }
        blocked = StrategyEvaluator().evaluate(
            {"kind": "attack", "params": {}},
            state(visible_entities=[decoration]),
        )
        self.assertFalse(blocked["allowed"])
        unit = {
            "object_id": 9, "type_id": "E1", "kind": "infantry",
            "relation": "enemy", "alive": True, "visible": True,
        }
        allowed = StrategyEvaluator().evaluate(
            {"kind": "attack", "params": {}},
            state(visible_entities=[decoration, unit]),
        )
        self.assertTrue(allowed["allowed"])

    def test_attack_rejects_empty_control_group(self):
        result = StrategyEvaluator().evaluate(
            {"kind": "attack", "params": {"group_number": 2}},
            state(visible_entities=[{"object_id": 9, "relation": "enemy", "alive": True, "visible": True}],),
        )
        self.assertFalse(result["allowed"])
        self.assertEqual(result["blockers"][0]["code"], "group_empty")

    def test_attack_allows_live_target_and_group(self):
        result = StrategyEvaluator().evaluate(
            {"kind": "attack", "params": {"group_number": 2}},
            state(visible_entities=[
                {"object_id": 1, "relation": "self", "alive": True, "group_number": 2},
                {"object_id": 9, "relation": "enemy", "alive": True, "visible": True},
            ]),
        )
        self.assertTrue(result["allowed"])

    def test_action_validator_distinguishes_dispatch_from_completion(self):
        validator = ActionValidator()
        pending = validator.validate({}, {"action": "produce"}, state(), {"accepted": True, "complete": False, "status": "queued"})
        self.assertTrue(pending["accepted"])
        self.assertFalse(pending["complete"])
        rejected = validator.validate({}, {"action": "attack"}, state(), {"accepted": True, "verified": False})
        self.assertFalse(rejected["accepted"])
        self.assertTrue(rejected["retryable"])

    def test_order_engine_keeps_unverified_action_retryable(self):
        validator = ActionValidator()
        engine = OrderPlanEngine(
            lambda plan, order, current: {"accepted": True, "verified": False},
            validator=validator.validate,
        )
        created = engine.create("validation", [{"action": "attack", "max_attempts": 1}])
        engine.tick(state())
        current = engine.plans(created["plan_id"], include_finished=True)[0]
        self.assertEqual(current["status"], "failed")
        self.assertIn("validation", current["orders"][0]["result"])


if __name__ == "__main__":
    unittest.main()
