"""Perception cadence settings and the per-task scheduler.

The capture producer is deliberately *not* scheduled here.  Capture is a
single 20 fps (or user configured) stream; this module only decides whether a
consumed frame should run the relatively expensive change, unit or card tasks.
（HUD OCR 任务已随 OCR 全链路移除，任务集固定为 change/unit/card。）

Design rules
------------
* All deadlines use a monotonic clock.  Wall-clock jumps must never make a task
  run early or wedge forever.
* Every task owns its deadline and its frame-id high-water mark.  Passing the
  same captured frame twice cannot increment counters or advance a deadline.
* A late scheduler drops missed slots instead of replaying a backlog: the next
  deadline is based on the time a task actually ran.
* Settings changes are strict.  Unknown keys, wrong types, NaN/infinity and
  out-of-range values are rejected rather than silently clamped.
* Legacy config keys (``hud_fps``/``ocr_fps``/``roi_auto_calibrate``/
  ``recording_*``) are ignored on purpose; :func:`legacy_config_warnings`
  surfaces them so callers can log a warning instead of failing.
"""

from __future__ import annotations

import math
import numbers
import time
from dataclasses import dataclass, field
from typing import Any, ClassVar, Mapping

TASKS: tuple[str, ...] = ("change", "unit", "card")
VALID_PRESETS: frozenset[str] = frozenset({"low_cost", "balanced", "quality", "custom"})

# (capture, change, unit, card) frames per second.  顺序固定为采集/变化/单位/卡牌，
# HUD 档位已删除。
PRESETS: dict[str, tuple[float, float, float, float]] = {
    "low_cost": (15.0, 8.0, 2.0, 3.0),
    "balanced": (20.0, 10.0, 4.0, 6.0),
    "quality": (30.0, 12.0, 6.0, 10.0),
}

# Inclusive limits.  Every task rate must additionally be <= capture_fps.
SETTING_LIMITS: dict[str, tuple[float, float]] = {
    "capture_fps": (1.0, 30.0),
    "change_fps": (0.5, 15.0),
    "unit_fps": (0.5, 12.0),
    "card_fps": (0.5, 12.0),
}

FPS_KEYS: tuple[str, ...] = ("capture_fps", "change_fps", "unit_fps", "card_fps")
CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "perception_preset",
        "capture_fps",
        "change_fps",
        "unit_fps",
        "card_fps",
    }
)

# 旧配置键：兼容忽略（不再参与任何校验/生效），出现时由调用方记 warning。
LEGACY_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "hud_fps",
        "ocr_fps",
        "roi_auto_calibrate",
        "auto_calibrate",
        "roi_mode",
        "recording_enabled",
        "recording_max_mb",
        "recording_max_bytes",
        "recording_jpeg_quality",
        "recording_scope",
    }
)


class SettingsValidationError(ValueError):
    """A settings change was not accepted exactly as supplied."""


def legacy_config_warnings(config: Mapping[str, Any] | None) -> list[str]:
    """列出配置里出现但已被移除的旧键（供调用方记录 warning）。"""
    if not isinstance(config, Mapping):
        return []
    nested = config.get("kards")
    raw: Mapping[str, Any] = nested if isinstance(nested, Mapping) else config
    found = sorted(set(raw) & LEGACY_CONFIG_KEYS)
    return [f"legacy_key_ignored:{key}" for key in found]


