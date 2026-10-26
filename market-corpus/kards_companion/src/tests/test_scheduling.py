"""Contracts for perception cadence settings and the due scheduler.

任务集为 change/unit/card（HUD OCR 任务已移除）；预设四元组顺序为
capture/change/unit/card。
"""

from __future__ import annotations

import math
from dataclasses import FrozenInstanceError

import pytest
from kards_companion.perception.scheduling import (
    CONFIG_KEYS,
    PRESETS,
    TASKS,
    PerceptionScheduler,
    PerceptionSettings,
    SettingsValidationError,
    legacy_config_warnings,
)

# ------------------------------------------------------------------ settings

def test_task_set_and_config_keys_match_new_contract() -> None:
    assert TASKS == ("change", "unit", "card")
    assert CONFIG_KEYS == frozenset({
        "perception_preset", "capture_fps", "change_fps", "unit_fps", "card_fps",
    })


def test_default_settings_are_balanced_and_frozen() -> None:
    settings = PerceptionSettings()
    assert settings.preset == "balanced"
    assert settings.perception_preset == "balanced"
    assert (settings.capture_fps, settings.change_fps, settings.unit_fps,
            settings.card_fps) == (20.0, 10.0, 4.0, 6.0)
    assert settings.evidence_enabled is True
    with pytest.raises(FrozenInstanceError):
        settings.capture_fps = 10  # type: ignore[misc]


def test_named_preset_applies_its_rates_without_repeating_them() -> None:
    low = PerceptionSettings(preset="low_cost")
    assert (low.capture_fps, low.change_fps, low.unit_fps, low.card_fps) == (
        15.0, 8.0, 2.0, 3.0
    )
    quality = PerceptionSettings(preset="quality")
    assert (quality.capture_fps, quality.change_fps, quality.unit_fps,
            quality.card_fps) == (30.0, 12.0, 6.0, 10.0)
    assert PRESETS["balanced"] == (20.0, 10.0, 4.0, 6.0)


def test_direct_advanced_rates_switch_to_custom() -> None:
    settings = PerceptionSettings(capture_fps=25)
    assert settings.preset == "custom"
    assert settings.capture_fps == 25
    assert settings.change_fps == 10.0


def test_validate_changes_preset_and_advanced_edit() -> None:
    result = PerceptionSettings.validate_changes(
        {"perception_preset": "quality"}
    )
    assert result == {
        "perception_preset": "quality",
        "capture_fps": 30.0,
        "change_fps": 12.0,
        "unit_fps": 6.0,
        "card_fps": 10.0,
    }

    advanced = PerceptionSettings.validate_changes({"card_fps": 8})
    assert advanced["perception_preset"] == "custom"
    assert advanced["card_fps"] == 8.0
    assert advanced["capture_fps"] == 20.0
    assert advanced["change_fps"] == 10.0
    assert advanced["unit_fps"] == 4.0

    current = PerceptionSettings(preset="low_cost")
    edited = PerceptionSettings.validate_changes(
        {"perception_preset": "low_cost", "card_fps": 4}, current=current
    )
    assert edited["perception_preset"] == "custom"
    assert edited["card_fps"] == 4.0
    assert edited["capture_fps"] == 15.0


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"unknown": 1}, "unknown"),
        ({"capture_fps": True}, "finite number"),
        ({"capture_fps": math.nan}, "finite"),
        ({"capture_fps": math.inf}, "finite"),
        ({"capture_fps": 0.5}, "between 1 and 30"),
        ({"capture_fps": 31}, "between 1 and 30"),
        ({"change_fps": 0.25}, "between 0.5 and 15"),
        ({"unit_fps": 13}, "between 0.5 and 12"),
        ({"card_fps": 12.5}, "between 0.5 and 12"),
        # 已移除的旧键按 unknown 拒绝（兼容忽略只发生在 config 读取路径）。
        ({"hud_fps": 4}, "unknown"),
        ({"ocr_fps": 4}, "unknown"),
        ({"roi_auto_calibrate": True}, "unknown"),
        ({"recording_enabled": True}, "unknown"),
        ({"recording_max_mb": 2048}, "unknown"),
        ({"recording_jpeg_quality": 85}, "unknown"),
        ({"evidence_enabled": False}, "unknown"),
        ({"perception_preset": "low"}, "perception_preset"),
    ],
)
def test_validate_changes_rejects_instead_of_clamping(
    changes: dict[str, object], message: str
) -> None:
    with pytest.raises(SettingsValidationError, match=message):
        PerceptionSettings.validate_changes(changes)


def test_instance_validate_changes_uses_instance_as_current() -> None:
    current = PerceptionSettings(preset="low_cost")
    result = current.validate_changes({"unit_fps": 3})
    assert result["perception_preset"] == "custom"
    assert result["capture_fps"] == 15.0
    assert result["unit_fps"] == 3.0


