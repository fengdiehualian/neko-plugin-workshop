import importlib
import unittest

from ._bootstrap import bootstrap_sdk

bootstrap_sdk()

tools = importlib.import_module("neko_tlm.tools")


class FakeDirector:
    def __init__(self):
        self.calls = []

    async def get_activity(self, **kwargs):
        self.calls.append(("get", kwargs))
        return {"success": True, "activity": {"type": "idle"}}

    async def get_capabilities(self, **kwargs):
        self.calls.append(("capabilities", kwargs))
        return {"success": True, "tlm_tasks": []}

    async def set_activity(self, target, **kwargs):
        self.calls.append(("set", target, kwargs))
        result = {"success": True, "target": dict(target), "status": "ACTIVE"}
        if target.get("type") == "tlm_task":
            result["final_activity"] = {
                "tlm_task": {
                    "id": "touhou_little_maid:attack",
                    "name": "Attack",
                    "suppressed": False,
                }
            }
        if target.get("type") == "skill":
            result["target_result"] = {
                "skill_id": target.get("skill_id"),
                "skill_name": target.get("skill"),
                "status": "RUNNING",
            }
        return result

    async def stop(self, **kwargs):
        self.calls.append(("stop", kwargs))
        return {"success": True, "status": "STOPPED"}

    async def execute_body_mutation(self, mutation, **kwargs):
        self.calls.append(("body", kwargs))
        return {
            "success": True,
            "operation": kwargs.get("operation"),
            "result": await mutation(),
        }


class FakePlugin:
    connected = True

    def __init__(self):
        self._maid_activity_director = FakeDirector()
        self._skill_runner = object()
        self._maid_status_cache = {}
        self.logger = type("Logger", (), {"info": lambda *args, **kwargs: None})()

    def _resolve_maid_id(self, maid_id=None):
        return maid_id or "maid-1"

    async def _send_request(self, request, timeout=5):
        if (
            request.get("type") == "get_game_context"
            and request.get("data", {}).get("category") == "equipment"
        ):
            return {
                "type": "game_context",
                "data": {"main_hand": "minecraft:diamond_sword"},
            }
        if request.get("type") == "get_maid_status":
            return {
                "type": "maid_status",
                "data": {"maids": [{
                    "id": "maid-1",
                    "main_hand_item": "minecraft:torch",
                    "off_hand_item": "",
                }]},
            }
        command = request.get("data", {}).get("command", "")
        data = {"success": True}
        if command == "equip_item":
            data["equipped_item"] = "minecraft:torch"
        return {"type": "command_result", "data": data}


class FakeSkillRunner:
    def get_status(self, skill_id):
        if skill_id != "skill-done":
            return None
        return {
            "skill_id": skill_id,
            "skill_name": "mine_ore",
            "status": "SUCCEEDED",
        }


class ActivityToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_follow_is_verified_from_authoritative_status(self):
        plugin = FakePlugin()
        requests = []

        async def send_request(request, timeout=5):
            requests.append(request)
            if request.get("type") == "get_maid_status":
                return {
                    "type": "maid_status",
                    "data": {"maids": [{
                        "id": "maid-1",
                        "is_following": True,
                        "is_sitting": False,
                    }]},
                }
            return {
                "type": "command_result",
                "data": {"success": True, "state": "following_stood_up"},
            }

        plugin._send_request = send_request
        result = await tools.do_switch_follow(plugin, action="follow")

        self.assertFalse(result["is_error"])
        self.assertTrue(result["output"]["verified"])
        self.assertTrue(result["output"]["is_following"])
        self.assertFalse(result["output"]["is_sitting"])
        self.assertTrue(result["output"]["stood_up"])
        self.assertEqual("get_maid_status", requests[-1]["type"])

    async def test_follow_does_not_claim_success_when_status_disagrees(self):
        plugin = FakePlugin()

        async def send_request(request, timeout=5):
            if request.get("type") == "get_maid_status":
                return {
                    "type": "maid_status",
                    "data": {"maids": [{
                        "id": "maid-1",
                        "is_following": True,
                        "is_sitting": True,
                    }]},
                }
            return {
                "type": "command_result",
                "data": {"success": True, "state": "following"},
            }

        plugin._send_request = send_request
        result = await tools.do_switch_follow(plugin, action="follow")

        self.assertTrue(result["is_error"])
        self.assertEqual("FOLLOW_STATE_VERIFICATION_FAILED", result["error"])
        self.assertFalse(result["output"]["verified"])

    async def test_follow_rejects_unknown_action_before_command(self):
        plugin = FakePlugin()
        result = await tools.do_switch_follow(plugin, action="toggle")
        self.assertTrue(result["is_error"])
        self.assertEqual([], plugin._maid_activity_director.calls)

    async def test_query_tools_delegate_to_director(self):
        plugin = FakePlugin()
        activity = await tools.do_get_maid_activity(plugin)
        capabilities = await tools.do_get_maid_capabilities(plugin)

        self.assertFalse(activity["is_error"])
        self.assertFalse(capabilities["is_error"])
        self.assertEqual("get", plugin._maid_activity_director.calls[0][0])
        self.assertEqual(
            "capabilities", plugin._maid_activity_director.calls[1][0]
        )

    async def test_activity_query_can_recover_terminal_action_and_skill(self):
        plugin = FakePlugin()
        plugin._skill_runner = FakeSkillRunner()

        async def send_request(request, timeout=5):
            self.assertEqual("get_maid_action_status", request["type"])
            return {
                "type": "maid_action_status",
                "data": {
                    "found": True,
                    "action_id": "action-done",
                    "maid_id": "maid-1",
                    "generation": 1,
                    "sequence": 2,
                    "kind": "navigate",
                    "status": "SUCCEEDED",
                },
            }

        plugin._send_request = send_request
        result = await tools.do_get_maid_activity(
            plugin, action_id="action-done", skill_id="skill-done"
        )

        self.assertFalse(result["is_error"])
        output = result["output"]
        self.assertEqual("SUCCEEDED", output["requested_action"]["status"])
        self.assertEqual("SUCCEEDED", output["requested_skill"]["status"])

    async def test_terminal_skill_query_survives_current_activity_failure(self):
        plugin = FakePlugin()
        plugin._skill_runner = FakeSkillRunner()

        async def failed_activity(**kwargs):
            return {
                "success": False,
                "error_code": "ACTION_QUERY_FAILED",
                "action_query_error": {"error": "temporary failure"},
            }

        plugin._maid_activity_director.get_activity = failed_activity
        result = await tools.do_get_maid_activity(
            plugin, skill_id="skill-done"
        )

        self.assertFalse(result["is_error"])
        output = result["output"]
        self.assertTrue(output["partial"])
        self.assertFalse(output["current_activity_available"])
        self.assertEqual(
            "ACTION_QUERY_FAILED",
            output["current_activity_error"]["error_code"],
        )
        self.assertEqual("SUCCEEDED", output["requested_skill"]["status"])

    async def test_set_builds_one_normalized_activity_target(self):
        plugin = FakePlugin()
        result = await tools.do_set_maid_activity(
            plugin,
            activity_type="skill",
            skill="mine_ore",
            args={"target_count": 8},
            switch_policy="reject_if_busy",
            request_id="request-1",
        )

        self.assertFalse(result["is_error"])
        _, target, kwargs = plugin._maid_activity_director.calls[0]
        self.assertEqual(
            {
                "type": "skill",
                "skill": "mine_ore",
                "args": {"target_count": 8},
            },
            target,
        )
        self.assertEqual("reject_if_busy", kwargs["switch_policy"])
        self.assertEqual("request-1", kwargs["request_id"])

    async def test_set_rejects_non_object_args_before_director(self):
        plugin = FakePlugin()
        result = await tools.do_set_maid_activity(
            plugin, activity_type="agent_action", kind="navigate", args=[]
        )

        self.assertTrue(result["is_error"])
        self.assertEqual("INVALID_ACTIVITY_ARGUMENTS", result["error"])
        self.assertEqual([], plugin._maid_activity_director.calls)

    async def test_stop_preserves_switch_to_idle_choice(self):
        plugin = FakePlugin()
        result = await tools.do_stop_maid_activity(
            plugin, switch_to_idle=False, request_id="stop-1"
        )

        self.assertFalse(result["is_error"])
        _, kwargs = plugin._maid_activity_director.calls[0]
        self.assertFalse(kwargs["switch_to_idle"])
        self.assertEqual("stop-1", kwargs["request_id"])

    async def test_legacy_switch_task_is_routed_through_safe_director(self):
        plugin = FakePlugin()

        result = await tools.do_switch_task(plugin, task="attack")

        self.assertFalse(result["is_error"])
        _, target, kwargs = plugin._maid_activity_director.calls[0]
        self.assertEqual(
            {"type": "tlm_task", "task": "attack"}, target
        )
        self.assertEqual("cancel_then_switch", kwargs["switch_policy"])
        self.assertTrue(result["output"]["verified"])

    async def test_combat_switch_is_rejected_without_matching_weapon(self):
        plugin = FakePlugin()

        async def send_request(request, timeout=5):
            self.assertEqual("equipment", request["data"]["category"])
            return {
                "type": "game_context",
                "data": {"main_hand": "minecraft:torch"},
            }

        plugin._send_request = send_request
        result = await tools.do_switch_task(plugin, task="打怪")

        self.assertTrue(result["is_error"])
        self.assertIn("当前：minecraft:torch", str(result))
        self.assertEqual([], plugin._maid_activity_director.calls)

    async def test_legacy_start_skill_uses_same_director_lock(self):
        plugin = FakePlugin()

        result = await tools.do_start_skill(
            plugin,
            skill="mine_ore",
            args={"target_count": 8},
            skill_id="skill-1",
        )

        self.assertFalse(result["is_error"])
        _, target, kwargs = plugin._maid_activity_director.calls[0]
        self.assertEqual("skill", target["type"])
        self.assertEqual("skill-1", target["skill_id"])
        self.assertEqual("cancel_then_switch", kwargs["switch_policy"])

    async def test_legacy_body_tools_use_director_guard(self):
        plugin = FakePlugin()

        results = [
            await tools.do_switch_follow(plugin, action="stay"),
            await tools.do_switch_sit(plugin, action="sit"),
            await tools.do_switch_schedule(plugin, schedule="all"),
            await tools.do_equip_item(plugin, item="minecraft:torch"),
        ]

        self.assertTrue(all(not item["is_error"] for item in results))
        operations = [
            call[1]["operation"] for call in plugin._maid_activity_director.calls
            if call[0] == "body"
        ]
        self.assertEqual(
            ["switch_follow", "switch_sit", "switch_schedule", "equip_item"],
            operations,
        )


if __name__ == "__main__":
    unittest.main()
