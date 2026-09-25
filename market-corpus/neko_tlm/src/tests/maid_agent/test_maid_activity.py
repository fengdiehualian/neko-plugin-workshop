import asyncio
import importlib
import unittest

from ._bootstrap import bootstrap

bootstrap()

MaidActivityDirector = importlib.import_module(
    "neko_tlm.maid_activity"
).MaidActivityDirector


class FakePlugin:
    connected = True

    def __init__(self):
        self._maid_status_cache = {}

    def _resolve_maid_id(self):
        return "maid-1"


class FakeActionService:
    class Registry:
        SUPPORTED_KINDS = frozenset({
            "navigate", "harvest_blocks", "return_to_position"
        })

    def __init__(self):
        self.registry = self.Registry()
        self.records = {}
        self.cancel_delay = 0.02
        self.never_finishes = False
        self.cancel_calls = []
        self.start_calls = []
        self.query_error = None

    async def query_active_actions(self, *, maid_id=""):
        if self.query_error:
            return {
                "success": False,
                "error_code": "REQUEST_FAILED",
                "error": self.query_error,
                "actions": [],
            }
        return {
            "success": True,
            "actions": await self.list_active_actions(maid_id=maid_id),
        }

    async def list_active_actions(self, *, maid_id=""):
        return [
            dict(value) for value in self.records.values()
            if value["status"] not in {
                "SUCCEEDED", "FAILED", "CANCELLED", "SUPERSEDED", "TIMEOUT"
            }
            and (not maid_id or value["maid_id"] == maid_id)
        ]

    async def get_action_status(self, action_id):
        value = self.records.get(action_id)
        return dict(value) if value else None

    async def cancel_action(self, action_id, *, maid_id=""):
        self.cancel_calls.append(action_id)
        record = self.records[action_id]
        record["status"] = "CANCEL_REQUESTED"
        if not self.never_finishes:
            asyncio.get_running_loop().call_later(
                self.cancel_delay,
                lambda: record.update(status="CANCELLED"),
            )
        return {"success": True, "accepted": True, "action_id": action_id}

    async def start_action(self, **kwargs):
        self.start_calls.append(dict(kwargs))
        action_id = kwargs["action_id"]
        self.records[action_id] = {
            "action_id": action_id,
            "maid_id": kwargs["maid_id"],
            "kind": kwargs["kind"],
            "status": "RUNNING",
        }
        return {"success": True, "accepted": True, **self.records[action_id]}


class FakeSkillRunner:
    def __init__(self, action_service=None):
        self._definitions = {"mine_ore": object()}
        self.action_service = action_service
        self.records = {}
        self.cancel_calls = []

    def list_skills(self, maid_id="", include_terminal=True):
        terminal = {"SUCCEEDED", "FAILED", "CANCELLED", "BLOCKED"}
        return [
            dict(value) for value in self.records.values()
            if (not maid_id or value["maid_id"] == maid_id)
            and (include_terminal or value["status"] not in terminal)
        ]

    def get_status(self, skill_id):
        value = self.records.get(skill_id)
        return dict(value) if value else None

    def registered_skills(self):
        return tuple(sorted(self._definitions))

    async def cancel(self, skill_id="", maid_id=""):
        self.cancel_calls.append(skill_id)
        child_id = self.records[skill_id].get("current_action_id", "")
        if child_id and self.action_service is not None:
            await self.action_service.cancel_action(child_id, maid_id=maid_id)
        self.records[skill_id]["status"] = "CANCELLED"
        return dict(self.records[skill_id])

    async def start(self, skill_name, maid_id, args, skill_id=None, replace_existing=True):
        skill_id = skill_id or "new-skill"
        self.records[skill_id] = {
            "skill_id": skill_id,
            "maid_id": maid_id,
            "skill_name": skill_name,
            "args": dict(args),
            "status": "RUNNING",
            "current_action_id": "",
        }
        return dict(self.records[skill_id])

    async def reconcile(self):
        for record in self.records.values():
            child_id = record.get("current_action_id", "")
            child = self.action_service.records.get(child_id) if child_id else None
            if child is not None and child.get("status") in {
                "SUCCEEDED", "FAILED", "CANCELLED", "SUPERSEDED", "TIMEOUT",
            }:
                record["status"] = "BLOCKED"
        return {"success": True}


