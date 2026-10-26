import asyncio
import importlib
import json
import unittest
from unittest.mock import patch

from test_speech import SpeechWorker

diag = importlib.import_module("nvb_speech_tests.diagnostics")


class DiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_speech_error_keeps_stage_without_exporting_secrets(self):
        secret = "https://secret.invalid/?api_key=do-not-log"

        class Bridge:
            async def request(self, path, data):
                self.result = data

        class Speech:
            stage = "asr.import"

            async def handle(self, job):
                raise ModuleNotFoundError(secret, name="main_logic.asr_client")

        bridge = Bridge()
        worker = SpeechWorker(bridge, Speech())
        with patch.object(diag, "_logger") as logger:
            await worker.perform({"kind": "asr", "id": "fixture"})
            logged = logger.error.call_args.args[0]
        self.assertEqual(bridge.result["error"], "NEKO_ASR_HOST_IMPORT_FAILED")
        self.assertEqual(worker.last_diagnostic["stage"], "asr.import")
        self.assertEqual(worker.last_diagnostic["missing_symbol"], "main_logic.asr_client")
        self.assertNotIn(secret, logged)
        self.assertNotIn(secret, json.dumps(worker.last_diagnostic))
        self.assertNotIn("api_key", logged)
        self.assertTrue(worker.last_diagnostic["frames"])

    async def test_timeout_auth_and_signature_failures_are_distinct(self):
        self.assertEqual(diag.speech_failure(TimeoutError(), "asr", "asr.wait_transcript")["code"], "NEKO_ASR_TIMEOUT")
        self.assertEqual(diag.speech_failure(TypeError("secret"), "tts", "tts.route")["code"], "NEKO_TTS_HOST_API_MISMATCH")
        error = RuntimeError("private service response")
        error.status_code = 401
        self.assertEqual(diag.speech_failure(error, "asr", "asr.connect")["code"], "NEKO_ASR_AUTH_FAILED")

    async def test_cancelled_job_is_not_reported_as_a_provider_failure(self):
        class Speech:
            async def handle(self, job):
                raise asyncio.CancelledError()

        worker = SpeechWorker(object(), Speech())
        with self.assertRaises(asyncio.CancelledError):
            await worker.perform({"kind": "asr", "id": "fixture"})
        self.assertIsNone(worker.last_diagnostic)

    async def test_websocket_close_codes_do_not_expose_reason_text(self):
        class Close:
            code = 1008
            reason = "private provider message with a token"

        class ConnectionClosedError(Exception):
            rcvd = Close()
            sent = None

        report = diag.speech_failure(ConnectionClosedError("private"), "asr", "asr.send_audio")
        self.assertEqual(report["code"], "NEKO_ASR_CONNECTION_CLOSED")
        self.assertEqual(report["ws_rcvd_code"], 1008)
        self.assertNotIn("private", json.dumps(report))

    async def test_host_progress_only_exports_bounded_counters(self):
        error = RuntimeError('NEKO_HOST_NO_TRANSCRIPT')
        error.host_progress = {'sent_frames': 200, 'audio_bytes': 0, 'audio_done': False,
                               'transcript': 'private words', 'reply_chars': 'secret'}
        report = diag.speech_failure(error, 'asr', 'host_voice.wait_reply')
        self.assertEqual(report['host_progress'], {'sent_frames': 200, 'audio_bytes': 0, 'audio_done': False})
