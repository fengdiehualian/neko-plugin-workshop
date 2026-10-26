"""Host contract tests with simulated providers; no paid speech calls."""
import asyncio
import base64
import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

pkg = types.ModuleType("nvb_speech_tests")
pkg.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[pkg.__name__] = pkg
speech_module = importlib.import_module(pkg.__name__ + ".neko_speech")
SpeechWorker = importlib.import_module(pkg.__name__ + ".speech_worker").SpeechWorker


def module(name, **fields):
    value = types.ModuleType(name)
    value.__dict__.update(fields)
    return value


class SpeechTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_clears_live_error_but_retains_timestamped_history(self):
        class Bridge:
            async def request(self, *args):
                return {}

        class Speech:
            stage = "host_voice.wait_reply"
            async def handle(self, job):
                if job["id"] == "bad":
                    raise RuntimeError("NEKO_HOST_NO_TRANSCRIPT")
                return {"text": "ok"}

        worker = SpeechWorker(Bridge(), Speech())
        await worker.perform({"id": "bad", "kind": "asr"})
        await worker.perform({"id": "ok", "kind": "asr"})
        self.assertIsNone(worker.last_error)
        self.assertIsNone(worker.last_diagnostic)
        self.assertEqual(len(worker.error_history), 1)
        self.assertEqual(worker.error_history[0]["code"], "NEKO_HOST_NO_TRANSCRIPT")
        self.assertGreater(worker.error_history[0]["at_ms"], 0)

    async def test_asr_uses_host_route_and_closes_isolated_session(self):
        sessions = []

        class Manager:
            async def aensure_region_resolved(self):
                pass

            def get_model_api_config(self, role):
                self.role = role
                return {"api_type": "qwen"}

            def get_core_config(self):
                return {}

        class Session:
            async def connect(self, **kwargs):
                self.received = bytearray()

            async def stream_audio(self, data, sample_rate_hz):
                self.received.extend(data)
                assert sample_rate_hz == 16000

            async def signal_user_activity_end(self):
                await self.callback("猫娘你好")

            async def close(self):
                self.closed = True

        def factory(core, **kwargs):
            self.assertEqual(core, "qwen")
            self.assertEqual(kwargs["config"].endpointing_mode, "manual")
            session = Session()
            session.callback = kwargs["on_input_transcript"]
            sessions.append(session)
            return session

        manager = Manager()
        cm = module("utils.config_manager", get_config_manager=lambda: manager)
        asr = module("main_logic.asr_client", create_asr_session=factory, AsrSessionConfig=types.SimpleNamespace,
                     get_asr_core_capabilities=lambda _: types.SimpleNamespace(supports_independent_asr=True))
        with patch.dict(sys.modules, {cm.__name__: cm, asr.__name__: asr}):
            pcm = b"\x00\x20" * 320
            result = await speech_module.NekoSpeech().handle({"kind": "asr", "pcm": base64.b64encode(pcm).decode(), "sample_rate": 16000})
        self.assertEqual(result, {"text": "猫娘你好"})
        self.assertTrue(sessions[0].closed)
        self.assertTrue(sessions[0].received.startswith(pcm))

    async def test_tts_preserves_character_voice_and_pcm_completion(self):
        calls = []

        class Manager:
            async def aensure_region_resolved(self):
                pass

            async def aload_characters(self):
                return {"当前猫娘": "角色甲", "猫娘": {"角色甲": {"voice_id": "character-voice"}}}

        def worker(requests, responses, key, voice):
            calls.append((key, voice))
            responses.put(("__ready__", True))
            sid, text = requests.get(timeout=3)
            calls.append((sid, text))
            self.assertEqual(requests.get(timeout=3), (None, None))
            responses.put(("__audio__", sid, b"\x00\x20" * 480))
            responses.put(("__audio_done__", sid))

        cm = module("utils.config_manager", get_config_manager=Manager,
                    get_reserved=lambda character, key, **_: character[key])
        voice = module("utils.voice_config", read_legacy_voice_id=lambda value: value)
        service = speech_module.NekoSpeech()
        with patch.dict(sys.modules, {cm.__name__: cm, voice.__name__: voice}), patch.object(
                service, "tts_spec", lambda manager, selected: (worker, "host-only-secret", selected)):
            result = await service.handle({"kind": "tts", "text": "你好小明"})
        self.assertEqual(calls[0], ("host-only-secret", "character-voice"))
        self.assertEqual(calls[1][1], "你好小明")
        self.assertEqual(set(result), {"pcm", "sample_rate"})
        self.assertEqual(base64.b64decode(result["pcm"]), b"\x00\x20" * 480)
        self.assertFalse(service.tts_thread.is_alive())

    async def test_nonfree_native_transcript_without_exporting_credentials(self):
        captured = {}

        class Manager:
            async def aensure_region_resolved(self):
                pass

            def get_model_api_config(self, _):
                return {"api_type": "openai", "base_url": "wss://fixture.invalid", "api_key": "host-secret", "model": "free"}

            def get_core_config(self):
                return {}

        class Native:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.commit = asyncio.Event()

            async def connect(self, **kwargs):
                self.audio = bytearray()

            async def stream_audio(self, data):
                self.audio.extend(data)

            async def signal_user_activity_end(self):
                self.commit.set()

            async def handle_messages(self):
                captured["receiver_started"] = True
                try:
                    await self.commit.wait()
                    await captured["on_input_transcript"]("原生转写")
                    await asyncio.Event().wait()
                finally:
                    captured["receiver_stopped"] = True

            async def close(self):
                captured["closed"] = True

        cm = module("utils.config_manager", get_config_manager=Manager)
        asr = module("main_logic.asr_client", create_asr_session=None, AsrSessionConfig=types.SimpleNamespace,
                     get_asr_core_capabilities=lambda _: types.SimpleNamespace(supports_independent_asr=False))
        native = module("main_logic.omni_realtime_client", OmniRealtimeClient=Native,
                        TurnDetectionMode=types.SimpleNamespace(MANUAL="manual"))
        with patch.dict(sys.modules, {x.__name__: x for x in (cm, asr, native)}):
            text = await asyncio.wait_for(speech_module.NekoSpeech().transcribe(b"\x00\x20" * 320), 4)
        self.assertEqual(text, "原生转写")
        self.assertEqual(captured["api_key"], "host-secret")
        self.assertEqual(captured["tool_definitions"], [])
        self.assertTrue(captured["closed"])
        self.assertTrue(captured["receiver_started"])
        self.assertTrue(captured["receiver_stopped"])

    async def test_expired_job_cancels_host_work_without_sending_result(self):
        started, cancelled = asyncio.Event(), asyncio.Event()

        class Speech:
            async def handle(self, job):
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()

        class Bridge:
            sent = False

            async def request(self, path, data):
                if path == "/v1/speech/disconnect":
                    return {"disconnected": True}
                self_outer.assertEqual(path, "/v1/speech/next")
                if data.get("active_id"):
                    return {"active": False, "job": None}
                if not self.sent:
                    self.sent = True
                    return {"job": {"id": "expired", "kind": "asr"}}
                return {"job": None}

        self_outer = self
        worker = SpeechWorker(Bridge(), Speech())
        await worker.start()
        try:
            await asyncio.wait_for(started.wait(), 2)
            await asyncio.wait_for(cancelled.wait(), 2)
        finally:
            await worker.stop()

    def test_tts_credentials_do_not_fall_back_for_explicit_empty_override(self):
        class Manager:
            def get_core_config(self):
                return {"CORE_API_TYPE": "free"}

            def get_model_api_config(self, role):
                return {"api_key": "unrelated-provider-key"}

            def voice_id_exists_in_any_storage(self, voice):
                return False

        def worker(*args):
            return None
        tts = module("main_logic.tts_client", dummy_tts_worker=object(), get_tts_worker=lambda **_: (worker, "", "vllm_omni"))
        gsv = module("utils.gptsovits_config", is_gsv_disabled_voice_id=lambda _: True)
        voices = module("utils.tts.native_voice_registry", is_free_preset_voice_id=lambda _: True,
                        resolve_native_voice_for_routing=lambda *args, **kwargs: ("", False))
        with patch.dict(sys.modules, {x.__name__: x for x in (tts, gsv, voices)}):
            _, key, _ = speech_module.NekoSpeech.tts_spec(Manager(), "")
        self.assertEqual(key, "")

    async def test_native_receiver_failure_is_not_masked_by_cleanup(self):
        closed = []

        class Manager:
            async def aensure_region_resolved(self):
                pass

            def get_model_api_config(self, _):
                return {"api_type": "openai", "base_url": "wss://fixture.invalid", "model": "free"}

            def get_core_config(self):
                return {}

        class Native:
            def __init__(self, **_):
                pass

            async def connect(self, **_):
                pass

            async def stream_audio(self, data):
                pass

            async def handle_messages(self):
                raise RuntimeError("NEKO_ASR_CONNECTION_CLOSED")

            async def close(self):
                closed.append(True)
                raise RuntimeError("cleanup must not mask primary error")

        cm = module("utils.config_manager", get_config_manager=Manager)
        asr = module("main_logic.asr_client", create_asr_session=None, AsrSessionConfig=types.SimpleNamespace,
                     get_asr_core_capabilities=lambda _: types.SimpleNamespace(supports_independent_asr=False))
        native = module("main_logic.omni_realtime_client", OmniRealtimeClient=Native,
                        TurnDetectionMode=types.SimpleNamespace(MANUAL="manual"))
        with patch.dict(sys.modules, {x.__name__: x for x in (cm, asr, native)}):
            with self.assertRaisesRegex(RuntimeError, "NEKO_ASR_CONNECTION_CLOSED"):
                await asyncio.wait_for(speech_module.NekoSpeech().transcribe(b"\x00\x20" * 320), 2)
        self.assertEqual(closed, [True])
