import asyncio
import base64
import sys
import threading
import uuid
from types import SimpleNamespace

import pytest
from plugin.plugins.neko_ads.desktop import DesktopController
from plugin.plugins.neko_ads.session import AdSession


class Key:
    def __init__(self):
        self.closed = False

    def register(self, shortcut, callback):
        if shortcut == 'Ctrl+Alt+F8':
            raise ValueError('occupied')
        self.callback = callback
        return shortcut

    def close(self):
        self.closed = True


def setup(**kwargs):
    kwargs.setdefault('prepare_voices', False)
    session = AdSession()
    session.ready()
    return DesktopController(session, hotkey_factory=Key, **kwargs)


@pytest.mark.asyncio
async def test_enable_stop_round_disable_and_reenable_have_separate_states():
    desktop = setup()
    try:
        assert desktop.snapshot()['phase'] == 'waiting'
        owner, rid = str(uuid.uuid4()), str(uuid.uuid4())
        await desktop.poll(owner, '当前猫娘')
        assert desktop.snapshot()['phase'] == 'standby'
        assert desktop.hotkey is None and not desktop.jobs
        with pytest.raises(ValueError, match='occupied'):
            await desktop.start(rid, owner, True, 'Ctrl+Alt+F8')
        assert desktop.snapshot()['phase'] == 'standby' and desktop.hotkey is None
        rid = str(uuid.uuid4())
        await desktop.start(rid, owner, True, 'Ctrl+Shift+1')
        assert desktop.snapshot()['phase'] == 'playing'
        key = desktop.hotkey
        await desktop.stop(rid)
        assert desktop.snapshot()['phase'] == 'standby' and key.closed
        assert desktop.snapshot()['ready'] is True and not desktop.jobs
        await desktop.close()
        desktop.session.shutdown()
        assert desktop.snapshot()['phase'] == 'disabled'
        desktop.session.ready()
        await desktop.poll(owner, '当前猫娘')
        assert desktop.snapshot()['phase'] == 'standby'
        assert desktop.hotkey is None and not desktop.jobs
        assert not (await desktop.poll(owner, '当前猫娘'))['active']
    finally:
        await desktop.close()


@pytest.mark.asyncio
async def test_main_connection_confirmation_and_global_exit():
    desktop = setup()
    owner, rid = str(uuid.uuid4()), str(uuid.uuid4())
    with pytest.raises(ValueError, match='主界面'):
        await desktop.start(rid, owner, True, 'Ctrl+Shift+1')
    await desktop.poll(owner, '当前猫娘')
    with pytest.raises(ValueError, match='确认'):
        await desktop.start(rid, owner, False, 'Ctrl+Shift+1')
    assert (await desktop.start(rid, owner, True, 'Ctrl+Shift+1'))['active']
    key = desktop.hotkey
    key.callback()
    await asyncio.sleep(.03)
    assert not desktop.snapshot()['active'] and key.closed
    with pytest.raises(ValueError):
        await desktop.start(rid, owner, True, 'Ctrl+Shift+1')
    await desktop.close()


@pytest.mark.asyncio
async def test_failed_key_change_preserves_original_and_role_switch_releases_key():
    desktop = setup()
    owner, rid = str(uuid.uuid4()), str(uuid.uuid4())
    await desktop.poll(owner, '当前猫娘')
    await desktop.start(rid, owner, True, 'Ctrl+Shift+1')
    old = desktop.hotkey
    with pytest.raises(ValueError):
        await desktop.change_shortcut(rid, 'Ctrl+Alt+F8')
    assert desktop.hotkey is old and not old.closed
    await desktop.change_shortcut(rid, 'Ctrl+Shift+2')
    assert old.closed
    current = desktop.hotkey
    await desktop.poll(owner, '另一只猫娘')
    assert not desktop.snapshot()['active'] and current.closed
    await desktop.close()


@pytest.mark.asyncio
async def test_stale_stop_cannot_end_new_round_and_lost_renderer_expires():
    now = [0]
    desktop = setup(clock=lambda: now[0])
    desktop.session._clock = lambda: now[0]
    owner, old, new = (str(uuid.uuid4()) for _ in range(3))
    await desktop.poll(owner, '当前猫娘')
    await desktop.start(old, owner, True, 'Ctrl+Shift+1')
    await desktop.stop(old)
    await desktop.start(new, owner, True, 'Ctrl+Shift+1')
    await desktop.stop(old)
    assert desktop.snapshot()['active'] and desktop.round_id == new
    key = desktop.hotkey
    desktop.run()
    now[0] = desktop.session.lease_seconds + 1
    await asyncio.sleep(.3)
    assert not desktop.snapshot()['active'] and key.closed
    await desktop.close()


