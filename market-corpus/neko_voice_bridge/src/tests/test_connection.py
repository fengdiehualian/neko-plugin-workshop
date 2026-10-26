"""Connection supervision with a simulated bridge; no running host required."""
import asyncio
import base64
import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

pkg = types.ModuleType("nvb_connection_tests")
pkg.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[pkg.__name__] = pkg
module = importlib.import_module(pkg.__name__ + ".connection")
CONFIG = {"bridge_url": "http://127.0.0.1:18765", "bridge_token": "test-token-" * 5}


class Bridge:
    def __init__(self):
        self.instance = "exe-1"
        self.active = False
        self.ready = False
        self.available = True
        self.character = None

    async def request(self, path, data=None):
        if not self.available:
            raise OSError("offline")
        if path == "/v1/plugin/heartbeat":
            self.character = data["character"]
            self.ready = data["worker_ready"]
        if path == "/v1/plugin/disconnect":
            self.ready = self.active = False
        if path == "/v1/join":
            self.active = True
        if path == "/v1/leave":
            self.active = False
        return {"instance_id": self.instance, "speech": {"mode": "neko"},
                "plugin": {"connected": self.ready}, "state": "ACTIVE" if self.active else "READY",
                "rtc_connected": True, "members": []}


class Worker:
    def __init__(self, client, backend, config):
        self.config = config
        self.task = None
        self.processed = 0
        self.last_error = self.speech = None
        self.stopped = False

    async def start(self):
        self.task = asyncio.create_task(asyncio.Event().wait())

    async def stop(self):
        self.stopped = True
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)


class Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = Bridge()
        self.character = {"name": "小月", "revision": "one"}

        async def character():
            return dict(self.character)

        self.link = module.Connection(Path(self.temp.name) / "bridge.json", character_reader=character,
                                      worker_factory=Worker, client_factory=lambda _: self.bridge)

    async def asyncTearDown(self):
        await self.link.close()
        self.temp.cleanup()

    async def test_pair_code_state_controls_and_no_manual_names(self):
        raw = {**CONFIG, "wake_words": ["错误名称"]}
        code = "NVB1:" + base64.urlsafe_b64encode(json.dumps(raw).encode()).decode().rstrip("=")
        await self.link.pair(code)
        self.assertEqual(self.link.worker.config["response_mode"], "wake_word")
        self.assertEqual(self.link.snapshot()["phase"], "connected")
        self.assertNotIn(CONFIG["bridge_token"], json.dumps(self.link.snapshot()))
        self.assertNotIn("wake_words", json.loads(self.link.config_path.read_text()))
        await self.link.control("start")
        self.assertEqual(self.link.snapshot()["phase"], "receiving")
        # A stop made in the EXE is observed by the panel on its next heartbeat.
        self.bridge.active = False
        await self.link.pulse()
        self.assertEqual(self.link.snapshot()["phase"], "connected")
        await self.link.control("disconnect")
        self.assertEqual(self.link.snapshot()["phase"], "disconnected")
        self.assertFalse(json.loads(self.link.config_path.read_text())["enabled"])
        with self.assertRaisesRegex(RuntimeError, "BRIDGE_NOT_CONNECTED"):
            await self.link.control("start")
        await self.link.control("reconnect")
        self.assertTrue(self.link.snapshot()["connected"])

    async def test_exe_restart_and_character_changes_replace_old_worker(self):
        await self.link.pair(json.dumps(CONFIG))
        old = self.link.worker
        self.bridge.instance = "exe-2"
        await self.link.pulse()
        self.assertTrue(old.stopped)
        old = self.link.worker
        self.character = {"name": "新称呼", "revision": "two"}
        await self.link.pulse()
        self.assertTrue(old.stopped)
        self.assertEqual(self.link.worker.config["response_mode"], "wake_word")
        self.assertEqual(self.bridge.character, self.character)

    async def test_outage_and_recovery_are_supervised(self):
        await self.link.pair(json.dumps(CONFIG))
        old = self.link.worker
        self.bridge.available = False
        self.link.task = asyncio.create_task(self.link.run())
        for _ in range(30):
            if self.link.error:
                break
            await asyncio.sleep(.01)
        self.assertEqual(self.link.snapshot()["phase"], "reconnecting")
        self.assertTrue(old.stopped)
        self.bridge.available = True
        for _ in range(150):
            if self.link.snapshot()["connected"]:
                break
            await asyncio.sleep(.01)
        self.assertTrue(self.link.snapshot()["connected"])

    async def test_read_host_nickname_and_profile_fallback(self):
        cm = types.ModuleType("utils.config_manager")
        character = {"昵称": "主人设定的称呼"}
        cm.get_config_manager = lambda: types.SimpleNamespace(get_character_data=lambda: (
            "主人", "角色档案", {}, {"角色档案": character}, {}, {"角色档案": "提示词"}))
        with patch.dict(sys.modules, {cm.__name__: cm}):
            first = await module.read_character()
            self.assertEqual(first["name"], "主人设定的称呼")
            character.clear()
            second = await module.read_character()
            self.assertEqual(second["name"], "角色档案")
            self.assertNotEqual(first["revision"], second["revision"])

    async def test_nonlocal_connection_code_rejected(self):
        with self.assertRaises(ValueError):
            module.parse_connection_code(json.dumps({**CONFIG, "bridge_url": "https://example.com"}))
