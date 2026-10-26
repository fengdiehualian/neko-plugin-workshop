from __future__ import annotations

import unittest

from mo_companion.building_command import (
    building_query_categories,
    building_record,
    combat_target_priority,
    is_valid_attack_target,
    load_building_database,
    select_assault_target,
    strategic_building_priority,
)


class BuildingCommandDatabaseTests(unittest.TestCase):
    @staticmethod
    def _entity(object_id, kind, x, y, **extra):
        return {
            "object_id": object_id, "kind": kind, "cell": {"x": x, "y": y},
            "alive": True, "visible": True, **extra,
        }

    def test_four_factions_and_twelve_subfactions_are_registered(self):
        database = load_building_database()
        self.assertEqual(set(database["factions"]), {"allied", "soviet", "epsilon", "foehn"})
        self.assertEqual(sum(len(item["subfactions"]) for item in database["factions"].values()), 12)

    def test_power_category_resolves_all_four_factions(self):
        categories = building_query_categories("优先攻击敌人的电厂")
        self.assertEqual(categories, {"power"})
        ids = {"GAPOWR", "NAPOWR", "YAPOWR", "FATRAP"}
        self.assertEqual({building_record(item)["category"] for item in ids}, {"power"})

    def test_real_foehn_building_beats_map_decoration(self):
        real = {"type_id": "FACNST", "kind": "building"}
        decoration = {"type_id": "CITY01", "kind": "building"}
        self.assertEqual(strategic_building_priority(real), 100)
        self.assertEqual(strategic_building_priority(decoration), -1)
        self.assertTrue(is_valid_attack_target(real))
        self.assertFalse(is_valid_attack_target(decoration))

    def test_attached_upgrade_points_to_parent_and_is_not_independent_target(self):
        turbine = building_record("GAPOWRUP")
        self.assertEqual(turbine["parent"], "GAPOWR")
        self.assertTrue(turbine["is_upgrade"])
        self.assertFalse(turbine["strategic_target"])

    def test_each_mcv_maps_to_its_real_construction_yard(self):
        self.assertEqual(
            {building_record(type_id)["deploys_to"] for type_id in ("AMCV", "SMCV", "PCV", "FMCV")},
            {"GACNST", "NACNST", "YACNST", "FACNST"},
        )

    def test_civilians_are_never_automatic_attack_targets(self):
        civilian = {
            "type_id": "CIVSF", "kind": "infantry", "relation": "enemy",
            "flags": {"civilian": True},
        }
        self.assertFalse(is_valid_attack_target(civilian))
        self.assertEqual(combat_target_priority(civilian), -1)

    def test_combat_unit_outranks_every_static_building(self):
        factory = {"type_id": "FAWEAP", "kind": "building"}
        tank = {"type_id": "HTNK", "kind": "vehicle"}
        self.assertGreater(combat_target_priority(tank), combat_target_priority(factory))

    def test_rules_catalog_defenses_are_recognized_without_static_database_rows(self):
        for type_id in ("TESLA", "NALASR", "GAPILL", "YARAIL", "YAHADE", "GTGCAN", "FAAVAL", "FARAIL"):
            defense = {"type_id": type_id, "kind": "building"}
            self.assertTrue(is_valid_attack_target(defense), type_id)
            self.assertEqual(combat_target_priority(defense), 110, type_id)

    def test_base_assault_clears_local_combat_before_buildings(self):
        unit = self._entity(1, "infantry", 10, 10, type_id="E1")
        yard = self._entity(10, "building", 20, 20, type_id="GACNST")
        defense = self._entity(11, "building", 19, 20, building_class="defense")
        enemy = self._entity(12, "vehicle", 21, 20, type_id="MTNK")
        selected = select_assault_target([unit], [yard, defense], [yard, defense, enemy])
        self.assertEqual(selected["object_id"], 12)

    def test_base_assault_destroys_defense_before_production_and_yard(self):
        unit = self._entity(1, "infantry", 10, 10, type_id="E1")
        yard = self._entity(10, "building", 20, 20, type_id="GACNST")
        factory = self._entity(11, "building", 18, 20, type_id="GAWEAP")
        defense = self._entity(12, "building", 22, 20, building_class="defense")
        selected = select_assault_target([unit], [yard, factory, defense])
        self.assertEqual(selected["object_id"], 12)

    def test_production_is_selected_before_nearer_technology(self):
        unit = self._entity(1, "infantry", 10, 10, type_id="E1")
        yard = self._entity(10, "building", 20, 20, type_id="GACNST")
        barracks = self._entity(11, "building", 19, 20, type_id="GAPILE")
        factory = self._entity(12, "building", 18, 20, type_id="GAWEAP")
        tech = self._entity(13, "building", 11, 10, type_id="GATECH")
        selected = select_assault_target([unit], [yard, barracks, factory, tech])
        self.assertEqual(selected["object_id"], 12)

    def test_power_and_factory_share_production_layer_then_nearest_wins(self):
        seal = self._entity(1, "infantry", 10, 10, type_id="GHOST")
        yard = self._entity(10, "building", 20, 20, type_id="GACNST")
        factory = self._entity(11, "building", 13, 10, type_id="GAWEAP")
        power = self._entity(12, "building", 19, 20, type_id="GAPOWR")
        selected = select_assault_target([seal], [yard, factory, power])
        self.assertEqual(selected["object_id"], 11)

    def test_technology_is_selected_before_a_nearer_construction_yard(self):
        unit = self._entity(1, "infantry", 10, 10, type_id="E1")
        yard = self._entity(10, "building", 11, 10, type_id="GACNST")
        tech = self._entity(11, "building", 20, 20, type_id="GATECH")
        self.assertEqual(
            select_assault_target([unit], [yard, tech])["object_id"], 11
        )

    def test_construction_yard_is_last_base_layer(self):
        unit = self._entity(1, "infantry", 10, 10, type_id="E1")
        yard = self._entity(10, "building", 11, 10, type_id="GACNST")
        power = self._entity(11, "building", 20, 20, type_id="GAPOWR")
        self.assertEqual(
            select_assault_target([unit], [yard, power])["object_id"], 11
        )
        self.assertEqual(select_assault_target([unit], [yard])["object_id"], 10)


if __name__ == "__main__":
    unittest.main()
