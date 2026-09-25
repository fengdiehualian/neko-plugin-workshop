import asyncio
import importlib
import json
import tempfile
import unittest
import uuid
from pathlib import Path

from ._bootstrap import bootstrap

bootstrap()

MaidActionService = importlib.import_module(
    "neko_tlm.maid_agent.service"
).MaidActionService
SkillFeedbackHandler = importlib.import_module(
    "neko_tlm.maid_agent.skill_feedback"
).SkillFeedbackHandler
_base = importlib.import_module("neko_tlm.maid_agent.skills.base")
Blocked = _base.Blocked
Complete = _base.Complete
SkillRun = _base.SkillRun
StartAction = _base.StartAction
action_fingerprint = _base.action_fingerprint
SkillCheckpointStore = importlib.import_module(
    "neko_tlm.maid_agent.skills.checkpoint"
).SkillCheckpointStore
MineOreSkill = importlib.import_module(
    "neko_tlm.maid_agent.skills.mine_ore"
).MineOreSkill
SkillRunner = importlib.import_module(
    "neko_tlm.maid_agent.skills.runner"
).SkillRunner


class FakePlugin:
    connected = True


class FlakyPushPlugin(FakePlugin):
    def __init__(self, failures):
        self.failures = int(failures)
        self.attempts = 0
        self.pushes = []

    async def _push_minecraft_context(self, text, **kwargs):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise RuntimeError("temporary context delivery failure")
        self.pushes.append((text, kwargs))


class ServiceIntegrationPlugin(FakePlugin):
    def __init__(self):
        self.pushes = []
        self.requests = []

    async def _send_request(self, request, timeout=30):
        self.requests.append((dict(request), timeout))
        data = dict(request.get("data") or {})
        return {
            "type": "maid_action_started",
            "data": {
                "accepted": True,
                "action_id": data["action_id"],
                "maid_id": data["maid_id"],
                "generation": 1,
                "sequence": 1,
                "kind": data["kind"],
                "status": "RUNNING",
                "stage": "STARTED",
            },
        }

    async def _push_minecraft_context(self, text, **kwargs):
        self.pushes.append((text, kwargs))
        return True


class FakeFeedback:
    def __init__(self):
        self.progress_runs = []
        self.blocked_runs = []
        self.finished_runs = []

    async def progress(self, snapshot):
        self.progress_runs.append(dict(snapshot))

    async def blocked(self, snapshot):
        self.blocked_runs.append(dict(snapshot))

    async def finished(self, snapshot):
        self.finished_runs.append(dict(snapshot))


class FakeActionClient:
    def __init__(self, checkpoint_dir=None):
        self.checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else None
        self.consumer = None
        self.claimed = {}
        self.start_calls = []
        self.cancel_calls = []
        self.active = {}
        self.statuses = {}
        self.status_override = None
        self.emit_terminal_on_cancel = False

    def register_event_consumer(self, consumer):
        self.consumer = consumer

    def unregister_event_consumer(self, consumer=None):
        if consumer is None or self.consumer is consumer:
            self.consumer = None

    def claim_action(self, action_id, owner_id, feedback_policy="internal"):
        self.claimed[action_id] = (owner_id, feedback_policy)

    def release_action(self, action_id, owner_id=""):
        current = self.claimed.get(action_id)
        if current and (not owner_id or current[0] == owner_id):
            self.claimed.pop(action_id, None)

    async def start_action(self, **request):
        self.start_calls.append(dict(request))
        if self.checkpoint_dir:
            path = self.checkpoint_dir / f"{request['owner_id']}.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            assert payload["status"] == "STARTING_ACTION"
            assert payload["current_action_id"] == request["action_id"]
            assert payload["current_action_fingerprint"]
        snapshot = {
            "success": True,
            "accepted": True,
            "action_id": request["action_id"],
            "maid_id": request["maid_id"],
            "generation": 1,
            "sequence": 1,
            "kind": request["kind"],
            "status": "RUNNING",
            "stage": "STARTED",
        }
        self.active[request["action_id"]] = snapshot
        self.statuses[request["action_id"]] = snapshot
        self.claim_action(
            request["action_id"], request["owner_id"], request["feedback_policy"]
        )
        return snapshot

    async def cancel_action(self, action_id, maid_id=""):
        self.cancel_calls.append((action_id, maid_id))
        self.active.pop(action_id, None)
        if self.emit_terminal_on_cancel and self.consumer is not None:
            snapshot = {
                "action_id": action_id,
                "maid_id": maid_id,
                "generation": 1,
                "sequence": 2,
                "status": "CANCELLED",
                "end_reason": "REQUESTED",
                "result": {},
            }
            asyncio.get_running_loop().call_soon(
                lambda: asyncio.create_task(self.consumer.on_action_event(
                    "maid_action_finished", snapshot, snapshot
                ))
            )
        return {"success": True, "accepted": True, "action_id": action_id}

    async def get_action_status(self, action_id):
        if self.status_override is not None:
            return dict(self.status_override)
        return self.statuses.get(action_id)

    async def list_active_actions(self, maid_id=""):
        return list(self.active.values())


