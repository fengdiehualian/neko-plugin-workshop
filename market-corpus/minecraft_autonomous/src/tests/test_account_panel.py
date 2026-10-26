import importlib
import json
from unittest.mock import AsyncMock

import pytest
from plugin.sdk.plugin import Err, Ok


@pytest.mark.parametrize("limit", [32768, 65536, 98304, 131072, 1048576])
def test_context_limit_save_and_public_roundtrip_preserves_explicit_value(plugin_instance, plugin_module, run_async, monkeypatch, limit):
    dashboard = importlib.import_module(plugin_module.__name__ + ".dashboard")
    patch = {"autonomy": {"context_tokens": limit}}
    update = AsyncMock(return_value={})
    monkeypatch.setattr(plugin_instance.config, "update", update)
    assert isinstance(run_async(plugin_instance.save_settings(patch)), Ok)
    update.assert_awaited_once_with(patch)
    assert dashboard.public_config(plugin_module.Settings.parse(patch))["autonomy"]["context_tokens"] == limit
    assert dashboard.public_config(plugin_module.Settings.parse({}))["autonomy"]["context_tokens"] == 65536


def test_full_context_setting_roundtrip(plugin_instance, plugin_module, run_async, monkeypatch):
    dashboard = importlib.import_module(plugin_module.__name__ + ".dashboard")
    update = AsyncMock(return_value={})
    monkeypatch.setattr(plugin_instance.config, "update", update)
    patch = {"autonomy": {"context_mode": "full", "context_tokens": 1048576}}
    assert isinstance(run_async(plugin_instance.save_settings(patch)), Ok)
    update.assert_awaited_once_with(patch)
    assert dashboard.public_config(plugin_module.Settings.parse(patch))["autonomy"]["context_mode"] == "full"
    assert isinstance(run_async(plugin_instance.save_settings({"autonomy": {"context_mode": "unlimited"}})), Err)


@pytest.mark.parametrize("headroom", [0, 8192])
def test_full_history_headroom_roundtrip_preserves_capacity(plugin_instance, plugin_module, run_async, monkeypatch, headroom):
    dashboard = importlib.import_module(plugin_module.__name__ + ".dashboard")
    update = AsyncMock(return_value={})
    monkeypatch.setattr(plugin_instance.config, "update", update)
    patch = {"autonomy": {"context_mode": "full", "context_tokens": 131072,
                          "context_history_headroom_tokens": headroom}}
    assert isinstance(run_async(plugin_instance.save_settings(patch)), Ok)
    update.assert_awaited_once_with(patch)
    public = dashboard.public_config(plugin_module.Settings.parse(patch))["autonomy"]
    assert public["context_history_headroom_tokens"] == headroom
    assert public["context_tokens"] == 131072
    assert isinstance(run_async(plugin_instance.save_settings({"autonomy": {"context_history_headroom_tokens": -1}})), Err)


def test_account_settings_save_only_and_native_link_contains_no_account_payload(plugin_instance, plugin_module, run_async, monkeypatch, tmp_path):
    patch = {"server": {"host": "micro.kangqiang.site", "port": 25702, "username": "ag_NEKO", "auth": "offline", "connection_type": "custom", "version": "1.21.1"},
             "operator": {"port": 28986}, "runtime": {"mode": "external", "data_directory": str(tmp_path)}}
    update = AsyncMock(return_value={})
    monkeypatch.setattr(plugin_instance.config, "update", update)
    assert isinstance(run_async(plugin_instance.save_settings(patch)), Ok)
    update.assert_awaited_once_with(patch)
    assert plugin_instance._worker is None
    monkeypatch.setattr(plugin_instance.config, "dump", AsyncMock(return_value=patch))
    result = run_async(plugin_instance.dashboard())
    assert not result["operator"]["available"]
    assert "device" not in json.dumps(result)
    assert result["settings"]["operator"] == {"port": 28986}
    assert isinstance(run_async(plugin_instance.save_settings({"operator": {"url": "https://evil.test"}})), Err)
    assert isinstance(run_async(plugin_instance.save_settings({"operator": {"token": "secret"}})), Err)


def test_operator_configuration_requires_stopping_owned_worker(plugin_instance, run_async, monkeypatch):
    from types import SimpleNamespace
    plugin_instance._worker = SimpleNamespace(snapshot=lambda: {"worker_alive": True})
    update = AsyncMock(return_value={})
    monkeypatch.setattr(plugin_instance.config, "update", update)
    assert isinstance(run_async(plugin_instance.save_settings({"operator": {"port": 28986}})), Err)
    update.assert_not_awaited()


def test_default_connection_remains_local_and_no_challenge_service_exists(plugin_module):
    dashboard = importlib.import_module(plugin_module.__name__ + ".dashboard")
    result = dashboard.public_config(plugin_module.Settings.parse({}))
    assert result["server"]["connection_type"] == "local"
    assert result["server"]["host"] == ""
    assert result["operator"] == {"port": 0}
    assert not hasattr(dashboard.Dashboard, "operator_state")


def test_older_framework_reports_unavailable_and_cannot_silently_accept_operator_settings(plugin_instance, plugin_module, run_async, monkeypatch):
    dashboard = importlib.import_module(plugin_module.__name__ + ".dashboard")
    monkeypatch.setattr(dashboard, "operator_link", None)
    update = AsyncMock(return_value={})
    monkeypatch.setattr(plugin_instance.config, "update", update)
    assert isinstance(run_async(plugin_instance.save_settings({"operator": {"port": 28986}})), Err)
    update.assert_not_awaited()
    assert run_async(plugin_instance.dashboard())["operator"]["reason"] == "operator_capability_unavailable"
