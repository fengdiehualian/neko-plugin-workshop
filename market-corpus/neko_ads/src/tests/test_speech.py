import asyncio
import threading
from types import SimpleNamespace

import pytest
from plugin.plugins.neko_ads.speech import VoiceCache, synthesize_feedback


@pytest.mark.asyncio
async def test_first_pcm_arrives_before_completion_and_completed_line_reuses_current_voice(monkeypatch):
    import main_logic.tts_client as tts
    import utils.language_utils as language

    release = threading.Event()
    calls, emitted = [], []
    config = {'url': 'test-voice-endpoint'}

    def worker(requests, responses, key, voice):
        calls.append((key, voice))
        responses.put(('__ready__', True))
        assert requests.get(timeout=2)[1] == '嘲讽台词'
        responses.put(b'\x00\x01' * 200)
        release.wait(2)
        responses.put(b'\x02\x03' * 200)
        responses.put(('__audio_done__', 'neko_ads'))

    manager = SimpleNamespace(
        core_api_type='test', voice_id='current-voice',
        _config_manager=SimpleNamespace(get_core_config=lambda: {}, get_model_api_config=lambda _: config.copy()),
        resolve_tts_api_key=lambda *_: 'local-test-key',
    )
    manager._effective_tts_route = lambda: (manager.voice_id, False)
    monkeypatch.setattr(tts, 'get_tts_worker', lambda **_: (worker, None, 'test'))
    monkeypatch.setattr(language, 'get_tts_language_code', lambda: 'zh-CN')
    cache = VoiceCache()
    task = asyncio.create_task(synthesize_feedback(manager, '嘲讽台词', lambda: True, emit=emitted.append, cache=cache))
    try:
        for _ in range(100):
            if emitted:
                break
            await asyncio.sleep(.01)
        assert emitted == [b'\x00\x01' * 200]
        assert not task.done(), 'playable PCM must precede the utterance-complete event'
    finally:
        release.set()
    result = await asyncio.wait_for(task, 2)
    assert result[0].startswith(b'RIFF') and len(emitted) == 2
    assert await synthesize_feedback(manager, '嘲讽台词', lambda: True, cache=cache) == result
    assert len(calls) == 1, 'repeated fixed lines must not reconnect to the provider'
    manager.voice_id = 'new-current-voice'
    await synthesize_feedback(manager, '嘲讽台词', lambda: True, cache=cache)
    assert calls[-1][1] == 'new-current-voice' and len(calls) == 2
    config['url'] = 'changed-voice-endpoint'
    await synthesize_feedback(manager, '嘲讽台词', lambda: True, cache=cache)
    assert len(calls) == 3, 'changed routing must invalidate old audio'


def test_voice_cache_is_bounded_and_expires():
    now = [0]
    cache = VoiceCache(clock=lambda: now[0], max_bytes=8, ttl=10)
    cache.put('old', (b'1234', 'audio/wav'))
    cache.put('recent', (b'5678', 'audio/wav'))
    assert cache.get('old')
    cache.put('new', (b'abcd', 'audio/wav'))
    assert cache.get('recent') is None and cache.size == 8
    now[0] = 11
    assert cache.get('old') is None and cache.get('new') is None and cache.size == 0