def _number(value: Any, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise SettingsValidationError(f"{key} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise SettingsValidationError(f"{key} must be finite")
    return result


def _boolean(value: Any, key: str) -> bool:
    if not isinstance(value, bool):
        raise SettingsValidationError(f"{key} must be a boolean")
    return value


def _preset(value: Any) -> str:
    if not isinstance(value, str):
        raise SettingsValidationError("perception_preset must be a string")
    normalized = value.strip().lower()
    if normalized not in VALID_PRESETS:
        allowed = ", ".join(sorted(VALID_PRESETS))
        raise SettingsValidationError(
            f"perception_preset must be one of: {allowed}"
        )
    return normalized


def _validate_fps(value: Any, key: str) -> float:
    result = _number(value, key)
    lower, upper = SETTING_LIMITS[key]
    if not lower <= result <= upper:
        raise SettingsValidationError(
            f"{key} must be between {_fmt(lower)} and {_fmt(upper)}"
        )
    return result


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _check_task_rates(
    capture_fps: float,
    change_fps: float,
    unit_fps: float,
    card_fps: float,
) -> None:
    for key, value in (
        ("change_fps", change_fps),
        ("unit_fps", unit_fps),
        ("card_fps", card_fps),
    ):
        if value > capture_fps:
            raise SettingsValidationError(
                f"{key} ({_fmt(value)}) cannot exceed capture_fps "
                f"({_fmt(capture_fps)})"
            )


class _HybridValidateChanges:
    """Allow both class-level and instance-level ``validate_changes`` calls."""

    def __get__(self, instance: Any, owner: type["PerceptionSettings"]):
        def bound(
            changes: Mapping[str, Any],
            current: "PerceptionSettings | Mapping[str, Any] | None" = None,
        ) -> dict[str, Any]:
            if current is None and instance is not None:
                current = instance
            return owner._validate_changes_impl(changes, current)

        bound.__name__ = "validate_changes"
        bound.__doc__ = "Validate a change set; instance calls default to self."
        return bound


@dataclass(frozen=True)
class PerceptionSettings:
    """Validated perception cadence policy (capture/change/unit/card).

    ``preset='custom'`` is the only way to retain rates that do not match one
    of the three named preset tuples.  Constructing a named preset with a
    non-default rate tuple auto-selects custom instead of lying about the
    active preset.  录像与 ROI 校验字段已随对应功能删除。
    """

    preset: str = "balanced"
    capture_fps: float = 20.0
    change_fps: float = 10.0
    unit_fps: float = 4.0
    card_fps: float = 6.0
    evidence_enabled: bool = True

    PRESETS: ClassVar[dict[str, tuple[float, float, float, float]]] = PRESETS
    LIMITS: ClassVar[dict[str, tuple[float, float]]] = SETTING_LIMITS
    CONFIG_KEYS: ClassVar[frozenset[str]] = CONFIG_KEYS

    def __post_init__(self) -> None:
        preset = _preset(self.preset)
        capture = _validate_fps(self.capture_fps, "capture_fps")
        change = _validate_fps(self.change_fps, "change_fps")
        unit = _validate_fps(self.unit_fps, "unit_fps")
        card = _validate_fps(self.card_fps, "card_fps")
        evidence = _boolean(self.evidence_enabled, "evidence_enabled")

        values = (capture, change, unit, card)
        balanced = PRESETS["balanced"]
        if preset in PRESETS:
            expected = PRESETS[preset]
            if not _same_rates(values, expected):
                if _same_rates(values, balanced) and preset != "balanced":
                    # Constructing ``PerceptionSettings(preset='low_cost')``
                    # applies that preset's rates; callers need not repeat them.
                    capture, change, unit, card = expected
                else:
                    # Explicit advanced rates are never silently rounded back
                    # into a named preset.
                    preset = "custom"

        _check_task_rates(capture, change, unit, card)

        object.__setattr__(self, "preset", preset)
        object.__setattr__(self, "capture_fps", capture)
        object.__setattr__(self, "change_fps", change)
        object.__setattr__(self, "unit_fps", unit)
        object.__setattr__(self, "card_fps", card)
        object.__setattr__(self, "evidence_enabled", evidence)

    @property
    def perception_preset(self) -> str:
        """Config-key alias for :attr:`preset`."""
        return self.preset

    @property
    def rates(self) -> dict[str, float]:
        return {
            "capture_fps": self.capture_fps,
            "change_fps": self.change_fps,
            "unit_fps": self.unit_fps,
            "card_fps": self.card_fps,
        }

    def fps_for(self, task: str) -> float:
        if task == "capture":
            return self.capture_fps
        if task not in TASKS:
            raise ValueError(f"unknown perception task: {task!r}")
        return float(getattr(self, f"{task}_fps"))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly snapshot using config-key names.

        ``evidence_enabled`` 是固定诊断位，不在 :meth:`validate_changes` 的
        白名单里；旧 hud/recording/roi 键不再出现。
        """
        return {
            "perception_preset": self.preset,
            "capture_fps": self.capture_fps,
            "change_fps": self.change_fps,
            "unit_fps": self.unit_fps,
            "card_fps": self.card_fps,
            "evidence_enabled": self.evidence_enabled,
        }

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> "PerceptionSettings":
        """Build settings from a (possibly full/nested) config mapping.

        Unrelated config keys are ignored because callers often pass the whole
        plugin config; removed legacy keys (hud/ocr/roi/recording) likewise
        never influence the result.  Legacy ``perf_mode`` is honoured when no
        explicit preset or task-rate edits are present; new settings always
        win.  ``capture_fps`` is special because the legacy config used that
        same key: a pure legacy ``perf_mode`` mapping keeps its old capture
        rate out of the new schedule, while a config carrying
        ``perception_preset`` treats capture_fps as an explicit new-setting
        edit.
        """
        if config is None:
            return cls()
        if not isinstance(config, Mapping):
            raise SettingsValidationError("config must be a mapping")
        raw: Mapping[str, Any] = config
        nested = raw.get("kards")
        if isinstance(nested, Mapping):
            raw = nested

        if "perception_preset" in raw:
            preset = _preset(raw["perception_preset"])
        else:
            legacy_mode = str(raw.get("perf_mode", "")).strip().lower()
            if legacy_mode == "power_saver":
                preset = "low_cost"
            elif legacy_mode == "realtime":
                preset = "balanced"
            else:
                preset = "balanced"

        if preset in PRESETS:
            capture, change, unit, card = PRESETS[preset]
        else:
            capture, change, unit, card = (
                float(cls.capture_fps),
                float(cls.change_fps),
                float(cls.unit_fps),
                float(cls.card_fps),
            )

        # ``capture_fps`` existed in the legacy config too.  A legacy
        # perf_mode-only mapping keeps its old capture_fps out of the new
        # schedule, while a perception_preset or any task-rate edit opts the
        # capture rate into the new meaning (advanced changes auto-custom).
        raw_capture: float | None = None
        if "capture_fps" in raw:
            # Even a legacy-valued capture_fps must be a finite number in the
            # supported range; accepting it silently would hide a bad config.
            raw_capture = _validate_fps(raw["capture_fps"], "capture_fps")
        task_rate_edit = any(key in raw for key in FPS_KEYS[1:])
        explicit_fps = task_rate_edit or preset == "custom"
        if raw_capture is not None:
            # With a new preset or no legacy perf_mode, capture_fps is an
            # explicit new-setting edit.  A pure legacy mapping keeps its old
            # capture_fps out of the new schedule instead of reinterpreting it.
            if "perception_preset" in raw or "perf_mode" not in raw:
                explicit_fps = True
        supplied_rates: dict[str, float] = {}
        if explicit_fps:
            for key in FPS_KEYS:
                if key in raw:
                    supplied_rates[key] = _validate_fps(raw[key], key)
            capture = supplied_rates.get("capture_fps", capture)
            change = supplied_rates.get("change_fps", change)
            unit = supplied_rates.get("unit_fps", unit)
            card = supplied_rates.get("card_fps", card)
            if preset in PRESETS:
                if not all(
                    key not in supplied_rates
                    or supplied_rates[key] == PRESETS[preset][index]
                    for index, key in enumerate(FPS_KEYS)
                ):
                    preset = "custom"

        return cls(
            preset=preset,
            capture_fps=capture,
            change_fps=change,
            unit_fps=unit,
            card_fps=card,
        )

    validate_changes = _HybridValidateChanges()

    @classmethod
    def _validate_changes_impl(
        cls,
        changes: Mapping[str, Any],
        current: "PerceptionSettings | Mapping[str, Any] | None" = None,
    ) -> dict[str, Any]:
        """Validate an exact UI/config change set and return its effect.

        Unknown keys and fixed/read-only fields are rejected; removed legacy
        keys (hud/ocr/roi/recording) count as unknown here so callers surface
        the removal instead of silently dropping the edit.  Any advanced rate
        edit selects ``custom``.  When a named preset is selected the returned
        mapping includes all four resolved rates, making it safe to
        persist/merge without relying on a later fallback.
        """
        if not isinstance(changes, Mapping):
            raise SettingsValidationError("changes must be a mapping")
        unknown = sorted(set(changes) - CONFIG_KEYS)
        if unknown:
            raise SettingsValidationError(
                "unknown perception setting(s): " + ", ".join(unknown)
            )

        if current is None:
            base = cls()
        elif isinstance(current, cls):
            base = current
        elif isinstance(current, Mapping):
            base = cls.from_config(current)
        else:
            raise SettingsValidationError(
                "current must be PerceptionSettings, a mapping, or None"
            )

        preset_value: str | None = None
        if "perception_preset" in changes:
            preset_value = _preset(changes["perception_preset"])

        fps_changes: dict[str, float] = {}
        for key in FPS_KEYS:
            if key in changes:
                fps_changes[key] = _validate_fps(changes[key], key)

        schedule_touched = preset_value is not None or bool(fps_changes)
        if not schedule_touched:
            return {}

        if preset_value is not None:
            if preset_value in PRESETS:
                capture, change, unit, card = PRESETS[preset_value]
            else:
                capture, change, unit, card = (
                    base.capture_fps,
                    base.change_fps,
                    base.unit_fps,
                    base.card_fps,
                )
            resulting_preset = preset_value
        else:
            capture, change, unit, card = (
                base.capture_fps,
                base.change_fps,
                base.unit_fps,
                base.card_fps,
            )
            resulting_preset = "custom"

        values = {
            "capture_fps": capture,
            "change_fps": change,
            "unit_fps": unit,
            "card_fps": card,
        }
        values.update(fps_changes)

        if preset_value is not None and preset_value in PRESETS:
            expected = PRESETS[preset_value]
            if any(values[key] != expected[index] for index, key in enumerate(FPS_KEYS)):
                resulting_preset = "custom"

        if fps_changes and preset_value is None:
            resulting_preset = "custom"
        if preset_value == "custom":
            resulting_preset = "custom"

        _check_task_rates(
            values["capture_fps"],
            values["change_fps"],
            values["unit_fps"],
            values["card_fps"],
        )

        result: dict[str, Any] = {"perception_preset": resulting_preset}
        result.update(values)
        return result


def _same_rates(
    values: tuple[float, float, float, float],
    expected: tuple[float, float, float, float],
) -> bool:
    return all(abs(float(a) - float(b)) <= 1e-9 for a, b in zip(values, expected))


@dataclass
class _TaskState:
    actual: int = 0
    missed: int = 0
    last_frame_id: Any = None
    has_frame_id: bool = False
    last_accepted_at: float | None = None
    next_deadline: float | None = None
    durations: list[float] | None = None
    # 实测频率的滚动窗口：最近若干次接受时刻，span/次数 → measured fps。
    accepted_history: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.durations is None:
            self.durations = []


class PerceptionScheduler:
    """Independent change/unit/card deadlines over a shared capture stream.

    ``due(task, now=None, frame_id=None)`` only reads the injected clock and
    never calls wall-clock time.  Supplying a frame id is recommended: the
    same id can be accepted at most once per task.  If it is omitted, callers
    are responsible for not polling a task twice for one frame.
    """

    MAX_DURATIONS = 64
    MAX_ACCEPTED_HISTORY = 32

    def __init__(
        self,
        settings: PerceptionSettings | Mapping[str, Any] | None = None,
        clock: Any = time.monotonic,
    ) -> None:
        if settings is None:
            parsed = PerceptionSettings()
        elif isinstance(settings, PerceptionSettings):
            parsed = settings
        elif isinstance(settings, Mapping):
            parsed = PerceptionSettings.from_config(settings)
        else:
            raise SettingsValidationError(
                "settings must be PerceptionSettings, a mapping, or None"
            )
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._clock = clock
        self._settings = parsed
        self._tasks: dict[str, _TaskState] = {task: _TaskState() for task in TASKS}
        # Last accepted frame id per task is separate on purpose: one frame may
        # legitimately feed both the change and unit task.

    @property
    def settings(self) -> PerceptionSettings:
        return self._settings

    def _now(self) -> float:
        value = float(self._clock())
        if not math.isfinite(value):
            raise ValueError("clock returned a non-finite value")
        return value

    @staticmethod
    def _validate_task(task: str) -> str:
        if task not in TASKS:
            raise ValueError(
                f"unknown scheduler task: {task!r}; expected one of {TASKS}"
            )
        return task

    def update(self, settings: PerceptionSettings | Mapping[str, Any]) -> None:
        """Replace cadence settings without losing counters.

        Existing deadlines are re-derived from the last successful run so a
        rate increase can take effect immediately, while a rate decrease does
        not replay a burst of missed slots.
        """
        if isinstance(settings, Mapping):
            parsed = PerceptionSettings.from_config(settings)
        elif isinstance(settings, PerceptionSettings):
            parsed = settings
        else:
            raise SettingsValidationError(
                "settings must be PerceptionSettings or a mapping"
            )
        self._settings = parsed
        for task, state in self._tasks.items():
            interval = 1.0 / parsed.fps_for(task)
            if state.last_accepted_at is None:
                state.next_deadline = None
            else:
                # Derive from the last run so a shorter interval can take
                # effect immediately without replaying a missed backlog.
                state.next_deadline = state.last_accepted_at + interval

    def due(
        self,
        task: str,
        now: float | None = None,
        frame_id: Any = None,
    ) -> bool:
        """Return True when ``task`` should run for this call.

        The first call for a task is due immediately.  Subsequent calls use an
        independent deadline.  A repeated ``frame_id`` returns False without
        touching counters, frame ids or deadlines.
        """
        task = self._validate_task(task)
        state = self._tasks[task]
        current = self._now() if now is None else float(now)
        if not math.isfinite(current):
            raise ValueError("now must be finite")

        if (
            frame_id is not None
            and state.has_frame_id
            and frame_id == state.last_frame_id
        ):
            return False

        if state.next_deadline is not None and current < state.next_deadline:
            return False

        interval = 1.0 / self._settings.fps_for(task)
        if state.last_accepted_at is not None and interval > 0:
            elapsed = max(0.0, current - state.last_accepted_at)
            # Whole intervals that we slept through, excluding the slot now
            # being served.  This is telemetry only; no work is queued.
            missed = max(0, int(math.floor(elapsed / interval + 1e-9)) - 1)
            state.missed += missed

        state.actual += 1
        state.last_accepted_at = current
        state.next_deadline = current + interval
        state.accepted_history.append(current)
        if len(state.accepted_history) > self.MAX_ACCEPTED_HISTORY:
            del state.accepted_history[: len(state.accepted_history) - self.MAX_ACCEPTED_HISTORY]
        if frame_id is not None:
            state.last_frame_id = frame_id
            state.has_frame_id = True
        return True

    def mark_duration(self, task: str, seconds: float) -> None:
        """Record one measured task duration in seconds (optional)."""
        task = self._validate_task(task)
        duration = _number(seconds, "duration")
        if duration < 0.0:
            raise SettingsValidationError("duration must be non-negative")
        durations = self._tasks[task].durations
        assert durations is not None
        durations.append(duration)
        if len(durations) > self.MAX_DURATIONS:
            del durations[: len(durations) - self.MAX_DURATIONS]

    @staticmethod
    def _measured_fps(state: _TaskState) -> float | None:
        """滚动窗口实测频率：窗口内 (次数-1) / 时间跨度。"""
        history = state.accepted_history
        if len(history) < 2:
            return None
        span = history[-1] - history[0]
        if span <= 0.0:
            return None
        return round((len(history) - 1) / span, 3)

    def measured_fps(self) -> dict[str, float | None]:
        """{task: 实测Hz}，供状态面 fps_measured（与目标值并列展示）。"""
        return {task: self._measured_fps(state) for task, state in self._tasks.items()}

    def next_deadline(self, task: str) -> float | None:
        return self._tasks[self._validate_task(task)].next_deadline

    def snapshot(self) -> dict[str, Any]:
        """JSON-friendly counters/telemetry, copied under the current clock."""
        now = self._now()
        tasks: dict[str, Any] = {}
        total_actual = 0
        total_missed = 0
        for task in TASKS:
            state = self._tasks[task]
            durations = list(state.durations or [])
            duration_ms = [round(value * 1000.0, 3) for value in durations]
            total_actual += state.actual
            total_missed += state.missed
            tasks[task] = {
                "fps": self._settings.fps_for(task),
                "actual": state.actual,
                "missed": state.missed,
                "measured_fps": self._measured_fps(state),
                "durations": [round(value, 6) for value in durations],
                "durations_ms": duration_ms,
                "last_duration_ms": duration_ms[-1] if duration_ms else None,
                "average_duration_ms": (
                    round(sum(duration_ms) / len(duration_ms), 3)
                    if duration_ms
                    else None
                ),
                "next_deadline": state.next_deadline,
                "next_in_ms": (
                    round(max(0.0, state.next_deadline - now) * 1000.0, 1)
                    if state.next_deadline is not None
                    else None
                ),
                "last_accepted_at": state.last_accepted_at,
            }
        return {
            "settings": self._settings.to_dict(),
            "tasks": tasks,
            "stats": tasks,
            # Convenience aliases for dashboards/tests that read tasks directly
            # or want one column across all tasks.
            **tasks,
            "actual": {task: tasks[task]["actual"] for task in TASKS},
            "missed": {task: tasks[task]["missed"] for task in TASKS},
            "durations": {task: tasks[task]["durations"] for task in TASKS},
            "measured_fps": {task: tasks[task]["measured_fps"] for task in TASKS},
            "totals": {
                "actual": total_actual,
                "missed": total_missed,
            },
        }
