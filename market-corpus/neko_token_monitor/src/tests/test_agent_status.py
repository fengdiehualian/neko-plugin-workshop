import importlib.util
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location("agent_status", Path(__file__).resolve().parents[1] / "agent_status.py")
agent_status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_status)


class AgentStatusTests(unittest.TestCase):
    def test_crosspet_active_and_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "gpt-state.json").write_text(json.dumps({"pose": "thinking", "ts": 100, "tool": "private"}))
            (root / "claude-state.json").write_text(json.dumps({"pose": "happy", "ts": 100}))
            self.assertEqual(agent_status.crosspet_activity(root, now=110), {
                "active": [{"id": "gpt", "name": "Codex", "activity": "thinking", "at": 100}],
                "seen": True,
            })
            self.assertEqual(agent_status.crosspet_activity(root, now=100 + 29 * 60)["active"][0]["name"], "Codex")
            self.assertEqual(agent_status.crosspet_activity(root, now=100 + 30 * 60 + 1), {"active": [], "seen": False})

    def test_crosspet_rejects_unknown_and_invalid_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "gpt-state.json").write_text('{"pose":"thinking","ts":false}')
            (root / "claude-state.json").write_text('{"pose":"made_up","ts":100}')
            (root / "deepseek-state.json").write_text("{broken")
            self.assertEqual(agent_status.crosspet_activity(root, now=105), {"active": [], "seen": False})

    def test_neko_task_status(self):
        self.assertEqual(agent_status.classify({"tasks": [{"status": "running"}]}), {"status": "busy"})
        self.assertEqual(agent_status.classify({"tasks": [{"status": "completed"}]}), {"status": "idle"})
        self.assertEqual(agent_status.classify({"tasks": [{"status": "strange"}]}), {"status": "unknown"})

    def test_completion_requires_observed_task_identity_and_explicit_success(self):
        status = agent_status.AgentStatus()
        status.observe_tasks({"status": "idle", "tasks": [{"id": "history", "status": "completed"}]}, now=100)
        self.assertEqual(status.completion_events, [])
        status.observe_tasks({"status": "busy", "tasks": [{"id": "a", "status": "running"}, {"id": "b", "status": "running"}]}, now=101)
        status.observe_tasks({"status": "busy", "tasks": [{"id": "a", "status": "completed"}, {"id": "b", "status": "running"}]}, now=102)
        self.assertEqual(len(status.completion_events), 1)
        self.assertEqual(status.completion_events[0]["source"], "neko_tasks")
        status.observe_tasks({"status": "idle", "tasks": [{"id": "a", "status": "completed"}, {"id": "b", "status": "failed"}]}, now=103)
        self.assertEqual(len(status.completion_events), 1)

    def test_disappearance_failure_and_unknown_state_are_not_completion(self):
        status = agent_status.AgentStatus()
        status.observe_tasks({"status": "busy", "tasks": [{"id": "a", "status": "running"}]}, now=100)
        status.observe_tasks({"status": "unknown"}, now=101)
        self.assertEqual(status.completion_events, [])
        status.observe_tasks({"status": "idle", "tasks": []}, now=102)
        status.observe_tasks({"status": "idle", "tasks": [{"id": "a", "status": "completed"}]}, now=103)
        self.assertEqual(status.completion_events, [])

    def test_completion_queue_expires_and_does_not_export_task_ids(self):
        status = agent_status.AgentStatus()
        status.observe_tasks({"status": "busy", "tasks": [{"id": "private-task-id", "status": "queued"}]}, now=100)
        status.observe_tasks({"status": "idle", "tasks": [{"id": "private-task-id", "status": "completed"}]}, now=101)
        self.assertNotIn("private-task-id", json.dumps(status.completion_events))
        status.observe_tasks({"status": "idle", "tasks": []}, now=222)
        self.assertEqual(status.completion_events, [])

    def test_model_from_current_crosspet_host_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "deepseek-state.json").write_text(json.dumps({"pose": "thinking", "ts": 100, "tool_input": "private"}))
            (root / "gpt-host.json").write_text(json.dumps({"character": "deepseek", "model": "deepseek-v3.2", "ts": 99.9}))
            active = agent_status.crosspet_activity(root, now=110)["active"][0]
            self.assertEqual(active["model"], "deepseek-v3.2")
            self.assertEqual(active["host"], "gpt")
            self.assertNotIn("tool_input", active)

    def test_rejects_stale_or_malformed_model_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "gpt-state.json").write_text(json.dumps({"pose": "running", "ts": 100, "model": "gpt\nprivate"}))
            (root / "gpt-host.json").write_text(json.dumps({"character": "gpt", "model": "claude-sonnet", "ts": 80}))
            self.assertNotIn("model", agent_status.crosspet_activity(root, now=110)["active"][0])

    def test_local_agent_changes_are_visible_after_200ms_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "gpt-state.json"
            path.write_text(json.dumps({"pose": "thinking", "ts": time.time()}))
            status = agent_status.AgentStatus(root)
            status.neko_checked = 10
            with mock.patch.object(agent_status.time, "monotonic", return_value=10) as clock:
                self.assertEqual(status.snapshot()["active_agents"][0]["id"], "gpt")
                path.write_text(json.dumps({"pose": "idle", "ts": time.time()}))
                clock.return_value = 10.1
                self.assertEqual(status.snapshot()["status"], "busy")
                clock.return_value = 10.21
                self.assertEqual(status.snapshot()["status"], "idle")
                (root / "claude-state.json").write_text(json.dumps({"pose": "writing", "ts": time.time()}))
                clock.return_value = 10.42
                self.assertEqual([a["id"] for a in status.snapshot()["active_agents"]], ["claude"])

    def test_slow_neko_query_does_not_block_or_duplicate_local_updates(self):
        entered, release = threading.Event(), threading.Event()

        def slow_query():
            entered.set()
            release.wait(2)
            return {"status": "idle"}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "gpt-state.json").write_text(json.dumps({"pose": "running", "ts": time.time()}))
            status = agent_status.AgentStatus(root)
            with mock.patch.object(status, "_read_neko", side_effect=slow_query) as reader:
                try:
                    started = time.monotonic()
                    data = status.snapshot()
                    self.assertLess(time.monotonic() - started, 0.2)
                    self.assertTrue(entered.wait(0.5))
                    self.assertEqual(data["active_agents"][0]["id"], "gpt")
                    for _ in range(5):
                        self.assertEqual(status.snapshot()["status"], "busy")
                    self.assertEqual(reader.call_count, 1)
                finally:
                    release.set()


if __name__ == "__main__":
    unittest.main()
