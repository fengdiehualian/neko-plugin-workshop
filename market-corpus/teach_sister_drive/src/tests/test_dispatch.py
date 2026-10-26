import asyncio
import threading
import time
import unittest
from unittest.mock import Mock, patch

from plugin.plugins.teach_sister_drive import (
    BridgeError,
    NekoPluginBase,
    TeachSisterDrivePlugin,
    _decode_bridge,
    _validate_receipt,
)
from plugin.plugins.teach_sister_drive.intent import parse_command


def make_plugin():
    # Skip host storage/IPC setup, exercise the real plugin state and methods.
    with patch.object(NekoPluginBase, "__init__", return_value=None):
        plugin = TeachSisterDrivePlugin()
    plugin._cfg = {"teach_sister_drive": {"fallback_enabled": True}}
    plugin._worker = Mock(is_alive=Mock(return_value=True))
    plugin._set_snapshot(
        {
            "ok": True,
            "protocol_version": 2,
            "scene_id": "1:road",
            "game_active": True,
            "input_owner": "plugin",
            "session_id": plugin._session_id,
            "lanlan_name": "悠怡",
        }
    )
    return plugin


def executed(job):
    return {
        "ok": True,
        "request_id": job.request_id,
        "status": "executed",
        "results": [{"action": job.plan.commands[0]["action"], "status": "executed"}],
        "telemetry": {"speed_kmh": 0},
    }