@pytest.mark.asyncio
async def test_slow_connection_keeps_round_and_custom_native_exit_still_stops_immediately():
    now = [0.0]
    desktop = setup(clock=lambda: now[0])
    desktop.session._clock = lambda: now[0]
    owner, rid = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        await desktop.poll(owner, '当前猫娘', bridge_version='0.2.0-step2f')
        await desktop.start(rid, owner, True, 'Ctrl+Alt+K')
        now[0] = 17
        status = desktop.snapshot()
        assert status['active'] and status['desktop_ready']
        assert status['desktop']['bridge_version'] == '0.2.0-step2f'
        policy = status['connection_policy']
        assert policy['lease_seconds'] * 1000 > policy['reconnect_grace_ms'] + policy['poll_timeout_ms']
        key = desktop.hotkey
        key.callback()
        await asyncio.sleep(.03)
        assert not desktop.snapshot()['active'] and key.closed
        await desktop.poll(owner, '当前猫娘')
        assert not desktop.snapshot()['active'], 'a delayed heartbeat cannot revive a native-key exit'
        with pytest.raises(ValueError):
            await desktop.start(rid, owner, True, 'Ctrl+Alt+K')
    finally:
        await desktop.close()


@pytest.mark.asyncio
async def test_exit_cancels_only_this_rounds_pending_voice():
    generating, cancelled = asyncio.Event(), asyncio.Event()
    async def speech(character, text, valid, *, emit=None):
        assert character == '当前猫娘'
        generating.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    desktop = setup(speech_factory=speech)
    owner, rid = str(uuid.uuid4()), str(uuid.uuid4())
    await desktop.poll(owner, '当前猫娘')
    await desktop.start(rid, owner, True, 'Ctrl+Shift+1')
    job = desktop.speech(rid, 'start')
    await generating.wait()
    await desktop.stop(rid)
    await asyncio.sleep(0)
    assert cancelled.is_set() and desktop.speech_result(job['job_id']) == {'ready': True}
    await desktop.close()


@pytest.mark.asyncio
async def test_streamed_speech_is_available_early_and_exit_discards_the_rest():
    started, cancelled = asyncio.Event(), asyncio.Event()
    async def speech(character, text, valid, *, emit):
        emit(b'\x01\x02')
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    desktop = setup(speech_factory=speech)
    owner, rid = str(uuid.uuid4()), str(uuid.uuid4())
    await desktop.poll(owner, '当前猫娘')
    await desktop.start(rid, owner, True, 'Ctrl+Shift+1')
    job = desktop.speech(rid, 'fake', 2)
    await started.wait()
    result = desktop.speech_result(job['job_id'])
    assert result['ready'] is False and base64.b64decode(result['pcm']) == b'\x01\x02'
    assert desktop.speech_result(job['job_id']) == {'ready': False}, 'PCM packets must be consumed once'
    await desktop.stop(rid)
    await asyncio.sleep(0)
    assert cancelled.is_set() and desktop.speech_result(job['job_id']) == {'ready': True}
    await desktop.close()


@pytest.mark.asyncio
async def test_reordered_voice_cancellation_does_not_cancel_a_newer_reaction():
    async def speech(character, text, valid, *, emit):
        await asyncio.Event().wait()
    desktop = setup(speech_factory=speech)
    owner, rid = str(uuid.uuid4()), str(uuid.uuid4())
    await desktop.poll(owner, '当前猫娘')
    await desktop.start(rid, owner, True, 'Ctrl+Shift+1')
    await desktop.command('cancel_speech', {'round_id': rid, 'sequence': 2})
    assert desktop.speech(rid, 'start', 2) == {}, 'cancelled requests cannot arrive late and restart'
    new = desktop.speech(rid, 'fake', 4)
    await desktop.command('cancel_speech', {'round_id': rid, 'sequence': 2})
    assert new['job_id'] in desktop.jobs
    await desktop.command('cancel_speech', {'round_id': rid, 'sequence': 4})
    assert desktop.speech_result(new['job_id']) == {'ready': True}
    await desktop.close()


