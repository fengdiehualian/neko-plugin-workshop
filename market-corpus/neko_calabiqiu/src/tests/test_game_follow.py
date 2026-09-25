from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from neko_calabiqiu.adapters import data_layer_process as module
from neko_calabiqiu.core.game_process import ProcessWatcher
from test_assistant_process_control import make_manager
from test_plugin_lifecycle_serialization import _load_entrypoint


@pytest.fixture
def follow(tmp_path, monkeypatch):
    manager = make_manager(tmp_path, monkeypatch)
    manager.config.data_layer_auto_start = True
    state = {'names': set(), 'now': 0.0, 'error': False, 'healthy': True}
    spawned = []
    killed = []

    def names():
        if state['error']:
            raise OSError('enumeration unavailable')
        return state['names']

    manager._game_watcher = ProcessWatcher(debounce=2, list_processes=names)
    monkeypatch.setattr(module.time, 'monotonic', lambda: state['now'])

    class Process:
        def __init__(self):
            self.pid = 1000 + len(spawned)
            self.exited = False

        def poll(self):
            return 1 if self.exited else None

        def wait(self, timeout):
            assert self.exited

        def terminate(self):
            killed.append(self.pid)
            self.exited = True

    def popen(*_args, **_kwargs):
        proc = Process()
        spawned.append(proc)
        return proc

    def health(*_args, **_kwargs):
        live = next((p for p in spawned if not p.exited), None)
        reachable = bool(live and state['healthy'])
        return module.DataLayerHealth(reachable, reachable, 'ok' if reachable else 'unreachable',
                                      {'service_instance_id': str(live.pid)} if reachable else {})

    monkeypatch.setattr(module, '_spawn_assistant_tree', popen)
    monkeypatch.setattr(module.subprocess, 'run', lambda *_a, **_kw: None)
    monkeypatch.setattr(module, 'inspect_data_layer_health', health)

    def tick():
        state['now'] += 1.0
        return manager.sync_game()

    def start_game():
        state['names'] = {'Calabiyau-Win64-Shipping.exe'}
        tick()
        tick()

    return SimpleNamespace(manager=manager, state=state, spawned=spawned,
                           killed=killed, tick=tick, start_game=start_game)


def test_no_game_or_launcher_keeps_backend_absent_and_debounces_real_game(follow):
    f = follow
    assert f.manager.start_if_needed()['mode'] == 'waiting_game'
    f.state['names'] = {'CalabiYau.exe', 'Strinova.exe'}
    for _ in range(3):
        assert not f.tick()
    assert not f.spawned
    f.state['names'].add('Calabiyau-Win64-Shipping.exe')
    assert not f.tick()
    # Repeated frame ticks must not count as separate process observations.
    for _ in range(10):
        assert not f.manager.sync_game()
    assert not f.spawned
    assert f.tick()
    assert len(f.spawned) == 1
    for _ in range(3):
        assert f.tick()
    assert len(f.spawned) == 1
    f.state['names'] = {'CalabiYau.exe'}
    assert f.tick()  # One missing sample is insufficient.
    assert not f.killed
    assert not f.tick()
    assert f.killed == [f.spawned[0].pid]
    assert f.manager.snapshot()['mode'] == 'waiting_game'


def test_manual_stop_stays_stopped_until_explicit_resume_or_new_game(follow):
    f = follow
    f.start_game()
    assert f.manager.control('stop')['game_paused']
    for _ in range(4):
        assert not f.tick()
        assert f.manager.restart_if_crashed() is None
    assert len(f.spawned) == 1
    assert f.manager.control('start')['started_by_plugin']
    assert len(f.spawned) == 2
    f.manager.control('stop')
    f.state['names'] = set()
    f.tick()
    f.tick()
    f.start_game()
    assert len(f.spawned) == 3
    assert not f.manager.snapshot()['game_paused']