class DispatchTests(unittest.TestCase):
    def test_default_installer_service_never_acquires_game_control(self):
        plugin = make_plugin()
        plugin._cfg = {}
        state = {**plugin._snapshot, "input_owner": "none"}
        plugin._http = Mock(side_effect=lambda path, *args: {"protocol_version": 2} if path == "/health" else state)
        plugin._maintain_session()
        self.assertFalse(any(call.args[0] == "/session" for call in plugin._http.call_args_list))

    def test_fallback_yields_native_microphone_without_reacquiring(self):
        for owner in ("plugin", "none", "browser"):
            plugin = make_plugin()
            status = {
                **plugin._snapshot,
                "input_owner": owner,
                "body": {"tutorial_state": {"microphone_stage": "waiting_input"}},
            }
            plugin._http = Mock(
                side_effect=lambda path, *args: {"protocol_version": 2} if path == "/health" else status
            )
            plugin._maintain_session()
            sessions = [c for c in plugin._http.call_args_list if c.args[0] == "/session"]
            self.assertEqual(len(sessions), 1 if owner == "plugin" else 0)
            if sessions:
                self.assertFalse(sessions[0].args[1]["active"])

    def test_fallback_resumes_after_native_microphone_exercise(self):
        plugin = make_plugin()
        status = {
            **plugin._snapshot,
            "input_owner": "none",
            "body": {"tutorial_state": {"microphone_stage": "continuing"}},
        }
        plugin._http = Mock(side_effect=lambda path, *args: {"protocol_version": 2} if path == "/health" else status)
        plugin._maintain_session()
        sessions = [c for c in plugin._http.call_args_list if c.args[0] == "/session"]
        self.assertEqual(len(sessions), 1)
        self.assertTrue(sessions[0].args[1]["active"])

    def test_invalid_bridge_responses_never_succeed(self):
        for value in ("", "<html>bad gateway</html>", "{}", "[]", '{"ok":"true"}', '{"ok":false}'):
            with self.subTest(value=value), self.assertRaises(BridgeError):
                _decode_bridge(value)

    def test_receipt_requires_matching_id_and_executed_action(self):
        for receipt in (
            {"ok": True, "request_id": "wrong", "status": "executed", "results": []},
            {"ok": True, "request_id": "req", "status": "executed", "results": []},
            {
                "ok": True,
                "request_id": "req",
                "status": "executed",
                "results": [{"action": "left", "status": "executed"}],
            },
            {"ok": False, "request_id": "req", "status": "expired", "results": []},
        ):
            with self.subTest(receipt=receipt), self.assertRaises(BridgeError):
                _validate_receipt(receipt, "req", "forward")
        pending = {"ok": False, "request_id": "req", "status": "executing", "results": []}
        self.assertIs(_validate_receipt(pending, "req", "forward"), pending)

    def test_observer_is_fast_queue_only_and_character_scoped(self):
        plugin = make_plugin()
        plugin._http = Mock(side_effect=AssertionError("observer must not perform network I/O"))
        start = time.monotonic()
        result = asyncio.run(plugin.observe_driving_voice("刹车", "悠怡"))
        self.assertLess(time.monotonic() - start, 0.5)
        self.assertEqual(result.value["action"], "noop")
        self.assertTrue(result.value["queued"])
        self.assertEqual(plugin._jobs.qsize(), 1)
        result = asyncio.run(plugin.observe_driving_voice("往前开", "另一角色"))
        self.assertEqual(result.value["reason"], "different_character")
        result = asyncio.run(plugin.observe_driving_voice("不要往前开", "悠怡"))
        self.assertEqual(result.value["reason"], "no_immediate_command")
        self.assertEqual(plugin._jobs.qsize(), 1)

    def test_browser_owner_and_stale_snapshot_block_fallback(self):
        plugin = make_plugin()
        plugin._snapshot["input_owner"] = "browser"
        self.assertIsNone(plugin._enqueue(parse_command("刹车"), "刹车", "agent")[0])
        plugin._snapshot["input_owner"] = "plugin"
        plugin._snapshot_at -= 4
        self.assertIsNone(plugin._enqueue(parse_command("刹车"), "刹车", "voice_transcript")[0])
        self.assertEqual(plugin._jobs.qsize(), 0)

    def test_cross_source_pairing_preserves_repeated_voice_commands(self):
        plugin = make_plugin()
        plan = parse_command("左转")
        first, _ = plugin._enqueue(plan, "左转", "voice_transcript")
        second, _ = plugin._enqueue(plan, "左转", "voice_transcript")
        self.assertIsNot(first, second)
        self.assertIs(plugin._enqueue(plan, "左转", "agent")[0], first)
        self.assertIs(plugin._enqueue(plan, "左转", "agent")[0], second)
        self.assertEqual(plugin._jobs.qsize(), 2)

    def test_scene_change_does_not_pair_with_old_command(self):
        plugin = make_plugin()
        first, _ = plugin._enqueue(parse_command("左转"), "左转", "voice_transcript")
        plugin._snapshot["scene_id"] = "2:road"
        second, _ = plugin._enqueue(parse_command("左转"), "左转", "agent")
        self.assertIsNot(first, second)
        self.assertEqual(second.scene_id, "2:road")

    def test_fresh_voice_does_not_reuse_old_agent_failure(self):
        plugin = make_plugin()
        first, _ = plugin._enqueue(parse_command("左转"), "左转", "agent")
        plugin._complete(first, {"ok": False, "status": "rejected"})
        second, _ = plugin._enqueue(parse_command("左转"), "左转", "voice_transcript")
        self.assertIsNot(first, second)

    def test_expired_job_is_never_sent(self):
        plugin = make_plugin()
        job, _ = plugin._enqueue(parse_command("往前开"), "往前开", "agent")
        job.created_at -= 2
        plugin._http = Mock()
        plugin._execute(job)
        plugin._http.assert_not_called()
        self.assertFalse(job.receipt["ok"])

    def test_scene_change_is_checked_before_sending(self):
        plugin = make_plugin()
        job, _ = plugin._enqueue(parse_command("往前开"), "往前开", "agent")
        plugin._http = Mock(return_value={**plugin._snapshot, "scene_id": "2:road"})
        plugin._execute(job)
        self.assertEqual(plugin._http.call_count, 1)
        self.assertFalse(job.receipt["ok"])

    def test_uncertain_transport_polls_same_id_without_resending(self):
        plugin = make_plugin()
        job, _ = plugin._enqueue(parse_command("往前开"), "往前开", "agent")
        plugin._http = Mock(side_effect=[plugin._snapshot, BridgeError("timeout"), executed(job)])
        plugin._execute(job)
        paths = [call.args[0] for call in plugin._http.call_args_list]
        self.assertEqual(paths, ["/status", "/command", "/commands/" + job.request_id])
        self.assertTrue(job.receipt["ok"])

    def test_pending_receipt_is_polled_not_mistaken_for_completion(self):
        plugin = make_plugin()
        job, _ = plugin._enqueue(parse_command("刹车"), "刹车", "agent")
        pending = {"ok": False, "request_id": job.request_id, "status": "executing", "results": []}
        plugin._http = Mock(side_effect=[plugin._snapshot, pending, executed(job)])
        plugin._execute(job)
        self.assertTrue(job.receipt["ok"])
        self.assertEqual(plugin._http.call_count, 3)

    def test_explicit_rejection_is_preserved(self):
        plugin = make_plugin()
        job, _ = plugin._enqueue(parse_command("刹车"), "刹车", "agent")
        plugin._http = Mock(
            side_effect=[
                plugin._snapshot,
                {
                    "ok": False,
                    "request_id": job.request_id,
                    "status": "rejected",
                    "error": "stale_scene",
                    "results": [],
                },
            ]
        )
        plugin._execute(job)
        self.assertEqual(job.receipt["error"], "stale_scene")
        self.assertEqual(plugin._http.call_count, 2)

    def test_worker_executes_job_and_releases_lease_on_shutdown(self):
        plugin = make_plugin()
        job, _ = plugin._enqueue(parse_command("刹车"), "刹车", "agent")
        plugin._http = Mock(side_effect=[plugin._snapshot, executed(job), {"ok": True}])
        worker = threading.Thread(target=plugin._worker_loop)
        plugin._worker = worker
        worker.start()
        try:
            self.assertTrue(job.done.wait(1))
        finally:
            plugin._stop.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(job.receipt["ok"])
        release = plugin._http.call_args_list[-1]
        self.assertEqual(release.args[0], "/session")
        self.assertFalse(release.args[1]["active"])

    def test_later_agent_uses_voice_receipt_instead_of_second_command(self):
        plugin = make_plugin()
        job, _ = plugin._enqueue(parse_command("刹车"), "刹车", "voice_transcript")
        plugin._complete(job, executed(job))
        result = asyncio.run(plugin.drive(action="forward", intent="刹车"))
        self.assertEqual(result.value["request_id"], job.request_id)
        self.assertEqual(result.value["commands"], [{"action": "brake", "target_speed": 0}])
        self.assertEqual(plugin._jobs.qsize(), 1)

    def test_context_original_negation_overrides_agent_rewrite(self):
        plugin = make_plugin()
        result = asyncio.run(
            plugin.drive(action="forward", intent="往前开", _ctx={"latest_user_request": "不要往前开"})
        )
        self.assertEqual(result.value["status"], "not_requested")
        self.assertEqual(result.value["request_id"], "")
        self.assertEqual(plugin._jobs.qsize(), 0)


if __name__ == "__main__":
    unittest.main()