@pytest.mark.asyncio
async def test_replaced_line_can_finish_warming_but_exit_cancels_every_job():
    started, stopped = asyncio.Event(), asyncio.Event()
    async def speech(character, text, valid, *, emit):
        emit(b'\x01\x02')
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    desktop = setup(speech_factory=speech)
    owner, rid = str(uuid.uuid4()), str(uuid.uuid4())
    await desktop.poll(owner, '当前猫娘')
    await desktop.start(rid, owner, True, 'Ctrl+Shift+1')
    old = desktop.speech(rid, 'fake', 2)['job_id']
    await started.wait()
    await desktop.command('cancel_speech', {'round_id': rid, 'sequence': 2, 'preserve': True})
    assert old in desktop.warming and not stopped.is_set()
    assert desktop.speech_result(old) == {'ready': True}, 'a warming line is never available for stale playback'
    assert not desktop.warming[old].chunks
    await desktop.stop(rid)
    await asyncio.sleep(0)
    assert stopped.is_set() and not desktop.warming and not desktop.jobs
    await desktop.close()


@pytest.mark.asyncio
async def test_old_renderer_receives_a_complete_clip_instead_of_stream_packets():
    async def speech(character, text, valid, *, emit):
        assert emit is None
        return b'complete-clip', 'audio/wav'
    desktop = setup(speech_factory=speech)
    owner, rid = str(uuid.uuid4()), str(uuid.uuid4())
    await desktop.poll(owner, '当前猫娘')
    await desktop.start(rid, owner, True, 'Ctrl+Shift+1')
    job = desktop.speech(rid, 'start')
    await asyncio.sleep(0)
    result = desktop.speech_result(job['job_id'])
    assert result['ready'] and base64.b64decode(result['audio']) == b'complete-clip'
    await desktop.close()


@pytest.mark.asyncio
async def test_stop_wins_over_a_pending_native_key_registration():
    entered, release = threading.Event(), threading.Event()
    class SlowKey(Key):
        def register(self, shortcut, callback):
            entered.set()
            release.wait(2)
            return super().register(shortcut, callback)
    desktop = setup()
    desktop.hotkey_factory = SlowKey
    owner, rid = str(uuid.uuid4()), str(uuid.uuid4())
    await desktop.poll(owner, '当前猫娘')
    start = asyncio.create_task(desktop.start(rid, owner, True, 'Ctrl+Shift+1'))
    assert await asyncio.to_thread(entered.wait, 1)
    await desktop.stop(rid)
    release.set()
    with pytest.raises(ValueError):
        await start
    assert not desktop.snapshot()['active'] and desktop.hotkey is None
    await desktop.close()


@pytest.mark.asyncio
async def test_old_stop_does_not_cancel_a_new_round_waiting_for_native_registration():
    entered, release = threading.Event(), threading.Event()
    class SlowKey(Key):
        def register(self, shortcut, callback):
            entered.set()
            release.wait(2)
            return super().register(shortcut, callback)
    desktop = setup()
    owner, old, new = (str(uuid.uuid4()) for _ in range(3))
    await desktop.poll(owner, '当前猫娘')
    old_epoch = desktop.stop_epoch
    await desktop.start(old, owner, True, 'Ctrl+Shift+1')
    await desktop.stop(old)
    desktop.hotkey_factory = SlowKey
    starting = asyncio.create_task(desktop.start(new, owner, True, 'Ctrl+Shift+1'))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        assert (await desktop.command('stop', {'stop_epoch':old_epoch}))['stop_ignored']
        await desktop.stop(old)
        release.set()
        assert (await starting)['round_id'] == new
        assert desktop.snapshot()['active']
    finally:
        release.set()
        await asyncio.gather(starting, return_exceptions=True)
        await desktop.close()


@pytest.mark.asyncio
async def test_unscoped_old_stop_token_does_not_cancel_pending_or_active_replacement():
    desktop = setup()
    owner, old, rid = (str(uuid.uuid4()) for _ in range(3))
    await desktop.poll(owner, '当前猫娘')
    old_epoch = desktop.snapshot()['stop_epoch']
    try:
        await desktop.start(old, owner, True, 'Ctrl+Shift+1')
        await desktop.stop(old)
        await desktop.start(rid, owner, True, 'Ctrl+Shift+1')
        assert (await desktop.command('stop', {'stop_epoch':old_epoch}))['stop_ignored']
        assert desktop.snapshot()['active']
        with pytest.raises(ValueError, match='停止请求'):
            await desktop.command('stop', {})
        await desktop.command('stop', {'round_id':rid})
        with pytest.raises(ValueError):
            await desktop.start(rid, owner, True, 'Ctrl+Shift+1')
    finally:
        await desktop.close()


@pytest.mark.asyncio
async def test_idle_polls_cannot_renew_a_round_whose_stop_was_lost():
    now = [0.0]
    desktop = setup(clock=lambda:now[0])
    desktop.session._clock = lambda:now[0]
    owner, rid = str(uuid.uuid4()), str(uuid.uuid4())
    await desktop.poll(owner, '当前猫娘')
    try:
        await desktop.start(rid, owner, True, 'Ctrl+Shift+1')
        for now[0] in range(1, 32):
            await desktop.poll(owner, '当前猫娘', round_id=None)
        assert not desktop.snapshot()['active']
        assert desktop.hotkey is None
        assert rid in desktop.snapshot()['retired_round_ids']
    finally:
        await desktop.close()


