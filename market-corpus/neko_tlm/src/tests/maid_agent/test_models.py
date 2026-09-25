import importlib
import unittest

from ._bootstrap import bootstrap

bootstrap()

ActionTracker = importlib.import_module(
    "neko_tlm.maid_agent.models"
).ActionTracker
_registry = importlib.import_module("neko_tlm.maid_agent.registry")
ActionRegistry = _registry.ActionRegistry
ActionValidationError = _registry.ActionValidationError


class ActionTrackerTests(unittest.TestCase):
    def test_preserves_java_progress_detail_for_skill_projection(self):
        tracker = ActionTracker()
        record, accepted = tracker.apply({
            "action_id": "mining",
            "generation": 1,
            "sequence": 2,
            "kind": "autonomous_mining",
            "status": "RUNNING",
            "stage": "HARVESTING",
            "detail": {"collected_count": 4, "target_count": 10},
        })
        self.assertTrue(accepted)
        self.assertEqual(4, record.detail["collected_count"])
        self.assertEqual(record.detail, record.as_dict()["detail"])

    def test_rejects_old_generation_and_duplicate_sequence(self):
        tracker = ActionTracker()
        first, accepted = tracker.apply({
            "action_id": "action",
            "generation": 2,
            "sequence": 4,
            "status": "RUNNING",
            "stage": "MOVING",
        })
        self.assertTrue(accepted)

        duplicate, accepted = tracker.apply({
            "action_id": "action", "generation": 2, "sequence": 4,
            "status": "SUCCEEDED",
        })
        self.assertFalse(accepted)
        self.assertIs(duplicate, first)
        self.assertEqual("RUNNING", first.status)

        _, accepted = tracker.apply({
            "action_id": "action", "generation": 1, "sequence": 99,
            "status": "SUCCEEDED",
        })
        self.assertFalse(accepted)

        replacement, accepted = tracker.apply({
            "action_id": "action", "generation": 3, "sequence": 0,
            "status": "RUNNING", "stage": "PATHFINDING",
        })
        self.assertTrue(accepted)
        self.assertEqual(3, replacement.generation)
        self.assertEqual("PATHFINDING", replacement.stage)

    def test_terminal_cannot_regress_to_running(self):
        tracker = ActionTracker()
        tracker.apply({
            "action_id": "done", "generation": 1, "sequence": 2,
            "status": "SUCCEEDED",
        })
        record, accepted = tracker.apply({
            "action_id": "done", "generation": 1, "sequence": 3,
            "status": "RUNNING",
        })
        self.assertFalse(accepted)
        self.assertEqual("SUCCEEDED", record.status)


class ActionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = ActionRegistry()

    def test_normalizes_navigate_defaults(self):
        args = self.registry.normalize("navigate", {"target": {"x": 1, "y": 64, "z": -2}})
        self.assertEqual({"x": 1, "y": 64, "z": -2}, args["target"])
        self.assertEqual(0.7, args["speed"])
        self.assertEqual(1.5, args["stop_distance"])

    def test_normalizes_return_to_position_defaults(self):
        args = self.registry.normalize(
            "return_to_position",
            {"target": {"x": 10, "y": 72, "z": -4}},
        )
        self.assertEqual({"x": 10, "y": 72, "z": -4}, args["target"])
        self.assertEqual(0.7, args["speed"])
        self.assertEqual(1.5, args["stop_distance"])
        self.assertEqual("recorded_tunnels_first", args["route_policy"])
        self.assertEqual(
            "safe_support_and_water_seal", args["placement_policy"]
        )
        self.assertEqual(0, args["max_placements"])
        self.assertNotIn("operation_id", args)

    def test_normalizes_return_to_position_with_y_only(self):
        args = self.registry.normalize(
            "return_to_position", {"target": {"y": 96}}
        )
        self.assertEqual({"y": 96}, args["target"])

        for target in ({"x": 1, "y": 96}, {"y": 96, "z": 2}):
            with self.subTest(target=target), self.assertRaises(
                ActionValidationError
            ):
                self.registry.normalize(
                    "return_to_position", {"target": target}
                )

    def test_normalizes_simple_return_destinations(self):
        for destination in ("surface", "mine_entry", "player"):
            with self.subTest(destination=destination):
                args = self.registry.normalize(
                    "return_to_position", {"destination": destination}
                )
                self.assertEqual(destination, args["destination"])
                self.assertNotIn("target", args)

        for args in (
            {"destination": "somewhere"},
            {
                "destination": "surface",
                "target": {"x": 1, "y": 2, "z": 3},
            },
        ):
            with self.subTest(args=args), self.assertRaises(
                ActionValidationError
            ):
                self.registry.normalize("return_to_position", args)

    def test_remote_player_recall_handoff_is_explicit_and_player_only(self):
        args = self.registry.normalize("return_to_position", {
            "destination": "player",
            "handoff_to_follow": True,
        })
        self.assertTrue(args["handoff_to_follow"])

        with self.assertRaises(ActionValidationError):
            self.registry.normalize("return_to_position", {
                "destination": "surface",
                "handoff_to_follow": True,
            })
        with self.assertRaises(ActionValidationError):
            self.registry.normalize("return_to_position", {
                "destination": "player",
                "handoff_to_follow": "true",
            })

    def test_normalizes_return_to_position_operation_and_policy(self):
        args = self.registry.normalize("return_to_position", {
            "target": {"x": 1, "y": 80, "z": 2},
            "operation_id": "A5ED1E8B-F31A-4E7A-944D-97806F20B212",
            "route_policy": "SAFE_SHORTEST",
            "placement_policy": "disabled",
            "max_placements": 12,
            "speed": 0.8,
            "stop_distance": 2,
        })
        self.assertEqual(
            "a5ed1e8b-f31a-4e7a-944d-97806f20b212",
            args["operation_id"],
        )
        self.assertEqual("safe_shortest", args["route_policy"])
        self.assertEqual("disabled", args["placement_policy"])
        self.assertEqual(12, args["max_placements"])

    def test_rejects_invalid_return_to_position_contract(self):
        invalid = (
            {},
            {"target": {"x": 1, "y": 2, "z": 3}, "operation_id": "not-a-uuid"},
            {"target": {"x": 1, "y": 2, "z": 3}, "route_policy": "teleport"},
            {"target": {"x": 1, "y": 2, "z": 3}, "placement_policy": "unsafe"},
            {"target": {"x": 1, "y": 2, "z": 3}, "max_placements": 4097},
            {"target": {"x": 1, "y": 2, "z": 3}, "player_clearance": False},
        )
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ActionValidationError):
                self.registry.normalize("return_to_position", args)

    def test_normalizes_excavate_segment_contract(self):
        args = self.registry.normalize(
            "excavate_segment",
            {"direction": "WEST", "shape": "staircase_down", "length": 8},
        )
        self.assertEqual({
            "direction": "west",
            "shape": "staircase_down",
            "length": 8,
        }, args)

    def test_rejects_invalid_excavate_segment_geometry(self):
        invalid = (
            {"direction": "up", "shape": "level", "length": 1},
            {"direction": "north", "shape": "shaft", "length": 1},
            {"direction": "north", "shape": "level", "length": 9},
        )
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ActionValidationError):
                self.registry.normalize("excavate_segment", args)

    def test_normalizes_autonomous_mining_java_contract(self):
        args = self.registry.normalize("autonomous_mining", {
            "selector": {"type": "tag", "id": "Minecraft:Diamond_Ores"},
            "target_count": 16,
            "direction": "WEST",
            "shape": "staircase_down",
            "segment_length": 6,
            "speed": 0.8,
            "discovery_mode": "exposed_only",
            "placement_policy": "disabled",
            "max_placements": 12,
        })
        self.assertEqual({
            "selector": {"type": "tag", "id": "minecraft:diamond_ores"},
            "target_count": 16,
            "direction": "west",
            "shape": "staircase_down",
            "segment_length": 6,
            "speed": 0.8,
            "discovery_mode": "exposed_only",
            "placement_policy": "disabled",
            "max_placements": 12,
        }, args)

    def test_autonomous_mining_defaults_are_java_owned_auto(self):
        args = self.registry.normalize("autonomous_mining", {
            "selector": {"type": "block", "id": "mod:tin_ore"},
        })
        self.assertEqual(1, args["target_count"])
        self.assertEqual("auto", args["direction"])
        self.assertEqual("auto", args["shape"])
        self.assertEqual(8, args["segment_length"])
        self.assertEqual(0.7, args["speed"])
        self.assertEqual("loaded_scan", args["discovery_mode"])
        self.assertEqual(
            "disabled", args["placement_policy"]
        )
        self.assertEqual(0, args["max_placements"])

    def test_rejects_unknown_or_out_of_range_autonomous_mining_fields(self):
        base = {"selector": {"type": "tag", "id": "minecraft:coal_ores"}}
        invalid = (
            {**base, "direction": "up"},
            {**base, "shape": "shaft"},
            {**base, "segment_length": 9},
            {**base, "speed": 0.1},
            {**base, "discovery_mode": "xray"},
            {**base, "placement_policy": "unsafe_everything"},
            {**base, "max_placements": -1},
            {**base, "max_placements": 4097},
            {**base, "route": [{"x": 0, "y": 0, "z": 0}]},
        )
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ActionValidationError):
                self.registry.normalize("autonomous_mining", args)

    def test_harvest_requires_exactly_one_targeting_mode(self):
        with self.assertRaises(ActionValidationError):
            self.registry.normalize("harvest_blocks", {})
        with self.assertRaises(ActionValidationError):
            self.registry.normalize("harvest_blocks", {
                "target_pos": {"x": 0, "y": 64, "z": 0},
                "selector": {"type": "block", "id": "minecraft:stone"},
            })

    def test_normalizes_harvest_selector(self):
        args = self.registry.normalize("harvest_blocks", {
            "selector": {"type": "tag", "id": "minecraft:base_stone_overworld"},
            "search_radius": 8,
            "max_blocks": 2,
        })
        self.assertEqual("tag", args["selector"]["type"])
        self.assertEqual(8, args["search_radius"])
        self.assertEqual("require_correct", args["tool_policy"])
        self.assertFalse(args["vein_mining"])

    def test_ore_selectors_default_to_whole_vein(self):
        selectors = (
            {"type": "tag", "id": "minecraft:diamond_ores"},
            {"type": "tag", "id": "c:ores/diamond"},
            {"type": "block", "id": "minecraft:deepslate_diamond_ore"},
        )
        for selector in selectors:
            with self.subTest(selector=selector):
                args = self.registry.normalize("harvest_blocks", {
                    "selector": selector,
                })
                self.assertTrue(args["vein_mining"])
                self.assertEqual(1, args["max_blocks"])

    def test_explicit_vein_count_and_single_block_override(self):
        selector = {"type": "tag", "id": "minecraft:iron_ores"}
        limited = self.registry.normalize("harvest_blocks", {
            "selector": selector,
            "max_blocks": 12,
        })
        self.assertTrue(limited["vein_mining"])
        self.assertEqual(12, limited["max_blocks"])

        single = self.registry.normalize("harvest_blocks", {
            "selector": selector,
            "vein_mining": False,
            "max_blocks": 1,
        })
        self.assertFalse(single["vein_mining"])
        self.assertEqual(1, single["max_blocks"])

    def test_non_vein_targets_keep_eight_block_limit(self):
        selector = {"type": "tag", "id": "minecraft:logs"}
        args = self.registry.normalize("harvest_blocks", {"selector": selector})
        self.assertFalse(args["vein_mining"])
        self.assertEqual(1, args["max_blocks"])
        with self.assertRaisesRegex(ActionValidationError, "between 1 and 8"):
            self.registry.normalize("harvest_blocks", {
                "selector": selector,
                "max_blocks": 9,
            })

    def test_vein_mining_requires_boolean_and_selector(self):
        with self.assertRaisesRegex(ActionValidationError, "must be a boolean"):
            self.registry.normalize("harvest_blocks", {
                "selector": {"type": "tag", "id": "minecraft:coal_ores"},
                "vein_mining": "true",
            })
        with self.assertRaisesRegex(ActionValidationError, "requires selector"):
            self.registry.normalize("harvest_blocks", {
                "target_pos": {"x": 0, "y": 64, "z": 0},
                "vein_mining": True,
            })

    def test_harvest_without_mining_plan_preserves_legacy_shape(self):
        args = self.registry.normalize("harvest_blocks", {
            "selector": {"type": "tag", "id": "minecraft:coal_ores"},
        })
        self.assertNotIn("mining_plan", args)
        self.assertTrue(args["vein_mining"])

    def test_normalizes_forward_tunnel_mining_plan_defaults(self):
        args = self.registry.normalize("harvest_blocks", {
            "selector": {"type": "tag", "id": "minecraft:iron_ores"},
            "max_blocks": 4,
            "mining_plan": {"mode": "forward_tunnel"},
        })
        self.assertEqual(4, args["max_blocks"])
        self.assertEqual({
            "mode": "forward_tunnel",
            "direction": "maid_facing",
            "max_distance": 8,
            "max_depth": 0,
            "max_segments": 1,
            "excavation_budget": 24,
        }, args["mining_plan"])

    def test_normalizes_staircase_and_auto_depth_defaults(self):
        for mode in ("staircase_down", "auto"):
            with self.subTest(mode=mode):
                args = self.registry.normalize("harvest_blocks", {
                    "selector": {"type": "tag", "id": "minecraft:diamond_ores"},
                    "mining_plan": {"mode": mode, "direction": "north"},
                })
                self.assertEqual(4, args["mining_plan"]["max_depth"])
                self.assertEqual("north", args["mining_plan"]["direction"])
                self.assertEqual(1, args["mining_plan"]["max_segments"])
                self.assertEqual(24, args["mining_plan"]["excavation_budget"])

    def test_multi_segment_plan_gets_larger_default_budget(self):
        args = self.registry.normalize("harvest_blocks", {
            "selector": {"type": "tag", "id": "minecraft:diamond_ores"},
            "mining_plan": {"mode": "auto", "max_segments": 3},
        })
        self.assertEqual(3, args["mining_plan"]["max_segments"])
        self.assertEqual(64, args["mining_plan"]["excavation_budget"])

        explicit = self.registry.normalize("harvest_blocks", {
            "selector": {"type": "tag", "id": "minecraft:diamond_ores"},
            "mining_plan": {
                "mode": "auto", "max_segments": 4,
                "excavation_budget": 256,
            },
        })
        self.assertEqual(4, explicit["mining_plan"]["max_segments"])
        self.assertEqual(256, explicit["mining_plan"]["excavation_budget"])

    def test_rejects_non_nearby_plan_for_explicit_target(self):
        with self.assertRaisesRegex(ActionValidationError, "require selector"):
            self.registry.normalize("harvest_blocks", {
                "target_pos": {"x": 0, "y": 64, "z": 0},
                "mining_plan": {"mode": "auto"},
            })

    def test_rejects_forward_tunnel_depth_and_invalid_plan_bounds(self):
        selector = {"type": "tag", "id": "minecraft:coal_ores"}
        with self.assertRaisesRegex(ActionValidationError, "max_depth=0"):
            self.registry.normalize("harvest_blocks", {
                "selector": selector,
                "mining_plan": {"mode": "forward_tunnel", "max_depth": 1},
            })
        with self.assertRaisesRegex(ActionValidationError, "positive"):
            self.registry.normalize("harvest_blocks", {
                "selector": selector,
                "mining_plan": {"mode": "staircase_down", "max_depth": 0},
            })
        with self.assertRaisesRegex(ActionValidationError, "max_distance >= max_depth"):
            self.registry.normalize("harvest_blocks", {
                "selector": selector,
                "mining_plan": {
                    "mode": "staircase_down", "max_distance": 2, "max_depth": 3,
                },
            })
        with self.assertRaisesRegex(ActionValidationError, "max_distance > max_depth"):
            self.registry.normalize("harvest_blocks", {
                "selector": selector,
                "mining_plan": {"mode": "auto", "max_distance": 4, "max_depth": 4},
            })
        with self.assertRaisesRegex(ActionValidationError, "between 1 and 16"):
            self.registry.normalize("harvest_blocks", {
                "selector": selector,
                "mining_plan": {"mode": "auto", "max_distance": 17},
            })
        for max_segments in (0, 5):
            with self.subTest(max_segments=max_segments):
                with self.assertRaisesRegex(ActionValidationError, "between 1 and 4"):
                    self.registry.normalize("harvest_blocks", {
                        "selector": selector,
                        "mining_plan": {
                            "mode": "auto", "max_segments": max_segments,
                        },
                    })
        with self.assertRaisesRegex(ActionValidationError, "between 0 and 256"):
            self.registry.normalize("harvest_blocks", {
                "selector": selector,
                "mining_plan": {
                    "mode": "auto", "excavation_budget": 257,
                },
            })
        with self.assertRaisesRegex(ActionValidationError, "unsupported fields"):
            self.registry.normalize("harvest_blocks", {
                "selector": selector,
                "mining_plan": {"mode": "auto", "branch_length": 8},
            })


if __name__ == "__main__":
    unittest.main()