def test_fps_changes_must_not_exceed_capture() -> None:
    with pytest.raises(SettingsValidationError, match="cannot exceed capture_fps"):
        PerceptionSettings.validate_changes({"capture_fps": 9})
    with pytest.raises(SettingsValidationError, match="cannot exceed capture_fps"):
        PerceptionSettings.validate_changes(
            {"capture_fps": 7, "change_fps": 8}
        )
    accepted = PerceptionSettings.validate_changes(
        {"capture_fps": 6, "change_fps": 6, "unit_fps": 6, "card_fps": 6}
    )
    assert accepted["capture_fps"] == 6
    assert accepted["perception_preset"] == "custom"


def test_evidence_flag_is_fixed_and_not_a_config_key() -> None:
    with pytest.raises(SettingsValidationError, match="unknown"):
        PerceptionSettings.validate_changes({"evidence_enabled": True})


# ------------------------------------------------------------------- legacy

def test_legacy_config_keys_are_ignored_with_warning() -> None:
    cfg = {
        "capture_fps": 20,
        "change_fps": 10,
        "hud_fps": 4,                    # OCR 任务已移除
        "recording_enabled": True,       # 录像已移除
        "recording_max_mb": 512,
        "roi_auto_calibrate": False,
    }
    settings = PerceptionSettings.from_config(cfg)
    # 旧键被忽略：不影响新四元组。
    assert (settings.capture_fps, settings.change_fps, settings.unit_fps,
            settings.card_fps) == (20.0, 10.0, 4.0, 6.0)
    warnings = legacy_config_warnings(cfg)
    assert "legacy_key_ignored:hud_fps" in warnings
    assert "legacy_key_ignored:recording_enabled" in warnings
    assert "legacy_key_ignored:recording_max_mb" in warnings
    assert "legacy_key_ignored:roi_auto_calibrate" in warnings
    # 未携带旧键时不产生 warning。
    assert legacy_config_warnings({"capture_fps": 20}) == []
    assert legacy_config_warnings(None) == []


# ------------------------------------------------------------------ config

def test_from_config_new_settings_win_over_legacy_perf_mode() -> None:
    settings = PerceptionSettings.from_config(
        {
            "perf_mode": "power_saver",
            "perception_preset": "quality",
        }
    )
    assert settings.preset == "quality"
    assert settings.capture_fps == 30
    assert settings.card_fps == 10


def test_named_preset_with_explicit_new_capture_rate_becomes_custom() -> None:
    settings = PerceptionSettings.from_config(
        {"perception_preset": "quality", "capture_fps": 25}
    )
    assert settings.preset == "custom"
    assert settings.capture_fps == 25


def test_from_config_honours_legacy_perf_mode_only_without_new_keys() -> None:
    low = PerceptionSettings.from_config(
        {"perf_mode": "power_saver", "capture_fps": 4}
    )
    assert low.preset == "low_cost"
    assert low.capture_fps == 15
    assert low.card_fps == 3.0

    balanced = PerceptionSettings.from_config({"perf_mode": "realtime"})
    assert balanced.preset == "balanced"
    assert balanced.capture_fps == 20


def test_from_config_advanced_rate_is_custom_and_nested_config_works() -> None:
    settings = PerceptionSettings.from_config(
        {
            "kards": {
                "perception_preset": "balanced",
                "card_fps": 9,
            }
        }
    )
    assert settings.preset == "custom"
    assert settings.card_fps == 9


def test_from_config_to_dict_round_trip() -> None:
    original = PerceptionSettings(
        preset="custom",
        capture_fps=24,
        change_fps=6,
        unit_fps=3,
        card_fps=6,
    )
    payload = original.to_dict()
    assert payload["perception_preset"] == "custom"
    assert payload["card_fps"] == 6
    assert payload["evidence_enabled"] is True
    assert set(payload) >= CONFIG_KEYS
    assert PerceptionSettings.from_config(payload) == original


def test_named_preset_round_trip_stays_named() -> None:
    original = PerceptionSettings(preset="quality")
    assert PerceptionSettings.from_config(original.to_dict()) == original


def test_from_config_rejects_bad_types() -> None:
    with pytest.raises(SettingsValidationError):
        PerceptionSettings.from_config({"capture_fps": "20"})
    with pytest.raises(SettingsValidationError):
        PerceptionSettings.from_config({"card_fps": None})


# ----------------------------------------------------------------- scheduler

class FakeClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def set(self, value: float) -> None:
        self.value = value


def make_scheduler(settings: PerceptionSettings | None = None) -> tuple[
    PerceptionScheduler, FakeClock
]:
    clock = FakeClock()
    return PerceptionScheduler(settings, clock=clock), clock


