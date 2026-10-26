"""Isolated use of the selected character's project TTS route."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import queue
import threading
import time
import wave
from collections import OrderedDict


class VoiceCache:
    """Bounded, memory-only clips; route/configuration changes select a new key."""
    def __init__(self, clock=time.monotonic, max_bytes=16_000_000, ttl=600):
        self.clock, self.max_bytes, self.ttl = clock, max_bytes, ttl
        self.items, self.size = OrderedDict(), 0

    def get(self, key):
        item = self.items.get(key)
        if item is None:
            return None
        if self.clock() - item[0] >= self.ttl:
            self.size -= len(self.items.pop(key)[1][0])
            return None
        self.items.move_to_end(key)
        return item[1]

    def put(self, key, value):
        if not value[0] or len(value[0]) > self.max_bytes:
            return
        if key in self.items:
            self.size -= len(self.items.pop(key)[1][0])
        self.items[key] = self.clock(), value
        self.size += len(value[0])
        while self.size > self.max_bytes or len(self.items) > 24:
            self.size -= len(self.items.popitem(last=False)[1][1][0])


_voice_cache = VoiceCache()


class VoiceError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


async def synthesize_current(character, text, valid, *, emit=None):
    """Use the installed host's voice dispatch, with no ordinary chat queues."""
    manager = await asyncio.to_thread(_current_voice_manager, character)
    if manager is None or not valid():
        return b'', 'audio/wav'
    return await synthesize_feedback(manager, text, valid, emit=emit)


async def current_voice_identity(character):
    def resolve():
        manager = _current_voice_manager(character)
        if manager is None or manager._config_manager.get_core_config().get('DISABLE_TTS'):
            return None
        return _voice_route(manager)[-1]
    return await asyncio.to_thread(resolve)


def _current_voice_manager(character):
    from main_logic.core import LLMSessionManager
    from utils.config_manager import get_config_manager, get_reserved
    from utils.tts.native_voice_registry import is_free_preset_voice_id
    from utils.voice_config import read_legacy_voice_id

    config = get_config_manager()
    roles = config.load_characters()
    if roles.get('当前猫娘') != character:
        return None
    role = roles.get('猫娘', {}).get(character, {})
    # Read only the voice-routing fields. No manager is initialized and no
    # session, websocket, conversation queue or shared TTS worker is created.
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager._config_manager = config
    manager.core_api_type = config.get_model_api_config('realtime').get('api_type') or config.get_core_config().get('CORE_API_TYPE', '')
    manager.voice_id = read_legacy_voice_id(get_reserved(role, 'voice_id', default='', legacy_keys=('voice_id',)))
    manager._is_free_preset_voice = is_free_preset_voice_id(manager.voice_id)
    if manager._is_free_preset_voice and manager.core_api_type != 'free':
        manager.voice_id, manager._is_free_preset_voice = '', False
    return manager


def _voice_route(manager):
    from main_logic.tts_client import get_tts_worker
    from utils.language_utils import get_tts_language_code

    voice, custom = manager._effective_tts_route()
    worker, override, provider = get_tts_worker(
        core_api_type=manager.core_api_type, has_custom_voice=custom, voice_id=voice,
        excluded_provider_keys=getattr(manager, "_tts_excluded_provider_keys", frozenset()))
    config = manager._config_manager.get_model_api_config("tts_custom" if custom else "tts_default")
    key = manager.resolve_tts_api_key(provider, override, config)
    # Fingerprints stay in memory and are never logged. Changed voices, endpoint,
    # credentials, language or routing cannot reuse a previous configuration's clip.
    identity = (manager.core_api_type, manager.voice_id, voice, custom, provider,
                worker.__module__, worker.__qualname__, key, config, get_tts_language_code())
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).digest()
    return worker, key, voice, fingerprint


async def synthesize_feedback(manager, text, valid, *, emit=None, cache=None):
    if manager._config_manager.get_core_config().get('DISABLE_TTS'):
        return b'', 'audio/wav'
    worker, key, voice, route = _voice_route(manager)
    fingerprint = hashlib.sha256(route + text.encode()).digest()
    cache = _voice_cache if cache is None else cache
    cached = cache.get(fingerprint)
    if cached is not None and valid():
        return cached
    requests, responses = queue.Queue(), queue.Queue(maxsize=256)
    thread = threading.Thread(target=worker, args=(requests, responses, key, voice), daemon=True)
    thread.start()
    chunks = []
    size = 0
    started = False
    completed = False
    failure = 'timeout'
    stream_pcm = None
    deadline = time.monotonic() + 12
    try:
        while valid() and time.monotonic() < deadline:
            try:
                data = responses.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.02)
                continue
            if isinstance(data, tuple):
                if data[0] == "__ready__":
                    if not data[1]:
                        failure = 'provider_unavailable'
                        break
                    if not started:
                        started = True
                        requests.put(("neko_ads", text))
                        requests.put((None, None))
                elif data[0] == "__audio_done__":
                    completed = True
                    break
                elif data[0] == "__error__":
                    failure = 'provider_error'
                    break
                elif len(data) == 3 and data[0] == "__audio__":
                    data = data[2]
            if isinstance(data, (bytes, bytearray)):
                chunk = bytes(data)
                if stream_pcm is None and chunk:
                    stream_pcm = not chunk.startswith((b'RIFF', b'OggS'))
                chunks.append(chunk)
                size += len(data)
                if emit and stream_pcm and valid():
                    emit(chunk)
                if size > 4_000_000:
                    failure = 'audio_limit'
                    break
    finally:
        requests.put(("__interrupt__", None))
        requests.put(("__shutdown__", None))
        # Drain a bounded output queue even if a provider completes after cancellation.
        def drain():
            while thread.is_alive():
                try:
                    responses.get(timeout=0.2)
                except queue.Empty:
                    pass
        threading.Thread(target=drain, name="neko-ads-tts-drain", daemon=True).start()
    if not valid():
        return b"", "audio/wav"
    if not completed:
        raise VoiceError(failure)
    audio = b"".join(chunks)
    if not audio:
        raise VoiceError('empty_audio')
    if audio.startswith(b"OggS"):
        result = audio, "audio/ogg"
    elif audio.startswith(b"RIFF"):
        result = audio, "audio/wav"
    else:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(48000)
            wav.writeframes(audio[:len(audio) // 2 * 2])
        result = buffer.getvalue(), "audio/wav"
    if completed and valid():
        cache.put(fingerprint, result)
    return result
