# pyright: reportMissingImports=false
"""Configurable reminder floor: persistence, strict entry input, and turn gating."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import PNG_BYTES, FakeBus, FakeConfig, FakeHostContext, build_plugin, user_message_record
from sticker_manager.core.configuration import StickerManagerSettings


def _plugin(tmp_path, run_async, *, floor: float = 60.0):
    config = FakeConfig(data={
        "sticker_manager": {
            "enabled": True,
            "send": {"eagerness": "eager", "cooldown_sec": 17.0, "probability": 0.75},
            "awareness": {
                "min_interval_sec": floor,
                "inject_interval_n": 3,
                "event_gated": False,
            },
        },
    })
    host = FakeHostContext(data_root=tmp_path, config=config, bus=FakeBus(memory_records=[]))
    plugin, host = build_plugin(host)
    run_async(plugin._reload_settings())
    return plugin, host


class TestReminderIntervalReadIn:
    def test_missing_value_keeps_sixty_second_default(self):
        assert StickerManagerSettings.from_config({}).awareness.min_interval_sec == 60.0
        settings = StickerManagerSettings.from_config({"sticker_manager": {"awareness": {}}})
        assert settings.awareness.min_interval_sec == 60.0

    @pytest.mark.parametrize(("cooldown", "sent"), [(0, True), (0.0, True), (20.0, False)])
    def test_reloaded_send_cooldown_is_separate_from_zero_reminder_interval(
        self, tmp_path, run_async, caplog, cooldown, sent,
    ):
        caplog.set_level("INFO")
        config = FakeConfig(data={"sticker_manager": {
            "enabled": True,
            "send": {"cooldown_sec": cooldown, "recent_dedup_count": 0},
            "awareness": {"min_interval_sec": 0},
        }})
        plugin, host = build_plugin(FakeHostContext(data_root=tmp_path, config=config))
        settings = run_async(plugin._reload_settings())
        plugin._library.load()
        sticker, error = plugin._library.add(data=PNG_BYTES, desc="smile", tags=[])
        assert not error
        assert settings.send.cooldown_sec == cooldown and settings.awareness.min_interval_sec == 0
        assert run_async(
            plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0),
        ).ok
        again = run_async(
            plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1018.0),
        )
        assert again.ok is sent
        if not sent:
            assert again.code == "send_cooldown"
        assert len(host.push.calls) == (2 if sent else 1)
        assert f"send_cooldown_sec={float(cooldown)}" in caplog.text
        assert "reminder_min_interval_sec=0.0" in caplog.text

    @pytest.mark.parametrize("raw", [0, 0.0, 1, 12.5, 59, 60, 60.0])
    def test_valid_numeric_values_are_preserved(self, raw: float):
        settings = StickerManagerSettings.from_config({
            "sticker_manager": {"awareness": {"min_interval_sec": raw}},
        })
        assert settings.awareness.min_interval_sec == raw

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(-0.01, 0.0), (-100, 0.0), (60.01, 60.0), (3600, 60.0), (10**400, 60.0)],
    )
    def test_out_of_range_config_is_clamped(self, raw: Any, expected: float):
        settings = StickerManagerSettings.from_config({
            "sticker_manager": {"awareness": {"min_interval_sec": raw}},
        })
        assert settings.awareness.min_interval_sec == expected

    @pytest.mark.parametrize("raw", [True, False, None, "0", "", [], {}, float("nan"), float("inf")])
    def test_invalid_config_falls_back_to_sixty(self, raw: Any):
        settings = StickerManagerSettings.from_config({
            "sticker_manager": {"awareness": {"min_interval_sec": raw}},
        })
        assert settings.awareness.min_interval_sec == 60.0


class TestReminderIntervalEntry:
    @pytest.mark.parametrize("value", [0, 0.0, 1, 12.5, 59, 60, 60.0])
    def test_valid_value_is_persisted_and_applied(self, tmp_path, run_async, value: float):
        plugin, host = _plugin(tmp_path, run_async)
        result = run_async(plugin.set_reminder_interval_entry(min_interval_sec=value))
        assert result.is_ok()
        assert result.value["note"] == "reminder_interval_set"
        assert result.value["min_interval_sec"] == float(value)
        assert host.config.writes == [("sticker_manager.awareness.min_interval_sec", float(value))]
        assert plugin._settings.awareness.min_interval_sec == value
        assert host.push.calls == []

    @pytest.mark.parametrize(
        "value",
        [True, False, None, "0", "60", "", [], {}, float("nan"), float("inf"), -0.01, 60.01, 10**400],
    )
    def test_invalid_entry_value_is_rejected_without_mutation(self, tmp_path, run_async, value: Any):
        plugin, host = _plugin(tmp_path, run_async, floor=25.0)
        previous = plugin._settings
        result = run_async(plugin.set_reminder_interval_entry(min_interval_sec=value))
        assert not result.is_ok()
        assert str(result.error) == "invalid_value"
        assert host.config.writes == []
        assert plugin._settings == previous
        assert host.push.calls == []

    def test_default_argument_restores_sixty_seconds(self, tmp_path, run_async):
        plugin, host = _plugin(tmp_path, run_async, floor=0.0)
        result = run_async(plugin.set_reminder_interval_entry())
        assert result.is_ok()
        assert result.value["min_interval_sec"] == 60.0
        assert host.config.writes == [("sticker_manager.awareness.min_interval_sec", 60.0)]

    def test_persisted_interval_survives_plugin_reconstruction(self, tmp_path, run_async):
        plugin, host = _plugin(tmp_path, run_async)
        assert run_async(plugin.set_reminder_interval_entry(min_interval_sec=0)).is_ok()
        restored, _ = build_plugin(host)
        run_async(restored._reload_settings())
        assert restored._settings.awareness.min_interval_sec == 0.0
        assert run_async(restored.dashboard_context())["awareness"]["min_interval_sec"] == 0.0

    def test_storage_failure_preserves_running_settings(self, tmp_path, run_async):
        plugin, host = _plugin(tmp_path, run_async, floor=25.0)
        previous = plugin._settings
        host.config.set_error = RuntimeError("storage unavailable")
        result = run_async(plugin.set_reminder_interval_entry(min_interval_sec=0))
        assert not result.is_ok()
        assert str(result.error) == "config_unavailable"
        assert host.config.writes == []
        assert plugin._settings == previous
        assert host.config.data["sticker_manager"]["awareness"]["min_interval_sec"] == 25.0
        assert host.push.calls == []

    def test_set_does_not_change_send_rules_or_turn_threshold_or_counter(self, tmp_path, run_async):
        plugin, host = _plugin(tmp_path, run_async)
        host.bus.memory.records.append(user_message_record(100.0, "business", "K"))
        run_async(plugin._awareness._turns.poll_all())
        before_send = plugin._settings.send
        before_awareness = plugin._settings.awareness
        assert run_async(plugin.set_reminder_interval_entry(min_interval_sec=0)).is_ok()
        assert plugin._settings.send == before_send
        assert plugin._settings.awareness.inject_interval_n == before_awareness.inject_interval_n
        assert plugin._settings.awareness.inject_mode == before_awareness.inject_mode
        assert plugin._settings.awareness.event_gated == before_awareness.event_gated
        assert plugin._awareness._turns.turns_since("K") == 1
        assert host.push.calls == []

    @pytest.mark.parametrize("floor", [0.0, 17.5, 60.0])
    def test_dashboard_reports_applied_interval(self, tmp_path, run_async, floor: float):
        plugin, _ = _plugin(tmp_path, run_async, floor=floor)
        state = run_async(plugin.dashboard_context())
        assert state["awareness"]["min_interval_sec"] == floor

    def test_ui_action_has_matching_id_and_refreshes_context(self, tmp_path, run_async):
        plugin, _ = _plugin(tmp_path, run_async)
        metadata = plugin.set_reminder_interval_entry.__neko_stub_meta__
        assert any(
            item["kwargs"].get("id") == "set_reminder_interval"
            and item["kwargs"].get("refresh_context") is True
            for item in metadata
        )
        schemas = [
            item["kwargs"]["input_schema"]
            for item in metadata
            if "input_schema" in item["kwargs"]
        ]
        assert len(schemas) == 1
        schema = schemas[0]["properties"]["min_interval_sec"]
        assert schema["type"] == "number"
        assert schema["minimum"] == 0
        assert schema["maximum"] == 60


class TestReminderIntervalTurnBehavior:
    @staticmethod
    def _append(host, start: float, count: int = 3):
        host.bus.memory.records.extend(
            user_message_record(start + index, "business", "K")
            for index in range(count)
        )

    def test_zero_allows_next_eligible_turn_without_time_wait(self, tmp_path, run_async):
        plugin, host = _plugin(tmp_path, run_async)
        plugin._library.add(data=PNG_BYTES, desc="wave", tags=[])
        assert run_async(plugin.set_reminder_interval_entry(min_interval_sec=0)).is_ok()
        self._append(host, 100.0)
        assert run_async(plugin._awareness.maybe_run(settings=plugin._settings, now=50.0))["status"] == "injected"
        self._append(host, 103.0)
        assert run_async(plugin._awareness.maybe_run(settings=plugin._settings, now=50.1))["status"] == "injected"
        assert len(host.push.calls) == 2
        assert plugin._awareness._turns.turns_since("K") == 0
        assert run_async(plugin._awareness.maybe_run(settings=plugin._settings, now=50.2))["status"] == "idle"
        assert len(host.push.calls) == 2

    def test_sixty_keeps_blocked_turn_count_until_new_eligible_turn(self, tmp_path, run_async):
        plugin, host = _plugin(tmp_path, run_async, floor=0.0)
        plugin._library.add(data=PNG_BYTES, desc="wave", tags=[])
        assert run_async(plugin.set_reminder_interval_entry(min_interval_sec=60)).is_ok()
        self._append(host, 100.0)
        assert run_async(plugin._awareness.maybe_run(settings=plugin._settings, now=50.0))["status"] == "injected"
        self._append(host, 103.0)
        assert run_async(plugin._awareness.maybe_run(settings=plugin._settings, now=109.9))["status"] == "not_due"
        assert plugin._awareness._turns.turns_since("K") == 3
        assert len(host.push.calls) == 1
        assert run_async(plugin._awareness.maybe_run(settings=plugin._settings, now=110.0))["status"] == "idle"
        assert plugin._awareness._turns.turns_since("K") == 3
        self._append(host, 106.0, count=1)
        assert run_async(plugin._awareness.maybe_run(settings=plugin._settings, now=110.0))["status"] == "injected"
        assert plugin._awareness._turns.turns_since("K") == 0
        assert len(host.push.calls) == 2
