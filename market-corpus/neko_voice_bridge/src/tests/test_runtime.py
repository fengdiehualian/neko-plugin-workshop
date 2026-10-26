"""Host-independent tests. Host client and plugin SDK are deliberately simulated."""
import asyncio
import importlib
import json
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("nvb_test_modules")
pkg.__path__ = [str(ROOT)]
sys.modules[pkg.__name__] = pkg
BridgeClient = importlib.import_module("nvb_test_modules.bridge_client").BridgeClient
worker_module = importlib.import_module("nvb_test_modules.worker")
NekoBackend = importlib.import_module("nvb_test_modules.neko_backend").NekoBackend


def event(**changes):
    return {"type": "transcript.final", "epoch": "room-1", "utterance_id": "turn-1",
            "at_ms": time.time() * 1000, "text": "猫娘你好", "speaker": {
                "display_name": "小明", "peer_id": "rtc1", "identity_verified": False,
                "identity_source": "operator_confirmed"}, **changes}


class PolicyTests(unittest.TestCase):
    def test_bridge_filter_switch_only_relaxes_name_not_identity_or_host_handled(self):
        cfg = {"wake_words": ["猫娘"], "allow_rtc_display_names": True}
        evt = event(text="你好", wake_filter_enabled=False)
        evt["speaker"].update(identity_source="rtc_signaling", audio_source_verified=True)
        self.assertTrue(worker_module.should_reply(evt, cfg, "room-1"))
        self.assertFalse(worker_module.should_reply({**evt, "wake_filter_enabled": True}, cfg, "room-1"))
        self.assertFalse(worker_module.should_reply({**evt, "host_handled": True}, cfg, "room-1"))
        evt["speaker"]["audio_source_verified"] = False
        self.assertFalse(worker_module.should_reply(evt, cfg, "room-1"))

    def test_rtc_display_names_require_audio_mapping_and_explicit_setting(self):
        evt = event()
        evt["speaker"].update(identity_source="rtc_signaling", audio_source_verified=True)
        cfg = {"wake_words": ["猫娘"], "allow_rtc_display_names": True}
        self.assertTrue(worker_module.should_reply(evt, cfg, "room-1"))
        evt["speaker"]["audio_source_verified"] = False
        self.assertFalse(worker_module.should_reply(evt, cfg, "room-1"))
        evt["speaker"]["audio_source_verified"] = True
        cfg["allow_rtc_display_names"] = False
        self.assertFalse(worker_module.should_reply(evt, cfg, "room-1"))

    def test_unverified_identity_needs_explicit_operator_opt_in(self):
        cfg = {"wake_words": ["猫娘"]}
        self.assertFalse(worker_module.should_reply(event(), cfg, "room-1"))
        cfg["allow_operator_confirmed"] = True
        self.assertTrue(worker_module.should_reply(event(), cfg, "room-1"))
        self.assertFalse(worker_module.should_reply(event(), cfg, "another-room"))
        self.assertFalse(worker_module.should_reply(event(text="大家好"), cfg, "room-1"))
        self.assertFalse(worker_module.should_reply(event(at_ms=0), cfg, "room-1"))

    def test_remote_bridge_and_url_credentials_rejected(self):
        for url in ["https://example.com", "http://127.0.0.1@evil.com", "http://127.0.0.1/other", "http://localhost:18765"]:
            with self.assertRaises(ValueError):
                BridgeClient({"bridge_url": url, "bridge_token": "x" * 64})
        BridgeClient({"bridge_url": "http://127.0.0.1:18765", "bridge_token": "x" * 64})

    def test_manifest_and_localized_entries(self):
        text = (ROOT / "plugin.toml").read_text()
        self.assertIn('id = "neko_voice_bridge"', text)
        self.assertIn('entry = "plugin.plugins.neko_voice_bridge:NekoVoiceBridgePlugin"', text)
        for language in ("zh-CN", "en"):
            self.assertEqual({key for key in json.loads((ROOT / "i18n" / (language + ".json")).read_text()) if key.startswith("entries.")},
                             {"entries.ui_only"})


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_backend_uses_character_and_preserves_speaker_data(self):
        clients = []

        class FakeClient:
            def __init__(self, **kw):
                self.kw = kw
                self.closed = False
                clients.append(self)

            async def connect(self, instructions):
                self.instructions = instructions

            async def stream_text(self, text, turn_id):
                self.input, self.turn_id = json.loads(text), turn_id
                await self.kw["on_text_delta"]("你好", True)
                await self.kw["on_text_delta"]("小明", False)

            async def close(self):
                self.closed = True

        class Manager:
            async def aensure_region_resolved(self):
                pass

            def get_model_api_config(self, role):
                self.role = role
                return {"base_url": "https://fixture.invalid", "api_key": "secret-sentinel", "model": "fixture"}

            def get_character_data(self):
                return ("档案测试名", "测试猫娘", {"昵称": "阿青"}, {}, {}, {"测试猫娘": "这是测试角色人格"}, {}, {}, {})

        host = types.ModuleType("main_logic.omni_offline_client")
        host.OmniOfflineClient = FakeClient
        cm = types.ModuleType("utils.config_manager")
        cm.get_config_manager = Manager
        with patch.dict(sys.modules, {host.__name__: host, cm.__name__: cm}):
            b = NekoBackend()
            self.assertEqual(await b.respond(event()), "你好小明")
            c = clients[0]
            self.assertIn("这是测试角色人格", c.instructions)
            self.assertIn('"昵称": "阿青"', c.instructions)
            self.assertIn('开头先叫一下', c.instructions)
            self.assertNotIn("secret-sentinel", c.instructions)
            self.assertEqual(c.kw["tool_definitions"], [])
            self.assertEqual(c.input["speaker"]["display_name"], "小明")
            await b.close()
            self.assertTrue(c.closed)

    async def test_worker_delivers_structured_reply_and_cancels_on_stop(self):
        replied = asyncio.Event()

        class Bridge:
            def __init__(self):
                self.calls = []

            async def request(self, path, data=None):
                self.calls.append((path, data))
                if path == "/v1/status":
                    return {"protocol": "NVB-POC/1", "sequence": 0, "epoch": "room-1", "listening": True, "muted": False}
                if path.startswith("/v1/events"):
                    return {"cursor": 1, "epoch": "room-1", "gap": False,
                            "events": [event()] if path.endswith("=0") else []}
                if path == "/v1/reply":
                    replied.set()
                return {}

        class Backend:
            async def respond(self, evt):
                return "小明你好"

            async def close(self):
                pass

        bridge = Bridge()
        w = worker_module.VoiceWorker(bridge, Backend(), {"wake_words": ["猫娘"], "allow_operator_confirmed": True})
        await w.start()
        try:
            await asyncio.wait_for(replied.wait(), 2)
        finally:
            await w.stop()
        payload = next(data for path, data in bridge.calls if path == "/v1/reply")
        self.assertEqual(payload, {"utterance_id": "turn-1", "epoch": "room-1", "text": "小明你好"})
        self.assertIn(("/v1/cancel", {}), bridge.calls)


if __name__ == "__main__":
    unittest.main()
