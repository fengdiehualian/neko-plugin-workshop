from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
from neko_calabiqiu.adapters import data_layer_process as module
from neko_calabiqiu.core.contracts import CbqConfig
from test_plugin_lifecycle_serialization import _load_entrypoint


def make_manager(tmp_path, monkeypatch):
    manager = module.DataLayerProcessManager(
        CbqConfig(data_layer_auto_start=False, assistant_directory=str(tmp_path)),
        plugin_root=tmp_path, external_only=True,
    )
    monkeypatch.setattr(module, "inspect_data_layer_health", lambda *_a, **_kw:
                        module.DataLayerHealth(False, False, "unreachable", {}))
    monkeypatch.setattr(manager, "validate_assistant_directory", lambda _p: tmp_path)
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(module.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(module.time, "sleep", lambda _t: None)
    return manager


def test_explicit_control_owns_one_process_and_stops_its_tree(tmp_path, monkeypatch):
    manager = make_manager(tmp_path, monkeypatch)
    spawned = []
    commands = []

    class Process:
        pid = 123
        exited = False
        def poll(self):
            return 0 if self.exited else None
        def wait(self, timeout):
            assert self.exited
            return 0
        def terminate(self):
            self.exited = True

    def run(cmd, **kwargs):
        commands.append(cmd)
        assert kwargs['creationflags'] == module.subprocess.CREATE_NO_WINDOW
    def popen(cmd, **kwargs):
        assert cmd[-1] == '--standalone'
        process = Process()
        spawned.append(process)
        return process

    monkeypatch.setattr(module.subprocess, 'run', run)
    monkeypatch.setattr(module, '_spawn_assistant_tree', popen)
    assert manager.start_if_needed()['mode'] == 'missing'
    assert not spawned
    assert manager.control('start')['mode'] == 'starting'
    manager.control('start')  # Duplicate click during cold startup must not spawn.
    assert len(spawned) == 1
    monkeypatch.setattr(module, 'inspect_data_layer_health', lambda *_a, **_kw:
                        module.DataLayerHealth(True, True, 'ok', {'service_instance_id': 'one'}))
    assert manager.start_if_needed()['started_by_plugin']
    assert manager.snapshot()['mode'] == 'managed'
    monkeypatch.setattr(module, 'inspect_data_layer_health', lambda *_a, **_kw:
                        module.DataLayerHealth(False, False, 'unreachable', {}))
    assert manager.control('restart')['mode'] == 'starting'
    assert len(spawned) == 2 and spawned[0].exited
    assert manager.control('stop')['mode'] == 'stopped'
    assert manager.snapshot()['pid'] is None
    assert not manager.can_consume_scene()
    assert manager.restart_if_crashed() is None
    assert all(command[0] == 'powershell.exe' for command in commands)
    assert all(process.exited for process in spawned)


@pytest.mark.parametrize('operation', ['start', 'stop', 'restart'])
def test_does_not_kill_external_port_owner(tmp_path, monkeypatch, operation):
    manager = make_manager(tmp_path, monkeypatch)
    monkeypatch.setattr(module, 'inspect_data_layer_health', lambda *_a, **_kw:
                        module.DataLayerHealth(True, True, 'ok', {}))
    with pytest.raises(ValueError, match='外部启动'):
        manager.control(operation)
    assert manager.snapshot()['pid'] is None


def test_failed_stop_keeps_ownership_and_never_restarts(tmp_path, monkeypatch):
    manager = make_manager(tmp_path, monkeypatch)
    manager._started_by_plugin = True
    manager._mode = 'managed'
    manager._process = SimpleNamespace(pid=321, poll=lambda: None,
        terminate=lambda: (_ for _ in ()).throw(OSError('job termination failed')))
    with pytest.raises(OSError):
        manager.control('restart')
    assert manager.snapshot()['pid'] == 321
    assert manager.snapshot()['started_by_plugin']


def test_bundle_validation_and_port_match(tmp_path):
    manager = module.DataLayerProcessManager(CbqConfig(), plugin_root=tmp_path, external_only=True)
    with pytest.raises(ValueError, match='完整路径'):
        manager.validate_assistant_directory('relative')
    for relative in ['.venv-infer/Scripts/python.exe', '.venv-infer/pyvenv.cfg',
                     'runtime/python/python.exe', 'tools/start_data_layer.py',
                     'tools/rebind_venv.ps1', 'config/plugin.toml', 'data_layer/__main__.py']:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
    assert manager.validate_assistant_directory(str(tmp_path)) == tmp_path
    (tmp_path / 'config/plugin.toml').write_text('[neko_calabiqiu]\ndata_layer_url="http://localhost:9999"\n')
    with pytest.raises(ValueError, match='端口不一致'):
        manager.validate_assistant_directory(str(tmp_path))


def test_action_persists_directory_and_shutdown_rejects_late_start(tmp_path, monkeypatch):
    entry = _load_entrypoint(monkeypatch)
    plugin = object.__new__(entry.NekoCalabiqiuPlugin)
    plugin.cfg = CbqConfig()
    plugin._stop = threading.Event()
    plugin._lifecycle_lock = threading.RLock()
    saved = []
    calls = []
    async def save(key, value):
        saved.append((key, value))
    plugin.config = SimpleNamespace(set=save)
    plugin.data_layer_manager = SimpleNamespace(
        validate_assistant_directory=lambda _p: tmp_path,
        control=lambda operation: calls.append(operation) or {'mode': 'starting'},
    )
    assert isinstance(asyncio.run(plugin.assistant_control('start', str(tmp_path))), dict)
    assert saved == [('neko_calabiqiu.assistant_directory', str(tmp_path))]
    assert plugin.cfg.assistant_directory == str(tmp_path)
    plugin._stop.set()
    assert '正在关闭' in str(asyncio.run(plugin.assistant_control('start')))
    assert calls == ['start']


def test_ui_snapshot_does_not_wait_for_process_operation(tmp_path, monkeypatch):
    manager = make_manager(tmp_path, monkeypatch)
    before = manager.snapshot()
    locked = threading.Event()
    release = threading.Event()
    def hold_lock():
        with manager._lock:
            locked.set()
            assert release.wait(3)
    worker = threading.Thread(target=hold_lock)
    worker.start()
    assert locked.wait(3)
    try:
        # Would deadlock until the event timeout if snapshot waited for the lock.
        assert manager.snapshot() == before
        assert worker.is_alive()
    finally:
        release.set()
        worker.join(3)


def test_start_returns_pid_without_waiting_for_model_health(tmp_path, monkeypatch):
    manager = make_manager(tmp_path, monkeypatch)
    spawned = False
    def health(*_args, **_kwargs):
        assert not spawned, "start action must not wait for health after spawning"
        return module.DataLayerHealth(False, False, 'unreachable', {})
    def popen(*_args, **_kwargs):
        nonlocal spawned
        spawned = True
        return SimpleNamespace(pid=456)
    monkeypatch.setattr(module, 'inspect_data_layer_health', health)
    monkeypatch.setattr(module.subprocess, 'run', lambda *_a, **_kw: None)
    monkeypatch.setattr(module, '_spawn_assistant_tree', popen)
    result = manager.control('start')
    assert result['mode'] == 'starting'
    assert result['pid'] == 456
    assert result['started_by_plugin']
