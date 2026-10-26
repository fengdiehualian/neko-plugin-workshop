"""Exercise host frontend framing, handshake and cancellation without cloud calls."""
import asyncio
import base64
import importlib
import json
import struct
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

pkg = types.ModuleType('nvb_host_voice_tests')
pkg.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[pkg.__name__] = pkg
HostVoice = importlib.import_module(pkg.__name__ + '.host_voice').HostVoice
NekoSpeech = importlib.import_module(pkg.__name__ + '.neko_speech').NekoSpeech


class Socket:
    def __init__(self, scenario='ok'):
        self.incoming = asyncio.Queue()
        self.sent = []
        self.scenario = scenario
        self.frames = 0

    async def send(self, raw):
        self.sent.append(raw)
        if isinstance(raw, str):
            message = json.loads(raw)
            if message['action'] == 'start_session':
                await self.incoming.put(json.dumps({'type': 'session_started', 'input_mode': 'audio',
                    'request_id': message['request_id'], 'microphone_route': 'native'}))
        else:
            self.frames += 1
            if self.frames == (111 if self.scenario == 'late_vad' else 1) and self.scenario != 'no_transcript':
                events = [
                    {'type': 'user_transcript', 'text': '猫娘你好'},
                    {'type': 'gemini_response', 'text': '你好呀', 'turn_id': 's1'},
                    {'type': 'system', 'data': 'turn end'},
                    {'type': 'audio_chunk', 'speech_id': 's1'},
                    b'OggS0000' if self.scenario == 'ogg' else b'\x00\x20' * 480,
                    {'type': 'audio_done', 'speech_id': 's1'},
                ]
                if self.scenario == 'transcript_only':
                    events = events[:1]
                for event in events:
                    await self.incoming.put(event if isinstance(event, bytes) else json.dumps(event))

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.incoming.get()


class HostVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_cable_waits_for_capture_without_websocket_reply_audio(self):
        class TranscriptOnly(Socket):
            async def send(self, raw):
                if isinstance(raw, str):
                    return await super().send(raw)
                self.frames += 1
                if self.frames == 1:
                    await self.incoming.put(json.dumps({'type': 'user_transcript', 'text': '猫娘你好'}))
        polls = []

        async def progress(text):
            polls.append(text)
            return {'done': len(polls) >= 3}

        ws = TranscriptOnly()
        result = await HostVoice(cable_progress=progress).exchange(ws, bytes(640), {})
        self.assertEqual(result, {'text': '猫娘你好', 'output_transport': 'cable'})
        self.assertGreater(ws.frames, 5)  # Keep host socket/input alive until capture finishes.
        self.assertTrue(all(t == '猫娘你好' for t in polls))
        count = ws.frames
        await asyncio.sleep(.03)
        self.assertEqual(ws.frames, count)

    async def test_cable_reports_missing_device_and_cancels_poll_task(self):
        polls = []

        async def progress(text):
            polls.append(text)
            return {'error': 'NEKO_CABLE_DEVICE_LOST'}

        with self.assertRaisesRegex(RuntimeError, 'NEKO_CABLE_DEVICE_LOST'):
            await HostVoice(cable_progress=progress).exchange(Socket('transcript_only'), bytes(640), {})
        count = len(polls)
        await asyncio.sleep(.25)
        self.assertEqual(len(polls), count)

    async def test_cable_keeps_complete_native_reply_when_system_cable_is_silent(self):
        async def progress(text):
            return {'done': False}

        result = await asyncio.wait_for(HostVoice(cable_progress=progress).exchange(Socket(), bytes(640), {}), 2)
        self.assertEqual(result['output_transport'], 'host_pcm')
        self.assertEqual(base64.b64decode(result['host_reply']['pcm']), b'\x00\x20' * 480)

    async def test_cable_can_still_finish_when_socket_returns_compressed_audio(self):
        async def progress(text):
            return {'done': bool(text)}

        result = await HostVoice(cable_progress=progress).exchange(Socket('ogg'), bytes(640), {})
        self.assertEqual(result, {'text': '猫娘你好', 'output_transport': 'cable'})

    async def test_real_frontend_protocol_waits_for_audio_done_and_keeps_pcm(self):
        ws = Socket()
        contexts = []
        voice = HostVoice(lambda speaker: contexts.append(speaker) or True)
        result = await voice.exchange(ws, b'\x00\x10' * 320, {'display_name': '小明'})
        self.assertEqual(result['text'], '猫娘你好')
        self.assertEqual(result['host_reply']['text'], '你好呀')
        self.assertEqual(base64.b64decode(result['host_reply']['pcm']), b'\x00\x20' * 480)
        control, start = [json.loads(x) for x in ws.sent if isinstance(x, str)]
        self.assertEqual(control['owner'], 'core')
        self.assertEqual(start['input_type'], 'audio')
        self.assertNotIn('instructions', start)
        frames = [x for x in ws.sent if isinstance(x, bytes)]
        self.assertEqual(struct.unpack('<4sI', frames[0][:8]), (b'NEKO', 16000))
        self.assertEqual(frames[0][8:], b'\x00\x10' * 320)
        self.assertEqual(contexts, [{'display_name': '小明'}, None])

    async def test_each_turn_sets_its_own_nickname_before_audio_and_clears_afterward(self):
        contexts = []
        for name in ('小明', '小雨'):
            ws = Socket()

            def send_context(speaker):
                contexts.append(speaker)
                if speaker is not None:
                    self.assertEqual(ws.frames, 0)
                return True

            await HostVoice(send_context).exchange(ws, bytes(640), {'display_name': name})
        self.assertEqual(contexts, [{'display_name': '小明'}, None, {'display_name': '小雨'}, None])

    async def test_rejected_speaker_context_does_not_send_unattributed_audio(self):
        ws = Socket()
        with self.assertRaisesRegex(RuntimeError, 'SPEAKER_CONTEXT_UNAVAILABLE'):
            await HostVoice(lambda _: False).exchange(ws, bytes(640), {'display_name': '小明'})
        self.assertEqual(ws.frames, 0)

    async def test_cancellation_releases_the_previous_speaker_context(self):
        contexts = []
        ws = Socket('no_transcript')
        task = asyncio.create_task(HostVoice(lambda s: contexts.append(s) or True).exchange(ws, bytes(640), {'display_name': '小明'}))
        while not ws.frames:
            await asyncio.sleep(.001)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(contexts, [{'display_name': '小明'}, None])

    async def test_compressed_audio_is_never_played_as_pcm_noise(self):
        with self.assertRaisesRegex(RuntimeError, 'FORMAT_UNSUPPORTED'):
            await HostVoice().exchange(Socket('ogg'), b'\0\0' * 320, {})

    async def test_cancel_stops_sender_and_receiver_without_global_end_session(self):
        ws = Socket()
        task = asyncio.create_task(HostVoice().exchange(ws, bytes(640), {}))
        while ws.frames == 0:
            await asyncio.sleep(.001)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        count = ws.frames
        await asyncio.sleep(.03)
        self.assertEqual(ws.frames, count)
        self.assertFalse(any(isinstance(x, str) and 'end_session' in x for x in ws.sent))

    async def test_free_mode_requires_new_exe_and_uses_local_session(self):
        cm = types.ModuleType('utils.config_manager')
        cm.get_config_manager = lambda: types.SimpleNamespace(
            get_model_api_config=lambda _: {'api_type': 'free'}, get_core_config=lambda: {})
        host = importlib.import_module(pkg.__name__ + '.host_voice')
        calls = []
        async def turn(self, pcm, speaker):
            calls.append((pcm, speaker))
            return {'text': '猫娘你好', 'host_reply': {'text': '你好', 'pcm': 'AAA=', 'sample_rate': 48000}}
        job = {'kind': 'asr', 'sample_rate': 16000, 'pcm': 'AAA=', 'speaker': {'display_name': '小明'}}
        with patch.dict(sys.modules, {cm.__name__: cm}), patch.object(host.HostVoice, 'turn', turn):
            with self.assertRaisesRegex(RuntimeError, 'UPDATE_EXE'):
                await NekoSpeech().handle(job)
            result = await NekoSpeech().handle({**job, 'host_voice_protocol': 1})
        self.assertIn('host_reply', result)
        self.assertEqual(calls, [(bytes(2), {'display_name': '小明'})])

    async def test_host_ended_notification_fails_without_sending_pcm(self):
        ws = Socket()
        await ws.incoming.put(json.dumps({'type': 'session_ended_by_server'}))
        with self.assertRaisesRegex(RuntimeError, 'SESSION_UNAVAILABLE'):
            await HostVoice().exchange(ws, bytes(640), {})
        self.assertEqual(ws.frames, 0)

    async def test_vad_can_commit_after_more_than_two_seconds_of_silence(self):
        ws = Socket('late_vad')
        result = await asyncio.wait_for(HostVoice().exchange(ws, bytes(640), {}), 5)
        self.assertGreaterEqual(ws.frames, 111)
        self.assertEqual(result['text'], '猫娘你好')
        self.assertTrue(all(not any(x[8:]) for x in ws.sent if isinstance(x, bytes)))

    async def test_no_transcript_is_not_reported_as_reply_timeout(self):
        ws = Socket('no_transcript')
        mod = importlib.import_module(pkg.__name__ + '.host_voice')
        with patch.object(mod, 'REPLY_TIMEOUT', .08):
            with self.assertRaisesRegex(RuntimeError, 'NEKO_HOST_NO_TRANSCRIPT') as caught:
                await HostVoice().exchange(ws, bytes(640), {})
        progress = caught.exception.host_progress
        self.assertEqual(progress['transcript_chars'], 0)
        self.assertEqual(progress['audio_bytes'], 0)
        frames = ws.frames
        await asyncio.sleep(.04)
        self.assertEqual(ws.frames, frames)

    async def test_reply_text_without_audio_is_distinguished(self):
        class TextOnly(Socket):
            async def send(self, raw):
                if isinstance(raw, str):
                    return await super().send(raw)
                self.frames += 1
                if self.frames == 1:
                    for event in [{'type': 'user_transcript', 'text': '你好'},
                                  {'type': 'gemini_response', 'text': '你好呀'},
                                  {'type': 'system', 'data': 'turn end'}]:
                        await self.incoming.put(json.dumps(event))
        mod = importlib.import_module(pkg.__name__ + '.host_voice')
        with patch.object(mod, 'REPLY_TIMEOUT', .08):
            with self.assertRaisesRegex(RuntimeError, 'NEKO_HOST_NO_REPLY_AUDIO'):
                await HostVoice().exchange(TextOnly(), bytes(640), {})
