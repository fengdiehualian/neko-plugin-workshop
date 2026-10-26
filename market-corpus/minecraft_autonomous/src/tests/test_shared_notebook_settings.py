import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from plugin.sdk.plugin import Err, Ok


def test_shared_note_and_observation_settings_save_without_starting_a_body(plugin_instance, plugin_module, run_async, monkeypatch, tmp_path):
    raw = {"runtime": {"mode": "external", "data_directory": str(tmp_path / "unused")},
           "server": {"host": "fixture.test", "username": "Alice"}}
    update = AsyncMock()
    monkeypatch.setattr(plugin_instance.config, "dump", AsyncMock(return_value=raw))
    monkeypatch.setattr(plugin_instance.config, "update", update)
    patch = {"memory": {"mode": "local-vector", "agent_id": "stable-role"},
             "perception": {"enabled": False, "mineral_vision": False}}
    result = run_async(plugin_instance.save_settings(settings=patch))
    assert isinstance(result, Ok) and result.value["running_session_changed"] is False
    update.assert_awaited_once_with(patch)
    settings = plugin_module.Settings.parse({**raw, **patch})
    public = importlib.import_module(plugin_module.__name__ + ".dashboard").public_config(settings)
    assert public["memory"]["mode"] == "local-vector"
    assert public["memory"]["agent_id"] == "stable-role"
    assert public["perception"] == {"enabled": False, "mineral_vision": False}
    assert settings.identity == plugin_module.Settings.parse(raw).identity
    assert plugin_instance._worker is None and not (tmp_path / "unused").exists()


@pytest.mark.parametrize("patch", [
    {"perception": {"mineral_vision": "true"}},
    {"memory": {"mode": "local-vector", "learning_enabled": True}},
    {"memory": {"agent_id": "owner\x7f"}},
])
def test_invalid_shared_settings_are_rejected_before_configuration_write(plugin_instance, run_async, monkeypatch, patch):
    update = AsyncMock()
    monkeypatch.setattr(plugin_instance.config, "update", update)
    result = run_async(plugin_instance.save_settings(settings=patch))
    assert isinstance(result, Err)
    update.assert_not_awaited()
    assert plugin_instance._worker is None


def test_embedded_running_body_still_blocks_memory_scope_change(plugin_instance, run_async, monkeypatch):
    update = AsyncMock()
    monkeypatch.setattr(plugin_instance.config, "update", update)
    plugin_instance._worker = SimpleNamespace(snapshot=lambda: {"worker_alive": True})
    result = run_async(plugin_instance.save_settings(settings={"memory": {"agent_id": "other-role"}}))
    assert isinstance(result, Err) and result.error["code"] == "settings_runtime_active"
    update.assert_not_awaited()
    plugin_instance._worker = None
