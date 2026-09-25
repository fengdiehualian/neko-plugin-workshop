from __future__ import annotations

import asyncio
import importlib.util
import sys
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace

from neko_calabiqiu.core.context_restore_retry import ContextRestoreRetry
from neko_calabiqiu.core.contracts import CbqConfig, SceneState


class _Logger:
    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass


def _load_entrypoint(monkeypatch):
    plugin_package = types.ModuleType("plugin")
    sdk_package = types.ModuleType("plugin.sdk")
    sdk_module = types.ModuleType("plugin.sdk.plugin")

    def decorator(*_args, **_kwargs):
        return lambda value: value

    class _Ui:
        action = staticmethod(decorator)
        context = staticmethod(decorator)

    class _NekoPluginBase:
        pass

    class _SdkError(Exception):
        pass

    sdk_module.Err = lambda value: value
    sdk_module.NekoPluginBase = _NekoPluginBase
    sdk_module.Ok = lambda value: value
    sdk_module.SdkError = _SdkError
    sdk_module.lifecycle = decorator
    sdk_module.neko_plugin = lambda value: value
    sdk_module.plugin_entry = decorator
    sdk_module.ui = _Ui()
    monkeypatch.setitem(sys.modules, "plugin", plugin_package)
    monkeypatch.setitem(sys.modules, "plugin.sdk", sdk_package)
    monkeypatch.setitem(sys.modules, "plugin.sdk.plugin", sdk_module)

    module_name = "neko_calabiqiu._entrypoint_lifecycle_test"
    sys.modules.pop(module_name, None)
    path = Path(__file__).resolve().parents[1] / "__init__.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_config_apply_waits_for_an_in_flight_tick(monkeypatch):
    module = _load_entrypoint(monkeypatch)
    plugin = object.__new__(module.NekoCalabiqiuPlugin)
    evaluate_entered = threading.Event()
    release_evaluate = threading.Event()
    configured = threading.Event()

    class _Manager:
        def can_consume_scene(self):
            return True

        def validate_scene_instance(self, _instance_id):
            return True

        def configure(self, _cfg):
            configured.set()

    class _Runtime:
        def __init__(self):
            self.state = SceneState(connected=True, frame_seq=1)
            self.safety = SimpleNamespace(record_failure=lambda: None)

        def evaluate(self, _previous, _new):
            evaluate_entered.set()
            assert release_evaluate.wait(2.0)
            return SimpleNamespace(
                candidates=[], chosen=None, scenario="IDLE", chain=[], output=""
            )

        def configure(self, _cfg):
            pass

        def reset(self):
            self.state = SceneState()

    plugin._lifecycle_lock = threading.RLock()
    plugin._state_lock = threading.Lock()
    plugin._context_restore = ContextRestoreRetry()
    plugin._context_active = False
    plugin.cfg = CbqConfig(dry_run=False, data_layer_auto_start=False)
    plugin.data_layer_manager = _Manager()
    plugin.client = SimpleNamespace(
        poll=lambda: SceneState(
            connected=True,
            data_layer_instance_id="instance-1",
            frame_seq=2,
            game_running=False,
        )
    )
    plugin.runtime = _Runtime()
    plugin.safety = plugin.runtime.safety
    plugin.timeline = SimpleNamespace(
        mark_tick=lambda: None,
        configure=lambda **_kwargs: None,
        record_stage=lambda **_kwargs: None,
        record_decision=lambda **_kwargs: None,
    )
    plugin.dispatcher = SimpleNamespace(push_context=lambda _instructions: True)
    plugin.logger = _Logger()

    tick_thread = threading.Thread(target=plugin._tick)
    tick_thread.start()
    assert evaluate_entered.wait(2.0)

    new_cfg = CbqConfig(dry_run=True, data_layer_auto_start=False)
    config_thread = threading.Thread(target=plugin._apply_config, args=(new_cfg,))
    config_thread.start()
    assert not configured.wait(0.1)

    release_evaluate.set()
    tick_thread.join(2.0)
    config_thread.join(2.0)
    assert not tick_thread.is_alive()
    assert not config_thread.is_alive()
    assert configured.is_set()
    assert plugin.cfg.dry_run is True


