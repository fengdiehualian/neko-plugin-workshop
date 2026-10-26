from __future__ import annotations

import unittest

from mo_companion.tech_capture_strategy import (
    capturable_neutral_tech_buildings,
    capture_target_is_safe,
    choose_capture_assignment,
    owned_engineers,
)


def entity(object_id, relation, kind, x, y, **extra):
    return {
        "object_id": object_id, "relation": relation, "kind": kind,
        "cell": {"x": x, "y": y}, "alive": True, "visible": True,
        "controllable": relation == "self", **extra,
    }


class TechCaptureStrategyTests(unittest.TestCase):
    def test_only_visible_neutral_tech_is_selected(self):
        neutral = entity(10, "neutral", "building", 20, 20, type_id="CAOILD")
        enemy = entity(11, "enemy", "building", 22, 20, type_id="CAHOSP")
        state = {
            "visible_entities": [neutral, enemy],
            "landmarks": [
                {**neutral, "kind": "tech_building"},
                {**enemy, "kind": "tech_building"},
            ],
        }
        self.assertEqual(
            [item["object_id"] for item in capturable_neutral_tech_buildings(state)],
            [10],
        )

    def test_engineer_type_fallback_works_without_flag(self):
        engineer = entity(1, "self", "infantry", 5, 5, type_id="ENGINEER")
        self.assertEqual(owned_engineers({"visible_entities": [engineer]}), [engineer])

    def test_unsafe_target_waits_for_escort(self):
        target = entity(10, "neutral", "building", 20, 20)
        enemy = entity(20, "enemy", "vehicle", 21, 20)
        state = {"visible_entities": [target, enemy]}
        self.assertFalse(capture_target_is_safe(state, target))
        state["visible_entities"].extend([
            entity(30, "self", "vehicle", 19, 20),
            entity(31, "self", "infantry", 19, 21),
        ])
        self.assertTrue(capture_target_is_safe(state, target))

    def test_nearest_safe_assignment_is_chosen(self):
        target = entity(10, "neutral", "building", 20, 20)
        far = entity(1, "self", "infantry", 1, 1, type_id="ENGINEER")
        near = entity(2, "self", "infantry", 18, 20, type_id="ENGINEER")
        state = {"visible_entities": [target, far, near]}
        assignment = choose_capture_assignment(state, [target], [far, near])
        self.assertIsNotNone(assignment)
        self.assertEqual(assignment[1]["object_id"], 2)


if __name__ == "__main__":
    unittest.main()
