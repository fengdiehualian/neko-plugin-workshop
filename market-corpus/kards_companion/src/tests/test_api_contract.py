"""Main-entry API contract tests.

The host SDK is not installed in the test environment, so this module loads the
plugin package with a recording SDK stub. The stub records every decorator id
and returns a result object shaped like the real host ``Ok``/``Err`` wrappers.

The production ``__init__.py`` is the integration surface under test: entry ids,
UI action ids, LLM tool names, the runtime status schema, and the
capture-trust → detector → dispatcher wiring are pinned here.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import inspect
import json
import sys
import time
import types
from pathlib import Path
from typing import Any

import pytest
from kards_companion.perception.capture import CaptureResult, WindowInfo
from kards_companion.runtime.health import CaptureHealthSnapshot
from kards_companion.state.model import (
    E_HQ_DAMAGE,
    E_MATCH_START,
    E_ORDER_CARD,
    E_TURN_SWITCH,
    E_UNIT_PLAYED,
    SCENE_MATCH,
    BoardUnit,
    MatchState,
)
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "kards_companion_api_contract"
API_CALLS: list[tuple[str, str, dict[str, Any]]] = []


class _Base:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx


class _Result:
    """Minimal host result wrapper: ``.data``/``.value`` plus ``is_error()``."""

    def __init__(self, data: Any = None, error: str | None = None) -> None:
        self.data = data
        self.value = data
        self._error = error
        self.ok = error is None

    @property
    def error(self) -> str | None:
        return self._error

    def is_error(self) -> bool:
        return self._error is not None


def _recording_decorator(kind: str):
    def factory(**kwargs: Any):
        def decorate(func):
            func.__api_decorators__ = [
                *getattr(func, "__api_decorators__", []),
                (kind, dict(kwargs)),
            ]
            API_CALLS.append((func.__name__, kind, dict(kwargs)))
            return func

        return decorate

    return factory


def _build_recording_sdk() -> types.ModuleType:
    api = types.ModuleType("plugin.sdk.plugin")

    class _Ui:
        action = staticmethod(_recording_decorator("ui.action"))
        context = staticmethod(_recording_decorator("ui.context"))

    api.NekoPluginBase = _Base
    api.neko_plugin = lambda cls: cls
    api.plugin_entry = _recording_decorator("plugin_entry")
    api.lifecycle = _recording_decorator("lifecycle")
    api.llm_tool = _recording_decorator("llm_tool")
    api.message = _recording_decorator("message")
    api.ui = _Ui()
    api.Ok = lambda payload=None: _Result(data=payload)
    api.Err = lambda error: _Result(error=str(error))
    api.SdkError = type("SdkError", (Exception,), {})
    return api


def _load_main_module() -> types.ModuleType:
    sdk_pkg = sys.modules.get("plugin.sdk")
    assert sdk_pkg is not None, "conftest must install the SDK stub first"
    previous = sys.modules.get("plugin.sdk.plugin")
    previous_attr = getattr(sdk_pkg, "plugin", None)
    recording = _build_recording_sdk()
    sys.modules["plugin.sdk.plugin"] = recording
    sdk_pkg.plugin = recording

    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(PACKAGE, None)
        raise
    finally:
        if previous is None:
            sys.modules.pop("plugin.sdk.plugin", None)
        else:
            sys.modules["plugin.sdk.plugin"] = previous
        if previous_attr is not None:
            sdk_pkg.plugin = previous_attr
    return module


MAIN = _load_main_module()
PLUGIN_CLASS = MAIN.KardsCompanionPlugin


class FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, tuple[Any, ...]]] = []

    def _record(self, level: str, message: str, args: tuple[Any, ...]) -> None:
        self.records.append((level, message, args))

    def debug(self, message: str, *args: Any) -> None:
        self._record("debug", message, args)

    def info(self, message: str, *args: Any) -> None:
        self._record("info", message, args)

    def warning(self, message: str, *args: Any) -> None:
        self._record("warning", message, args)

    def error(self, message: str, *args: Any) -> None:
        self._record("error", message, args)


class FakeConfig:
    def __init__(self) -> None:
        self.payload: dict[str, Any] = {}
        self.updates: list[dict[str, Any]] = []
        self.dumps = 0

    async def dump(self, timeout: float | None = None) -> dict[str, Any]:
        self.dumps += 1
        return dict(self.payload)

    async def update(self, patch: dict[str, Any]) -> None:
        self.updates.append(patch)
        for section, values in patch.items():
            bucket = self.payload.setdefault(section, {})
            if isinstance(values, dict):
                bucket.update(values)


class FakeStore:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    async def get(self, key: str, default: Any = None) -> _Result:
        return _Result(self.values.get(key, default))


@pytest.fixture
def plugin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    logger = FakeLogger()
    pushes: list[dict[str, Any]] = []
    base = MAIN.NekoPluginBase
    monkeypatch.setattr(
        base,
        "enable_file_logging",
        lambda self, log_level=None: logger,
        raising=False,
    )
    monkeypatch.setattr(
        base,
        "data_path",
        lambda self, name: Path(tmp_path) / name,
        raising=False,
    )

    def record_push(self: Any, **kwargs: Any) -> dict[str, Any]:
        pushes.append(kwargs)
        return {"submitted": True}

    monkeypatch.setattr(base, "push_message", record_push, raising=False)

    instance = PLUGIN_CLASS(ctx=object())
    instance.config = FakeConfig()
    instance._events_log = []
    instance.pushes = pushes
    # These contracts target orchestration, not cadence.  Back-to-back ticks
    # have no wall-clock delay, so make every task due; scheduling itself is
    # covered by test_perception_scheduler_gates_expensive_tasks below.
    instance.scheduler.due = lambda *args, **kwargs: True
    # 同理：change 门控恒脏，让 unit/card 任务每 tick 都可推进（dHash 门控
    # 本身由 capture/调度测试覆盖）。
    monkeypatch.setattr(type(instance), "_change_dirty", _AlwaysDirty(),
                        raising=False)
    return instance


class _AlwaysDirty:
    """数据描述符：读恒 True、写忽略，供编排测试绕过 dHash 帧差门控。"""

    def __get__(self, obj, objtype=None):
        return True

    def __set__(self, obj, value):
        pass


# Tests that exercise the read/context channel with a fake UI detector must
# also fake the *capability table*; production weights are absent in tests and
# an unavailable UI model must not produce current match facts.
FAKE_UI_CAPABILITY_MODELS: dict[str, Any] = {
    "ui_det": {
        "state": "loaded", "loaded": True,
        "supported_classes": [
            "unit_board", "card_hand", "played_card", "frontline",
            "end_turn_button"],
        "unsupported_classes": [
            "hq", "end_turn_button_active", "screen_victory", "screen_defeat"],
        "diagnostics": "",
    },
    "card_cls": {"state": "loaded", "loaded": True},
    "trusted_card_count": 0,
}


def enable_fake_ui_capability(monkeypatch: pytest.MonkeyPatch,
                              plugin: Any) -> None:
    monkeypatch.setattr(
        plugin, "_models_payload",
        lambda: {**FAKE_UI_CAPABILITY_MODELS,
                 "ui_det": dict(FAKE_UI_CAPABILITY_MODELS["ui_det"]),
                 "card_cls": dict(FAKE_UI_CAPABILITY_MODELS["card_cls"])})


# ------------------------------------------------------------- declared surface

def test_declared_plugin_entries() -> None:
    entries = {
        (func, meta.get("id"))
        for func, kind, meta in API_CALLS
        if kind == "plugin_entry"
    }
    assert entries == {
        ("monitor_start", "monitor_start"),
        ("monitor_stop", "monitor_stop"),
        ("monitor_pause", "monitor_pause"),
        ("monitor_resume", "monitor_resume"),
        ("monitor_status", "monitor_status"),
        ("monitor_diagnostics", "monitor_diagnostics"),
        ("mode_set", "mode_set"),
        ("dry_run_set", "dry_run_set"),
        ("perception_settings_set", "perception_settings_set"),
        ("deck_import", "deck_import"),
        ("target_set", "target_set"),
        ("monitor_test_say", "monitor_test_say"),
    }
    for name, _entry_id in entries:
        assert inspect.iscoroutinefunction(getattr(PLUGIN_CLASS, name))


def test_declared_lifecycle_ui_actions_and_contexts() -> None:
    lifecycles = {
        (func, meta.get("id"))
        for func, kind, meta in API_CALLS
        if kind == "lifecycle"
    }
    assert lifecycles == {
        ("startup", "startup"),
        ("on_config_change", "config_change"),
        ("shutdown", "shutdown"),
    }

    actions = {
        (func, meta.get("id"))
        for func, kind, meta in API_CALLS
        if kind == "ui.action"
    }
    # Every UI action id must be the exact same id as its plugin entry.
    assert actions == {
        ("monitor_start", "monitor_start"),
        ("monitor_stop", "monitor_stop"),
        ("monitor_pause", "monitor_pause"),
        ("monitor_resume", "monitor_resume"),
        ("monitor_status", "monitor_status"),
        ("monitor_diagnostics", "monitor_diagnostics"),
        ("mode_set", "mode_set"),
        ("dry_run_set", "dry_run_set"),
        ("perception_settings_set", "perception_settings_set"),
        ("deck_import", "deck_import"),
        ("target_set", "target_set"),
        ("monitor_test_say", "monitor_test_say"),
    }
    action_ids = {meta.get("id") for _func, kind, meta in API_CALLS
                  if kind == "ui.action"}
    entry_ids = {meta.get("id") for _func, kind, meta in API_CALLS
                 if kind == "plugin_entry"}
    assert action_ids == entry_ids

    contexts = {
        (func, meta.get("id"))
        for func, kind, meta in API_CALLS
        if kind == "ui.context"
    }
    assert contexts == {("dashboard_context", "dashboard")}
    assert inspect.iscoroutinefunction(PLUGIN_CLASS.dashboard_context)


def test_declared_llm_tools() -> None:
    tools = {
        (func, meta.get("name"))
        for func, kind, meta in API_CALLS
        if kind == "llm_tool"
    }
    assert tools == {
        ("kards_get_live_state", "kards_get_live_state"),
        ("kards_search_cards", "kards_search_cards"),
        ("kards_import_deck", "kards_import_deck"),
        ("kards_get_match_history", "kards_get_match_history"),
    }
    for name, _tool_name in tools:
        assert inspect.iscoroutinefunction(getattr(PLUGIN_CLASS, name))
    for legacy in ("kards_live_state", "kards_card_info", "kards_match_history"):
        assert legacy not in {meta.get("name") for _f, _k, meta in API_CALLS}


def test_declared_chat_quiet_message_hook() -> None:
    hooks = {
        (func, meta.get("id"), meta.get("source"))
        for func, kind, meta in API_CALLS
        if kind == "message"
    }
    assert hooks == {("on_chat_message", "chat_quiet_window", "chat")}


def test_chat_hook_records_timing_only(plugin: Any) -> None:
    before = plugin.dispatcher.last_user_chat_at
    result = asyncio.run(plugin.on_chat_message(
        text="SECRET_USER_MESSAGE",
        _ctx={"lanlan_name": "lanlan"}))

    assert result.ok is True
    assert plugin.dispatcher.last_user_chat_at > before
    rendered = json.dumps(plugin.timeline.recent(10), ensure_ascii=False)
    assert "SECRET_USER_MESSAGE" not in rendered
    assert "lanlan" not in rendered
    assert any(entry["stage"] == "chat" for entry in plugin.timeline.recent(10))


# --------------------------------------------------------------- status schema

STATUS_KEYS = {
    # 旧字段：保持运行状态兼容
    "monitoring",
    "window_found",
    "foreground",
    "capture_backend",
    "capture_stats",
    "scene",
    "mode",
    "dry_run",
    "in_match",
    "game_number",
    "detector_loaded",
    "catalog",
    "deck",
    "recent_events",
}
RUNTIME_STATUS_KEYS = {
    "schema_version",
    "pipeline",
    "capture",
    "detectors",
    "delivery",
    "perception_settings",
    "models",
    "recognized_cards",
    "fps_measured",
    "timeline",
    "last_snapshot",
}
CATALOG_KEYS = {"ready", "cards", "fetched_at", "error", "builtin"}
CAPTURE_KEYS = {
    "status",
    "reason",
    "reading",
    "backend",
    "frame_id",
    "frame_age_ms",
    "width",
    "height",
    "frame",
    "trusted",
}
PIPELINE_KEYS = {
    "monitoring",
    "started",
    "loop_running",
    "paused",
    "pause_reason",
    "tick_seconds",
    "capture_fps",
    "mode",
    "dry_run",
}
DETECTOR_KEYS = {"registry", "last", "paused", "match_tracker"}
DELIVERY_KEYS = {"stats", "pending", "breaker", "mode"}


def test_get_status_schema_before_pipeline_start(plugin: Any) -> None:
    result = asyncio.run(plugin.monitor_status())

    assert result.ok is True
    payload = result.data
    assert STATUS_KEYS <= set(payload)
    assert RUNTIME_STATUS_KEYS <= set(payload)
    assert payload["schema_version"] == "kards_status_v2"
    assert CAPTURE_KEYS <= set(payload["capture"])
    assert PIPELINE_KEYS <= set(payload["pipeline"])
    assert DETECTOR_KEYS <= set(payload["detectors"])
    assert DELIVERY_KEYS <= set(payload["delivery"])
    assert payload["monitoring"] is False
    assert payload["window_found"] is False
    assert payload["foreground"] is False
    assert payload["capture_backend"] is None
    assert payload["capture_stats"] == {}
    assert payload["scene"] == MAIN.SCENE_UNKNOWN
    assert payload["mode"] == "companion"
    assert payload["dry_run"] is False
    assert payload["in_match"] is False
    assert payload["game_number"] == 0
    # Model loading depends on whether onnxruntime is installed in this test
    # environment; the schema must expose the boolean either way.
    assert isinstance(payload["detector_loaded"], bool)
    # OCR 全链路已移除：状态面不再出现 OCR 键。
    assert "ocr_available" not in payload
    assert "ocr_telemetry" not in payload
    assert "recording" not in payload
    assert set(payload["catalog"]) == CATALOG_KEYS
    assert payload["deck"] is None
    assert payload["recent_events"] == []
    # No trusted frame yet: recognition must be reported as paused.
    assert payload["capture"]["status"] == "stopped"
    assert payload["capture"]["reading"] is False
    assert payload["capture"]["occluded"] is None
    assert payload["capture"]["occlusion_detection"] == "unavailable"
    assert payload["pipeline"]["paused"] is True
    perception = payload["perception_settings"]
    assert perception["perception_preset"] == "balanced"
    assert perception["preset"] == "balanced"
    assert perception["fps"] == {
        "capture_fps": 20.0,
        "change_fps": 10.0,
        "unit_fps": 4.0,
        "card_fps": 6.0,
    }
    assert perception["presets"]["low_cost"] == {
        "capture_fps": 15.0, "change_fps": 8.0, "unit_fps": 2.0, "card_fps": 3.0}
    assert perception["presets"]["quality"] == {
        "capture_fps": 30.0, "change_fps": 12.0, "unit_fps": 6.0, "card_fps": 10.0}
    assert perception["caps"] == {
        "capture_fps": 30.0, "change_fps": 15.0, "unit_fps": 12.0, "card_fps": 12.0}
    assert perception["evidence_enabled"] is True
    assert "tracking_enabled" not in perception
    assert "frame_evidence_enabled" not in perception
    assert "regions" not in perception
    assert "hud_fps" not in perception
    assert "roi_auto_calibrate" not in perception
    assert "recording_enabled" not in perception
    # 双模型段：权重不可用时显式 unavailable；测试环境若装了 onnxruntime
    # 且权重存在则会真实加载，两态都必须是显式状态而不是抛错或假 ready。
    models = payload["models"]
    assert set(models) >= {"ui_det", "card_cls", "class_map_versions",
                           "trusted_card_count"}
    assert models["ui_det"]["state"] in {"unavailable", "loaded"}
    assert models["card_cls"]["state"] in {"unavailable", "loaded"}
    assert models["ui_det"]["nc"] == 10
    assert models["card_cls"]["nc"] == 1083
    assert models["trusted_card_count"] == 0
    assert payload["recognized_cards"] == []
    assert set(payload["fps_measured"]) == {"capture", "change", "unit", "card"}
    json.dumps(payload, ensure_ascii=False)


def test_get_status_reflects_runtime_objects(plugin: Any) -> None:
    plugin._monitoring = True
    plugin._capture = types.SimpleNamespace(
        window=object(),
        foreground=True,
        backend="dxcam",
        stats={"frames": 4, "errors": 0},
    )
    plugin._last_scene = "match"
    plugin._cfg = {**MAIN._CONFIG_DEFAULTS, "mode": "coach", "dry_run": True}

    payload = asyncio.run(plugin.monitor_status()).data

    assert payload["monitoring"] is True
    assert payload["window_found"] is True
    assert payload["foreground"] is True
    assert payload["capture_backend"] == "dxcam"
    assert payload["capture_stats"] == {"frames": 4, "errors": 0}
    assert payload["scene"] == "match"
    assert payload["mode"] == "coach"
    assert payload["dry_run"] is True
    json.dumps(payload, ensure_ascii=False)


def test_monitor_diagnostics_extended_timeline(plugin: Any) -> None:
    payload = asyncio.run(plugin.monitor_diagnostics()).data
    assert RUNTIME_STATUS_KEYS <= set(payload)
    assert set(payload["timeline"]) == {"recent", "dropped"}
    assert payload["last_snapshot"] is None


def test_dashboard_context_embeds_status_payload(plugin: Any) -> None:
    context = asyncio.run(plugin.dashboard_context())
    assert set(context) == {"status"}
    assert STATUS_KEYS <= set(context["status"])
    assert RUNTIME_STATUS_KEYS <= set(context["status"])


# ---------------------------------------------------------------- entry behaviour

def test_mode_set_rejects_unknown_and_updates_valid_mode(plugin: Any) -> None:
    rejected = asyncio.run(plugin.mode_set(mode="bogus"))
    assert rejected.ok is False
    assert "无效模式" in (rejected.error or "")
    assert plugin.arbiter.config.mode == "companion"
    assert plugin.config.updates == []

    accepted = asyncio.run(plugin.mode_set(mode=" Coach "))
    assert accepted.ok is True
    assert accepted.data == {"mode": "coach"}
    assert plugin.config.updates == [{"kards": {"mode": "coach"}}]
    assert plugin._cfg["mode"] == "coach"
    assert plugin.arbiter.config.mode == "coach"


def test_dry_run_set_updates_config_and_toggles(plugin: Any) -> None:
    result = asyncio.run(plugin.dry_run_set(enabled=True))
    assert result.ok is True and result.data == {"dry_run": True}
    assert plugin._cfg["dry_run"] is True
    assert plugin.config.updates[-1] == {"kards": {"dry_run": True}}

    toggled = asyncio.run(plugin.dry_run_set())
    assert toggled.ok is True and toggled.data == {"dry_run": False}
    assert plugin._cfg["dry_run"] is False
    assert plugin.config.updates[-1] == {"kards": {"dry_run": False}}


def test_perception_settings_set_rejects_unknown_and_out_of_range(plugin: Any) -> None:
    before = dict(plugin._cfg)
    rejected = asyncio.run(plugin.perception_settings_set(
        perception_preset="quality", unknown_key=True))
    assert rejected.ok is False
    assert plugin._cfg == before
    assert plugin.config.updates == []

    for key, cap in (
        ("capture_fps", 30), ("change_fps", 15),
        ("unit_fps", 12), ("card_fps", 12),
    ):
        result = asyncio.run(plugin.perception_settings_set(**{key: cap + 1}))
        assert result.ok is False, key
        assert plugin.config.updates == []

    bad_preset = asyncio.run(plugin.perception_settings_set(
        perception_preset="ultra"))
    assert bad_preset.ok is False
    # Strict A surface: no legacy alias or hidden toggle is accepted; removed
    # HUD/ROI/recording keys are rejected outright.
    for key, value in (
        ("preset", "quality"),
        ("detect_fps", 9),
        ("ocr_fps", 4),
        ("tracker_fps", 8),
        ("hud_fps", 4),
        ("roi_auto_calibrate", False),
        ("auto_calibrate", True),
        ("tracking_enabled", False),
        ("frame_evidence_enabled", False),
        ("recording_enabled", True),
        ("recording_max_mb", 512),
        ("recording_max_bytes", 512 * 1024 * 1024),
        ("recording_jpeg_quality", 90),
        ("recording_scope", "all"),
    ):
        result = asyncio.run(plugin.perception_settings_set(**{key: value}))
        assert result.ok is False, key
        assert plugin.config.updates == []


def test_perception_settings_set_applies_presets_and_custom_values(plugin: Any) -> None:
    accepted = asyncio.run(plugin.perception_settings_set(
        perception_preset="quality"))
    assert accepted.ok is True
    assert plugin._cfg["perception_preset"] == "quality"
    assert plugin._cfg["capture_fps"] == 30
    assert plugin._cfg["change_fps"] == 12
    assert plugin._cfg["unit_fps"] == 6
    assert plugin._cfg["card_fps"] == 10
    assert accepted.data["perception_settings"]["fps"] == {
        "capture_fps": 30.0, "change_fps": 12.0, "unit_fps": 6.0, "card_fps": 10.0,
    }
    assert plugin.config.updates[-1]["kards"]["perception_preset"] == "quality"

    custom = asyncio.run(plugin.perception_settings_set(
        perception_preset="balanced", capture_fps=12, change_fps=9))
    assert custom.ok is True
    assert plugin._cfg["perception_preset"] == "custom"
    assert plugin._cfg["capture_fps"] == 12
    assert plugin._cfg["change_fps"] == 9
    # Unspecified fields follow the explicitly requested preset baseline.
    assert plugin._cfg["unit_fps"] == 4
    assert plugin._cfg["card_fps"] == 6

    low = asyncio.run(plugin.perception_settings_set(
        perception_preset="low_cost"))
    assert low.ok is True
    assert [
        plugin._cfg[key] for key in MAIN.PERCEPTION_FPS_KEYS
    ] == [15, 8, 2, 3]


def test_perception_settings_removed_keys_are_rejected(plugin: Any) -> None:
    # ROI 校准与录像键已删除：动作白名单不再接受它们。
    for key, value in (
        ("roi_auto_calibrate", False),
        ("recording_enabled", True),
        ("recording_max_mb", 512),
    ):
        result = asyncio.run(plugin.perception_settings_set(**{key: value}))
        assert result.ok is False, key
        assert plugin.config.updates == []
    payload = asyncio.run(plugin.monitor_status()).data
    assert "recording" not in payload
    assert "recording_enabled" not in payload["perception_settings"]
    assert "roi_auto_calibrate" not in payload["perception_settings"]


def test_legacy_config_keys_are_ignored_with_warning(plugin: Any) -> None:
    # config.dump 路径（而非动作白名单）里出现的旧键：忽略并记 warning。
    def legacy_warnings() -> int:
        return sum(1 for record in plugin.logger.records
                   if "legacy_key_ignored" in str(record[1])
                   or any("legacy_key_ignored" in str(arg) for arg in record[2]))

    before = legacy_warnings()
    plugin._apply_config({"kards": {
        "tick_ms": 333,
        "hud_fps": 4,
        "recording_enabled": True,
        "recording_max_mb": 2048,
        "roi_auto_calibrate": False,
    }})
    assert legacy_warnings() > before
    assert plugin._cfg["perception_preset"] == "balanced"
    assert "hud_fps" not in plugin._cfg
    assert "recording_enabled" not in plugin._cfg


def test_deck_import_rejects_invalid_and_persists_valid(plugin: Any) -> None:
    rejected = asyncio.run(plugin.deck_import(code="not a deck code"))
    assert rejected.ok is False
    assert plugin._deck is None

    accepted = asyncio.run(plugin.deck_import(code="%%45|aabb;ccdd;ee"))
    assert accepted.ok is True
    assert accepted.data == {"main": "Soviet", "ally": "USA", "cards": 9}
    assert plugin._deck is not None
    assert plugin._deck.main_nation == "Soviet"
    assert plugin._deck.ally_nation == "USA"
    assert plugin.config.updates[-1]["kards"]["imported_deck_code"].startswith("%%45|")

    assert asyncio.run(plugin.monitor_status()).data["deck"] == {
        "main": "Soviet",
        "ally": "USA",
        "cards": 9,
    }


def test_kards_import_deck_tool_maps_ok_and_error(plugin: Any) -> None:
    ok = asyncio.run(plugin.kards_import_deck(code="%%31|aa"))
    assert ok["output"]["main"] == "Japan"
    assert ok["output"]["ally"] == "Germany"
    assert "is_error" not in ok

    failed = asyncio.run(plugin.kards_import_deck(code="bad"))
    assert failed["is_error"] is True
    assert "error" in failed["output"]


def test_kards_get_live_state_tool_schema(plugin: Any) -> None:
    payload = asyncio.run(plugin.kards_get_live_state())
    assert {
        "monitoring",
        "paused",
        "capture",
        "in_match",
        "state",
        "recent_events",
        "deck",
        "reason",
        "context_valid",
        "classification_valid",
        "session_id",
        "data_policy",
    } <= set(payload)
    assert payload["monitoring"] is False
    assert payload["paused"] is True
    assert payload["capture"]["reading"] is False
    assert payload["capture"]["occluded"] is None
    assert payload["in_match"] is False
    assert payload["state"] is None
    assert payload["recent_events"] == []
    assert payload["deck"] is None
    json.dumps(payload, ensure_ascii=False)


def test_kards_get_match_history_tool_schema(plugin: Any) -> None:
    plugin.store = FakeStore(
        {
            "matches": [{"game": 1, "result": "won"}],
            "records": {"overall": {"won": 1, "lost": 0}},
        }
    )
    payload = asyncio.run(plugin.kards_get_match_history(limit=5))
    assert payload == {
        "matches": [{"game": 1, "result": "won"}],
        "records": {"overall": {"won": 1, "lost": 0}},
    }
    json.dumps(payload, ensure_ascii=False)


# ------------------------------------------------------- perception integration

class FakeCapture:
    """Duck-typed ScreenCapture: one queued result/health per perception tick."""

    window = None
    foreground = True
    backend = "dxcam"
    stats: dict[str, int] = {}

    def __init__(self, results: list[Any], healths: list[CaptureHealthSnapshot]) -> None:
        self._results = list(results)
        self._healths = list(healths)
        self.capture_calls = 0

    def capture_frame(self) -> Any:
        self.capture_calls += 1
        return self._results.pop(0) if self._results else None

    def health_snapshot(self) -> CaptureHealthSnapshot:
        if not self._healths:
            return CaptureHealthSnapshot(status="stopped", reading=False)
        return self._healths.pop(0) if len(self._healths) > 1 else self._healths[0]


class FakeClassifier:
    """ScreenClassifier 替身：忽略检测框，直接回放 (scene, my_turn)。"""

    def __init__(self, scene: str = MAIN.SCENE_MATCH, my_turn: bool = True) -> None:
        self.scene = scene
        self.my_turn = my_turn
        self.calls: list[Any] = []

    def classify(self, detections: Any) -> tuple[str, bool | None]:
        self.calls.append(detections)
        return self.scene, self.my_turn


class FakeUiDetector:
    """UiDetector 替身：默认只回报一个 frontline 框（无单位、场景可用）。"""

    def __init__(self, detections: list[Any] | None = None) -> None:
        if detections is None:
            from kards_companion.perception.detector import Detection
            detections = [Detection(5, "frontline", 0.9, 0.0, 0.0, 8.0, 1.0)]
        self._detections = detections
        self.calls: list[Any] = []

    def detect(self, image: Any) -> list[Any]:
        self.calls.append(image)
        return list(self._detections)


class FakeHud:
    telemetry = {"calls": 0, "hits": 0}

    def __init__(self, readings: list[dict[str, Any]]) -> None:
        self._readings = list(readings)
        self.calls: list[Any] = []
        self.layout: Any = None

    def set_regions(self, layout: Any) -> None:
        self.layout = layout

    def read(self, image: Any) -> dict[str, Any]:
        self.calls.append(image)
        return self._readings.pop(0) if self._readings else {}


class FakeCardResult:
    def __init__(self, card_id: str, name: str, conf: float) -> None:
        self.card_id = card_id
        self.name = name
        self.conf = conf

    @property
    def known(self) -> bool:
        return self.card_id != "unknown"


class FakeCardClassifier:
    available = True

    def __init__(self, results: list[FakeCardResult] | None = None) -> None:
        self._results = list(results or [])
        self.calls: list[Any] = []

    def classify(self, crop: Any, appearance: Any = None) -> Any:
        self.calls.append((crop, appearance))
        if self._results:
            return self._results.pop(0)
        return FakeCardResult("unknown", "", 0.3)


def trusted_health(frame_id: int, width: int = 8, height: int = 8) -> CaptureHealthSnapshot:
    return CaptureHealthSnapshot(
        status="capturing", reason="", reading=True, backend="dxcam",
        frame_id=frame_id, frame_age_ms=5.0, width=width, height=height,
        foreground=True, visible=True,
    )


def blocked_health(reason: str = "window_not_found") -> CaptureHealthSnapshot:
    return CaptureHealthSnapshot(
        status=reason, reason=reason, reading=False, backend="dxcam",
        frame_id=0, frame_age_ms=None,
    )


def capture_result(frame_id: int, color: tuple[int, int, int]) -> CaptureResult:
    image = Image.new("RGB", (8, 8), color)
    return CaptureResult(
        frame_id=frame_id, captured_at=time.time(), width=8, height=8,
        backend="dxcam", image=image, foreground=True, source="dxcam",
    )


def test_perceive_once_pauses_recognition_without_trusted_frame(
        plugin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333, "chatter_interval_seconds": 0}
    enable_fake_ui_capability(monkeypatch, plugin)
    classifier = FakeClassifier()
    plugin.screen_classifier = classifier
    plugin.ui_detector = FakeUiDetector()
    plugin._capture = FakeCapture(
        [capture_result(1, (0, 0, 0)), None, None],
        [trusted_health(1), blocked_health(), blocked_health()],
    )

    plugin._perceive_once()  # trusted
    assert len(classifier.calls) == 1
    assert plugin.tracker.in_match and plugin.tracker.game_number == 1
    events_after_first = len(plugin._events_log)

    plugin._perceive_once()  # untrusted
    assert len(classifier.calls) == 1, "detection must not run without a trusted frame"
    assert len(plugin._events_log) == events_after_first
    assert plugin.tracker.game_number == 1
    assert plugin._previous_snapshot is not None
    assert plugin._previous_snapshot.trusted is False
    tombstones = [p for p in plugin.pushes
                  if p.get("metadata", {}).get("context_expired") is True
                  and p.get("metadata", {}).get("segment") == "core"]
    assert len(tombstones) == 1
    # The delivery owner's expire also restores the context slot; that is a
    # second slot transition, not a second perception pause.
    context_restores = [p for p in plugin.pushes
                        if p.get("metadata", {}).get("segment") == "context"
                        and p.get("metadata", {}).get("context_action") == "restore"]
    assert len(context_restores) <= 1
    paused = [e for e in plugin.timeline.recent(20)
              if e["stage"] == "capture" and e["outcome"] == "paused"]
    assert len(paused) == 1

    plugin._perceive_once()  # still untrusted: expire only once
    assert len(classifier.calls) == 1
    tombstones = [p for p in plugin.pushes
                  if p.get("metadata", {}).get("context_expired") is True
                  and p.get("metadata", {}).get("segment") == "core"]
    assert len(tombstones) == 1
    paused = [e for e in plugin.timeline.recent(20)
              if e["stage"] == "capture" and e["outcome"] == "paused"]
    assert len(paused) == 1

    assert asyncio.run(plugin.monitor_status()).data["pipeline"]["paused"] is True


def test_perceive_once_attaches_structured_frame_evidence(plugin: Any) -> None:
    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333, "chatter_interval_seconds": 0}
    plugin.screen_classifier = FakeClassifier()
    plugin.ui_detector = FakeUiDetector()
    plugin._capture_pause_active = True
    plugin._capture = FakeCapture(
        [capture_result(7, (0, 0, 0))], [trusted_health(7)])

    plugin._perceive_once()

    event = plugin._events_log[0]
    assert event["kind"] == E_MATCH_START
    evidence = event["details"]["evidence"]
    assert event["details"]["frame_id"] == 7
    assert evidence["frame_id"] == 7
    assert evidence["session_id"] == plugin._session_id
    assert evidence["seq"] == 1
    assert evidence["width"] == 8 and evidence["height"] == 8
    assert "image" not in json.dumps(event, ensure_ascii=False)
    resumed = [
        entry for entry in plugin.timeline.recent(20)
        if entry["stage"] == "capture" and entry["outcome"] == "resumed"
    ]
    assert resumed and resumed[-1]["detail"]["frame_id"] == 7


def test_frame_evidence_is_metadata_only(plugin: Any) -> None:
    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333, "chatter_interval_seconds": 0}
    plugin._apply_config({"kards": plugin._cfg})
    plugin.screen_classifier = FakeClassifier()
    plugin.ui_detector = FakeUiDetector()
    plugin._capture = FakeCapture(
        [capture_result(9, (0, 0, 0))], [trusted_health(9)])

    plugin._perceive_once()

    event = plugin._events_log[0]
    assert event["details"]["frame_id"] == 9
    evidence = event["details"]["evidence"]
    assert set(evidence) == {
        "frame_id", "captured_at", "session_id", "seq", "observed_at",
        "backend", "width", "height"}
    assert "image" not in json.dumps(event, ensure_ascii=False)


class FakeRecorder:
    """C-like recorder: start_match/set_active/submit/end_match/stop/snapshot."""

    def __init__(self) -> None:
        self.frames: list[int] = []
        self.seen: set[Any] = set()
        self.started: list[tuple[str, Any]] = []
        self.active_events: list[tuple[bool, str]] = []
        self.ended: list[str] = []
        self.stopped: list[str] = []
        self.active = False
        self.written = 0
        self.bytes = 0
        self.files = 0
        self.clean = True
        self.error = ""
        self.config: Any = None

    def update_config(self, config: Any) -> None:
        self.config = config
        self.active = False

    def start_match(self, session_id: str, match_id: Any) -> None:
        self.started.append((session_id, match_id))
        self.seen.clear()
        self.frames.clear()
        self.written = 0
        self.bytes = 0
        self.files = 0
        self.active = bool(getattr(self.config, "enabled", True))

    def set_active(self, active: bool, reason: str = "") -> None:
        self.active = bool(active)
        self.active_events.append((self.active, reason))

    def submit(self, frame: Any) -> bool:
        if not self.active:
            return False
        timestamp = float(getattr(frame, "captured_at", 0.0) or 0.0)
        frame_id = int(getattr(frame, "frame_id", 0) or 0)
        identity = timestamp if timestamp > 0 else frame_id
        if identity in self.seen:
            return False
        self.seen.add(identity)
        self.frames.append(frame_id)
        self.written += 1
        self.files += 1
        self.bytes += 100
        return True

    def end_match(self, reason: str = "match_end") -> None:
        self.ended.append(reason)
        self.active = False
        self.frames.clear()
        self.seen.clear()
        self.written = 0
        self.bytes = 0
        self.files = 0

    def stop(self, reason: str = "stopped") -> None:
        self.stopped.append(reason)
        self.active = False
        self.frames.clear()
        self.seen.clear()
        self.written = 0
        self.bytes = 0
        self.files = 0

    def snapshot(self) -> dict[str, Any]:
        if self.ended or self.stopped:
            state = "stopped" if self.stopped else "idle"
        elif self.active:
            state = "recording"
        else:
            state = "idle"
        return {
            "enabled": bool(getattr(self.config, "enabled", True)),
            "active": self.active,
            "state": state,
            "reason": "",
            "error": self.error,
            "bytes": self.bytes,
            "max_bytes": int(getattr(self.config, "max_bytes", 0) or 0),
            "written": self.written,
            "captured": self.written,
            "files": self.files,
            "dropped": 0,
            "pending": 0,
            "clean": self.clean,
        }


def frame_stub(frame_id: int) -> Any:
    window = WindowInfo(
        hwnd=4242, left=0, top=0, right=8, bottom=8,
        pid=99, process_name="kards.exe", title="KARDS")
    return types.SimpleNamespace(
        image=Image.new("RGB", (8, 8), (1, 2, 3)),
        frame_id=frame_id, captured_at=float(frame_id),
        width=8, height=8, backend="dxcam", source="dxcam",
        window=window, trusted=True)


def test_recording_pipeline_is_fully_removed(plugin: Any) -> None:
    # 录像链路删除契约：主类不再持有 recorder/回调/设置项。
    for attribute in ("recorder", "_recorder_io_lock", "_recording_match_live",
                      "_recording_in_match", "_recording_accepting",
                      "_on_capture_frame", "_start_recording_match",
                      "_end_recording_match", "_recording_payload"):
        assert not hasattr(plugin, attribute), attribute
    import inspect as _inspect
    source = _inspect.getsource(MAIN)
    for removed in ("TemporaryFrameRecorder", "RECORDING_JPEG_QUALITY_DEFAULT",
                    "recording_enabled"):
        assert removed not in source, removed


def test_unit_pipeline_writes_units_without_region_calibration(
        plugin: Any) -> None:
    # ROI 区域校准已随 OCR 链路移除：单位轨迹不再依赖 calibrator。
    detector_module = importlib.import_module(
        MAIN.__name__ + ".perception.detector")
    Detection = detector_module.Detection
    image = Image.new("RGB", (1920, 1080), (0, 0, 0))
    detections = [
        Detection(5, "frontline", 0.9, 860, 452, 1060, 552),
        Detection(0, "unit_board", 0.9, 1102, 598, 1202, 698),
    ]

    class FakeDetector:
        def detect(self, frame: Any) -> list[Any]:
            return detections

    plugin.ui_detector = FakeDetector()
    state = MatchState(scene=SCENE_MATCH, in_match=True)
    plugin._apply_detector(state, image, detections, frame_id=5)

    assert not hasattr(plugin, "region_calibrator")
    assert state.units
    unit = state.units[0]
    assert unit.bbox is not None and len(unit.bbox) == 4
    assert unit.slot is not None
    assert unit.first_seen_frame == 5
    payload = asyncio.run(plugin.monitor_status()).data
    assert payload["detectors"]["unit_tracker"]["frame_id"] == 5
    assert "regions" not in payload["perception_settings"]
    json.dumps(payload, ensure_ascii=False)


def test_perception_scheduler_gates_expensive_tasks_by_rate(
        plugin: Any) -> None:
    from kards_companion.perception.scheduling import (
        PerceptionScheduler,
        PerceptionSettings,
    )

    class Clock:
        def __init__(self) -> None:
            self.t = 0.0

        def __call__(self) -> float:
            return self.t

    clock = Clock()
    settings = PerceptionSettings(
        preset="custom", capture_fps=20.0, change_fps=5.0,
        unit_fps=2.0, card_fps=4.0)
    plugin.scheduler = PerceptionScheduler(settings, clock=clock)
    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333, "chatter_interval_seconds": 0}
    screen = FakeClassifier()
    plugin.screen_classifier = screen
    detector = FakeUiDetector()
    plugin.ui_detector = detector
    plugin._capture = FakeCapture(
        [capture_result(i, (i, i, i)) for i in range(1, 5)],
        [trusted_health(i) for i in range(1, 5)],
    )

    plugin._perceive_once()      # t=0: change + unit + card 全部到期
    clock.t = 0.10               # 无任务到期
    plugin._perceive_once()
    clock.t = 0.26               # card 到期，unit 未到（0.5s 周期）
    plugin._perceive_once()
    clock.t = 0.51               # unit 再次到期
    plugin._perceive_once()

    assert len(screen.calls) == 2, "场景推断只应随 unit 任务推进"
    assert len(detector.calls) == 2
    tasks = plugin.scheduler.snapshot()["tasks"]
    assert tasks["change"]["actual"] == 3   # 0.0/0.26/0.51（change 5fps=0.2s）
    assert tasks["unit"]["actual"] == 2     # unit 2fps=0.5s 周期
    assert tasks["card"]["actual"] == 3     # card 4fps=0.25s：0.0/0.26/0.51
    assert "hud" not in tasks


def test_scheduler_runs_units_between_change_ticks_and_keeps_state(
        plugin: Any) -> None:
    from kards_companion.perception.scheduling import (
        PerceptionScheduler,
        PerceptionSettings,
    )

    class Clock:
        def __init__(self) -> None:
            self.t = 0.0

        def __call__(self) -> float:
            return self.t

    class CountingDetector:
        def __init__(self) -> None:
            self.calls = 0

        def detect(self, _image: Any) -> list[Any]:
            self.calls += 1
            return []

    clock = Clock()
    settings = PerceptionSettings(
        preset="custom", capture_fps=20.0, change_fps=1.0,
        unit_fps=8.0, card_fps=4.0)
    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333, "chatter_interval_seconds": 0}
    plugin._apply_config({"kards": plugin._cfg})
    plugin.scheduler = PerceptionScheduler(settings, clock=clock)
    screen = FakeClassifier()
    plugin.screen_classifier = screen
    detector = CountingDetector()
    plugin.ui_detector = detector
    plugin._capture = FakeCapture(
        [capture_result(i, (i, i, i)) for i in range(1, 4)],
        [trusted_health(i) for i in range(1, 4)],
    )

    plugin._perceive_once()       # change + unit + card all due
    clock.t = 0.13                # change/card not due, unit due
    plugin._perceive_once()
    clock.t = 0.26                # unit + card due, change not due
    plugin._perceive_once()

    assert len(screen.calls) == 3, "unit task runs on its own 8 fps cadence"
    assert detector.calls == 3
    tasks = plugin.scheduler.snapshot()["tasks"]
    assert tasks["change"]["actual"] == 1
    assert tasks["unit"]["actual"] == 3
    assert plugin._pipeline_state.scene == SCENE_MATCH
    # HUD numbers are gone: my_hq stays None and is never filled with 0.
    assert plugin._pipeline_state.my_hq is None


def test_missing_capture_frame_id_is_generated_once_and_shared(
        plugin: Any) -> None:
    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333, "chatter_interval_seconds": 0}
    plugin._apply_config({"kards": plugin._cfg})
    plugin.screen_classifier = FakeClassifier()
    plugin.ui_detector = FakeUiDetector()
    plugin._capture = FakeCapture(
        [capture_result(0, (1, 1, 1)), capture_result(0, (2, 2, 2))],
        # Health may describe a newer producer frame; those ids must not be
        # borrowed for the image that is actually being processed.
        [trusted_health(77), trusted_health(78)],
    )
    used: list[tuple[str, Any]] = []

    def due(task: str, **kwargs: Any) -> bool:
        used.append((task, kwargs.get("frame_id")))
        return True

    plugin.scheduler.due = due
    # 单位观测以“模型不可得”的证据进入（场景推断仍由前线框驱动）。
    original_apply = plugin._apply_detector

    def apply_unavailable(state, image, detections, **kwargs):
        return original_apply(state, image, None, **kwargs)

    plugin._apply_detector = apply_unavailable
    plugin._perceive_once()
    first = plugin._previous_snapshot.frame.frame_id
    assert first > 0 and first not in {77, 78}
    assert plugin._events_log[0]["details"]["evidence"]["frame_id"] == first
    assert plugin._last_unit_diagnostics["frame_id"] == first
    assert plugin._pipeline_state.unit_observation_frame == first
    assert plugin._pipeline_state.unit_observation_valid is False
    first_used = list(used)
    used.clear()

    plugin._perceive_once()
    second = plugin._previous_snapshot.frame.frame_id
    assert second > first and second not in {77, 78}
    assert plugin._last_unit_diagnostics["frame_id"] == second
    assert plugin._pipeline_state.unit_observation_frame == second
    assert all(frame == first for _task, frame in first_used)
    assert all(frame == second for _task, frame in used)


def test_non_unit_tick_carries_units_without_advancing_unit_gates(
        plugin: Any) -> None:
    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333, "chatter_interval_seconds": 0}
    plugin._apply_config({"kards": plugin._cfg})
    plugin.screen_classifier = FakeClassifier()
    plugin.ui_detector = FakeUiDetector()
    plugin._capture = FakeCapture(
        [capture_result(i, (i, i, i)) for i in range(1, 5)],
        [trusted_health(i) for i in range(1, 5)],
    )
    seeded = MatchState(
        scene=SCENE_MATCH, in_match=True, my_hq=20, enemy_hq=20)
    plugin.scenario.update(seeded)
    plugin.tracker.update(seeded)
    plugin._pipeline_state = MatchState(
        scene=SCENE_MATCH, in_match=True, my_hq=20, enemy_hq=20,
        units=[BoardUnit(
            instance_id=1, side="mine", lane="front",
            health=0, trajectory=True, track_confirmed=True)],
    )
    plugin.scheduler.due = lambda task, **kwargs: task in {"change", "hud"}

    for _ in range(4):
        plugin._perceive_once()

    assert plugin._pipeline_state.unit_observation_valid is False
    assert plugin._pipeline_state.unit_observation_frame == 0
    assert plugin.tracker.snapshot()["units"] == 0
    kinds = [record["kind"] for record in plugin._events_log]
    assert "unit_died" not in kinds
    assert "unit_played" not in kinds


def _unit_detection(x1: float = 2.0, y1: float = 2.0,
                    x2: float = 5.0, y2: float = 5.0) -> Any:
    module = sys.modules[MAIN.__name__ + ".perception.detector"]
    return module.Detection(0, "unit_board", 0.9, x1, y1, x2, y2)


class SequencedDetector:
    def __init__(self, observations: list[Any]) -> None:
        self.observations = list(observations)
        self.calls = 0

    def detect(self, _image: Any) -> Any:
        self.calls += 1
        value = self.observations.pop(0) if self.observations else []
        if isinstance(value, Exception):
            raise value
        return value


def _unit_pipeline(plugin: Any, detector: Any, count: int) -> None:
    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333, "chatter_interval_seconds": 0}
    plugin._apply_config({"kards": plugin._cfg})
    plugin.screen_classifier = FakeClassifier()
    plugin.ui_detector = detector
    plugin._capture = FakeCapture(
        [capture_result(i, (i, i, i)) for i in range(1, count + 1)],
        [trusted_health(i) for i in range(1, count + 1)],
    )


def _detection(name: str, cls_id: int, box: tuple[float, float, float, float]) -> Any:
    module = sys.modules[MAIN.__name__ + ".perception.detector"]
    return module.Detection(cls_id, name, 0.9, *box)


def test_played_zoom_is_region_observation_never_an_order(
        plugin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MAIN, "CARD_CROP_MIN_SIDE", 2)
    front = _detection("frontline", 5, (0.0, 2.0, 8.0, 3.0))
    played = _detection("played_card", 3, (1.0, 1.0, 6.0, 6.0))
    detector = SequencedDetector([[front], [front, played], [front, played]])
    _unit_pipeline(plugin, detector, 3)
    # Fake trusted whitelist for this test only; production whitelist stays 0.
    plugin._trusted_card_count = 2
    plugin.card_classifier = FakeCardClassifier([
        FakeCardResult("t34_1942", "T-34 1942型", 0.91),
        FakeCardResult("t34_1942", "T-34 1942型", 0.92),
    ])

    for _ in range(3):
        plugin._perceive_once()

    state = plugin._pipeline_state
    assert state.last_order_card is None
    assert E_ORDER_CARD not in [record["kind"] for record in plugin._events_log]

    zoom = [item for item in plugin._recognized_cards_payload()
            if item["region"] == "played_card"]
    assert [item["card_id"] for item in zoom] == ["t34_1942", "t34_1942"]
    assert all("instance_id" not in item for item in zoom)
    observation_ids = [item["observation_id"] for item in zoom]
    assert len(set(observation_ids)) == len(observation_ids)
    assert all(str(item["observation_id"]).startswith("card:") for item in zoom)
    frame_ids = [item["frame_id"] for item in zoom]
    assert len(set(frame_ids)) == len(frame_ids)
    assert all(int(frame) > 0 for frame in frame_ids)


def test_zoom_never_names_new_board_units_or_borrows_card_stats(
        plugin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MAIN, "CARD_CROP_MIN_SIDE", 2)
    front = _detection("frontline", 5, (0.0, 2.0, 8.0, 3.0))
    played = _detection("played_card", 3, (1.0, 1.0, 6.0, 6.0))
    enemy = _detection("unit_board", 0, (2.0, 0.0, 5.0, 1.0))
    mine = _detection("unit_board", 0, (2.0, 6.0, 5.0, 7.0))
    detector = SequencedDetector([
        [front],
        [front, played],
        [front, played, enemy, mine],
        [front, played, enemy, mine],
    ])
    _unit_pipeline(plugin, detector, 4)
    plugin.card_classifier = FakeCardClassifier([
        FakeCardResult("t34_1942", "T-34 1942型", 0.91),
        FakeCardResult("t34_1942", "T-34 1942型", 0.91),
        FakeCardResult("t34_1942", "T-34 1942型", 0.91),
    ])

    for _ in range(4):
        plugin._perceive_once()

    state = plugin._pipeline_state
    assert state.last_order_card is None
    assert E_ORDER_CARD not in [record["kind"] for record in plugin._events_log]
    assert {unit.side for unit in state.units} == {"mine", "enemy"}
    for unit in state.units:
        assert unit.card_id is None
        assert unit.card_name is None
        assert unit.name_confidence == 0.0
        assert unit.attack is None
        assert unit.health is None
        assert unit.cost is None
    assert not [item for item in plugin._recognized_cards_payload()
                if item.get("region") == "board"]


def test_unknown_turn_is_carried_as_none_and_never_switches(
        plugin: Any) -> None:
    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333, "chatter_interval_seconds": 0}
    plugin._apply_config({"kards": plugin._cfg})
    front = _detection("frontline", 5, (0.0, 2.0, 8.0, 3.0))
    _unit_pipeline(plugin, SequencedDetector([[front], [front], [front]]), 3)
    plugin.screen_classifier = FakeClassifier(my_turn=None)

    for _ in range(3):
        plugin._perceive_once()

    assert plugin._pipeline_state.my_turn is None
    assert plugin._pipeline_state.to_public_dict()["my_turn"] is None
    assert not [record for record in plugin._events_log
                if record["kind"] == E_TURN_SWITCH]


def test_valid_empty_detections_lose_observation_but_never_die(plugin: Any) -> None:
    detection = _unit_detection()
    detector = SequencedDetector([[detection], [detection], [], [], []])
    _unit_pipeline(plugin, detector, 5)

    for _ in range(5):
        plugin._perceive_once()

    losses = plugin._unit_tracker.recent_losses()
    assert losses and all(item["kind"] == "unit_lost" for item in losses)
    assert plugin._last_unit_diagnostics["lost"]
    assert plugin._last_unit_diagnostics["frame_id"] == 5
    assert plugin._last_unit_diagnostics["observation_valid"] is True
    assert plugin._pipeline_state.unit_observation_valid is True
    assert plugin._pipeline_state.unit_observation_frame == 5
    assert not [
        event for event in plugin._events_log if event["kind"] == "unit_died"
    ]
    # The last known track is no longer part of the observed board, but the
    # loss record must remain diagnostics-only.
    assert plugin._pipeline_state.units == []


def test_model_unavailable_preserves_last_known_units_without_miss(
        plugin: Any) -> None:
    detection = _unit_detection()
    detector = SequencedDetector([
        [detection], [detection], RuntimeError("onnx down"), None,
    ])
    _unit_pipeline(plugin, detector, 4)

    plugin._perceive_once()
    plugin._perceive_once()
    known = plugin._last_unit_diagnostics
    assert known["detector_available"] is True
    unit_id = plugin._pipeline_state.units[0].instance_id

    plugin._perceive_once()  # detector exception -> None, not []
    state = plugin._pipeline_state
    assert state.units and state.units[0].instance_id == unit_id
    assert state.units[0].observed is False
    assert state.units[0].miss_count == 0
    error_diag = plugin._last_unit_diagnostics
    assert error_diag["detector_available"] is False
    assert error_diag["observation_valid"] is False
    assert error_diag["status"].startswith("detector_error")
    assert error_diag["lost"] == []
    assert plugin._unit_tracker.recent_losses() == []
    assert state.unit_observation_valid is False
    assert state.unit_observation_frame == 3

    plugin._perceive_once()  # detector returns None -> still unavailable
    assert plugin._last_unit_diagnostics["status"] == "model_unavailable"
    assert plugin._unit_tracker.recent_losses() == []
    assert plugin._pipeline_state.units
    assert plugin._pipeline_state.units[0].observed is False
    assert plugin._pipeline_state.unit_observation_valid is False
    assert plugin._pipeline_state.unit_observation_frame == 4


def test_perceive_once_respond_and_read_flow_through_dispatcher(
        plugin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin._monitoring = True
    plugin._cfg = {
        "tick_ms": 333, "chatter_interval_seconds": 0,
        "user_chat_quiet_window_seconds": 0.0,
        "battle_output_quiet_window_seconds": 0.0,
        "battle_output_backpressure_seconds": 0.0,
    }
    plugin._apply_config({"kards": plugin._cfg})
    enable_fake_ui_capability(monkeypatch, plugin)
    plugin.arbiter.update(MAIN.ArbiterConfig(
        mode="companion", min_priority=1,
        cooldown_seconds=0.0, critical_cooldown_seconds=0.0))
    from kards_companion.policy.safety import OutputSafetyConfig, OutputSafetyGuard
    plugin.dispatcher._guard = OutputSafetyGuard(OutputSafetyConfig(
        min_output_interval_seconds=0.0, critical_cooldown_seconds=0.0))
    module = sys.modules[MAIN.__name__ + ".perception.detector"]
    board = module.Detection(0, "unit_board", 0.9, 2.0, 4.0, 5.0, 7.0)
    front = module.Detection(5, "frontline", 0.9, 0.0, 0.0, 8.0, 1.0)
    # 首帧空棋盘（match_start 建空 baseline），随后两帧单位登场确认。
    detector = SequencedDetector([[front], [front, board], [front, board]])
    plugin.screen_classifier = FakeClassifier()
    plugin.ui_detector = detector
    plugin._capture = FakeCapture(
        [capture_result(1, (0, 0, 0)), capture_result(2, (255, 255, 255)),
         capture_result(3, (20, 20, 20))],
        [trusted_health(1), trusted_health(2), trusted_health(3)],
    )

    plugin._perceive_once()  # match_start；单位第 1 帧（待确认）
    plugin._perceive_once()  # 单位第 2 帧确认 -> unit_played
    plugin._perceive_once()  # 平稳 tick

    assert plugin.dispatcher.stats()["submitted"] == 2
    responds = [p for p in plugin.pushes if p.get("ai_behavior") == "respond"]
    assert [p["metadata"]["kind"] for p in responds] == [E_MATCH_START, E_UNIT_PLAYED]
    assert responds[0]["visibility"] == []
    assert responds[0]["coalesce_key"] == "kards:battle_event"
    assert responds[0]["metadata"]["battle_reply_contract"] == "short_tts_line"
    reads = [p for p in plugin.pushes if p.get("ai_behavior") == "read"]
    assert reads, "live state must be published through the dispatcher read channel"
    assert all(read["visibility"] == [] for read in reads)
    # Context enter is a direct publisher push (one per session/token), not a
    # dispatcher read submission.  The core live-state reads are counted.
    core_reads = [
        p for p in reads
        if p.get("metadata", {}).get("kind") == "game_live_state"
        and p.get("metadata", {}).get("segment") == "core"]
    assert core_reads
    assert plugin.dispatcher.stats()["read_submitted"] == len(core_reads)
    assert all(not p.get("metadata", {}).get("context_expired") for p in reads)


def test_configured_target_is_wired_to_read_and_respond(
        plugin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KARDS_TARGET_LANLAN", raising=False)
    # Manual publish helper needs a loaded UI capability for a valid situation
    # read; production weights are absent in tests.
    monkeypatch.setattr(plugin, "_models_payload", lambda: {
        "ui_det": {"state": "loaded", "loaded": True,
                   "supported_classes": ["end_turn_button"],
                   "unsupported_classes": []},
        "card_cls": {"state": "loaded", "loaded": True},
        "trusted_card_count": 0,
    })
    plugin._apply_config({"kards": {
        "target_lanlan": "lanlan",
        "reply_max_chars": 28,
        "battle_event_ttl_seconds": 12.0,
    }})
    assert plugin.dispatcher.config.target_lanlan == "lanlan"

    plugin._push_live_state(MatchState(
        scene=MAIN.SCENE_MATCH, in_match=True, my_hq=20, enemy_hq=20))
    read = [p for p in plugin.pushes if p.get("ai_behavior") == "read"][-1]
    assert read["target_lanlan"] == "lanlan"
    assert read["metadata"]["target_lanlan"] == "lanlan"

    event = MAIN.GameEvent(
        kind=E_HQ_DAMAGE, priority=5, summary="hq",
        details={"game": 1, "target_lanlan": "event-target"})
    plugin.dispatcher.submit(event, MatchState())
    assert plugin.dispatcher.drain()[0].status == "submitted"
    respond = [p for p in plugin.pushes if p.get("ai_behavior") == "respond"][-1]
    assert respond["target_lanlan"] == "event-target"
    assert respond["visibility"] == []


def test_perceive_once_dry_run_writes_events_only(plugin: Any, tmp_path: Path) -> None:
    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333, "dry_run": True, "chatter_interval_seconds": 0}
    plugin._apply_config({"kards": plugin._cfg})
    plugin.screen_classifier = FakeClassifier()
    plugin.ui_detector = FakeUiDetector()
    plugin._capture = FakeCapture(
        [capture_result(1, (10, 20, 30))], [trusted_health(1)])

    plugin._perceive_once()

    assert plugin.pushes == []
    assert plugin.tracker.current is not None
    assert plugin.tracker.current.my_hq is None  # HUD 数字恒 None
    assert plugin.dispatcher.stats()["simulated"] == 1
    log_path = tmp_path / "events.jsonl"
    assert log_path.is_file()
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert [record["kind"] for record in records] == [E_MATCH_START]
    # Dry-run still records the delivery decision instead of silently skipping
    # the dispatcher; only the host push is short-circuited.
    simulated = [entry for entry in plugin.timeline.recent(20)
                  if entry["outcome"] == "simulated"]
    assert simulated or plugin.dispatcher.stats()["simulated"] >= 1


# ------------------------------------------------------- privacy / live tools

def test_public_status_config_is_allowlisted(plugin: Any) -> None:
    plugin._cfg["api_key"] = "SECRET_CONFIG_VALUE"
    plugin._cfg["custom_prompt"] = "SECRET_PROMPT"
    payload = asyncio.run(plugin.monitor_status()).data
    assert "api_key" not in payload["config"]
    assert "custom_prompt" not in payload["config"]
    assert "SECRET" not in json.dumps(payload["config"], ensure_ascii=False)
    assert payload["config"]["target_lanlan_present"] is False
    for key in (
        "perception_preset", "capture_fps", "change_fps",
        "unit_fps", "card_fps",
    ):
        assert key in payload["config"]
    for removed in ("hud_fps", "roi_auto_calibrate", "recording_enabled",
                    "recording_max_mb"):
        assert removed not in payload["config"]
    for hidden in ("tracking_enabled", "frame_evidence_enabled"):
        assert hidden not in payload["config"]
    assert payload["perception_settings"]["evidence_enabled"] is True
    json.dumps(payload, ensure_ascii=False)


def test_llm_live_state_hides_details_when_consent_or_dry_run_blocks(
        plugin: Any) -> None:
    plugin._apply_config({"kards": {
        "llm_data_consent": False,
    }})
    blocked = asyncio.run(plugin.kards_get_live_state())
    assert blocked["state"] is None
    assert blocked["recent_events"] == []
    assert blocked["deck"] is None
    assert blocked["paused"] is True
    assert blocked["reason"] == "consent_required"
    assert blocked["data_policy"]["export_allowed"] is False

    plugin._apply_config({"kards": {"llm_data_consent": True, "dry_run": True}})
    dry = asyncio.run(plugin.kards_get_live_state())
    assert dry["state"] is None
    assert dry["recent_events"] == []
    assert dry["deck"] is None
    assert dry["reason"] == "dry_run"


def test_live_state_requires_fresh_trusted_match_context(plugin: Any) -> None:
    from kards_companion.runtime.snapshot import FrameMeta, RuntimeSnapshot

    plugin._monitoring = True
    state = MatchState(scene=MAIN.SCENE_MATCH, in_match=True, my_hq=20)
    plugin.tracker.update(state)
    plugin._session_id = plugin.tracker.session_id or "session"
    plugin.tracker.set_session_id(plugin._session_id)
    plugin._last_scene = MAIN.SCENE_MATCH
    plugin._last_trusted_snapshot = RuntimeSnapshot(
        seq=7, observed_at=time.time() - 10.0,
        health=trusted_health(7), frame=FrameMeta(frame_id=7, valid=True),
        state=state, image=object())

    payload = asyncio.run(plugin.kards_get_live_state())
    assert payload["state"] is None
    assert payload["context_valid"] is False
    assert payload["reason"] == "context_stale"
    assert payload["context_age_ms"] > 1000
    assert payload["snapshot_seq"] == 7


# ------------------------------------------------------------- test say gate

def test_monitor_test_say_requires_real_target_and_never_fakes_receipt(
        plugin: Any) -> None:
    from kards_companion.policy.safety import OutputSafetyConfig, OutputSafetyGuard
    plugin.guard = OutputSafetyGuard(OutputSafetyConfig(
        min_output_interval_seconds=0.0, critical_cooldown_seconds=0.0))
    plugin.dispatcher._guard = plugin.guard
    missing = asyncio.run(plugin.monitor_test_say(authorized_real_test=True))
    assert missing.ok is False

    denied = asyncio.run(plugin.monitor_test_say(
        target="lanlan", authorized_real_test=False))
    assert denied.ok is False

    plugin._apply_config({"kards": {"target_lanlan": "lanlan"}})
    result = asyncio.run(plugin.monitor_test_say(
        target="lanlan", authorized_real_test=True))
    assert result.ok is True
    assert result.data["accepted"] is True
    assert result.data["sdk_submission"] == "accepted"
    assert result.data["non_game"] is True
    assert result.data["history"] is None
    assert result.data["llm_generated"] == "unknown"
    assert result.data["tts_played"] == "unknown"
    test_push = plugin.pushes[-1]
    assert test_push["ai_behavior"] == "respond"
    assert test_push["target_lanlan"] == "lanlan"
    assert test_push["metadata"]["non_game"] is True
    assert test_push["metadata"]["no_history"] is True
    assert test_push["metadata"]["history_available"] is False
    assert test_push["metadata"]["delivery_intent"] == "connection_test"
    assert test_push["metadata"]["max_reply_chars"] == 28
    text = test_push["parts"][0]["text"]
    assert "KARDS 陪玩解说边界" in text
    assert "JSON" in text

    # The real test is consumed; a future explicit grant is a new user action.
    assert plugin.gate.snapshot()["real_test_used"] is True


def test_monitor_test_say_hard_denies_dry_run_and_consent_off(plugin: Any) -> None:
    plugin._apply_config({"kards": {"target_lanlan": "lanlan", "dry_run": True}})
    dry = asyncio.run(plugin.monitor_test_say(
        target="lanlan", authorized_real_test=True))
    assert dry.ok is False

    plugin._apply_config({"kards": {
        "target_lanlan": "lanlan", "dry_run": False,
        "llm_data_consent": False}})
    no_consent = asyncio.run(plugin.monitor_test_say(
        target="lanlan", authorized_real_test=True))
    assert no_consent.ok is False
    assert plugin.pushes == []
