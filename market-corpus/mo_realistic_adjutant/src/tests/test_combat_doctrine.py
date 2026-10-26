from __future__ import annotations

import unittest

from mo_companion.action_validation import ActionValidator
from mo_companion.combat_doctrine import (
    classify_combat_context,
    load_combat_doctrine,
    recommend_combat_doctrine,
    validate_combat_doctrine,
)
from mo_companion.combat_doctrine_generator import (
    ECONOMIES, FACTIONS, FORCE_RATIOS, STAGES, TERRAINS, THREATS,
)
from mo_companion.us_strategy import OPENING_FORCE, US_BUILD_ORDER, choose_us_decision


class CombatDoctrineTests(unittest.TestCase):
    def test_catalog_exceeds_requested_scale_and_is_valid(self):
        result = validate_combat_doctrine()
        self.assertTrue(result["passed"], result)
        self.assertGreaterEqual(result["entries"], 7000)
        self.assertGreaterEqual(result["unique_ids"], 2000)
        self.assertEqual(result["unique_conditions"], result["entries"])
        self.assertGreaterEqual(result["unit_responses"], 2000)
        self.assertGreaterEqual(result["total_entries"], 7000)

    def test_catalog_is_complete_cartesian_matrix(self):
        expected = (
            len(FACTIONS) * len(THREATS) * len(STAGES)
            * len(ECONOMIES) * len(FORCE_RATIOS) * len(TERRAINS)
        )
        payload = load_combat_doctrine()
        self.assertEqual(payload["metadata"]["matrix_entry_count"], expected)
        self.assertEqual(len(payload["entries"]), expected)

    def test_mass_infantry_selects_real_counter_doctrine(self):
        state = {
            "game": {"running": True, "frame": 1000},
            "economy": {"credits": 9000, "net_per_minute": 1200},
            "visible_entities": [
                {"object_id": i, "relation": "enemy", "visible": True,
                 "alive": True, "kind": "infantry", "factions": ["soviet"],
                 "strength": 100}
                for i in range(1, 31)
            ] + [
                {"object_id": 100 + i, "relation": "self", "visible": True,
                 "alive": True, "kind": "vehicle", "strength": 100}
                for i in range(40)
            ],
        }
        context = classify_combat_context(state)
        self.assertEqual(context["enemy_faction"], "soviet")
        self.assertEqual(context["primary_threat"], "infantry_swarm")
        result = recommend_combat_doctrine(state)
        self.assertIsNotNone(result["rule"])
        self.assertIn("area_anti_infantry", result["rule"]["production_roles"])
        self.assertIn(result["rule"]["posture"], {"pressure", "attack", "decisive_attack"})

    def test_every_rule_preserves_player_authority_and_independent_lanes(self):
        for entry in load_combat_doctrine()["entries"]:
            constraints = set(entry["constraints"])
            self.assertIn("preserve_player_authority", constraints)
            self.assertIn("do_not_block_independent_production_lanes", constraints)

    def test_doctrine_role_selects_real_us_counter_unit(self):
        owned = {type_id: target for type_id, target in US_BUILD_ORDER}
        owned.update({type_id: target for type_id, target, _ in OPENING_FORCE})
        decision, blocked = choose_us_decision(
            [{"type_id": "RIOT", "kind": "infantry"}], owned, {},
            credits=50000, economy_growing=True,
            combat_doctrine_rule={
                "id": "test_infantry_counter",
                "production_roles": ["area_anti_infantry"],
            },
        )
        self.assertIsNone(blocked)
        self.assertEqual(decision.type_id, "RIOT")
        self.assertIn("combat doctrine", decision.reason)

    def test_action_validator_rejects_combat_time_passenger_switch(self):
        result = ActionValidator().validate(
            {"combat_started": True},
            {"action": "load", "doctrine_constraints": {"passenger_switching_allowed": False}},
            {"visible_entities": []},
            {"accepted": True, "verified": True, "complete": True},
        )
        self.assertFalse(result["accepted"])
        self.assertFalse(result["retryable"])

    def test_unit_responses_have_data_driven_activation_and_priority(self):
        payload = load_combat_doctrine()
        response = next(
            item for item in payload["unit_responses"]
            if item.get("enemy_name") == "加特林坦克" and item.get("force_ratio") == "ahead"
        )
        self.assertGreaterEqual(response["activation"]["minimum_count"], 1)
        self.assertGreater(response["activation"]["minimum_total_threat"], 1)
        self.assertGreater(response["priority"], 50)
        self.assertLessEqual(response["tactical_controls"]["minimum_assembly_count"], 7)


if __name__ == "__main__":
    unittest.main()
