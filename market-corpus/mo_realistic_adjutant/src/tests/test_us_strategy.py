from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mo_companion.combat_learning import CombatExperimentLog
from mo_companion.us_strategy import (
    US_BUILD_ORDER,
    choose_us_convoy_escort_requirements,
    choose_us_convoy_requirements,
    choose_us_decision,
    cooling_production_lanes,
    production_lane,
    profiles_for_idle_lanes,
)


def profiles(*type_ids: str):
    kinds = {
        "GAPILE": "building", "GAREFN": "building", "GAWEAP": "building",
        "ADOG": "infantry", "E1": "infantry", "GGI": "infantry",
        "RIOT": "infantry", "ABRM": "vehicle", "ETNK": "vehicle",
        "ROBO": "vehicle", "FV": "vehicle", "STORM": "aircraft",
    }
    return [{"type_id": item, "kind": kinds.get(item, "building")} for item in type_ids]


class USStrategyTests(unittest.TestCase):
    def test_lane_cooldown_never_blocks_unrelated_factories(self):
        cooling = cooling_production_lanes(
            {"building": 10.0, "vehicle": 8.0}, now=10.2
        )
        available = profiles_for_idle_lanes(
            profiles("GAPOWR", "E1", "FV", "STORM"), cooling
        )
        self.assertEqual(
            {item["type_id"] for item in available}, {"E1", "FV", "STORM"}
        )

    def test_vehicle_lane_does_not_block_buildings(self):
        available = profiles_for_idle_lanes(
            profiles("GAPOWR", "E1", "FV"), {"vehicle"}
        )
        self.assertEqual({item["type_id"] for item in available}, {"GAPOWR", "E1"})

    def test_infantry_lane_does_not_block_vehicle_or_building(self):
        available = profiles_for_idle_lanes(
            profiles("GAPOWR", "E1", "FV"), {"infantry"}
        )
        self.assertEqual({item["type_id"] for item in available}, {"GAPOWR", "FV"})

    def test_building_placement_lane_does_not_block_unit_factories(self):
        available = profiles_for_idle_lanes(
            profiles("GAPOWR", "E1", "FV", "STORM"), {"building"}
        )
        self.assertEqual(
            {item["type_id"] for item in available}, {"E1", "FV", "STORM"}
        )

    def test_naval_units_have_a_separate_lane(self):
        self.assertEqual(
            production_lane({"kind": "vehicle", "role": "combat_naval"}),
            "naval",
        )

    def test_support_plugin_uses_its_own_lane(self):
        self.assertEqual(
            production_lane({
                "kind": "building", "powers_up_building": "GAPOWR",
            }),
            "support_upgrade",
        )

    def test_support_lane_does_not_block_regular_buildings(self):
        available = profiles_for_idle_lanes(
            [
                {"type_id": "GAPOWR", "kind": "building"},
                {"type_id": "GAPOWRUP", "kind": "building",
                 "powers_up_building": "GAPOWR"},
            ],
            {"support_upgrade"},
        )
        self.assertEqual([item["type_id"] for item in available], ["GAPOWR"])

    def test_busy_lane_cannot_start_a_second_product(self):
        available = profiles_for_idle_lanes(
            profiles("FV", "ABRM", "E1"), {"vehicle"}
        )
        self.assertEqual([item["type_id"] for item in available], ["E1"])

    def test_build_order_contains_each_power_turbine(self):
        self.assertEqual(
            [target for type_id, target in US_BUILD_ORDER if type_id == "GAPOWRUP"],
            [1, 2, 3, 4, 5, 6],
        )

    def test_building_lane_advances_before_parallel_infantry(self):
        available = profiles("GAREFN", "ADOG", "E1")
        decision, _ = choose_us_decision(
            available, {"GAPILE": 1}, {}, credits=3000, economy_growing=False
        )
        self.assertEqual((decision.type_id, decision.quantity), ("GAREFN", 1))

        # Once the building lane is filtered as busy, the infantry lane starts
        # the opening force without waiting for placement to finish.
        decision, _ = choose_us_decision(
            profiles("ADOG", "E1"), {"GAPILE": 1}, {"GAREFN": 1},
            credits=3000, economy_growing=False
        )
        self.assertEqual((decision.type_id, decision.quantity), ("ADOG", 5))

    def test_unavailable_parallel_air_support_does_not_block_t3(self):
        owned = {
            "GAPILE": 1, "GAREFN": 2, "GAPOWR": 3, "GAWEAP": 1,
            "GAAIRC": 1, "GAMERC": 1,
        }
        decision, blocked = choose_us_decision(
            profiles("GATECH", "GGI"), owned, {},
            credits=20000, economy_growing=True,
        )
        self.assertIsNone(blocked)
        self.assertEqual(decision.type_id, "GATECH")

    def test_locked_required_building_is_not_skipped(self):
        owned = {type_id: target for type_id, target in US_BUILD_ORDER[:7]}
        decision, blocked = choose_us_decision(
            profiles("GATECH"), owned, {}, credits=1000, economy_growing=False
        )
        self.assertIsNone(decision)
        self.assertEqual(blocked, "GAMERC")

    def test_t3_surplus_keeps_rolling_balanced_groups(self):
        owned = {type_id: target for type_id, target in US_BUILD_ORDER}
        available = profiles("GGI", "RIOT", "FV", "ABRM", "ETNK", "ROBO")
        decision, blocked = choose_us_decision(
            available, owned, {}, credits=50000, economy_growing=True
        )
        self.assertIsNone(blocked)
        self.assertIn(decision.type_id, {"GGI", "RIOT", "FV", "ABRM", "ETNK", "ROBO"})

    def test_video_convoy_keeps_fifteen_fixed_seats_without_gi(self):
        requirements = choose_us_convoy_requirements({})
        self.assertEqual(sum(count for _, count, _ in requirements), 15)
        counts = {type_id: count for type_id, count, _ in requirements}
        self.assertEqual(counts, {"TANY": 1, "ENGINEER": 4, "GGI": 5, "GHOST": 5})
        self.assertNotIn("E1", counts)

    def test_enemy_contact_does_not_switch_loaded_passenger_plan(self):
        requirements = choose_us_convoy_requirements({"armored": 6})
        self.assertEqual(requirements, choose_us_convoy_requirements({"combat_infantry": 30}))

    def test_mass_infantry_adds_external_riot_units_without_changing_seats(self):
        normal = dict((type_id, count) for type_id, count, _ in choose_us_convoy_escort_requirements({}))
        mass = dict((type_id, count) for type_id, count, _ in choose_us_convoy_escort_requirements({"combat_infantry": 25}))
        self.assertEqual(normal["RIOT"], 5)
        self.assertEqual(mass["RIOT"], 8)
        self.assertEqual(mass["ABRM"], 3)
        self.assertEqual(mass["ETNK"], 1)
        self.assertEqual(mass["ROBO"], 2)

    def test_combat_log_does_not_use_hidden_enemy_entities(self):
        with tempfile.TemporaryDirectory() as folder:
            log = CombatExperimentLog(Path(folder) / "combat.jsonl", disengage_seconds=0)
            state = {
                "game": {"running": True, "frame": 1},
                "visible_entities": [
                    {"visible": False, "relation": "enemy", "alive": True,
                     "kind": "vehicle", "type_id": "HTNK"},
                ],
            }
            log.observe(state, now=1)
            self.assertIsNone(log.active)
            self.assertFalse(log.path.exists())


if __name__ == "__main__":
    unittest.main()
