from __future__ import annotations

import unittest

from mo_companion.base_development import (
    choose_adaptive_defense,
    choose_anti_infantry_defense,
    choose_building,
    filter_defense_profiles_for_power,
    has_established_technology_center,
    power_reserve_allows_defense,
    power_reserve_allows_nasam,
)
from mo_companion.unit_knowledge import load_catalog, unit_profile
from mo_companion.unit_threat import summarize_visible_enemy_threat


class UnitThreatKnowledgeTests(unittest.TestCase):
    def test_all_four_factions_have_complete_combat_classification(self):
        seen = set()
        classified = 0
        for type_id, record in load_catalog().items():
            if record.get("kind") not in {"infantry", "vehicle", "aircraft"}:
                continue
            profile = unit_profile(type_id)
            self.assertIsNotNone(profile)
            for key in (
                "tech_level", "tech_tier", "battlefield_class", "threat_level",
                "threat_score", "anti_infantry", "anti_armor", "anti_air",
                "area_anti_infantry",
            ):
                self.assertIn(key, profile)
            seen.update(profile["factions"])
            classified += 1
        self.assertGreaterEqual(classified, 500)
        self.assertTrue({"allied", "soviet", "epsilon", "foehn"} <= seen)

    def test_all_faction_basic_infantry_contributes_to_one_pressure_total(self):
        entities = [
            {"type_id": type_id, "kind": "infantry", "relation": "enemy",
             "alive": True, "visible": True}
            for type_id in ("E1", "E2", "INIT", "KNIGHT")
        ]
        summary = summarize_visible_enemy_threat(entities, unit_profile)
        self.assertEqual(summary["combat_infantry"], 4)
        self.assertEqual(set(summary["by_faction"]), {
            "allied", "soviet", "epsilon", "foehn",
        })

    def test_engineer_does_not_count_as_combat_infantry(self):
        entities = [{
            "type_id": "ENGINEER", "kind": "infantry", "relation": "enemy",
            "alive": True, "visible": True,
        }]
        self.assertEqual(
            summarize_visible_enemy_threat(entities, unit_profile)["combat_infantry"],
            0,
        )

    def test_anti_air_detection_does_not_confuse_tesla_ap_with_aa(self):
        self.assertFalse(unit_profile("HTNK")["anti_air"])
        self.assertTrue(unit_profile("KNIGHT")["anti_air"])

    def test_twenty_five_infantry_triggers_first_machine_gun_pillbox(self):
        pillbox = {"type_id": "GAPILL", "kind": "building"}
        self.assertIsNone(choose_anti_infantry_defense([pillbox], {}, {}, 24))
        choice = choose_anti_infantry_defense([pillbox], {}, {}, 25)
        self.assertIs(choice[0], pillbox)
        self.assertIn("25", choice[1])

    def test_pressure_scales_without_overproducing_existing_pillboxes(self):
        pillbox = {"type_id": "GAPILL", "kind": "building"}
        self.assertIsNone(
            choose_anti_infantry_defense([pillbox], {"GAPILL": 2}, {}, 40)
        )
        self.assertIsNotNone(
            choose_anti_infantry_defense([pillbox], {"GAPILL": 1}, {}, 40)
        )

    def test_base_attack_immediately_requests_static_defense(self):
        pillbox = {
            "type_id": "GAPILL", "kind": "building",
            "building_class": "defense", "combat_relevant": True,
            "anti_infantry": True, "cost": 400,
        }
        choice = choose_adaptive_defense(
            [pillbox], {}, {}, {"combat_units": 2, "combat_infantry": 2},
            under_attack=True, credits=1500,
        )
        self.assertIs(choice[0], pillbox)

    def test_rich_economy_builds_defense_before_the_base_is_hit(self):
        pillbox = {
            "type_id": "GAPILL", "kind": "building",
            "building_class": "defense", "combat_relevant": True,
            "anti_infantry": True, "cost": 400,
        }
        self.assertIsNotNone(choose_adaptive_defense(
            [pillbox], {"GAPILL": 1}, {}, {},
            under_attack=False, credits=10000,
        ))
        self.assertIsNone(choose_adaptive_defense(
            [pillbox], {"GAPILL": 2}, {}, {},
            under_attack=False, credits=10000,
        ))

    def test_rich_economy_balances_pillbox_and_fixed_gun(self):
        pillbox = {
            "type_id": "GAPILL", "kind": "building",
            "building_class": "defense", "combat_relevant": True,
            "anti_infantry": True, "cost": 400,
        }
        gun = {
            "type_id": "GAGUN", "kind": "building",
            "building_class": "defense", "combat_relevant": True,
            "anti_armor": True, "cost": 800,
        }
        selected, _ = choose_adaptive_defense(
            [pillbox, gun], {"GAPILL": 1}, {}, {},
            under_attack=False, credits=20000,
        )
        self.assertEqual(selected["type_id"], "GAGUN")

    def test_defense_requires_a_real_power_reserve(self):
        self.assertFalse(power_reserve_allows_defense(None))
        self.assertFalse(power_reserve_allows_defense(0))
        self.assertFalse(power_reserve_allows_defense(99))
        self.assertTrue(power_reserve_allows_defense(100))

    def test_nasam_requires_stable_three_hundred_power_reserve(self):
        self.assertFalse(power_reserve_allows_nasam(500, power_stable=False))
        self.assertFalse(power_reserve_allows_nasam(299, power_stable=True))
        self.assertTrue(power_reserve_allows_nasam(300, power_stable=True))
        self.assertFalse(
            power_reserve_allows_nasam(
                500, power_stable=True, planned_power_drain=250
            )
        )

    def test_allied_low_power_defense_filters_nasam_to_safe_pair(self):
        profiles = [
            {"type_id": "NASAM", "kind": "building", "building_class": "defense"},
            {"type_id": "GAPILL", "kind": "building", "building_class": "defense"},
            {"type_id": "GAGUN", "kind": "building", "building_class": "defense"},
        ]
        selected = filter_defense_profiles_for_power(
            profiles, power_surplus=299, power_stable=True
        )
        self.assertEqual(
            {item["type_id"] for item in selected}, {"GAPILL", "GAGUN"}
        )

    def test_allied_stable_reserve_releases_nasam_candidate(self):
        profiles = [
            {"type_id": "NASAM", "kind": "building", "building_class": "defense"},
            {"type_id": "GAPILL", "kind": "building", "building_class": "defense"},
        ]
        selected = filter_defense_profiles_for_power(
            profiles, power_surplus=300, power_stable=True
        )
        self.assertEqual({item["type_id"] for item in selected}, {"NASAM", "GAPILL"})

    def test_adaptive_defense_path_cannot_select_nasam_before_power_gate(self):
        nasam = {
            "type_id": "NASAM", "kind": "building", "building_class": "defense",
            "combat_relevant": True, "anti_air": True, "cost": 800,
        }
        pillbox = {
            "type_id": "GAPILL", "kind": "building", "building_class": "defense",
            "combat_relevant": True, "anti_infantry": True, "cost": 400,
        }
        selected, _ = choose_adaptive_defense(
            [nasam, pillbox], {}, {}, {"air": 20, "combat_units": 20},
            under_attack=True, credits=20000, power_surplus=299,
            power_stable=True,
        )
        self.assertEqual(selected["type_id"], "GAPILL")

    def test_ore_expansion_requires_a_placed_technology_center(self):
        self.assertFalse(has_established_technology_center([]))
        self.assertTrue(has_established_technology_center([
            {"type_id": "GATECH", "kind": "building", "relation": "self",
             "alive": True, "visible": True},
        ]))

    def test_unknown_power_telemetry_still_builds_first_power_plant(self):
        power = {
            "type_id": "GAPOWR", "kind": "building",
            "building_class": "power", "cost": 800,
        }
        pillbox = {
            "type_id": "GAPILL", "kind": "building",
            "building_class": "defense", "combat_relevant": True,
            "anti_infantry": True, "cost": 400,
        }
        selected, _ = choose_building(
            [pillbox, power], {}, {}, power_surplus=None, defense_target=4
        )
        self.assertEqual(selected["type_id"], "GAPOWR")


if __name__ == "__main__":
    unittest.main()