def test_shutdown_retries_context_restore_before_dispatcher_is_lost(monkeypatch):
    module = _load_entrypoint(monkeypatch)
    plugin = object.__new__(module.NekoCalabiqiuPlugin)
    outcomes = iter((False, True))
    attempts = []

    class _Runtime:
        def reset(self):
            pass

    plugin._lifecycle_lock = threading.RLock()
    plugin._state_lock = threading.Lock()
    plugin._stop = threading.Event()
    plugin._thread = None
    plugin._shutdown_complete = threading.Event()
    plugin._shutdown_result = None
    plugin._shutdown_join_timeout_seconds = 1.0
    plugin._instance_gate_key = f"shutdown-test-{id(plugin)}"
    plugin._instance_gate_token = object()
    plugin._instance_gate_claimed = False
    plugin._context_active = True
    plugin._context_restore = ContextRestoreRetry()
    plugin.data_layer_manager = SimpleNamespace(stop=lambda: {"mode": "stopped"})
    plugin.runtime = _Runtime()
    plugin.dispatcher = SimpleNamespace(
        push_context=lambda instructions: (
            attempts.append(instructions), next(outcomes)
        )[1]
    )
    plugin.logger = _Logger()

    result = plugin.shutdown()

    assert len(attempts) == 2
    assert result["context_restore_pending"] is False
    assert plugin._context_active is False


def test_shutdown_returns_pending_when_tick_holds_the_lifecycle_lock(monkeypatch):
    module = _load_entrypoint(monkeypatch)
    plugin = object.__new__(module.NekoCalabiqiuPlugin)
    tick_entered = threading.Event()
    release_tick = threading.Event()
    manager_stopped = threading.Event()

    class _Runtime:
        def reset(self):
            pass

    plugin._lifecycle_lock = threading.RLock()
    plugin._state_lock = threading.Lock()
    plugin._stop = threading.Event()
    plugin._shutdown_complete = threading.Event()
    plugin._shutdown_result = None
    plugin._shutdown_join_timeout_seconds = 0.05
    plugin._instance_gate_key = f"pending-test-{id(plugin)}"
    plugin._instance_gate_token = object()
    assert module.try_claim_instance_gate(
        plugin._instance_gate_key, plugin._instance_gate_token
    )
    plugin._instance_gate_claimed = True
    plugin._context_active = False
    plugin._context_restore = ContextRestoreRetry()
    plugin.data_layer_manager = SimpleNamespace(
        stop=lambda: (manager_stopped.set(), {"mode": "stopped"})[1]
    )
    plugin.runtime = _Runtime()
    plugin.dispatcher = SimpleNamespace(push_context=lambda _instructions: True)
    plugin.logger = _Logger()

    def blocked_poll_thread():
        try:
            with plugin._lifecycle_lock:
                tick_entered.set()
                assert release_tick.wait(2.0)
        finally:
            plugin._finish_shutdown()

    plugin._thread = threading.Thread(target=blocked_poll_thread, daemon=True)
    plugin._thread.start()
    assert tick_entered.wait(2.0)

    started = time.monotonic()
    result = plugin.shutdown()
    elapsed = time.monotonic() - started

    assert result["status"] == "shutdown_pending"
    assert result["poll_thread_alive"] is True
    assert elapsed < 0.5
    assert not manager_stopped.is_set()

    same_instance_restart = asyncio.run(plugin.startup())
    assert isinstance(same_instance_restart, Exception)
    assert "still shutting down" in str(same_instance_restart)
    assert plugin._stop.is_set()

    replacement = object.__new__(module.NekoCalabiqiuPlugin)
    replacement._thread = None
    replacement._shutdown_complete = threading.Event()
    replacement._instance_gate_key = plugin._instance_gate_key
    replacement._instance_gate_token = object()
    replacement._instance_gate_claimed = False
    replacement_start = asyncio.run(replacement.startup())
    assert isinstance(replacement_start, Exception)
    assert "another plugin instance" in str(replacement_start)

    release_tick.set()
    assert plugin._shutdown_complete.wait(2.0)
    plugin._thread.join(2.0)
    assert manager_stopped.is_set()
    assert plugin._shutdown_result["status"] == "shutdown"
    assert module.try_claim_instance_gate(
        replacement._instance_gate_key, replacement._instance_gate_token
    )
    module.release_instance_gate(
        replacement._instance_gate_key, replacement._instance_gate_token
    )
