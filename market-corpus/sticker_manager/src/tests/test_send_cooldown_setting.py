"""Panel cooldown changes persist, apply to existing sends and preserve other settings."""

from __future__ import annotations

from dataclasses import replace

import pytest
from conftest import PNG_BYTES, FakeConfig, FakeHostContext, build_plugin


def _setup(tmp_path, run_async):
    config = FakeConfig(data={"sticker_manager": {
        "enabled": True,
        "send": {"cooldown_sec": 20.0, "eagerness": "eager", "recent_dedup_count": 5},
        "awareness": {"min_interval_sec": 0, "inject_interval_n": 4, "event_gated": False},
    }})
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path, config=config))
    run_async(plugin._reload_settings())
    return plugin, host


@pytest.mark.parametrize("value", [0, 0.0, 1, 12.5, 3600])
def test_save_persists_applies_and_preserves_other_settings(tmp_path, run_async, value):
    plugin, host = _setup(tmp_path, run_async)
    before = plugin._settings
    result = run_async(plugin.set_send_cooldown_entry(cooldown_sec=value))
    assert result.is_ok() and result.value == {"note": "send_cooldown_set", "cooldown_sec": float(value)}
    assert host.config.writes == [("sticker_manager.send.cooldown_sec", float(value))]
    assert plugin._settings == replace(before, send=replace(before.send, cooldown_sec=float(value)))
    assert run_async(plugin.dashboard_context())["config"]["cooldown_sec"] == value
    restored, _ = build_plugin(host)
    assert run_async(restored._reload_settings()).send.cooldown_sec == value
    assert not host.push.calls


@pytest.mark.parametrize("value", [True, None, "0", float("nan"), float("inf"), -1, 3600.1, 10**400])
def test_invalid_values_do_not_write_or_change_settings(tmp_path, run_async, value):
    plugin, host = _setup(tmp_path, run_async)
    before = plugin._settings
    result = run_async(plugin.set_send_cooldown_entry(cooldown_sec=value))
    assert not result.is_ok() and str(result.error) == "invalid_value"
    assert plugin._settings == before and not host.config.writes


def test_failed_save_preserves_current_value(tmp_path, run_async):
    plugin, host = _setup(tmp_path, run_async)
    before = plugin._settings
    host.config.set_error = RuntimeError("storage unavailable")
    result = run_async(plugin.set_send_cooldown_entry(cooldown_sec=0))
    assert not result.is_ok() and str(result.error) == "config_unavailable"
    assert not host.config.writes and plugin._settings == before


def test_failed_readback_does_not_claim_the_new_value_is_active(tmp_path, run_async):
    plugin, host = _setup(tmp_path, run_async)
    host.config.dump_error = RuntimeError("config unavailable")
    result = run_async(plugin.set_send_cooldown_entry(cooldown_sec=0))
    assert not result.is_ok() and str(result.error) == "config_unavailable"
    assert host.config.writes == [("sticker_manager.send.cooldown_sec", 0.0)]
    assert plugin._settings.send.cooldown_sec == 20.0


def test_changing_cooldown_applies_to_existing_send_timestamps_without_resetting_them(tmp_path, run_async):
    plugin, _host = _setup(tmp_path, run_async)
    plugin._library.load()
    stickers = [
        plugin._library.add(data=PNG_BYTES + bytes([index]), desc=str(index), tags=[])[0]
        for index in range(3)
    ]

    def send(index, now):
        return run_async(plugin._sender.send(
            stickers[index], lanlan="K", settings=plugin._settings, source="tool", now=now,
        ))

    assert send(0, 1000.0).ok
    assert send(1, 1001.0).code == "send_cooldown"
    last = plugin._sender._last_sent["K"]
    assert run_async(plugin.set_send_cooldown_entry(cooldown_sec=0)).is_ok()
    assert plugin._sender._last_sent["K"] == last
    assert send(1, 1001.0).ok
    assert run_async(plugin.set_send_cooldown_entry(cooldown_sec=60)).is_ok()
    assert send(2, 1018.0).code == "send_cooldown"
    assert run_async(plugin.set_send_cooldown_entry(cooldown_sec=5)).is_ok()
    assert send(2, 1018.0).ok


def test_cooldown_action_is_explicitly_saved_and_not_automatic_agent_work(tmp_path, run_async):
    plugin, _host = _setup(tmp_path, run_async)
    records = [item["kwargs"] for item in plugin.set_send_cooldown_entry.__neko_stub_meta__]
    assert any(row.get("id") == "set_send_cooldown" and row.get("refresh_context") for row in records)
    entry = next(row for row in records if "input_schema" in row)
    assert entry["metadata"]["agent_auto"] is False
    assert entry["input_schema"]["required"] == ["cooldown_sec"]
    assert entry["input_schema"]["properties"]["cooldown_sec"]["minimum"] == 0
    assert entry["input_schema"]["properties"]["cooldown_sec"]["maximum"] == 3600
