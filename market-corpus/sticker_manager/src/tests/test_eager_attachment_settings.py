"""Persist panel selection cadence and validate the integer configuration."""

from __future__ import annotations

from dataclasses import replace

import pytest
from conftest import FakeConfig, FakeHostContext, build_plugin
from sticker_manager.core.configuration import StickerManagerSettings


def _setup(tmp_path, run_async):
    host = FakeHostContext(data_root=tmp_path, config=FakeConfig({"sticker_manager": {
        "enabled": True, "send": {"eagerness": "eager", "cooldown_sec": 13},
    }}))
    plugin, host = build_plugin(host)
    run_async(plugin._reload_settings())
    return plugin, host


@pytest.mark.parametrize("turns", range(11))
def test_save_applies_persists_and_preserves_other_settings(tmp_path, run_async, turns):
    plugin, host = _setup(tmp_path, run_async)
    before = plugin._settings
    epoch = plugin._eager_attachment._epoch
    result = run_async(plugin.set_eager_attachment_turns_entry(turns=turns))
    assert result.is_ok() and result.value == {"note": "eager_attachment_turns_set", "turns": turns}
    assert host.config.writes == [("sticker_manager.send.eager_attachment_turns", turns)]
    assert plugin._settings == replace(before, send=replace(before.send, eager_attachment_turns=turns))
    assert plugin._eager_attachment._epoch > epoch
    assert run_async(plugin.dashboard_context())["config"]["eager_attachment_turns"] == turns
    restored, _ = build_plugin(host)
    assert run_async(restored._reload_settings()).send.eager_attachment_turns == turns
    assert not host.push.calls


@pytest.mark.parametrize("turns", [True, False, None, "0", 1.5, float("nan"), float("inf"), -1, 11, 10**400])
def test_invalid_panel_value_does_not_write(tmp_path, run_async, turns):
    plugin, host = _setup(tmp_path, run_async)
    before = plugin._settings
    result = run_async(plugin.set_eager_attachment_turns_entry(turns=turns))
    assert not result.is_ok() and str(result.error) == "invalid_value"
    assert not host.config.writes and plugin._settings == before


@pytest.mark.parametrize("operation", ["set", "dump"])
def test_storage_failure_does_not_claim_value_is_active(tmp_path, run_async, operation):
    plugin, host = _setup(tmp_path, run_async)
    before = plugin._settings
    setattr(host.config, operation + "_error", RuntimeError("unavailable"))
    result = run_async(plugin.set_eager_attachment_turns_entry(turns=10))
    assert not result.is_ok() and str(result.error) == "config_unavailable"
    assert plugin._settings == before


@pytest.mark.parametrize("value,expected", [
    (None, 1), (True, 1), ("4", 1), (float("nan"), 1), (float("inf"), 1),
    (-1, 0), (0, 0), (10, 10), (11, 10), (10**400, 10),
])
def test_config_read_uses_existing_sanitization_and_range(value, expected):
    settings = StickerManagerSettings.from_config({"sticker_manager": {
        "send": {"eager_attachment_turns": value},
    }})
    assert settings.send.eager_attachment_turns == expected


def test_missing_setting_defaults_to_one_reply():
    assert StickerManagerSettings.from_config({}).send.eager_attachment_turns == 1


def test_action_is_explicit_integer_setting_with_refresh(tmp_path, run_async):
    plugin, _ = _setup(tmp_path, run_async)
    records = [item["kwargs"] for item in plugin.set_eager_attachment_turns_entry.__neko_stub_meta__]
    assert any(row.get("id") == "set_eager_attachment_turns" and row.get("refresh_context") for row in records)
    entry = next(row for row in records if "input_schema" in row)
    assert entry["metadata"]["agent_auto"] is False
    assert entry["input_schema"]["required"] == ["turns"]
    assert entry["input_schema"]["properties"]["turns"]["type"] == "integer"
    assert entry["input_schema"]["properties"]["turns"]["minimum"] == 0
    assert entry["input_schema"]["properties"]["turns"]["maximum"] == 10
