from __future__ import annotations

import unittest

from mo_companion.intent_planning import IntentPlanEngine, IntentPlanError


WORLD = {"revision": 12, "source_frame": 800, "running": True}


class IntentPlanningTests(unittest.TestCase):
    def test_production_and_attack_sentence_becomes_dependency_chain(self):
        engine = IntentPlanEngine()
        plan = engine.create(
            "生产20个海豹突击队然后操控它们攻击最近的敌军步兵",
            WORLD,
        )
        self.assertEqual(plan["intent_kind"], "produce_and_attack")
        self.assertEqual(plan["force_size"], 20)
        self.assertEqual(plan["selected_product"], "海豹突击队")
        self.assertEqual(plan["target_query"], "敌军步兵")
        self.assertEqual([item["kind"] for item in plan["objectives"]], ["produce_force", "attack"])
        self.assertEqual(plan["objectives"][1]["depends_on"], ["objective_1"])

    def test_development_sentence_waits_for_auto_selected_force(self):
        engine = IntentPlanEngine()
        plan = engine.create(
            "部署机动建设车，发展基地维持经济并出部队攻击对面",
            WORLD,
        )
        self.assertEqual(plan["intent_kind"], "develop_and_attack")
        self.assertEqual([item["kind"] for item in plan["objectives"]], ["develop_base", "produce_force", "attack"])
        self.assertTrue(plan["objectives"][1]["params"]["auto_select"])

    def test_structured_objective_cycles_are_rejected(self):
        engine = IntentPlanEngine()
        with self.assertRaises(IntentPlanError):
            engine.create(
                "自定义战术",
                WORLD,
                objectives=[
                    {"objective_id": "a", "kind": "develop_base", "depends_on": ["b"]},
                    {"objective_id": "b", "kind": "attack", "depends_on": ["a"]},
                ],
            )

    def test_detached_task_keeps_id_and_pauses_on_disconnect(self):
        engine = IntentPlanEngine()
        plan = engine.create("攻击最近的敌人", {**WORLD, "session_started_at": 123.0})
        task_id = plan["task_id"]

        engine.set_connection(False, reason="temporary bridge loss")
        paused = engine.get(task_id)

        self.assertEqual(task_id, paused["intent_id"])
        self.assertTrue(paused["detached"])
        self.assertTrue(paused["pending"])
        self.assertEqual(paused["status"], "planned")
        self.assertEqual(paused["connection_state"], "paused_disconnected")
        self.assertEqual(paused["resume_policy"], "resume_same_session")

        engine.set_connection(True, reason="bridge recovered")
        resumed = engine.get(task_id)
        self.assertEqual(resumed["connection_state"], "connected")
        self.assertEqual(resumed["last_decision"], "bridge recovered")

        engine.set_connection(True, reason="must not overwrite every poll")
        stable = engine.get(task_id)
        self.assertEqual(stable["last_decision"], "bridge recovered")

    def test_heartbeat_refreshes_world_cursor(self):
        engine = IntentPlanEngine()
        plan = engine.create("攻击最近的敌人", WORLD)

        engine.heartbeat(plan["task_id"], world_revision=13, world_frame=900)
        current = engine.get(plan["task_id"])

        self.assertEqual(current["world_revision"], 13)
        self.assertEqual(current["world_frame"], 900)
        self.assertLess(current["heartbeat_age_seconds"], 1.0)

    def test_confirmed_session_exit_cancels_with_reason(self):
        engine = IntentPlanEngine()
        plan = engine.create("攻击最近的敌人", WORLD)

        engine.cancel(reason="confirmed match exit")
        current = engine.get(plan["task_id"])

        self.assertEqual(current["status"], "cancelled")
        self.assertFalse(current["pending"])
        self.assertEqual(current["last_error"], "confirmed match exit")


if __name__ == "__main__":
    unittest.main()