@pytest.mark.asyncio
async def test_public_start_rejects_confirmation_from_a_previous_plugin_epoch():
    desktop = setup()
    owner, rid = str(uuid.uuid4()), str(uuid.uuid4())
    await desktop.poll(owner, '当前猫娘')
    old_epoch = desktop.snapshot()['epoch']
    desktop.session.shutdown()
    desktop.session.ready()
    try:
        with pytest.raises(ValueError):
            await desktop.command('start', {'round_id':rid, 'owner':owner, 'epoch':old_epoch,
                                             'stop_epoch':desktop.stop_epoch, 'confirmed':True})
        assert not desktop.snapshot()['active'] and desktop.hotkey is None
    finally:
        await desktop.close()


@pytest.mark.asyncio
async def test_queued_start_rechecks_stop_generation_after_waiting_for_start_lock():
    entered, release = threading.Event(), threading.Event()
    class SlowKey(Key):
        def register(self, shortcut, callback):
            entered.set()
            release.wait(2)
            return super().register(shortcut, callback)
    desktop = setup()
    desktop.hotkey_factory = SlowKey
    owner, first, queued = (str(uuid.uuid4()) for _ in range(3))
    await desktop.poll(owner, '当前猫娘')
    status = desktop.snapshot()
    def args(rid):
        return {'round_id':rid,'owner':owner,'confirmed':True,'epoch':status['epoch'],'stop_epoch':status['stop_epoch']}
    one = asyncio.create_task(desktop.command('start',args(first)))
    two = None
    try:
        assert await asyncio.to_thread(entered.wait,1)
        two = asyncio.create_task(desktop.command('start',args(queued)))
        await asyncio.sleep(0)
        await desktop.stop(first)
        release.set()
        assert all(isinstance(result,ValueError) for result in await asyncio.gather(one,two,return_exceptions=True))
        assert not desktop.snapshot()['active'] and desktop.hotkey is None
    finally:
        release.set()
        await asyncio.gather(one,*([two] if two else []),return_exceptions=True)
        await desktop.close()


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != 'win32', reason='Windows global key')
async def test_real_windows_hotkey_dispatch_ends_round():
    from plugin.plugins.neko_ads.hotkey import GlobalExitHotkey
    desktop = setup()
    desktop.hotkey_factory = GlobalExitHotkey
    owner, rid = str(uuid.uuid4()), str(uuid.uuid4())
    await desktop.poll(owner, '当前猫娘')
    try:
        try:
            await desktop.start(rid, owner, True, 'Ctrl+Alt+Shift+F11')
        except ValueError:
            pytest.skip('Test combination is already in use')
        key = desktop.hotkey
        key_thread = key.thread
        assert key._user32.PostThreadMessageW(key.thread_id, 0x0312, 1, 0)
        for _ in range(100):
            if not desktop.snapshot()['active'] and not key_thread.is_alive():
                break
            await asyncio.sleep(.01)
        assert not desktop.snapshot()['active'] and desktop.hotkey is None
        assert not key_thread.is_alive(), 'native key ownership must be released after exit'
        replacement = GlobalExitHotkey()
        try:
            await asyncio.to_thread(replacement.register, 'Ctrl+Alt+Shift+F11', lambda: None)
        finally:
            await asyncio.to_thread(replacement.close)
    finally:
        await desktop.close()


def test_selected_voice_reads_reserved_character_field_without_chat_queues(monkeypatch):
    import utils.config_manager as config
    from main_logic.core import LLMSessionManager
    from plugin.plugins.neko_ads.speech import _current_voice_manager

    manager = SimpleNamespace(
        load_characters=lambda: {'当前猫娘': '当前猫娘', '猫娘': {'当前猫娘': {'_reserved': {'voice_id': 'chosen-character-voice'}}}},
        get_model_api_config=lambda _: {'api_type': 'free'}, get_core_config=lambda: {},
    )
    monkeypatch.setattr(config, 'get_config_manager', lambda: manager)
    voice = _current_voice_manager('当前猫娘')
    assert isinstance(voice, LLMSessionManager)
    assert voice.voice_id == 'chosen-character-voice'
    assert not hasattr(voice, 'tts_request_queue') and not hasattr(voice, 'websocket')
    assert _current_voice_manager('另一只猫娘') is None
