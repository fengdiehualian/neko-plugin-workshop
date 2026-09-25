import importlib
import unittest

from ._bootstrap import bootstrap

bootstrap()

MaidActionService = importlib.import_module(
    "neko_tlm.maid_agent.service"
).MaidActionService


class FakePlugin:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.requests = []
        self.pushes = []

    async def _send_request(self, request, timeout=30):
        self.requests.append((request, timeout))
        return self.responses.pop(0)

    async def _push_minecraft_context(self, text, **kwargs):
        self.pushes.append((text, kwargs))

    def _resolve_maid_id(self, maid_id=None):
        return maid_id or "m"


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class FakeSkillConsumer:
    def __init__(self, action_id="a"):
        self.action_id = action_id
        self.events = []

    def claims(self, action_id):
        return action_id == self.action_id

    async def on_action_event(self, event_type, record, payload):
        self.events.append((event_type, record, payload))


class RejectingSkillConsumer(FakeSkillConsumer):
    async def on_action_event(self, event_type, record, payload):
        self.events.append((event_type, record, payload))
        return False


class MaidActionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_remote_recall_retries_while_unloaded_maid_joins(self):
        pending = {
            "type": "maid_action_start_result",
            "data": {
                "accepted": False,
                "error_code": "MAID_LOAD_PENDING",
                "rejection_reason": "MAID_LOAD_PENDING",
            },
        }
        accepted = {
            "type": "maid_action_start_result",
            "data": {
                "accepted": True, "action_id": "recall", "maid_id": "m",
                "generation": 1, "sequence": 1,
                "kind": "return_to_position", "status": "RUNNING",
            },
        }
        plugin = FakePlugin([pending, pending, accepted])
        plugin.connected = True
        service = MaidActionService(plugin)

        result = await service.start_action(
            action_id="recall", maid_id="m", kind="return_to_position",
            args={"destination": "player", "handoff_to_follow": True},
        )

        self.assertTrue(result["success"])
        self.assertEqual(3, len(plugin.requests))
        self.assertTrue(all(
            request[0]["data"]["action_id"] == "recall"
            for request in plugin.requests
        ))

    async def test_return_to_position_service_default_has_no_deadline(self):
        plugin = FakePlugin([{
            "type": "maid_action_start_result",
            "data": {
                "accepted": True, "action_id": "return", "maid_id": "m",
                "generation": 1, "sequence": 1,
                "kind": "return_to_position", "status": "RUNNING",
            },
        }])
        plugin.connected = True
        service = MaidActionService(plugin)
        result = await service.start_action(
            action_id="return",
            maid_id="m",
            kind="return_to_position",
            args={"target": {"x": 0, "y": 70, "z": 0}},
        )
        self.assertTrue(result["success"])
        request, _ = plugin.requests[0]
        self.assertEqual(0, request["data"]["timeout_ms"])

    async def test_ore_selector_service_forces_no_deadline(self):
        plugin = FakePlugin([{
            "type": "maid_action_start_result",
            "data": {
                "accepted": True, "action_id": "ore", "maid_id": "m",
                "generation": 1, "sequence": 1,
                "kind": "harvest_blocks", "status": "RUNNING",
            },
        }])
        plugin.connected = True
        service = MaidActionService(plugin)
        result = await service.start_action(
            action_id="ore",
            maid_id="m",
            kind="harvest_blocks",
            args={
                "selector": {
                    "type": "tag", "id": "minecraft:diamond_ores",
                },
            },
            timeout_ms=60000,
        )
        self.assertTrue(result["success"])
        request, _ = plugin.requests[0]
        self.assertEqual(0, request["data"]["timeout_ms"])

    async def test_skill_owned_child_action_suppresses_regular_llm_feedback(self):
        plugin = FakePlugin()
        service = MaidActionService(plugin)
        consumer = FakeSkillConsumer()
        service.register_event_consumer(consumer)
        service.claim_action("a", "skill-1", feedback_policy="internal")

        await service.handle_message({
            "type": "maid_action_finished",
            "data": {
                "action_id": "a", "maid_id": "m", "generation": 1,
                "sequence": 2, "kind": "harvest_blocks", "status": "FAILED",
                "stage": "FAILED", "end_reason": "PATH_NOT_FOUND",
            },
        })

        self.assertEqual(1, len(consumer.events))
        self.assertEqual([], plugin.pushes)

    async def test_unconsumed_internal_terminal_falls_back_to_llm_feedback(self):
        plugin = FakePlugin()
        service = MaidActionService(plugin)
        consumer = RejectingSkillConsumer()
        service.register_event_consumer(consumer)
        service.claim_action("a", "missing-skill", feedback_policy="internal")

        await service.handle_message({
            "type": "maid_action_finished",
            "data": {
                "action_id": "a", "maid_id": "m", "generation": 1,
                "sequence": 46, "kind": "autonomous_mining",
                "status": "FAILED", "stage": "FAILED",
                "end_reason": "STUCK",
                "result": {
                    "phase": "BLOCKED",
                    "blocked_reason": "controlled_descend_failed",
                    "decision_required": True,
                },
            },
        })

        self.assertEqual(1, len(consumer.events))
        self.assertEqual(1, len(plugin.pushes))
        text, kwargs = plugin.pushes[0]
        self.assertEqual("respond", kwargs["ai_behavior"])
        self.assertIn("自主挖矿", text)
        self.assertIn("必须给出一个具体方案", text)

    async def test_construction_failure_fallback_gives_safe_specific_plan(self):
        cases = (
            ("no_building_material", "补充普通实心方块"),
            ("placement_budget_exhausted", "max_placements 改为0"),
            ("water_seal_failed", "更换方向或矿道形状"),
            ("water_seal_requires_dry_start", "有支撑的干燥位置"),
            ("placement_protected", "绝不能绕过保护"),
            ("placement_space_obstructed", "被实体占用"),
            ("placement_context_cannot_place", "改选支撑位"),
            ("placement_state_invalid", "改选支撑位"),
        )
        for sequence, (reason, expected) in enumerate(cases, start=60):
            with self.subTest(reason=reason):
                plugin = FakePlugin()
                service = MaidActionService(plugin)
                await service.handle_message({
                    "type": "maid_action_finished",
                    "data": {
                        "action_id": f"construction-{sequence}",
                        "maid_id": "m", "generation": 1,
                        "sequence": sequence, "kind": "autonomous_mining",
                        "status": "FAILED", "stage": "FAILED",
                        "end_reason": "PATH_NOT_FOUND",
                        "result": {"phase": "BLOCKED", "blocked_reason": reason},
                    },
                })
                text, kwargs = plugin.pushes[-1]
                self.assertEqual("respond", kwargs["ai_behavior"])
                self.assertIn(expected, text)

    async def test_progress_is_throttled_but_stage_change_is_immediate(self):
        plugin = FakePlugin()
        clock = Clock()
        service = MaidActionService(plugin, clock=clock)
        base = {
            "action_id": "a", "maid_id": "m", "generation": 1,
            "kind": "navigate", "status": "RUNNING",
        }
        await service.handle_message({
            "type": "maid_action_progress",
            "data": {**base, "sequence": 1, "stage": "MOVING", "progress": 0.1},
        })
        clock.value = 0.5
        await service.handle_message({
            "type": "maid_action_progress",
            "data": {**base, "sequence": 2, "stage": "MOVING", "progress": 0.2},
        })
        await service.handle_message({
            "type": "maid_action_progress",
            "data": {**base, "sequence": 3, "stage": "ARRIVING", "progress": 0.9},
        })
        self.assertEqual(2, len(plugin.pushes))
        self.assertTrue(all(push[1]["ai_behavior"] == "read" for push in plugin.pushes))
        self.assertTrue(all("%" not in push[0] for push in plugin.pushes))

    async def test_duplicate_response_is_not_returned_as_a_fresh_snapshot(self):
        service = MaidActionService(FakePlugin())
        service.tracker.apply({
            "action_id": "a", "maid_id": "m", "generation": 1,
            "sequence": 3, "status": "RUNNING",
        })
        observed = service.observe_response({
            "type": "maid_action_status",
            "data": {
                "action_id": "a", "maid_id": "m", "generation": 1,
                "sequence": 3, "status": "CANCEL_REQUESTED",
            },
        })
        self.assertEqual([], observed)
        self.assertEqual("RUNNING", service.tracker.get("a").status)

    async def test_stale_finished_event_is_ignored(self):
        plugin = FakePlugin()
        service = MaidActionService(plugin)
        service.tracker.apply({
            "action_id": "a", "maid_id": "m", "generation": 2,
            "sequence": 1, "status": "RUNNING",
        })
        handled = await service.handle_message({
            "type": "maid_action_finished",
            "data": {
                "action_id": "a", "maid_id": "m", "generation": 1,
                "sequence": 99, "status": "SUCCEEDED",
            },
        })
        self.assertTrue(handled)
        self.assertEqual([], plugin.pushes)

    async def test_finished_uses_respond_once(self):
        plugin = FakePlugin()
        service = MaidActionService(plugin)
        message = {
            "type": "maid_action_finished",
            "data": {
                "action_id": "a", "maid_id": "m", "generation": 1,
                "sequence": 4, "kind": "navigate", "status": "SUCCEEDED",
                "stage": "ARRIVED", "end_reason": "COMPLETED",
            },
        }
        await service.handle_message(message)
        await service.handle_message(message)
        self.assertEqual(1, len(plugin.pushes))
        self.assertEqual("respond", plugin.pushes[0][1]["ai_behavior"])

    async def test_return_to_position_feedback_has_companion_facing_name(self):
        plugin = FakePlugin()
        service = MaidActionService(plugin)
        await service.handle_message({
            "type": "maid_action_finished",
            "data": {
                "action_id": "return", "maid_id": "m", "generation": 1,
                "sequence": 9, "kind": "return_to_position",
                "status": "SUCCEEDED", "stage": "ARRIVED",
                "end_reason": "COMPLETED",
            },
        })
        self.assertIn("安全返程", plugin.pushes[0][0])

    async def test_unloaded_guessed_target_tells_model_to_retry_with_selector(self):
        plugin = FakePlugin()
        service = MaidActionService(plugin)
        await service.handle_message({
            "type": "maid_action_finished",
            "data": {
                "action_id": "a", "maid_id": "m", "generation": 1,
                "sequence": 4, "kind": "harvest_blocks", "status": "FAILED",
                "stage": "FAILED", "end_reason": "VALIDATION_FAILED",
                "result": {
                    "message": "target_chunk_not_loaded",
                    "retry_hint": "retry with a block/tag selector",
                },
            },
        })
        text, kwargs = plugin.pushes[0]
        self.assertEqual("respond", kwargs["ai_behavior"])
        self.assertIn("selector", text)
        self.assertIn("不要让玩家靠近", text)
        self.assertIn("不要强制加载区块", text)

    async def test_missing_ore_feedback_does_not_request_llm_retry(self):
        plugin = FakePlugin()
        service = MaidActionService(plugin)
        await service.handle_message({
            "type": "maid_action_finished",
            "data": {
                "action_id": "missing-ore", "maid_id": "maid", "generation": 1,
                "sequence": 4, "kind": "harvest_blocks", "status": "FAILED",
                "stage": "FAILED", "end_reason": "TARGET_CHANGED",
                "result": {
                    "message": "no_matching_block_found",
                    "selector": "tag:#minecraft:coal_ores",
                    "retry_hint": "Refresh the target or retry",
                },
            },
        })

        text = plugin.pushes[-1][0]
        self.assertIn("不要自动重复", text)
        self.assertIn("服务端", text)
        self.assertIn("minecraft:*_ores", text)
        self.assertNotIn("max_distance=8", text)
        self.assertNotIn("Refresh the target or retry", text)

    async def test_exhausted_server_prospect_does_not_invite_same_retry(self):
        plugin = FakePlugin()
        service = MaidActionService(plugin)
        await service.handle_message({
            "type": "maid_action_finished",
            "data": {
                "action_id": "exhausted", "maid_id": "maid", "generation": 1,
                "sequence": 9, "kind": "harvest_blocks", "status": "FAILED",
                "stage": "FAILED", "end_reason": "TARGET_CHANGED",
                "result": {
                    "message": "prospecting_budget_exhausted_without_match",
                    "retry_hint": "Refresh the target or retry",
                },
            },
        })

        text = plugin.pushes[-1][0]
        self.assertIn("不表示资源 selector 错误", text)
        self.assertIn("旧版服务端", text)
        self.assertIn("必须给出一个具体方案", text)
        self.assertNotIn("minecraft:*_ores", text)
        self.assertNotIn("Refresh the target or retry", text)

    async def test_every_prospect_limit_feedback_explains_budget_not_selector(self):
        messages = (
            "prospecting_distance_or_depth_budget_exhausted",
            "prospecting_excavation_budget_exhausted",
            "prospecting_excavation_budget_would_be_exceeded",
            "target_route_excavation_budget_would_be_exceeded",
            "prospecting_segment_limit_exhausted",
        )
        for sequence, message in enumerate(messages, start=20):
            with self.subTest(message=message):
                plugin = FakePlugin()
                service = MaidActionService(plugin)
                await service.handle_message({
                    "type": "maid_action_finished",
                    "data": {
                        "action_id": f"limit-{sequence}", "maid_id": "maid",
                        "generation": 1, "sequence": sequence,
                        "kind": "harvest_blocks", "status": "FAILED",
                        "stage": "FAILED", "end_reason": "PATH_NOT_FOUND",
                        "result": {
                            "message": message,
                            "retry_hint": "Increase search_radius or retry",
                            "prospect_segment": 2,
                            "prospect_max_segments": 2,
                            "prospect_segment_steps": 8,
                            "prospect_steps": 16,
                            "prospect_total_step_limit": 16,
                            "prospect_blocks_cleared": 64,
                            "prospect_excavation_budget": 64,
                            "prospect_remaining_excavation_budget": 0,
                        },
                    },
                })
                text = plugin.pushes[-1][0]
                self.assertIn("服务端安全限制", text)
                self.assertIn("当前段=2", text)
                self.assertIn("总步数上限=16", text)
                self.assertIn("开凿预算=64", text)
                self.assertIn("剩余开凿预算=0", text)
                self.assertIn("不表示资源 selector 错误", text)
                self.assertIn("当前实现已取消这些总量上限", text)
                self.assertIn("结构化诊断", text)
                self.assertIn("立即调用工具执行一次不同的恢复方案", text)
                self.assertNotIn("Increase search_radius or retry", text)
                self.assertNotIn("minecraft:*_ores", text)

    async def test_terrain_origin_drift_feedback_does_not_blame_selector(self):
        plugin = FakePlugin()
        service = MaidActionService(plugin)
        await service.handle_message({
            "type": "maid_action_finished",
            "data": {
                "action_id": "drift", "maid_id": "maid", "generation": 1,
                "sequence": 12, "kind": "harvest_blocks", "status": "FAILED",
                "stage": "FAILED", "end_reason": "STUCK",
                "result": {
                    "message": "terrain_origin_drift_replan_exhausted",
                    "retry_hint": "Refresh the target or retry with a broader selector",
                },
            },
        })

        text = plugin.pushes[-1][0]
        self.assertIn("路径执行位置偏移", text)
        self.assertIn("不要改变 selector", text)
        self.assertNotIn("broader selector", text)

    async def test_local_navigation_edge_feedback_requires_a_different_route(self):
        messages = (
            "native_navigation_cannot_reach_terrain_step",
            "native_navigation_rejected_terrain_step",
            "native_navigation_finished_before_terrain_step",
            "controlled_descend_made_no_progress",
        )
        for sequence, message in enumerate(messages, start=13):
            with self.subTest(message=message):
                plugin = FakePlugin()
                service = MaidActionService(plugin)
                await service.handle_message({
                    "type": "maid_action_finished",
                    "data": {
                        "action_id": f"local-edge-{sequence}", "maid_id": "maid",
                        "generation": 1, "sequence": sequence,
                        "kind": "harvest_blocks", "status": "FAILED",
                        "stage": "FAILED", "end_reason": "PATH_NOT_FOUND",
                        "result": {
                            "message": message,
                            "mining_plan": "auto",
                            "selector": "tag:#minecraft:diamond_ores",
                            "retry_hint": "Move closer or increase search_radius",
                        },
                    },
                })

                text = plugin.pushes[-1][0]
                self.assertIn("局部移动边无法执行", text)
                self.assertIn("改选安全开掘方向", text)
                self.assertIn("必须给出一个具体方案", text)
                self.assertNotIn("Move closer or increase search_radius", text)

    async def test_repeated_complex_failure_forbids_equivalent_auto_retry(self):
        plugin = FakePlugin()
        service = MaidActionService(plugin)
        for sequence, action_id in enumerate(("blocked-1", "blocked-2"), start=30):
            await service.handle_message({
                "type": "maid_action_finished",
                "data": {
                    "action_id": action_id, "maid_id": "maid",
                    "generation": 1, "sequence": sequence,
                    "kind": "harvest_blocks", "status": "FAILED",
                    "stage": "FAILED", "end_reason": "PATH_NOT_FOUND",
                    "result": {
                        "message": "no_safe_prospecting_step_found",
                        "recoverability": "llm_decision",
                    },
                },
            })

        text = plugin.pushes[-1][0]
        self.assertIn("连续第2次出现", text)
        self.assertIn("禁止再次自动提交相同或等价参数", text)
        self.assertIn("必须改用不同方案", text)

    async def test_exhausted_prospect_directions_require_new_geometry(self):
        plugin = FakePlugin()
        service = MaidActionService(plugin)
        await service.handle_message({
            "type": "maid_action_finished",
            "data": {
                "action_id": "directions-exhausted", "maid_id": "maid",
                "generation": 1, "sequence": 41,
                "kind": "harvest_blocks", "status": "FAILED",
                "stage": "FAILED", "end_reason": "PATH_NOT_FOUND",
                "result": {
                    "message": "all_auto_prospect_directions_exhausted",
                    "selector": "tag:#minecraft:diamond_ores",
                    "prospect_directions_exhausted": True,
                    "prospect_attempted_directions": [
                        "south", "west", "north", "east",
                    ],
                    "prospect_direction_attempts": 4,
                    "prospect_origin": {"x": 12, "y": 24, "z": -8},
                    "last_prospect_step_mode": "forward",
                    "retry_hint": "Move closer or increase search_radius",
                },
            },
        })

        text, kwargs = plugin.pushes[-1]
        self.assertEqual("respond", kwargs["ai_behavior"])
        self.assertIn("尝试或排除四个水平方向", text)
        self.assertIn("净空、支撑、危险地形或局部执行受阻", text)
        self.assertIn("几何上不同的方案", text)
        self.assertIn("保留原 selector", text)
        self.assertNotIn("Move closer or increase search_radius", text)

    async def test_legacy_prospect_dead_end_does_not_claim_four_directions(self):
        plugin = FakePlugin()
        service = MaidActionService(plugin)
        await service.handle_message({
            "type": "maid_action_finished",
            "data": {
                "action_id": "legacy-dead-end", "maid_id": "maid",
                "generation": 1, "sequence": 42,
                "kind": "harvest_blocks", "status": "FAILED",
                "stage": "FAILED", "end_reason": "PATH_NOT_FOUND",
                "result": {
                    "message": "no_safe_prospecting_step_found",
                    "retry_hint": "Increase search_radius and retry",
                },
            },
        })

        text = plugin.pushes[-1][0]
        self.assertIn("当前探矿方向没有安全下一步", text)
        self.assertNotIn("尝试四个水平方向", text)
        self.assertNotIn("Increase search_radius and retry", text)

    async def test_decision_required_uses_respond(self):
        plugin = FakePlugin()
        service = MaidActionService(plugin)
        await service.handle_message({
            "type": "maid_action_progress",
            "data": {
                "action_id": "a", "maid_id": "m", "generation": 1,
                "sequence": 4, "kind": "harvest_blocks", "status": "RUNNING",
                "stage": "WAITING_FOR_TOOL", "requires_decision": True,
            },
        })
        self.assertEqual("respond", plugin.pushes[0][1]["ai_behavior"])
        self.assertIn("必须基于服务端事实给出", plugin.pushes[0][0])
        self.assertIn("立即调用相应工具执行", plugin.pushes[0][0])

    async def test_decision_required_alias_uses_respond(self):
        plugin = FakePlugin()
        service = MaidActionService(plugin)
        await service.handle_message({
            "type": "maid_action_progress",
            "data": {
                "action_id": "decision-alias", "maid_id": "m",
                "generation": 1, "sequence": 4,
                "kind": "autonomous_mining", "status": "RUNNING",
                "stage": "blocked", "decision_required": True,
            },
        })
        self.assertEqual("respond", plugin.pushes[0][1]["ai_behavior"])

    async def test_reconcile_adopts_server_action_and_marks_missing_local_lost(self):
        plugin = FakePlugin(responses=[
            {
                "type": "maid_action_list",
                "data": {"actions": [{
                    "action_id": "server", "maid_id": "m", "generation": 4,
                    "sequence": 2, "kind": "navigate", "status": "RUNNING",
                }]},
            },
            {"type": "error", "data": {"message": "not found"}},
        ])
        service = MaidActionService(plugin)
        service.tracker.apply({
            "action_id": "local", "maid_id": "m", "generation": 1,
            "sequence": 3, "kind": "harvest_blocks", "status": "RUNNING",
        })
        result = await service.reconcile()
        self.assertEqual(["server"], result["adopted"])
        self.assertEqual(["local"], result["lost"])
        self.assertEqual("FAILED", service.tracker.get("local").status)
        self.assertEqual("SERVER_STATE_LOST", service.tracker.get("local").end_reason)
        self.assertEqual("respond", plugin.pushes[0][1]["ai_behavior"])

    async def test_reconcile_keeps_local_action_on_transient_status_error(self):
        plugin = FakePlugin(responses=[
            {"type": "maid_action_list", "data": {"actions": []}},
            {"type": "error", "data": {"message": "Request timed out"}},
        ])
        service = MaidActionService(plugin)
        service.tracker.apply({
            "action_id": "local", "maid_id": "m", "generation": 1,
            "sequence": 3, "kind": "navigate", "status": "RUNNING",
        })
        result = await service.reconcile()
        self.assertEqual(["local"], result["unresolved"])
        self.assertEqual("RUNNING", service.tracker.get("local").status)
        self.assertEqual([], plugin.pushes)

    async def test_reconcile_marks_flat_not_found_status_as_lost(self):
        plugin = FakePlugin(responses=[
            {"type": "maid_action_list", "data": {"actions": []}},
            {
                "type": "maid_action_status",
                "data": {"found": False, "error_code": "ACTION_NOT_FOUND"},
            },
        ])
        service = MaidActionService(plugin)
        service.tracker.apply({
            "action_id": "local", "maid_id": "m", "generation": 1,
            "sequence": 3, "kind": "navigate", "status": "RUNNING",
        })
        result = await service.reconcile()
        self.assertEqual(["local"], result["lost"])
        self.assertEqual("SERVER_STATE_LOST", service.tracker.get("local").end_reason)

    async def test_strict_active_query_distinguishes_error_from_empty(self):
        plugin = FakePlugin(responses=[
            {"type": "error", "data": {"message": "Request timed out"}},
            {"type": "maid_action_list", "data": {"actions": []}},
        ])
        plugin.connected = True
        service = MaidActionService(plugin)

        failed = await service.query_active_actions(maid_id="m")
        empty = await service.query_active_actions(maid_id="m")

        self.assertFalse(failed["success"])
        self.assertEqual("REQUEST_FAILED", failed["error_code"])
        self.assertTrue(empty["success"])
        self.assertEqual([], empty["actions"])

    async def test_legacy_active_list_remains_best_effort_on_error(self):
        plugin = FakePlugin(responses=[
            {"type": "error", "data": {"message": "Request timed out"}},
        ])
        plugin.connected = True
        service = MaidActionService(plugin)

        self.assertEqual([], await service.list_active_actions(maid_id="m"))

    async def test_strict_active_query_rejects_embedded_protocol_error(self):
        plugin = FakePlugin(responses=[{
            "type": "maid_action_list",
            "data": {
                "error_code": "SERVER_BUSY",
                "error": "Action store is reconciling",
                "actions": [],
            },
        }])
        plugin.connected = True
        service = MaidActionService(plugin)

        result = await service.query_active_actions(maid_id="m")

        self.assertFalse(result["success"])
        self.assertEqual("SERVER_BUSY", result["error_code"])

    async def test_strict_active_query_rejects_non_object_response(self):
        plugin = FakePlugin(responses=[None])
        plugin.connected = True
        service = MaidActionService(plugin)

        result = await service.query_active_actions(maid_id="m")

        self.assertFalse(result["success"])
        self.assertEqual("INVALID_RESPONSE", result["error_code"])
        self.assertEqual([], result["actions"])


if __name__ == "__main__":
    unittest.main()
