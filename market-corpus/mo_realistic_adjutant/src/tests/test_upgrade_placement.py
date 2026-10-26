from __future__ import annotations

import unittest

from mo_companion.base_development import (
    choose_needed_upgrade,
    deduplicate_base_tasks,
    filter_upgrade_profiles,
    upgrade_deficit,
    upgrade_host_candidates,
)


class UpgradePlacementTests(unittest.TestCase):
    def test_duplicate_base_controller_and_its_children_are_retired(self):
        bases = {
            "base-a": {"task_id": "base-a", "status": "starting", "created_at": 1.0},
            "base-b": {"task_id": "base-b", "status": "starting", "created_at": 2.0},
        }
        production = {
            "prod-a": {"owner_base_task_id": "base-a", "status": "queued"},
            "prod-b": {"owner_base_task_id": "base-b", "status": "queued"},
        }
        canonical = deduplicate_base_tasks(bases, production)
        self.assertEqual(canonical["task_id"], "base-a")
        self.assertEqual(bases["base-b"]["status"], "cancelled")
        self.assertEqual(production["prod-a"]["status"], "queued")
        self.assertEqual(production["prod-b"]["status"], "cancelled")

    def test_power_turbine_targets_exact_unused_power_plant(self):
        buildings = [
            {"object_id": 30, "type_id": "GAPOWR", "cell": {"x": 17, "y": 23}},
            {"object_id": 10, "type_id": "GAPOWR", "cell": {"x": 11, "y": 13}},
            {"object_id": 20, "type_id": "GAPILE", "cell": {"x": 7, "y": 9}},
        ]
        self.assertEqual(
            upgrade_host_candidates(buildings, "GAPOWR", {10}),
            [{"x": 17, "y": 23, "target_object_id": 30}],
        )

    def test_occupied_host_is_not_a_placement_candidate(self):
        buildings = [
            {"object_id": 10, "type_id": "GAPOWR", "cell": {"x": 11, "y": 13},
             "upgrade_occupied_count": 1},
            {"object_id": 20, "type_id": "GAPOWR", "cell": {"x": 17, "y": 23},
             "upgrade_occupied_count": 0},
        ]
        self.assertEqual(
            upgrade_host_candidates(buildings, "GAPOWR"),
            [{"x": 17, "y": 23, "target_object_id": 20}],
        )

    def test_invalid_or_missing_host_cells_are_never_submitted(self):
        buildings = [
            {"object_id": 1, "type_id": "GAPOWR"},
            {"object_id": 2, "type_id": "GAPOWR", "cell": {"x": 1}},
            {"object_id": "3", "type_id": "GAPOWR", "cell": {"x": 1, "y": 2}},
        ]
        self.assertEqual(upgrade_host_candidates(buildings, "GAPOWR"), [])

    def test_manual_plugins_remove_the_product_from_candidates(self):
        turbine = {
            "type_id": "GAPOWRUP", "kind": "building",
            "powers_up_building": "GAPOWR",
        }
        hosts = [
            {"object_id": 1, "type_id": "GAPOWR", "kind": "building",
             "upgrade_occupied_count": 1},
            {"object_id": 2, "type_id": "GAPOWR", "kind": "building",
             "upgrade_occupied_count": 1},
        ]
        self.assertEqual(upgrade_deficit(turbine, hosts), 0)
        self.assertEqual(filter_upgrade_profiles([turbine], hosts, {}), [])
        self.assertIsNone(choose_needed_upgrade([turbine], hosts, {}))

    def test_each_new_empty_host_creates_fresh_plugin_demand(self):
        turbine = {
            "type_id": "GAPOWRUP", "kind": "building", "cost": 500,
            "powers_up_building": "GAPOWR",
        }
        hosts = [
            {"object_id": 1, "type_id": "GAPOWR", "kind": "building",
             "upgrade_occupied_count": 1},
            {"object_id": 2, "type_id": "GAPOWR", "kind": "building",
             "upgrade_occupied_count": 0},
        ]
        self.assertEqual(upgrade_deficit(turbine, hosts), 1)
        self.assertIs(choose_needed_upgrade([turbine], hosts, {}), turbine)
        self.assertIsNone(choose_needed_upgrade([turbine], hosts, {"GAPOWRUP": 1}))

    def test_missing_slot_telemetry_still_requests_first_plugin(self):
        turbine = {
            "type_id": "GAPOWRUP", "kind": "building", "cost": 500,
            "powers_up_building": "GAPOWR",
        }
        hosts = [
            {"object_id": 1, "type_id": "GAPOWR", "kind": "building"},
            {"object_id": 2, "type_id": "GAPOWR", "kind": "building"},
        ]
        self.assertEqual(upgrade_deficit(turbine, hosts), 2)
        self.assertIs(choose_needed_upgrade([turbine], hosts, {}), turbine)
        self.assertIsNone(
            choose_needed_upgrade([turbine], hosts, {"GAPOWRUP": 2})
        )


if __name__ == "__main__":
    unittest.main()
