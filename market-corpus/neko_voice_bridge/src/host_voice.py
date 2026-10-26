"""Frontend-protocol client for the *local* N.E.K.O main server.

No provider URL/key/prompt is constructed here. The host owns authentication,
persona, voice, memory and generation. Pinned to upstream cd17a211.
"""
import asyncio
import base64
import json
import struct
import uuid
from urllib.parse import quote

REPLY_TIMEOUT = 40


class HostVoice:
    def __init__(self, context_sender=None, cable_progress=None):
        self.context_sender = context_sender
        self.cable_progress = cable_progress
        self.stage = "host_voice.idle"

    async def turn(self, pcm, speaker):
        from config import MAIN_SERVER_PORT
        from utils.config_manager import get_config_manager
        from websockets.asyncio.client import connect

        self.stage = "host_voice.config"
        snapshot = await asyncio.to_thread(get_config_manager().get_character_data)
        profile = snapshot[1]
        if not isinstance(profile, str) or not profile:
            raise RuntimeError("NEKO_HOST_CHARACTER_MISSING")
        port = int(MAIN_SERVER_PORT)
        if not 1 <= port <= 65535:
            raise RuntimeError("NEKO_HOST_PORT_INVALID")
        self.stage = "host_voice.connect"
        # Never connect to the cloud/free provider directly, never follow a
        # user-supplied endpoint. This is the installed host's frontend socket.
        async with connect(f"ws://127.0.0.1:{port}/ws/{quote(profile, safe='')}",
                           open_timeout=5, close_timeout=2, max_size=4_000_000) as ws:
            return await self.exchange(ws, pcm, speaker)

    async def exchange(self, ws, pcm, speaker):
        request_id = "nvb-" + uuid.uuid4().hex
        ready = asyncio.Event()
        result = asyncio.get_running_loop().create_future()
        transcript, answer, audio = [], [], bytearray()
        speech_id = None
        pending_header = None
        audio_done, turn_done = False, False
        sent_audio = False
        sent_frames = 0
        audio_frames = 0
        compressed_audio = False
        context_submitted = False

        def complete():
            if transcript and audio and not compressed_audio and turn_done and audio_done and not result.done():
                result.set_result({"text": "".join(transcript)[:4000],
                    **({"output_transport": "host_pcm"} if self.cable_progress else {}), "host_reply": {
                    "text": "".join(answer)[:1000],
                    "pcm": base64.b64encode(audio).decode(), "sample_rate": 48000}})

        async def receive():
            nonlocal speech_id, pending_header, audio_done, turn_done, audio_frames, compressed_audio
            async for raw in ws:
                if isinstance(raw, bytes):
                    if not pending_header or not sent_audio:
                        continue
                    sid = pending_header.get("speech_id")
                    pending_header = None
                    if not isinstance(sid, str) or not sid:
                        raise RuntimeError("NEKO_HOST_AUDIO_PROTOCOL_MISMATCH")
                    if speech_id is not None and sid != speech_id:
                        raise RuntimeError("NEKO_HOST_TURN_INTERRUPTED")
                    speech_id = sid
                    # Some optional host voices emit Ogg instead of PCM. Fail
                    # explicitly rather than sending compressed bytes as noise.
                    if raw.startswith(b"OggS"):
                        if self.cable_progress:
                            compressed_audio = True
                            audio.clear()
                            continue
                        raise RuntimeError("NEKO_HOST_AUDIO_FORMAT_UNSUPPORTED")
                    if compressed_audio:
                        continue
                    if len(raw) % 2 or len(audio) + len(raw) > 2_880_000:
                        raise RuntimeError("NEKO_HOST_AUDIO_TOO_LARGE")
                    audio.extend(raw)
                    audio_frames += 1
                    continue
                message = json.loads(raw)
                kind = message.get("type")
                if kind == "session_started" and message.get("request_id") == request_id:
                    if message.get("input_mode") != "audio" or message.get("microphone_route") == "blocked":
                        raise RuntimeError("NEKO_HOST_VOICE_ROUTE_BLOCKED")
                    ready.set()
                elif kind in ("session_failed", "catgirl_switched", "session_stopped", "session_ended_by_server"):
                    raise RuntimeError("NEKO_HOST_SESSION_UNAVAILABLE")
                elif kind == "status":
                    status = message.get("message", {})
                    status = json.loads(status) if isinstance(status, str) else status
                    code = status.get("code", "")
                    if code in ("VOICE_INPUT_CONTROL_REJECTED", "VOICE_INPUT_LEASE_REQUIRED"):
                        raise RuntimeError("NEKO_HOST_VOICE_LEASE_REJECTED")
                elif sent_audio:
                    if kind == "user_transcript" and message.get("text", "").strip():
                        transcript.append(message["text"].strip())
                        if sum(map(len, transcript)) > 4000:
                            raise RuntimeError("NEKO_HOST_TRANSCRIPT_TOO_LARGE")
                    elif kind == "gemini_response":
                        answer.append(str(message.get("text", "")))
                        if sum(map(len, answer)) > 12000:
                            raise RuntimeError("NEKO_HOST_RESPONSE_TOO_LARGE")
                    elif kind == "audio_chunk":
                        pending_header = message
                    elif kind == "audio_done" and speech_id and message.get("speech_id") == speech_id:
                        audio_done = True
                    elif kind == "system" and message.get("data") == "turn end":
                        turn_done = True
                    elif kind == "response_discarded" and not message.get("will_retry"):
                        raise RuntimeError("NEKO_HOST_RESPONSE_DISCARDED")
                    elif kind == "user_activity" and speech_id:
                        raise RuntimeError("NEKO_HOST_TURN_INTERRUPTED")
                    complete()
            raise RuntimeError("NEKO_HOST_VOICE_CONNECTION_CLOSED")

        receiver = asyncio.create_task(receive())

        async def wait_or_failure(awaitable, timeout):
            waiter = asyncio.ensure_future(awaitable)
            try:
                done, _ = await asyncio.wait((receiver, waiter), timeout=timeout,
                                             return_when=asyncio.FIRST_COMPLETED)
                if receiver in done:
                    await receiver
                if waiter not in done:
                    if self.stage != "host_voice.wait_reply":
                        raise RuntimeError("NEKO_HOST_VOICE_TIMEOUT")
                    code = ("NEKO_HOST_NO_TRANSCRIPT" if not transcript else
                            "NEKO_HOST_NO_REPLY" if not answer and not audio else
                            "NEKO_HOST_NO_REPLY_AUDIO" if not audio else
                            "NEKO_HOST_AUDIO_INCOMPLETE" if not audio_done else
                            "NEKO_HOST_TURN_NOT_FINISHED")
                    error = RuntimeError(code)
                    error.host_progress = {"sent_frames": sent_frames, "audio_frames": audio_frames,
                        "transcript_chars": sum(map(len, transcript)), "reply_chars": sum(map(len, answer)),
                        "audio_bytes": len(audio), "audio_done": audio_done, "turn_done": turn_done}
                    raise error
                return await waiter
            finally:
                if not waiter.done():
                    waiter.cancel()
                    await asyncio.gather(waiter, return_exceptions=True)

        try:
            self.stage = "host_voice.start"
            await ws.send(json.dumps({"action": "voice_input_control", "event": "lease_sync",
                                      "lease_generation": 1, "owner": "core", "hard_muted": False,
                                      "focus_suppressed": False, "engaged": True}))
            await ws.send(json.dumps({"action": "start_session", "input_type": "audio",
                                      "new_session": False, "request_id": request_id}))
            await wait_or_failure(ready.wait(), 20)
            if self.context_sender:
                # SDK read-only context is best effort, not a consumption ACK.
                # Keep member identity attached to the bridge's actual track too.
                accepted = await asyncio.to_thread(self.context_sender, speaker)
                if not accepted:
                    raise RuntimeError("NEKO_HOST_SPEAKER_CONTEXT_UNAVAILABLE")
                context_submitted = True
            self.stage = "host_voice.send_audio"
            sent_audio = True
            async def send_frame(chunk):
                nonlocal sent_frames
                await ws.send(struct.pack("<4sI", b"NEKO", 16000) + chunk)
                sent_frames += 1
                await asyncio.sleep(.02)

            async def send_audio():
                for offset in range(0, len(pcm), 640):
                    await send_frame(pcm[offset:offset + 640])

            await wait_or_failure(send_audio(), 20)
            self.stage = "host_voice.wait_reply"

            async def wait_with_silence():
                # The real microphone keeps producing silence after speech.
                # A fixed two-second tail can stop before the provider's VAD
                # commits the utterance. Keep framing until completion/deadline.
                async def silence():
                    while True:
                        await send_frame(bytes(640))
                tail = asyncio.create_task(silence())
                async def watch_cable():
                    while True:
                        status = await self.cable_progress("".join(transcript))
                        if status.get("error"):
                            code = str(status["error"])
                            raise RuntimeError(code if code.startswith("NEKO_CABLE_") else "NEKO_CABLE_CAPTURE_FAILED")
                        if status.get("done") and transcript:
                            return {"text": "".join(transcript)[:4000], "output_transport": "cable"}
                        await asyncio.sleep(.2)

                cable_task = asyncio.create_task(watch_cable()) if self.cable_progress else None
                try:
                    # Native realtime replies can be addressed to this socket
                    # instead of the desktop. Keep them; select ONE complete
                    # source and cancel the other to avoid duplicate playback.
                    done, _ = await asyncio.wait([tail, result] + ([cable_task] if cable_task else []),
                                                return_when=asyncio.FIRST_COMPLETED)
                    if tail in done:
                        await tail
                    if result in done:
                        return await result
                    return await cable_task
                finally:
                    tail.cancel()
                    if cable_task:
                        cable_task.cancel()
                    await asyncio.gather(*([tail, cable_task] if cable_task else [tail]), return_exceptions=True)

            return await wait_or_failure(wait_with_silence(), REPLY_TIMEOUT)
        finally:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)
            if context_submitted:
                # Replace any still-queued nickname with an end-of-turn cue.
                # This is best-effort SDK context, NOT a host consumption ACK.
                try:
                    await asyncio.wait_for(asyncio.to_thread(self.context_sender, None), 2)
                except Exception:
                    pass
            # Socket closure revokes only this socket's lease in the host. Do
            # not send a global end_session that could stop a replacement UI.
