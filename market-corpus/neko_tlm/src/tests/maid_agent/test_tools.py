import importlib
import unittest
from unittest.mock import patch

from ._bootstrap import bootstrap_sdk

bootstrap_sdk()

tools = importlib.import_module("neko_tlm.tools")


class RuntimeOk:
    """模拟真实 N.E.K.O 运行时中 plugin.sdk.plugin.Ok 的最小结构。"""

    def __init__(self, value):
        self.value = value

    def is_err(self):
        return False

    def value_or_none(self):
        return self.value


class FakePlugin:
    connected = True

    class Logger:
        def info(self, *_args, **_kwargs):
            pass

        def warning(self, *_args, **_kwargs):
            pass

    logger = Logger()

    def __init__(self, response):
        self.response = response
        self.requests = []
        self._maid_action_service = None
        self._maid_status_cache = {}

    def _resolve_maid_id(self, maid_id=None):
        return maid_id or "maid-1"

    async def _send_request(self, request, timeout=30):
        self.requests.append(request)
        return self.response(request) if callable(self.response) else self.response

    async def _push_minecraft_context(self, *args, **kwargs):
        pass


class FakeDirector:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def set_activity(self, target, **kwargs):
        self.calls.append((target, kwargs))
        return self.result


class MaidActionToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_combat_guard_uses_tlm_authoritative_mod_weapon_verdict(self):
        plugin = FakePlugin({
            "type": "game_context",
            "data": {
                "main_hand": "example_mod:unfamiliar_weapon",
                "combat_task_compatibility": {
                    "touhou_little_maid:attack": True,
                },
            },
        })

        result = await tools._guard_combat_task_equipment(plugin, "打怪")

        self.assertIsNone(result)
        self.assertEqual("equipment", plugin.requests[0]["data"]["category"])

    async def test_combat_guard_rejects_authoritative_incompatible_item(self):
        plugin = FakePlugin({
            "type": "game_context",
            "data": {
                "main_hand": "example_mod:decorative_blade",
                "combat_task_compatibility": {
                    "touhou_little_maid:attack": False,
                },
            },
        })

        result = await tools._guard_combat_task_equipment(plugin, "打怪")

        self.assertTrue(result["is_error"])

    def test_legacy_bridge_fallback_recognizes_slashblade(self):
        self.assertTrue(
            tools._weapon_matches_combat_task(
                "attack", "slashblade:slashblade"
            )
        )

    async def test_start_builds_normalized_protocol_request(self):
        plugin = FakePlugin({
            "type": "maid_action_start_result",
            "data": {
                "accepted": True, "action_id": "fixed", "maid_id": "maid-1",
                "generation": 1, "sequence": 0, "kind": "navigate",
                "status": "RUNNING", "stage": "PATHFINDING",
            },
        })
        result = await tools.do_start_maid_action(
            plugin,
            kind="navigate",
            action_id="fixed",
            args={"target": {"x": 4, "y": 65, "z": 9}},
        )
        self.assertFalse(result["is_error"])
        request = plugin.requests[0]
        self.assertEqual("start_maid_action", request["type"])
        self.assertEqual("maid-1", request["data"]["maid_id"])
        self.assertEqual(0.7, request["data"]["args"]["speed"])

    async def test_start_rejects_invalid_args_without_request(self):
        plugin = FakePlugin({})
        result = await tools.do_start_maid_action(
            plugin, kind="harvest_blocks", args={}
        )
        self.assertTrue(result["is_error"])
        self.assertEqual("INVALID_ACTION_ARGUMENTS", result["error"])
        self.assertEqual([], plugin.requests)

    async def test_start_passes_normalized_mining_plan_to_server(self):
        plugin = FakePlugin({
            "type": "maid_action_start_result",
            "data": {
                "accepted": True, "action_id": "mine", "maid_id": "maid-1",
                "generation": 1, "sequence": 1, "kind": "harvest_blocks",
                "status": "RUNNING", "stage": "SEARCHING",
            },
        })
        result = await tools.do_start_maid_action(
            plugin,
            kind="harvest_blocks",
            action_id="mine",
            args={
                "selector": {"type": "tag", "id": "minecraft:diamond_ores"},
                "max_blocks": 3,
                "mining_plan": {
                    "mode": "staircase_down",
                    "direction": "west",
                    "max_distance": 12,
                    "max_depth": 6,
                    "excavation_budget": 48,
                },
            },
        )
        self.assertFalse(result["is_error"])
        plan = plugin.requests[0]["data"]["args"]["mining_plan"]
        self.assertTrue(plugin.requests[0]["data"]["args"]["vein_mining"])
        self.assertEqual("staircase_down", plan["mode"])
        self.assertEqual("west", plan["direction"])
        self.assertEqual(12, plan["max_distance"])
        self.assertEqual(6, plan["max_depth"])
        self.assertEqual(1, plan["max_segments"])
        self.assertEqual(48, plan["excavation_budget"])

    async def test_default_ore_request_sends_whole_vein_contract(self):
        plugin = FakePlugin({
            "type": "maid_action_start_result",
            "data": {
                "accepted": True, "action_id": "vein", "maid_id": "maid-1",
                "generation": 1, "sequence": 1, "kind": "harvest_blocks",
                "status": "RUNNING", "stage": "SEARCHING",
            },
        })

        result = await tools.do_start_maid_action(
            plugin,
            kind="harvest_blocks",
            action_id="vein",
            args={"selector": {"type": "tag", "id": "minecraft:diamond_ores"}},
        )

        self.assertFalse(result["is_error"])
        normalized = plugin.requests[0]["data"]["args"]
        self.assertTrue(normalized["vein_mining"])
        self.assertEqual(1, normalized["max_blocks"])
        self.assertNotIn("mining_plan", normalized)
        self.assertEqual(0, plugin.requests[0]["data"]["timeout_ms"])

    async def test_multi_segment_plan_uses_safe_default_budget(self):
        plugin = FakePlugin({
            "type": "maid_action_start_result",
            "data": {
                "accepted": True, "action_id": "segments", "maid_id": "maid-1",
                "generation": 1, "sequence": 1, "kind": "harvest_blocks",
                "status": "RUNNING", "stage": "SEARCHING",
            },
        })
        result = await tools.do_start_maid_action(
            plugin,
            kind="harvest_blocks",
            action_id="segments",
            args={
                "selector": {"type": "tag", "id": "minecraft:diamond_ores"},
                "mining_plan": {"mode": "auto", "max_segments": 2},
            },
        )
        self.assertFalse(result["is_error"])
        plan = plugin.requests[0]["data"]["args"]["mining_plan"]
        self.assertEqual(2, plan["max_segments"])
        self.assertEqual(64, plan["excavation_budget"])

    async def test_start_generates_action_id(self):
        plugin = FakePlugin({
            "type": "maid_action_start_result",
            "data": {"accepted": True, "generation": 1, "status": "RUNNING"},
        })
        await tools.do_start_maid_action(
            plugin,
            kind="navigate",
            args={"target": {"x": 1, "y": 64, "z": 1}},
        )
        self.assertTrue(plugin.requests[0]["data"]["action_id"])
        self.assertEqual(60000, plugin.requests[0]["data"]["timeout_ms"])

    async def test_return_to_position_defaults_to_no_deadline(self):
        plugin = FakePlugin({
            "type": "maid_action_start_result",
            "data": {
                "accepted": True, "action_id": "return", "generation": 1,
                "status": "RUNNING", "kind": "return_to_position",
            },
        })
        result = await tools.do_start_maid_action(
            plugin,
            kind="return_to_position",
            action_id="return",
            args={"destination": "surface"},
        )
        self.assertFalse(result["is_error"])
        payload = plugin.requests[0]["data"]
        self.assertEqual(0, payload["timeout_ms"])
        self.assertEqual("surface", payload["args"]["destination"])
        self.assertEqual(
            "recorded_tunnels_first", payload["args"]["route_policy"]
        )
        self.assertEqual(
            "safe_support_and_water_seal",
            payload["args"]["placement_policy"],
        )
        self.assertEqual(0, payload["args"]["max_placements"])
        self.assertTrue(result["output"]["execution_pending"])
        self.assertFalse(result["output"]["completion_confirmed"])
        self.assertTrue(result["output"]["terminal_event_required"])

    async def test_player_move_outside_simulation_range_starts_agent_recall(self):
        def response(request):
            if request["type"] == "get_game_context":
                return {
                    "type": "game_context",
                    "data": {
                        "maid_dimension": "minecraft:overworld",
                        "owner_dimension": "minecraft:overworld",
                        "within_owner_simulation_distance": False,
                    },
                }
            return {
                "type": "maid_action_start_result",
                "data": {
                    "accepted": True, "action_id": "move", "generation": 1,
                    "status": "RUNNING", "kind": "return_to_position",
                },
            }

        plugin = FakePlugin(response)
        result = await tools.do_move_maid_to(plugin, destination="player")
        self.assertFalse(result["is_error"])
        self.assertEqual("agent_path", result["output"]["recall_mode"])
        self.assertEqual("get_game_context", plugin.requests[0]["type"])
        payload = plugin.requests[1]["data"]
        self.assertEqual("return_to_position", payload["kind"])
        self.assertEqual({
            "destination": "player",
            "speed": 0.7,
            "stop_distance": 1.5,
            "route_policy": "recorded_tunnels_first",
            "placement_policy": "safe_support_and_water_seal",
            "max_placements": 0,
            "handoff_to_follow": True,
        }, payload["args"])
        self.assertEqual(0, payload["timeout_ms"])
        self.assertTrue(payload["replace_existing"])
        self.assertFalse(result["output"]["completion_confirmed"])

    async def test_player_move_inside_simulation_range_uses_native_follow(self):
        def response(request):
            if request["type"] == "get_game_context":
                return {
                    "type": "game_context",
                    "data": {
                        "maid_dimension": "minecraft:overworld",
                        "owner_dimension": "minecraft:overworld",
                        "within_owner_simulation_distance": True,
                    },
                }
            if request["type"] == "command_maid":
                return {
                    "type": "command_result",
                    "data": {"success": True, "state": "following"},
                }
            return {
                "type": "maid_status",
                "data": {"maids": [{
                    "id": "maid-1", "is_following": True,
                    "is_sitting": False,
                }]},
            }

        plugin = FakePlugin(response)
        result = await tools.do_move_maid_to(plugin, destination="player")

        self.assertFalse(result["is_error"])
        self.assertEqual("native_follow", result["output"]["recall_mode"])
        self.assertTrue(result["output"]["verified"])
        self.assertEqual(
            ["get_game_context", "command_maid", "get_maid_status"],
            [request["type"] for request in plugin.requests],
        )
        self.assertFalse(any(
            request["type"] == "start_maid_action"
            for request in plugin.requests
        ))

    async def test_native_recall_accepts_real_sdk_result_objects(self):
        def response(request):
            if request["type"] == "get_game_context":
                return {
                    "type": "game_context",
                    "data": {
                        "maid_dimension": "minecraft:overworld",
                        "owner_dimension": "minecraft:overworld",
                        "within_owner_simulation_distance": True,
                    },
                }
            if request["type"] == "command_maid":
                return {
                    "type": "command_result",
                    "data": {"success": True, "state": "following"},
                }
            return {
                "type": "maid_status",
                "data": {"maids": [{
                    "id": "maid-1",
                    "is_following": True,
                    "is_sitting": False,
                }]},
            }

        with patch.object(tools, "Ok", RuntimeOk):
            result = await tools.do_move_maid_to(
                FakePlugin(response), destination="player"
            )

        self.assertIsInstance(result, RuntimeOk)
        self.assertEqual("native_follow", result.value["recall_mode"])
        self.assertTrue(result.value["verified"])

    async def test_agent_recall_accepts_real_sdk_result_objects(self):
        def response(request):
            if request["type"] == "get_game_context":
                return {
                    "type": "game_context",
                    "data": {
                        "maid_dimension": "minecraft:overworld",
                        "owner_dimension": "minecraft:overworld",
                        "within_owner_simulation_distance": False,
                    },
                }
            return {
                "type": "maid_action_start_result",
                "data": {
                    "accepted": True,
                    "action_id": "runtime-result-move",
                    "generation": 1,
                    "status": "RUNNING",
                    "kind": "return_to_position",
                },
            }

        with patch.object(tools, "Ok", RuntimeOk):
            result = await tools.do_move_maid_to(
                FakePlugin(response), destination="player"
            )

        self.assertIsInstance(result, RuntimeOk)
        self.assertEqual("agent_path", result.value["recall_mode"])
        self.assertEqual("runtime-result-move", result.value["action_id"])

    async def test_player_move_across_dimensions_does_not_enable_native_follow(self):
        plugin = FakePlugin({
            "type": "game_context",
            "data": {
                "maid_dimension": "minecraft:the_nether",
                "owner_dimension": "minecraft:overworld",
                "within_owner_simulation_distance": False,
            },
        })

        result = await tools.do_move_maid_to(plugin, destination="player")

        self.assertTrue(result["is_error"])
        self.assertEqual("OWNER_NOT_IN_MAID_DIMENSION", result["error"])
        self.assertEqual(["get_game_context"], [
            request["type"] for request in plugin.requests
        ])

    async def test_simple_move_tool_rejects_unknown_destination_without_request(self):
        plugin = FakePlugin({})
        result = await tools.do_move_maid_to(plugin, destination="somewhere")
        self.assertTrue(result["is_error"])
        self.assertEqual("INVALID_ACTION_ARGUMENTS", result["error"])
        self.assertEqual([], plugin.requests)

    async def test_simple_move_tool_director_path_is_still_pending(self):
        plugin = FakePlugin({})
        plugin._maid_activity_director = FakeDirector({
            "success": True,
            "target_result": {
                "action_id": "director-move", "kind": "return_to_position",
                "status": "RUNNING",
            },
        })
        result = await tools.do_move_maid_to(plugin, destination="surface")
        self.assertFalse(result["is_error"])
        self.assertTrue(result["output"]["execution_pending"])
        self.assertFalse(result["output"]["completion_confirmed"])
        self.assertTrue(result["output"]["terminal_event_required"])
        self.assertEqual([], plugin.requests)
        self.assertEqual("return_to_position",
                         plugin._maid_activity_director.calls[0][0]["kind"])

    async def test_coordinate_navigation_uses_non_destructive_navigate_action(self):
        plugin = FakePlugin({
            "type": "maid_action_start_result",
            "data": {
                "accepted": True, "action_id": "nav", "generation": 1,
                "status": "RUNNING", "kind": "navigate",
            },
        })

        result = await tools.do_navigate_maid_to(plugin, x=12, y=70, z=-4)

        self.assertFalse(result["is_error"])
        payload = plugin.requests[0]["data"]
        self.assertEqual("navigate", payload["kind"])
        self.assertEqual(
            {"x": 12.0, "y": 70.0, "z": -4.0},
            payload["args"]["target"],
        )
        self.assertEqual(60000, payload["timeout_ms"])

    async def test_coordinate_navigation_requires_all_numeric_coordinates(self):
        plugin = FakePlugin({})
        result = await tools.do_navigate_maid_to(
            plugin, x=12, y=None, z=-4
        )
        self.assertTrue(result["is_error"])
        self.assertEqual("INVALID_ACTION_ARGUMENTS", result["error"])
        self.assertEqual([], plugin.requests)

    async def test_duplicate_semantic_move_reuses_active_action(self):
        plugin = FakePlugin(lambda _request: {
            "type": "game_context",
            "data": {
                "maid_dimension": "minecraft:overworld",
                "owner_dimension": "minecraft:overworld",
                "within_owner_simulation_distance": False,
            },
        })
        plugin._maid_activity_director = FakeDirector({
            "success": True,
            "status": "ALREADY_ACTIVE",
            "final_activity": {
                "active_actions": [{
                    "action_id": "existing-move",
                    "kind": "return_to_position",
                    "args": {
                        "destination": "player",
                        "speed": 0.7,
                        "stop_distance": 1.5,
                        "route_policy": "recorded_tunnels_first",
                        "placement_policy": "safe_support_and_water_seal",
                        "max_placements": 0,
                        "handoff_to_follow": True,
                    },
                    "status": "RUNNING",
                }],
            },
        })

        result = await tools.do_move_maid_to(plugin, destination="player")

        self.assertFalse(result["is_error"])
        self.assertEqual("existing-move", result["output"]["action_id"])
        self.assertEqual("get_game_context", plugin.requests[0]["type"])
        target = plugin._maid_activity_director.calls[0][0]
        self.assertNotIn("action_id", target)

    async def test_completion_confirmation_requires_completed_and_arrived(self):
        base = {
            "kind": "return_to_position", "status": "SUCCEEDED",
            "end_reason": "COMPLETED",
        }
        missing_arrival = tools._action_execution_confirmation({
            **base, "result": {"arrived": False},
        })
        self.assertFalse(missing_arrival["execution_pending"])
        self.assertFalse(missing_arrival["completion_confirmed"])
        confirmed = tools._action_execution_confirmation({
            **base, "result": {"arrived": True},
        })
        self.assertTrue(confirmed["completion_confirmed"])
        missing_reason = tools._action_execution_confirmation({
            "kind": "harvest_blocks", "status": "SUCCEEDED",
        })
        self.assertFalse(missing_reason["completion_confirmed"])
        missing_harvest_contract = tools._action_execution_confirmation({
            "kind": "harvest_blocks",
            "status": "SUCCEEDED",
            "end_reason": "COMPLETED",
            "result": {"harvested": 8, "requested": 8},
        })
        self.assertFalse(missing_harvest_contract["completion_confirmed"])

    async def test_partial_harvest_never_confirms_completion(self):
        partial = tools._action_execution_confirmation({
            "kind": "harvest_blocks",
            "status": "SUCCEEDED",
            "end_reason": "COMPLETED",
            "result": {
                "harvested": 5,
                "requested": 8,
                "partial": True,
                "request_satisfied": False,
            },
        })
        self.assertFalse(partial["completion_confirmed"])
        self.assertFalse(partial["conversation_goal_confirmed"])

        satisfied = tools._action_execution_confirmation({
            "kind": "harvest_blocks",
            "status": "SUCCEEDED",
            "end_reason": "COMPLETED",
            "result": {
                "harvested": 8,
                "requested": 8,
                "partial": False,
                "request_satisfied": True,
            },
        })
        self.assertTrue(satisfied["action_completion_confirmed"])
        self.assertFalse(satisfied["conversation_goal_confirmed"])

    async def test_timeout_zero_is_accepted_but_subsecond_positive_is_rejected(self):
        response = {
            "type": "maid_action_start_result",
            "data": {"accepted": True, "generation": 1, "status": "RUNNING"},
        }
        plugin = FakePlugin(response)
        result = await tools.do_start_maid_action(
            plugin,
            kind="navigate",
            timeout_ms=0,
            args={"target": {"x": 1, "y": 64, "z": 1}},
        )
        self.assertFalse(result["is_error"])
        self.assertEqual(0, plugin.requests[0]["data"]["timeout_ms"])

        rejected = await tools.do_start_maid_action(
            FakePlugin(response),
            kind="navigate",
            timeout_ms=999,
            args={"target": {"x": 1, "y": 64, "z": 1}},
        )
        self.assertTrue(rejected["is_error"])
        self.assertIn("0 (no deadline)", rejected["output"]["error"])

    async def test_ore_selector_forces_no_deadline_over_model_timeout(self):
        plugin = FakePlugin({
            "type": "maid_action_start_result",
            "data": {"accepted": True, "generation": 1, "status": "RUNNING"},
        })
        result = await tools.do_start_maid_action(
            plugin,
            kind="harvest_blocks",
            timeout_ms=120000,
            args={"selector": {"type": "tag", "id": "minecraft:diamond_ores"}},
        )
        self.assertFalse(result["is_error"])
        self.assertEqual(0, plugin.requests[0]["data"]["timeout_ms"])

    async def test_cancel_without_id_uses_latest_active(self):
        plugin = FakePlugin({
            "type": "maid_action_cancel_result",
            "data": {"accepted": True, "action_id": "running", "status": "CANCEL_REQUESTED"},
        })
        service = tools._maid_action_service(plugin)
        service.tracker.apply({
            "action_id": "running", "maid_id": "maid-1", "generation": 1,
            "sequence": 1, "status": "RUNNING",
        })
        result = await tools.do_cancel_maid_action(plugin)
        self.assertFalse(result["is_error"])
        self.assertEqual("running", plugin.requests[0]["data"]["action_id"])

    async def test_status_and_list_observe_server_snapshots(self):
        plugin = FakePlugin({
            "type": "maid_action_status",
            "data": {
                "action_id": "a", "maid_id": "maid-1", "generation": 2,
                "sequence": 5, "kind": "navigate", "status": "SUCCEEDED",
            },
        })
        await tools.do_get_maid_action_status(plugin, action_id="a")
        self.assertEqual("SUCCEEDED", plugin._maid_action_service.tracker.get("a").status)

        plugin.response = {
            "type": "maid_action_list",
            "data": {"actions": [{
                "action_id": "b", "maid_id": "maid-1", "generation": 1,
                "sequence": 1, "kind": "harvest_blocks", "status": "RUNNING",
            }]},
        }
        await tools.do_list_active_maid_actions(plugin)
        self.assertEqual("RUNNING", plugin._maid_action_service.tracker.get("b").status)

    async def test_status_not_found_is_a_tool_error(self):
        plugin = FakePlugin({
            "type": "maid_action_status",
            "data": {"found": False, "error_code": "ACTION_NOT_FOUND"},
        })
        result = await tools.do_get_maid_action_status(plugin, action_id="missing")
        self.assertTrue(result["is_error"])
        self.assertEqual("ACTION_NOT_FOUND", result["error"])

    async def test_status_not_found_uses_observed_terminal_cache(self):
        plugin = FakePlugin({
            "type": "maid_action_status",
            "data": {"found": False, "error_code": "ACTION_NOT_FOUND"},
        })
        service = tools._maid_action_service(plugin)
        service.tracker.apply({
            "action_id": "expired", "maid_id": "maid-1", "generation": 1,
            "sequence": 4, "kind": "navigate", "status": "SUCCEEDED",
            "stage": "ARRIVED", "end_reason": "COMPLETED",
        })

        result = await tools.do_get_maid_action_status(
            plugin, action_id="expired"
        )

        self.assertFalse(result["is_error"])
        self.assertEqual("SUCCEEDED", result["output"]["status"])
        self.assertEqual("local_terminal_cache", result["output"]["source"])
        self.assertFalse(result["output"]["server_found"])

    async def test_list_embedded_error_is_not_hidden_as_empty(self):
        plugin = FakePlugin({
            "type": "maid_action_list",
            "data": {"error": "Server not ready", "error_code": "SERVER_NOT_READY"},
        })
        result = await tools.do_list_active_maid_actions(plugin)
        self.assertTrue(result["is_error"])
        self.assertEqual("SERVER_NOT_READY", result["error"])

    async def test_execute_command_scopes_request_to_assigned_maid_owner(self):
        plugin = FakePlugin({
            "type": "command_execution_result",
            "data": {
                "approved": True,
                "success": True,
                "command": "/time set day",
                "approved_by": "owner",
            },
        })

        result = await tools.do_execute_command(
            plugin, command="  /time set day  "
        )

        self.assertFalse(result["is_error"])
        self.assertEqual("/time set day", plugin.requests[0]["data"]["command"])
        self.assertEqual("maid-1", plugin.requests[0]["data"]["maid_id"])
        self.assertTrue(result["output"]["success"])

    async def test_execute_command_failure_is_a_tool_error(self):
        plugin = FakePlugin({
            "type": "command_execution_result",
            "data": {
                "approved": True,
                "success": False,
                "command": "/unknown",
                "error": "Unknown command",
            },
        })

        result = await tools.do_execute_command(plugin, command="/unknown")

        self.assertTrue(result["is_error"])
        self.assertIn("Unknown command", result["output"]["error"])

    async def test_execute_command_cancellation_is_not_reported_as_rejection(self):
        plugin = FakePlugin({
            "type": "command_execution_result",
            "data": {
                "approved": False,
                "cancelled": True,
                "message": "Command execution was disabled",
            },
        })

        result = await tools.do_execute_command(plugin, command="/time set day")

        self.assertTrue(result["is_error"])
        self.assertEqual("Command execution was disabled", result["output"]["error"])

    async def test_execute_command_requires_an_assigned_maid(self):
        plugin = FakePlugin({})
        plugin._resolve_maid_id = lambda maid_id=None: ""

        result = await tools.do_execute_command(plugin, command="/time set day")

        self.assertTrue(result["is_error"])
        self.assertEqual([], plugin.requests)


if __name__ == "__main__":
    unittest.main()