def test_scheduler_tasks_have_independent_deadlines() -> None:
    scheduler, clock = make_scheduler()
    assert scheduler.due("change", frame_id=1) is True
    assert scheduler.due("unit", frame_id=1) is True
    assert scheduler.due("card", frame_id=1) is True

    clock.set(0.10)
    assert scheduler.due("change", frame_id=2) is True   # 10fps -> 0.1s
    assert scheduler.due("unit", frame_id=2) is False    # 4fps  -> 0.25s
    assert scheduler.due("card", frame_id=2) is False    # 6fps  -> ~0.167s

    clock.set(0.17)
    assert scheduler.due("card", frame_id=3) is True
    assert scheduler.due("unit", frame_id=3) is False

    clock.set(0.25)
    assert scheduler.due("unit", frame_id=4) is True
    snapshot = scheduler.snapshot()
    assert snapshot["change"]["actual"] == 2
    assert snapshot["unit"]["actual"] == 2
    assert snapshot["card"]["actual"] == 2
    assert snapshot["totals"] == {"actual": 6, "missed": 0}


def test_repeated_frame_id_does_not_advance_counts_or_deadline() -> None:
    scheduler, clock = make_scheduler()
    assert scheduler.due("change", frame_id=7) is True
    deadline = scheduler.next_deadline("change")
    clock.set(10.0)
    assert scheduler.due("change", frame_id=7) is False
    state = scheduler.snapshot()["change"]
    assert state["actual"] == 1
    assert state["missed"] == 0
    assert scheduler.next_deadline("change") == deadline

    # A genuinely new frame is served now, but there is no replay backlog.
    assert scheduler.due("change", frame_id=8) is True
    state = scheduler.snapshot()["change"]
    assert state["actual"] == 2
    assert state["missed"] == 99
    assert scheduler.next_deadline("change") == pytest.approx(10.1)


def test_scheduler_accepts_one_frame_per_task_but_not_twice() -> None:
    scheduler, _clock = make_scheduler()
    assert scheduler.due("change", frame_id="f1") is True
    assert scheduler.due("card", frame_id="f1") is True
    assert scheduler.due("change", frame_id="f1") is False
    assert scheduler.due("card", frame_id="f1") is False


def test_scheduler_rejects_removed_and_unknown_tasks() -> None:
    scheduler, _clock = make_scheduler()
    with pytest.raises(ValueError, match="unknown scheduler task"):
        scheduler.due("capture", frame_id=1)
    # HUD 任务已删除。
    with pytest.raises(ValueError, match="unknown scheduler task"):
        scheduler.due("hud", frame_id=1)


def test_update_rebuilds_deadline_from_last_run_without_reset() -> None:
    scheduler, clock = make_scheduler()
    assert scheduler.due("card", frame_id=1) is True
    assert scheduler.next_deadline("card") == pytest.approx(1.0 / 6.0)

    scheduler.update(PerceptionSettings(preset="quality"))
    # card is 10 fps now; the old invocation remains counted.
    assert scheduler.snapshot()["card"]["actual"] == 1
    assert scheduler.next_deadline("card") == pytest.approx(0.1)
    clock.set(0.1)
    assert scheduler.due("card", frame_id=2) is True


def test_mark_duration_and_snapshot_are_bounded_and_serializable() -> None:
    scheduler, _clock = make_scheduler()
    scheduler.mark_duration("card", 0.0125)
    scheduler.mark_duration("card", 0.02)
    for index in range(PerceptionScheduler.MAX_DURATIONS + 10):
        scheduler.mark_duration("card", index / 1000.0)
    state = scheduler.snapshot()["card"]
    assert len(state["durations"]) == PerceptionScheduler.MAX_DURATIONS
    assert state["durations"][-1] == pytest.approx(0.073)
    assert state["last_duration_ms"] == pytest.approx(73.0)
    assert state["average_duration_ms"] is not None
    with pytest.raises(SettingsValidationError):
        scheduler.mark_duration("card", -1)
    with pytest.raises(ValueError):
        scheduler.mark_duration("capture", 1.0)
    with pytest.raises(ValueError):
        scheduler.mark_duration("hud", 1.0)


def test_measured_fps_uses_rolling_accepted_window() -> None:
    scheduler, clock = make_scheduler()
    assert scheduler.measured_fps()["card"] is None
    scheduler.due("card", frame_id=1)
    assert scheduler.measured_fps()["card"] is None  # 单次接受无 span
    clock.set(1.0)
    scheduler.due("card", frame_id=2)
    clock.set(2.0)
    scheduler.due("card", frame_id=3)
    measured = scheduler.measured_fps()
    assert measured["card"] == pytest.approx(1.0)  # 2 次间隔 / 2s
    snapshot = scheduler.snapshot()
    assert snapshot["tasks"]["card"]["measured_fps"] == pytest.approx(1.0)
    assert snapshot["measured_fps"]["change"] is None


def test_scheduler_uses_only_injected_clock() -> None:
    scheduler, clock = make_scheduler()
    assert scheduler.due("unit", frame_id=1) is True
    clock.set(3.0)
    assert scheduler.due("unit", frame_id=2) is True
    assert scheduler.snapshot()["unit"]["actual"] == 2