def test_manual_start_without_game_waits_and_config_reload_cannot_bypass_wait(follow):
    f = follow
    assert f.manager.control('start')['mode'] == 'waiting_game'
    assert f.manager.restart_for_config_change()['mode'] == 'waiting_game'
    assert not f.spawned
    f.start_game()
    f.manager.control('stop')
    f.manager.configure(f.manager.config)
    assert f.manager.restart_for_config_change()['mode'] == 'stopped'
    assert len(f.spawned) == 1


def test_game_exit_cancels_model_startup_and_can_start_next_session(follow):
    f = follow
    f.state['healthy'] = False
    f.start_game()
    assert f.manager.snapshot()['mode'] == 'starting'
    f.state['names'] = set()
    f.tick()
    f.tick()
    assert f.spawned[0].exited
    assert f.manager.snapshot()['pid'] is None
    f.start_game()
    assert len(f.spawned) == 2


def test_enumeration_failure_keeps_owned_process_and_resets_exit_debounce(follow):
    f = follow
    f.start_game()
    f.state['names'] = set()
    f.tick()
    f.state['error'] = True
    assert not f.tick()
    assert not f.killed
    assert f.manager.snapshot()['game_watch_error']
    f.state['error'] = False
    assert f.tick()
    assert not f.killed
    assert not f.manager.snapshot()['game_watch_error']
    assert not f.tick()
    assert len(f.killed) == 1


def test_failed_process_enumeration_never_starts_backend(follow):
    f = follow
    f.state['error'] = True
    assert not f.tick()
    f.manager.control('start')
    assert not f.spawned


def test_stop_failure_on_game_exit_retains_ownership_and_retries(follow, monkeypatch):
    f = follow
    f.start_game()
    process = f.spawned[0]
    terminate = process.terminate
    monkeypatch.setattr(process, 'terminate', lambda: (_ for _ in ()).throw(OSError('busy')))
    f.state['names'] = set()
    f.tick()
    assert not f.tick()
    assert f.manager.snapshot()['started_by_plugin']
    assert '停止失败' in f.manager.snapshot()['last_error']
    # High-frequency ticks do not repeat a failed stop between samples.
    for _ in range(3):
        assert not f.manager.sync_game()
    monkeypatch.setattr(process, 'terminate', terminate)
    assert not f.tick()
    assert not f.manager.snapshot()['started_by_plugin']


def test_game_monitor_never_stops_an_external_assistant(follow, monkeypatch):
    f = follow
    monkeypatch.setattr(module, 'inspect_data_layer_health',
                        lambda *_a, **_k: module.DataLayerHealth(True, True, 'ok', {}))
    f.start_game()
    assert f.manager.snapshot()['mode'] == 'external'
    f.state['names'] = set()
    f.tick()
    f.tick()
    assert not f.spawned and not f.killed


def test_crash_recovers_during_game_but_cannot_restart_after_game_exit(follow):
    f = follow
    f.start_game()
    f.spawned[0].exited = True
    assert not f.manager.can_consume_scene()
    assert f.manager.restart_if_crashed()['started_by_plugin']
    assert len(f.spawned) == 2
    f.state['names'] = set()
    f.tick()
    f.tick()
    assert f.manager.restart_if_crashed() is None
    assert len(f.spawned) == 2


def test_idle_plugin_tick_skips_http_poll_and_restores_game_context(monkeypatch):
    entry = _load_entrypoint(monkeypatch)
    plugin = object.__new__(entry.NekoCalabiqiuPlugin)
    calls = []
    plugin.cfg = SimpleNamespace(data_layer_auto_start=True)
    plugin._retry_context_restore = lambda: None
    plugin._lifecycle_lock = threading.RLock()
    plugin._reset_runtime_after_data_layer_loss = lambda: calls.append('reset')
    plugin.data_layer_manager = SimpleNamespace(sync_game=lambda: False)
    plugin.timeline = SimpleNamespace(mark_tick=lambda: calls.append('tick'))
    plugin.client = SimpleNamespace(poll=lambda: pytest.fail('idle must not query stopped backend'))
    plugin._tick()
    assert calls == ['tick', 'reset']
