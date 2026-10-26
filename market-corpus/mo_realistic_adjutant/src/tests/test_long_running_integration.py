from __future__ import annotations

import unittest

from mo_companion.action_validation import ActionValidator
from mo_companion.autonomy_nudge import build_reasoning_nudge
from mo_companion.intent_planning import IntentPlanEngine
from mo_companion.order_plans import OrderPlanEngine
from mo_companion.strategy_evaluation import StrategyEvaluator


class LongRunningIntegrationTests(unittest.TestCase):
    def test_compound_intent_has_dependency_chain_and_nudge_cursor(self):
        intent = IntentPlanEngine().create(
            "生产20个海豹突击队然后操控它们攻击最近的敌军步兵",
            {"revision": 1, "source_frame": 10, "running": True},
        )
        self.assertEqual(
            [item["kind"] for item in intent["objectives"]],
            ["produce_force", "attack"],
        )
        self.assertEqual(intent["objectives"][1]["depends_on"], ["objective_1"])
        prompt = build_reasoning_nudge(
            {"revision": 2, "source_frame": 11, "running": True}, [intent]
        )
        self.assertIn(intent["task_id"], prompt)
        self.assertIn('"world_revision":2', prompt)

    def test_commander_voice_goal_with_defeat_word_compiles(self):
        intent = IntentPlanEngine().create(
            "对面是和你一样的猫娘，现在你可以全权自由操作击败对面",
            {"revision": 1, "source_frame": 10, "running": True},
        )
        self.assertEqual(intent["intent_kind"], "autonomous_victory")
        self.assertTrue(intent["autonomy_granted"])
        self.assertEqual(
            [item["kind"] for item in intent["objectives"]],
            ["develop_base", "produce_force", "explore", "defend", "attack"],
        )
        self.assertEqual(intent["target_query"], "敌人")
        self.assertEqual(
            {item["objective_id"]: item["depends_on"] for item in intent["objectives"]},
            {
                "objective_1": [],
                "objective_2": ["objective_1"],
                "objective_3": ["objective_2"],
                "objective_4": ["objective_2"],
                "objective_5": ["objective_2"],
            },
        )
        by_kind = {item["kind"]: item["params"] for item in intent["objectives"]}
        self.assertEqual((by_kind["explore"]["unit_offset"], by_kind["explore"]["unit_limit"]), (0, 2))
        self.assertEqual((by_kind["defend"]["unit_offset"], by_kind["defend"]["unit_limit"]), (2, 4))
        self.assertEqual(by_kind["attack"]["unit_offset"], 6)

    def test_production_waits_then_attack_is_released(self):
        states = [
            {
                "game": {"running": True},
                "production": {"sidebar_available": [{"type_id": "GI"}]},
                "visible_entities": [],
            },
            {
                "game": {"running": True},
                "production": {"sidebar_available": [{"type_id": "GI"}]},
                "visible_entities": [
                    {"object_id": 10, "type_id": "GI", "relation": "self", "alive": True, "visible": True},
                    {"object_id": 90, "type_id": "E1", "relation": "enemy", "alive": True, "visible": True},
                ],
            },
        ]
        calls = []

        def execute(plan, order, state):
            calls.append(order["action"])
            if order["action"] == "produce":
                return {"accepted": True, "complete": False, "status": "waiting_for_units"}
            return {"accepted": True, "complete": True}

        engine = OrderPlanEngine(execute, validator=ActionValidator().validate)
        plan = engine.create("compound", [
            {"order_id": "produce", "action": "produce", "max_attempts": 2},
            {"order_id": "attack", "action": "attack", "depends_on": ["produce"]},
        ])
        engine.tick(states[0])
        first = engine.plans(plan["plan_id"], include_finished=True)[0]
        self.assertEqual(first["orders"][0]["status"], "retrying")
        self.assertNotIn("attack", calls)

        # Make the pending production complete on the next observer pass.
        def execute_after_observation(plan, order, state):
            return {"accepted": True, "complete": True}

        engine._executor = execute_after_observation
        engine._plans[plan["plan_id"]]["orders"][0]["next_attempt_at"] = 0
        engine.tick(states[1])
        second = engine.plans(plan["plan_id"], include_finished=True)[0]
        self.assertEqual(second["status"], "succeeded")
        self.assertEqual([item["status"] for item in second["orders"]], ["succeeded", "succeeded"])

    def test_strategy_blocks_then_allows_attack_after_enemy_appears(self):
        evaluator = StrategyEvaluator()
        objective = {"kind": "attack", "params": {"group_number": 2}}
        blocked = evaluator.evaluate(objective, {
            "game": {"running": True},
            "visible_entities": [],
        })
        self.assertFalse(blocked["allowed"])
        allowed = evaluator.evaluate(objective, {
            "game": {"running": True},
            "visible_entities": [
                {"object_id": 1, "relation": "self", "alive": True, "group_number": 2},
                {"object_id": 2, "relation": "enemy", "alive": True, "visible": True},
            ],
        })
        self.assertTrue(allowed["allowed"])


if __name__ == "__main__":
    unittest.main()