class DirectorFixture:
    def __init__(self):
        self.plugin = FakePlugin()
        self.actions = FakeActionService()
        self.skills = FakeSkillRunner(self.actions)
        self.status = {
            "id": "maid-1",
            "task": "touhou_little_maid:farm",
            "available_tasks": [
                {"id": "touhou_little_maid:farm", "name": "Farm"},
                {"id": "touhou_little_maid:attack", "name": "Attack"},
                {"id": "touhou_little_maid:idle", "name": "Idle"},
            ],
        }
        self.switch_calls = []

        async def status_provider(maid_id):
            return dict(self.status)

        async def task_switcher(maid_id, task):
            self.switch_calls.append((maid_id, task))
            if ":" not in task:
                task = "touhou_little_maid:" + task
            self.status["task"] = task
            return {"success": True, "verified": True, "current_task": task}

        self.director = MaidActivityDirector(
            self.plugin,
            action_service=self.actions,
            skill_runner=self.skills,
            status_provider=status_provider,
            task_switcher=task_switcher,
            poll_interval=0.005,
            transition_timeout=0.15,
        )


class MaidActivityDirectorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fx = DirectorFixture()

    async def asyncTearDown(self):
        await self.fx.director.close()

    def test_same_action_kind_and_args_is_current_without_caller_id(self):
        target = {
            "type": "agent_action",
            "kind": "navigate",
            "args": {"target": {"x": 1.0, "y": 64.0, "z": 2.0}},
        }
        current = {
            "active_actions": [{
                "action_id": "server-generated",
                "kind": "navigate",
                "args": {"target": {"x": 1.0, "y": 64.0, "z": 2.0}},
            }],
        }
        self.assertTrue(self.fx.director._target_is_current(target, current))

    def test_different_action_args_are_not_current(self):
        target = {
            "type": "agent_action",
            "kind": "navigate",
            "args": {"target": {"x": 1.0, "y": 64.0, "z": 2.0}},
        }
        current = {
            "active_actions": [{
                "action_id": "server-generated",
                "kind": "navigate",
                "args": {"target": {"x": 9.0, "y": 64.0, "z": 2.0}},
            }],
        }
        self.assertFalse(self.fx.director._target_is_current(target, current))

    async def test_cancel_then_switch_waits_for_action_terminal(self):
        self.fx.actions.records["a-1"] = {
            "action_id": "a-1", "maid_id": "maid-1",
            "kind": "navigate", "status": "RUNNING",
        }
        result = await self.fx.director.set_activity(
            {"type": "tlm_task", "task": "attack"}, request_id="switch-1"
        )
        self.assertTrue(result["success"], result)
        self.assertEqual(["a-1"], self.fx.actions.cancel_calls)
        self.assertEqual(
            "touhou_little_maid:attack",
            result["final_activity"]["tlm_task"]["id"],
        )

    async def test_skill_child_reaches_both_skill_and_action_terminal(self):
        self.fx.actions.records["child"] = {
            "action_id": "child", "maid_id": "maid-1",
            "kind": "autonomous_mining", "status": "RUNNING",
        }
        self.fx.skills.records["skill-1"] = {
            "skill_id": "skill-1", "maid_id": "maid-1",
            "skill_name": "mine_ore", "args": {}, "status": "WAITING_ACTION",
            "current_action_id": "child",
        }
        result = await self.fx.director.stop(request_id="stop-skill")
        self.assertTrue(result["success"], result)
        self.assertEqual(["skill-1"], self.fx.skills.cancel_calls)
        self.assertEqual(["child"], self.fx.actions.cancel_calls)

    async def test_snapshot_folds_skill_child_and_marks_tlm_suppressed(self):
        self.fx.actions.records["child"] = {
            "action_id": "child", "maid_id": "maid-1",
            "kind": "autonomous_mining", "status": "RUNNING",
        }
        self.fx.skills.records["skill-1"] = {
            "skill_id": "skill-1", "maid_id": "maid-1",
            "skill_name": "mine_ore", "args": {}, "status": "WAITING_ACTION",
            "current_action_id": "child",
        }
        snapshot = await self.fx.director.get_activity()
        self.assertEqual([], snapshot["active_actions"])
        self.assertEqual(
            "child", snapshot["active_skills"][0]["child_action"]["action_id"]
        )
        self.assertTrue(snapshot["tlm_task"]["suppressed"])

    async def test_request_id_is_idempotent_and_conflicts_on_changed_input(self):
        first = await self.fx.director.set_activity(
            {"type": "tlm_task", "task": "attack"}, request_id="same"
        )
        second = await self.fx.director.set_activity(
            {"type": "tlm_task", "task": "attack"}, request_id="same"
        )
        conflict = await self.fx.director.set_activity(
            {"type": "idle"}, request_id="same"
        )
        self.assertEqual(first, second)
        self.assertEqual(1, len(self.fx.switch_calls))
        self.assertEqual("IDEMPOTENCY_CONFLICT", conflict["error_code"])

    async def test_reject_if_busy_does_not_cancel_or_switch(self):
        self.fx.actions.records["a-1"] = {
            "action_id": "a-1", "maid_id": "maid-1",
            "kind": "navigate", "status": "RUNNING",
        }
        result = await self.fx.director.set_activity(
            {"type": "idle"},
            switch_policy="reject_if_busy",
            request_id="reject",
        )
        self.assertEqual("MAID_BUSY", result["error_code"])
        self.assertEqual([], self.fx.actions.cancel_calls)
        self.assertEqual([], self.fx.switch_calls)

    async def test_timeout_never_switches_before_terminal_reconciliation(self):
        self.fx.actions.records["stuck"] = {
            "action_id": "stuck", "maid_id": "maid-1",
            "kind": "navigate", "status": "RUNNING",
        }
        self.fx.actions.never_finishes = True
        result = await self.fx.director.set_activity(
            {"type": "idle"}, request_id="timeout", timeout=0.03
        )
        self.assertEqual("ACTIVITY_SWITCH_TIMEOUT", result["error_code"])
        self.assertEqual([], self.fx.switch_calls)

    async def test_fast_terminal_action_is_not_reported_as_verify_failure(self):
        async def completes_during_start(**kwargs):
            action_id = kwargs["action_id"]
            self.fx.actions.records[action_id] = {
                "action_id": action_id,
                "maid_id": kwargs["maid_id"],
                "kind": kwargs["kind"],
                "status": "SUCCEEDED",
            }
            return {
                "success": True,
                "accepted": True,
                "action_id": action_id,
                "maid_id": kwargs["maid_id"],
                "kind": kwargs["kind"],
                "status": "RUNNING",
            }

        self.fx.actions.start_action = completes_during_start
        result = await self.fx.director.set_activity(
            {"type": "agent_action", "kind": "navigate", "args": {}},
            request_id="fast-terminal",
        )

        self.assertTrue(result["success"], result)
        self.assertEqual("COMPLETED_DURING_START", result["status"])
        self.assertEqual("SUCCEEDED", result["terminal_activity"]["status"])

    async def test_after_current_is_visible_then_runs_without_cancelling(self):
        self.fx.actions.records["natural"] = {
            "action_id": "natural", "maid_id": "maid-1",
            "kind": "navigate", "status": "RUNNING",
        }
        queued = await self.fx.director.set_activity(
            {"type": "tlm_task", "task": "attack"},
            switch_policy="after_current",
            request_id="later",
        )
        self.assertEqual("QUEUED_AFTER_CURRENT", queued["status"])
        visible = await self.fx.director.get_activity()
        self.assertEqual("later", visible["pending_transition"]["request_id"])
        self.fx.actions.records["natural"]["status"] = "SUCCEEDED"
        for _ in range(50):
            repeated = await self.fx.director.set_activity(
                {"type": "tlm_task", "task": "attack"},
                switch_policy="after_current",
                request_id="later",
            )
            if repeated.get("status") == "ACTIVE":
                break
            await asyncio.sleep(0.005)
        self.assertEqual("ACTIVE", repeated.get("status"), repeated)
        self.assertEqual([], self.fx.actions.cancel_calls)

    async def test_after_current_persistent_query_error_becomes_terminal_error(self):
        self.fx.actions.records["natural"] = {
            "action_id": "natural", "maid_id": "maid-1",
            "kind": "navigate", "status": "RUNNING",
        }
        await self.fx.director.set_activity(
            {"type": "idle"},
            switch_policy="after_current",
            request_id="query-fails",
            timeout=0.03,
        )
        self.fx.actions.query_error = "transport down"
        await asyncio.sleep(0.12)
        repeated = await self.fx.director.set_activity(
            {"type": "idle"},
            switch_policy="after_current",
            request_id="query-fails",
            timeout=0.03,
        )
        self.assertEqual(
            "ACTIVITY_STATE_UNAVAILABLE", repeated.get("error_code"), repeated
        )

    async def test_immediate_stop_supersedes_queued_after_current(self):
        self.fx.actions.records["natural"] = {
            "action_id": "natural", "maid_id": "maid-1",
            "kind": "navigate", "status": "RUNNING",
        }
        queued = await self.fx.director.set_activity(
            {"type": "tlm_task", "task": "attack"},
            switch_policy="after_current",
            request_id="later",
        )
        self.assertEqual("QUEUED_AFTER_CURRENT", queued["status"])

        stopped = await self.fx.director.stop(request_id="urgent-stop")
        repeated = await self.fx.director.set_activity(
            {"type": "tlm_task", "task": "attack"},
            switch_policy="after_current",
            request_id="later",
        )

        self.assertTrue(stopped["success"], stopped)
        self.assertEqual("SUPERSEDED", repeated.get("error_code"), repeated)
        self.assertNotIn("maid-1", self.fx.director._pending)

    async def test_start_exception_is_returned_as_structured_switch_failure(self):
        async def fail_start(**_):
            raise RuntimeError("synthetic start failure")

        self.fx.actions.start_action = fail_start
        result = await self.fx.director.set_activity(
            {"type": "agent_action", "kind": "navigate", "args": {}},
            request_id="fails-cleanly",
        )

        self.assertEqual("ACTIVITY_SWITCH_FAILED", result.get("error_code"), result)
        self.assertIn("synthetic start failure", result.get("error", ""))

    async def test_pending_operation_is_pinned_in_idempotency_lru(self):
        self.fx.director.idempotency_limit = 8
        self.fx.actions.records["natural"] = {
            "action_id": "natural", "maid_id": "maid-1",
            "kind": "navigate", "status": "RUNNING",
        }
        await self.fx.director.set_activity(
            {"type": "tlm_task", "task": "attack"},
            switch_policy="after_current",
            request_id="pinned",
        )
        for index in range(10):
            await self.fx.director.set_activity(
                {"type": "idle"},
                switch_policy="after_current",
                request_id=f"noise-{index}",
            )

        self.assertIn(("maid-1", "pinned"), self.fx.director._operations)
        self.fx.actions.records["natural"]["status"] = "SUCCEEDED"
        for _ in range(50):
            repeated = await self.fx.director.set_activity(
                {"type": "tlm_task", "task": "attack"},
                switch_policy="after_current",
                request_id="pinned",
            )
            if repeated.get("status") == "ACTIVE":
                break
            await asyncio.sleep(0.005)
        self.assertEqual("ACTIVE", repeated.get("status"), repeated)
        self.assertEqual(1, len(self.fx.switch_calls))

    async def test_close_clears_previous_lifecycle_idempotency(self):
        first = await self.fx.director.set_activity(
            {"type": "tlm_task", "task": "attack"}, request_id="lifecycle"
        )
        self.assertTrue(first["success"], first)
        await self.fx.director.close()
        self.fx.status["task"] = "touhou_little_maid:farm"

        second = await self.fx.director.set_activity(
            {"type": "tlm_task", "task": "attack"}, request_id="lifecycle"
        )

        self.assertTrue(second["success"], second)
        self.assertEqual(2, len(self.fx.switch_calls))

    async def test_after_current_reconciles_missed_skill_terminal(self):
        self.fx.actions.records["child"] = {
            "action_id": "child", "maid_id": "maid-1",
            "kind": "navigate", "status": "RUNNING",
        }
        self.fx.skills.records["skill-1"] = {
            "skill_id": "skill-1", "maid_id": "maid-1",
            "skill_name": "mine_ore", "args": {}, "status": "WAITING_ACTION",
            "current_action_id": "child",
        }
        queued = await self.fx.director.set_activity(
            {"type": "tlm_task", "task": "attack"},
            switch_policy="after_current",
            request_id="reconcile-later",
        )
        self.assertEqual("QUEUED_AFTER_CURRENT", queued["status"])
        self.fx.actions.records["child"]["status"] = "SUCCEEDED"

        for _ in range(100):
            repeated = await self.fx.director.set_activity(
                {"type": "tlm_task", "task": "attack"},
                switch_policy="after_current",
                request_id="reconcile-later",
            )
            if repeated.get("status") == "ACTIVE":
                break
            await asyncio.sleep(0.005)

        self.assertEqual("ACTIVE", repeated.get("status"), repeated)
        self.assertEqual(1, len(self.fx.switch_calls))

    async def test_body_mutation_is_rejected_while_agent_controls_maid(self):
        self.fx.actions.records["busy"] = {
            "action_id": "busy", "maid_id": "maid-1",
            "kind": "navigate", "status": "RUNNING",
        }
        called = False

        async def mutation():
            nonlocal called
            called = True
            return {"type": "command_result", "data": {"success": True}}

        result = await self.fx.director.execute_body_mutation(
            mutation, maid_id="maid-1", operation="switch_sit"
        )

        self.assertEqual("MAID_BUSY", result.get("error_code"), result)
        self.assertFalse(called)

    async def test_blocked_skill_is_terminal_and_does_not_make_maid_busy(self):
        self.fx.skills.records["blocked"] = {
            "skill_id": "blocked", "maid_id": "maid-1",
            "skill_name": "mine_ore", "args": {}, "status": "BLOCKED",
            "current_action_id": "",
        }
        activity = await self.fx.director.get_activity()
        self.assertFalse(activity["busy"])
        result = await self.fx.director.stop(request_id="stop")
        self.assertTrue(result["success"], result)
        self.assertEqual([], self.fx.skills.cancel_calls)

    async def test_stop_can_preserve_restored_tlm_task(self):
        self.fx.actions.records["a-1"] = {
            "action_id": "a-1", "maid_id": "maid-1",
            "kind": "navigate", "status": "RUNNING",
        }
        result = await self.fx.director.stop(
            request_id="preserve", switch_to_idle=False
        )
        self.assertTrue(result["success"], result)
        self.assertEqual([], self.fx.switch_calls)
        self.assertEqual(
            "touhou_little_maid:farm",
            result["final_activity"]["tlm_task"]["id"],
        )

    async def test_strict_action_query_error_prevents_false_idle_switch(self):
        self.fx.actions.query_error = "transport down"
        snapshot = await self.fx.director.get_activity()
        self.assertFalse(snapshot["success"])
        self.assertIn("action_query_error", snapshot)
        result = await self.fx.director.stop(request_id="unsafe")
        self.assertEqual("ACTIVITY_STATE_UNAVAILABLE", result["error_code"])
        self.assertEqual([], self.fx.switch_calls)

    async def test_capabilities_are_dynamic(self):
        result = await self.fx.director.get_capabilities()
        self.assertTrue(result["success"])
        self.assertIn("navigate", result["agent_actions"])
        self.assertIn("return_to_position", result["agent_actions"])
        self.assertIn("mine_ore", result["skills"])
        self.assertEqual(3, len(result["tlm_tasks"]))


if __name__ == "__main__":
    unittest.main()