class BlockingStartActionClient(FakeActionClient):
    def __init__(self, checkpoint_dir=None):
        super().__init__(checkpoint_dir)
        self.start_entered = asyncio.Event()
        self.release_start = asyncio.Event()

    async def start_action(self, **request):
        self.start_entered.set()
        await self.release_start.wait()
        return await super().start_action(**request)


class OneActionSkill:
    name = "one_action"
    version = 1

    def normalize_args(self, raw):
        return {"target": str(raw.get("target") or "stone")}

    def initialize(self, run):
        run.result["initialized"] = True

    def next_directive(self, run, terminal_snapshot):
        if terminal_snapshot is None:
            return StartAction(
                "harvest_blocks",
                {
                    "selector": {"type": "block", "id": "minecraft:stone"},
                    "max_blocks": 1,
                },
            )
        if terminal_snapshot.get("status") == "SUCCEEDED":
            return Complete({"collected": 1})
        return Blocked(
            terminal_snapshot.get("end_reason", "CHILD_FAILED"),
            terminal_snapshot.get("result", {}),
        )


class TwoActionSkill(OneActionSkill):
    name = "two_action"

    def initialize(self, run):
        run.result["completed_children"] = 0

    def next_directive(self, run, terminal_snapshot):
        if terminal_snapshot is not None:
            run.result["completed_children"] += 1
        if run.result["completed_children"] < 2:
            return StartAction(
                "navigate",
                {"target": {"x": run.result["completed_children"], "y": 64, "z": 0}},
                timeout_ms=60000,
            )
        return Complete({"children": 2})


class AutonomousOneActionSkill(OneActionSkill):
    name = "autonomous_one_action"

    def next_directive(self, run, terminal_snapshot):
        if terminal_snapshot is None:
            return StartAction(
                "autonomous_mining",
                {
                    "selector": {"type": "tag", "id": "minecraft:coal_ores"},
                    "target_count": 10,
                },
                timeout_ms=0,
            )
        return Complete({"collected_count": 10})


class AlwaysBlockedSkill(OneActionSkill):
    name = "blocked"

    def next_directive(self, run, terminal_snapshot):
        return Blocked("NEEDS_PLAYER", {"message": "Choose a safe origin"})


async def wait_until(predicate, timeout=2):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition was not reached")
        await asyncio.sleep(0.01)


class SkillRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_checkpoints_and_claims_before_child_request(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeActionClient(directory)
            runner = SkillRunner(FakePlugin(), client, directory)
            runner.register(OneActionSkill())
            self.assertEqual(("one_action",), runner.registered_skills())
            result = await runner.start("one_action", "maid", {"target": "stone"})

            self.assertEqual("WAITING_ACTION", result["status"])
            self.assertEqual(1, len(client.start_calls))
            call = client.start_calls[0]
            self.assertEqual(result["skill_id"], call["owner_id"])
            self.assertEqual("internal", call["feedback_policy"])
            self.assertEqual(result["skill_id"], runner.claims(call["action_id"]))
            await runner.close()
            self.assertEqual([], client.cancel_calls)

    async def test_terminal_event_returns_before_next_network_request(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeActionClient(directory)
            runner = SkillRunner(FakePlugin(), client, directory)
            runner.register(TwoActionSkill())
            first = await runner.start("two_action", "maid", {})
            action_id = first["current_action_id"]
            terminal = {
                "action_id": action_id,
                "maid_id": "maid",
                "generation": 1,
                "sequence": 2,
                "kind": "navigate",
                "status": "SUCCEEDED",
                "end_reason": "COMPLETED",
                "result": {},
            }
            consumed = await runner.on_action_event(
                "maid_action_finished", terminal, terminal
            )
            self.assertTrue(consumed)
            self.assertEqual(1, len(client.start_calls))
            await wait_until(lambda: len(client.start_calls) == 2)
            self.assertEqual(
                "WAITING_ACTION", runner.get_status(first["skill_id"])["status"]
            )
            await runner.close()

    async def test_blocked_feedback_is_emitted_once_and_archived_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            feedback = FakeFeedback()
            client = FakeActionClient(directory)
            runner = SkillRunner(FakePlugin(), client, directory, feedback=feedback)
            runner.register(AlwaysBlockedSkill())
            result = await runner.start("blocked", "maid", {})
            self.assertEqual("BLOCKED", result["status"])
            self.assertTrue(result["decision_required"])
            self.assertEqual(
                "restart_with_adjusted_parameters",
                result["control_capabilities"]["decision_mode"],
            )
            self.assertFalse(result["control_capabilities"]["pause"])
            self.assertFalse(result["control_capabilities"]["resume"])
            self.assertFalse(result["control_capabilities"]["submit_decision"])
            self.assertEqual(1, len(feedback.blocked_runs))
            self.assertGreater(result["blocked_notification_revision"], 0)

            await runner.reconcile()
            self.assertEqual(1, len(feedback.blocked_runs))
            self.assertEqual([], runner.list_skills(include_terminal=False))
            await runner.close()

    async def test_autonomous_blocked_feedback_retries_after_delivery_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            plugin = FlakyPushPlugin(failures=3)
            client = FakeActionClient(directory)
            feedback = SkillFeedbackHandler(plugin)
            runner = SkillRunner(
                plugin, client, directory, feedback=feedback
            )
            runner.register(MineOreSkill())
            started = await runner.start("mine_ore", "maid", {
                "selector": {
                    "type": "tag", "id": "minecraft:diamond_ores",
                },
                "target_count": 1,
                "target_metric": "blocks_harvested",
            })
            action_id = started["current_action_id"]
            terminal = {
                "action_id": action_id,
                "maid_id": "maid",
                "generation": 1,
                "sequence": 46,
                "kind": "autonomous_mining",
                "status": "FAILED",
                "stage": "FAILED",
                "end_reason": "STUCK",
                "result": {
                    "phase": "BLOCKED",
                    "blocked_reason":
                        "maid_moved_above_controlled_descend_origin",
                    "decision_required": True,
                    "collected_count": 0,
                    "target_count": 1,
                    "remaining_target_count": 1,
                    "restart_supported": False,
                    "vein_locked": False,
                },
            }
            consumed = await runner.on_action_event(
                "maid_action_finished", terminal, terminal
            )
            self.assertTrue(consumed)
            await wait_until(
                lambda: bool(plugin.pushes)
                and runner.get_status(started["skill_id"])[
                    "blocked_notification_revision"
                ] > 0,
                timeout=6,
            )
            snapshot = runner.get_status(started["skill_id"])
            self.assertEqual("BLOCKED", snapshot["status"])
            self.assertEqual(4, plugin.attempts)
            self.assertEqual("respond", plugin.pushes[0][1]["ai_behavior"])
            self.assertIn("必须基于", plugin.pushes[0][0])
            await runner.close()

    async def test_backpack_full_terminal_reaches_llm_once_via_internal_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            plugin = ServiceIntegrationPlugin()
            service = MaidActionService(plugin)
            feedback = SkillFeedbackHandler(plugin)
            runner = SkillRunner(
                plugin, service, directory, feedback=feedback
            )
            runner.register(MineOreSkill())
            started = await runner.start("mine_ore", "maid", {
                "selector": {
                    "type": "tag", "id": "minecraft:diamond_ores",
                },
                "target_count": 10,
                "target_metric": "blocks_harvested",
            })
            action_id = started["current_action_id"]
            self.assertEqual(
                (started["skill_id"], "internal"),
                service._owners[action_id],
            )

            terminal = {
                "action_id": action_id,
                "maid_id": "maid",
                "generation": 1,
                "sequence": 2,
                "kind": "autonomous_mining",
                "status": "FAILED",
                "stage": "FAILED",
                "end_reason": "SAFETY_PREEMPTED",
                "result": {
                    "phase": "BLOCKED",
                    "blocked_reason": "backpack_full",
                    "decision_required": True,
                    "collected_count": 5,
                    "target_count": 10,
                    "remaining_target_count": 5,
                    "restart_supported": True,
                    "vein_locked": False,
                },
            }
            consumed = await service.handle_message({
                "type": "maid_action_finished", "data": terminal,
            })
            self.assertTrue(consumed)
            checkpoint_path = Path(directory) / f"{started['skill_id']}.json"

            def notification_persisted():
                try:
                    checkpoint = json.loads(
                        checkpoint_path.read_text(encoding="utf-8")
                    )
                except (FileNotFoundError, json.JSONDecodeError):
                    return False
                return checkpoint.get("blocked_notification_revision", 0) > 0

            await wait_until(
                lambda: len(plugin.pushes) == 1
                and runner.get_status(started["skill_id"])[
                    "blocked_notification_revision"
                ] > 0
                and notification_persisted(),
            )

            snapshot = runner.get_status(started["skill_id"])
            self.assertEqual("BLOCKED", snapshot["status"])
            self.assertEqual("BACKPACK_FULL", snapshot["last_failure_reason"])
            self.assertEqual(
                5,
                snapshot["decision_context"]["restart_template"]["target_count"],
            )
            self.assertEqual(1, len(plugin.pushes))
            text, kwargs = plugin.pushes[0]
            self.assertEqual("respond", kwargs["ai_behavior"])
            self.assertEqual(
                "Minecraft 女仆 Skill 阻塞",
                kwargs["metadata"]["description"],
            )
            self.assertIn("背包已满", text)
            self.assertNotIn("自主挖矿已结束", text)

            checkpoint = json.loads(
                checkpoint_path.read_text(encoding="utf-8")
            )
            self.assertGreater(checkpoint["blocked_notification_revision"], 0)
            self.assertEqual(
                snapshot["decision_context"], checkpoint["decision_context"]
            )

            await service.handle_message({
                "type": "maid_action_finished", "data": terminal,
            })
            await asyncio.sleep(0)
            self.assertEqual(1, len(plugin.pushes))
            await runner.close()

    async def test_load_reclaims_child_and_reconcile_adopts_server_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            skill_id = str(uuid.uuid4())
            action_id = str(uuid.uuid4())
            run = SkillRun(
                skill_id=skill_id,
                maid_id="maid",
                skill_name="one_action",
                args={"target": "stone"},
                status="WAITING_ACTION",
                current_action_id=action_id,
                current_action_generation=1,
                current_action_request={
                    "action_id": action_id,
                    "kind": "harvest_blocks",
                    "args": {
                        "selector": {"type": "block", "id": "minecraft:stone"},
                    },
                    "timeout_ms": 0,
                    "replace_existing": True,
                },
                revision=1,
            )
            run.current_action_fingerprint = action_fingerprint(
                run.maid_id, action_id, run.current_action_request
            )
            await SkillCheckpointStore(directory).save(run)
            client = FakeActionClient(directory)
            client.active[action_id] = {
                "action_id": action_id,
                "maid_id": "maid",
                "generation": 2,
                "sequence": 3,
                "status": "RUNNING",
            }
            runner = SkillRunner(FakePlugin(), client, directory)
            runner.register(OneActionSkill())
            await runner.load()
            self.assertEqual(skill_id, runner.claims(action_id))
            result = await runner.reconcile()
            self.assertIn(action_id, result["adopted"])
            self.assertEqual(
                2, runner.get_status(skill_id)["current_action_generation"]
            )
            await runner.close()

    async def test_starting_checkpoint_is_resubmitted_with_same_action_id(self):
        with tempfile.TemporaryDirectory() as directory:
            skill_id = str(uuid.uuid4())
            action_id = str(uuid.uuid4())
            request = {
                "action_id": action_id,
                "kind": "navigate",
                "args": {"target": {"x": 1, "y": 64, "z": 1}},
                "timeout_ms": 60000,
                "replace_existing": True,
            }
            run = SkillRun(
                skill_id=skill_id,
                maid_id="maid",
                skill_name="one_action",
                args={"target": "stone"},
                status="STARTING_ACTION",
                current_action_id=action_id,
                current_action_request=request,
                revision=1,
            )
            run.current_action_fingerprint = action_fingerprint(
                run.maid_id, action_id, request
            )
            await SkillCheckpointStore(directory).save(run)
            client = FakeActionClient(directory)
            runner = SkillRunner(FakePlugin(), client, directory)
            runner.register(OneActionSkill())
            await runner.load()
            await runner.reconcile()
            self.assertEqual(action_id, client.start_calls[0]["action_id"])
            self.assertEqual(
                "WAITING_ACTION", runner.get_status(skill_id)["status"]
            )
            await runner.close()

    async def test_cancel_waits_for_child_terminal_before_skill_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            feedback = FakeFeedback()
            client = FakeActionClient(directory)
            runner = SkillRunner(FakePlugin(), client, directory, feedback=feedback)
            runner.register(OneActionSkill())
            started = await runner.start("one_action", "maid", {})
            action_id = started["current_action_id"]

            cancelling = await runner.cancel(started["skill_id"], "maid")
            self.assertEqual("CANCEL_REQUESTED", cancelling["status"])
            self.assertEqual([], feedback.finished_runs)

            terminal = {
                "action_id": action_id,
                "maid_id": "maid",
                "generation": 1,
                "sequence": 2,
                "status": "CANCELLED",
                "end_reason": "REQUESTED",
                "result": {},
            }
            await runner.on_action_event("maid_action_finished", terminal, terminal)
            await wait_until(
                lambda: runner.get_status(started["skill_id"])["status"] == "CANCELLED"
            )
            await wait_until(lambda: len(feedback.finished_runs) == 1)
            final = runner.get_status(started["skill_id"])
            self.assertEqual("REQUESTED", final["last_failure_reason"])
            self.assertEqual(1, len(feedback.finished_runs))
            await runner.close()

    async def test_cancel_during_child_start_waits_then_cancels_accepted_action(self):
        with tempfile.TemporaryDirectory() as directory:
            client = BlockingStartActionClient(directory)
            client.emit_terminal_on_cancel = True
            runner = SkillRunner(FakePlugin(), client, directory)
            runner.register(OneActionSkill())
            skill_id = str(uuid.uuid4())

            start_task = asyncio.create_task(runner.start(
                "one_action", "maid", {}, skill_id=skill_id
            ))
            await asyncio.wait_for(client.start_entered.wait(), timeout=1)
            cancel_task = asyncio.create_task(runner.cancel(skill_id, "maid"))
            await asyncio.sleep(0)
            self.assertEqual([], client.cancel_calls)

            client.release_start.set()
            await start_task
            cancelling = await cancel_task
            self.assertEqual("CANCEL_REQUESTED", cancelling["status"])
            await wait_until(
                lambda: runner.get_status(skill_id)["status"] == "CANCELLED"
            )
            self.assertEqual(1, len(client.cancel_calls))
            await runner.close()

    async def test_cancelled_start_clears_inflight_start_barrier(self):
        with tempfile.TemporaryDirectory() as directory:
            client = BlockingStartActionClient(directory)
            runner = SkillRunner(FakePlugin(), client, directory)
            runner.register(OneActionSkill())
            skill_id = str(uuid.uuid4())

            start_task = asyncio.create_task(runner.start(
                "one_action", "maid", {}, skill_id=skill_id
            ))
            await asyncio.wait_for(client.start_entered.wait(), timeout=1)
            action_id = runner.get_status(skill_id)["current_action_id"]
            start_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await start_task

            self.assertNotIn(action_id, runner._starts_inflight)
            self.assertTrue(runner._start_event(action_id).is_set())
            await runner.close()

    async def test_claimed_progress_is_forwarded_without_waiting_for_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            feedback = FakeFeedback()
            client = FakeActionClient(directory)
            runner = SkillRunner(FakePlugin(), client, directory, feedback=feedback)
            runner.register(OneActionSkill())
            started = await runner.start("one_action", "maid", {})
            progress = {
                "action_id": started["current_action_id"],
                "maid_id": "maid",
                "generation": 1,
                "sequence": 2,
                "status": "RUNNING",
                "stage": "SEARCHING",
            }
            consumed = await runner.on_action_event(
                "maid_action_progress", progress, progress
            )
            self.assertTrue(consumed)
            await wait_until(lambda: len(feedback.progress_runs) == 1)
            self.assertEqual(
                "SEARCHING",
                feedback.progress_runs[0]["child_action"]["stage"],
            )
            await runner.close()

    async def test_autonomous_progress_updates_checkpointed_skill_count(self):
        with tempfile.TemporaryDirectory() as directory:
            feedback = FakeFeedback()
            client = FakeActionClient(directory)
            runner = SkillRunner(FakePlugin(), client, directory, feedback=feedback)
            runner.register(AutonomousOneActionSkill())
            started = await runner.start("autonomous_one_action", "maid", {})
            progress = {
                "action_id": started["current_action_id"],
                "maid_id": "maid",
                "generation": 1,
                "sequence": 2,
                "kind": "autonomous_mining",
                "status": "RUNNING",
                "stage": "HARVESTING",
                "detail": {
                    "phase": "HARVESTING",
                    "collected_count": 4,
                    "target_count": 10,
                    "segments_dug": 3,
                },
            }
            await runner.on_action_event("maid_action_progress", progress, progress)
            await wait_until(lambda: len(feedback.progress_runs) == 1)
            snapshot = runner.get_status(started["skill_id"])
            self.assertEqual(4, snapshot["collected_count"])
            self.assertEqual("HARVESTING", snapshot["result"]["stage"])
            self.assertEqual(3, snapshot["result"]["java_progress"]["segments_dug"])
            self.assertEqual(4, feedback.progress_runs[0]["collected_count"])
            await runner.close()

    async def test_resumed_autonomous_child_adopts_newer_generation_events(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = SkillRunner(FakePlugin(), FakeActionClient(directory), directory)
            runner.register(AutonomousOneActionSkill())
            started = await runner.start("autonomous_one_action", "maid", {})
            action_id = started["current_action_id"]

            resumed = {
                "action_id": action_id,
                "maid_id": "maid",
                "generation": 2,
                "sequence": 1,
                "kind": "autonomous_mining",
                "status": "RUNNING",
                "stage": "SCANNING",
                "detail": {"phase": "SCANNING", "collected_count": 2},
            }
            stale = {
                **resumed,
                "generation": 1,
                "sequence": 99,
                "detail": {"phase": "SCANNING", "collected_count": 9},
            }
            await runner.on_action_event("maid_action_progress", resumed, resumed)
            await runner.on_action_event("maid_action_progress", stale, stale)

            snapshot = runner.get_status(started["skill_id"])
            self.assertEqual(2, snapshot["current_action_generation"])
            self.assertEqual(2, snapshot["collected_count"])
            await runner.close()

    async def test_external_child_supersede_cancels_skill_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            feedback = FakeFeedback()
            client = FakeActionClient(directory)
            runner = SkillRunner(FakePlugin(), client, directory, feedback=feedback)
            runner.register(TwoActionSkill())
            started = await runner.start("two_action", "maid", {})
            terminal = {
                "action_id": started["current_action_id"],
                "maid_id": "maid",
                "generation": 1,
                "sequence": 2,
                "status": "SUPERSEDED",
                "end_reason": "SUPERSEDED",
                "result": {},
            }
            await runner.on_action_event("maid_action_finished", terminal, terminal)
            await wait_until(
                lambda: runner.get_status(started["skill_id"])["status"] == "CANCELLED"
            )
            await wait_until(lambda: len(feedback.finished_runs) == 1)
            final = runner.get_status(started["skill_id"])
            self.assertEqual("SUPERSEDED", final["last_failure_reason"])
            self.assertEqual(1, len(client.start_calls), "skill must not start a recovery child")
            self.assertEqual(1, len(feedback.finished_runs))
            await runner.close()

    async def test_replace_waits_for_old_terminal_then_starts_new_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeActionClient(directory)
            client.emit_terminal_on_cancel = True
            runner = SkillRunner(FakePlugin(), client, directory)
            runner.register(OneActionSkill())
            old = await runner.start("one_action", "maid", {"target": "old"})
            new = await runner.start(
                "one_action", "maid", {"target": "new"}, replace_existing=True
            )
            self.assertEqual("CANCELLED", runner.get_status(old["skill_id"])["status"])
            self.assertEqual("WAITING_ACTION", new["status"])
            self.assertNotEqual(old["current_action_id"], new["current_action_id"])
            self.assertEqual(2, len(client.start_calls))
            await runner.close()

    async def test_reconcile_query_error_stays_waiting_and_unresolved(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeActionClient(directory)
            runner = SkillRunner(FakePlugin(), client, directory)
            runner.register(OneActionSkill())
            started = await runner.start("one_action", "maid", {})
            client.active.clear()
            client.status_override = {
                "_query_error": True,
                "error_code": "REQUEST_FAILED",
            }
            result = await runner.reconcile()
            self.assertEqual([started["current_action_id"]], result["unresolved"])
            self.assertEqual(
                "WAITING_ACTION", runner.get_status(started["skill_id"])["status"]
            )
            await runner.close()

    async def test_load_blocks_tampered_action_fingerprint_without_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            skill_id = str(uuid.uuid4())
            action_id = str(uuid.uuid4())
            run = SkillRun(
                skill_id=skill_id,
                maid_id="maid",
                skill_name="one_action",
                args={"target": "stone"},
                status="STARTING_ACTION",
                current_action_id=action_id,
                current_action_fingerprint="tampered",
                current_action_request={
                    "action_id": action_id,
                    "kind": "navigate",
                    "args": {"target": {"x": 1, "y": 64, "z": 1}},
                    "timeout_ms": 60000,
                    "replace_existing": True,
                },
                revision=1,
            )
            await SkillCheckpointStore(directory).save(run)
            feedback = FakeFeedback()
            client = FakeActionClient(directory)
            runner = SkillRunner(FakePlugin(), client, directory, feedback=feedback)
            runner.register(OneActionSkill())
            await runner.load()
            self.assertIsNone(runner.claims(action_id))
            self.assertEqual(
                "CHECKPOINT_FINGERPRINT_MISMATCH",
                runner.get_status(skill_id)["last_failure_reason"],
            )
            await runner.reconcile()
            self.assertEqual([], client.start_calls)
            self.assertEqual(1, len(feedback.blocked_runs))
            await runner.close()


if __name__ == "__main__":
    unittest.main()
