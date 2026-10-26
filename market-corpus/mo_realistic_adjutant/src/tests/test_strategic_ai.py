from __future__ import annotations

import unittest

from mo_companion.offline_ai_lab import validate_trials
from mo_companion.strategic_ai import StrategicDirector


def state(*, own=10, enemy=2, buildings=True, attack_alert=False, frame=1):
    entities = []
    if buildings:
        entities.append({
            "object_id": 1, "relation": "self", "visible": True,
            "alive": True, "kind": "building", "building_class": "construction_yard",
        })
    entities.extend({
        "object_id": 10 + index, "relation": "self", "visible": True,
        "alive": True, "kind": "vehicle", "strength": 150, "health_ratio": 1.0,
    } for index in range(own))
    entities.extend({
        "object_id": 100 + index, "relation": "enemy", "visible": True,
        "alive": True, "kind": "vehicle", "strength": 100, "health_ratio": 1.0,
    } for index in range(enemy))
    entities.append({
        "object_id": 200, "type_id": "GAWEAP", "relation": "enemy", "visible": True,
        "alive": True, "kind": "building", "building_class": "vehicle_production",
    }) if enemy else None
    return {
        "game": {"running": True, "frame": frame},
        "visible_entities": entities,
        "economy": {"power_surplus": 100, "net_per_minute": 120},
        "alerts": ([{"kind": "under_attack"}] if attack_alert else []),
    }


class StrategicAITests(unittest.TestCase):
    def test_defense_preempts_attack(self):
        director = StrategicDirector()
        assessment = director.assess(state(own=20, enemy=1, attack_alert=True))
        self.assertEqual(assessment.phase, "defend")
        self.assertFalse(director.should_start_attack(assessment))

    def test_base_alert_always_preempts_a_new_attack(self):
        director = StrategicDirector()
        assessment = director.assess(state(own=40, enemy=1, attack_alert=True))
        self.assertEqual(assessment.phase, "defend")
        self.assertFalse(director.should_start_attack(assessment))

    def test_attack_requires_sustained_advantage_and_building(self):
        director = StrategicDirector()
        assessment = director.assess(state(own=20, enemy=2, frame=1))
        self.assertEqual(assessment.phase, "probe")
        self.assertFalse(director.should_start_attack(assessment))
        for frame in (60, 120, 180):
            assessment = director.assess(state(own=20, enemy=2, frame=frame))
        self.assertEqual(assessment.phase, "attack")
        self.assertTrue(director.should_start_attack(assessment))
        target = director.strategic_target(state(own=20, enemy=2))
        self.assertEqual((target["relation"], target["kind"]), ("enemy", "vehicle"))

    def test_probe_never_launches_a_strike(self):
        director = StrategicDirector()
        assessment = director.assess(state(own=12, enemy=2, frame=1))
        self.assertEqual(assessment.phase, "probe")
        self.assertFalse(director.should_start_attack(assessment))

    def test_no_hidden_target_is_ever_selected(self):
        director = StrategicDirector()
        hidden = state(own=20, enemy=0)
        hidden["visible_entities"].append({
            "object_id": 300, "relation": "enemy", "visible": False,
            "alive": True, "kind": "building", "building_class": "construction_yard",
        })
        assessment = director.assess(hidden)
        self.assertIsNone(director.strategic_target(hidden))
        self.assertNotEqual(assessment.enemy_buildings, 1)

    def test_visible_combat_force_can_be_attacked_before_building_is_found(self):
        director = StrategicDirector()
        combat_only = state(own=20, enemy=2, frame=1)
        combat_only["visible_entities"] = [
            item for item in combat_only["visible_entities"]
            if item.get("kind") != "building" or item.get("relation") == "self"
        ]
        assessment = director.assess(combat_only)
        self.assertEqual(assessment.enemy_buildings, 0)
        self.assertFalse(director.should_start_attack(assessment))
        for frame in (60, 120, 180):
            combat_only["game"]["frame"] = frame
            assessment = director.assess(combat_only)
        self.assertTrue(director.should_start_attack(assessment))
        self.assertEqual(director.strategic_target(combat_only)["relation"], "enemy")

    def test_map_decoration_never_counts_as_enemy_base_or_target(self):
        director = StrategicDirector()
        scenery = state(own=20, enemy=0)
        scenery["visible_entities"].append({
            "object_id": 301, "type_id": "CITY01", "relation": "enemy",
            "visible": True, "alive": True, "kind": "building",
            "building_class": "other",
        })
        assessment = director.assess(scenery)
        self.assertEqual(assessment.enemy_buildings, 0)
        self.assertIsNone(director.strategic_target(scenery))
        self.assertFalse(director.should_start_attack(assessment))

    def test_ten_thousand_trial_regression(self):
        result = validate_trials(10000, 30)
        self.assertTrue(result["passed"])
        self.assertEqual(result["deadlocks"], 0)
        self.assertEqual(result["unsafe_attacks"], 0)


if __name__ == "__main__":
    unittest.main()
