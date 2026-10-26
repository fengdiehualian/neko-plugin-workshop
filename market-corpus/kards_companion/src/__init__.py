"""KARDS 猫娘陪玩插件主类。

被动读屏（dxcam/ImageGrab）→ 可信帧门控 → 场景前置门控 → 结构化局势与单位履历
→ 优先级事件解说。无可信帧（窗口缺失/最小化/画面过期）时识别与解说安全暂停。

所有宿主推送（read/respond/context/expire/test）都经过同一个 HostPushGate。
生命周期使用 epoch + gate context token：感知 tick 在入口绑定 token，stop/reopen
之后旧 tick 在 gate 的提交锁内被拒绝。主 ``_lifecycle_lock`` 从不跨越宿主 push，
stop 的 ``begin_stop`` 在锁外线程中执行，避免与在途 push 互等。

目标运行时：宿主冻结 Python 3.11。只读屏：不读内存、不抓包、不代操作。
"""

import asyncio
import copy
import inspect
import json
import os
import threading
import time
import uuid
from typing import Any

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
    ui,
)

from .adapters.target import clean_target_lanlan, resolve_target_lanlan
from .delivery.arbiter import ArbiterConfig, CommentaryArbiter
from .delivery.contracts import (
    STATUS_ACCEPTED as STATUS_ACCEPTED,
)
from .delivery.contracts import (
    STATUS_LEGACY_UNCONFIRMED as STATUS_LEGACY_UNCONFIRMED,
)
from .delivery.contracts import (
    STATUS_UNKNOWN as STATUS_UNKNOWN,
)
from .delivery.contracts import (
    GateResult,
    HostPushConfig,
    HostPushGate,
)
from .delivery.publisher import (
    LiveStatePublisher,
    build_commentary_prompt,
    build_live_state_text,
)
from .knowledge.catalog import CardCatalog, CatalogError
from .knowledge.deck_code import DeckCodeError, ParsedDeck, parse_deck_code
from .perception.capture import (
    ScreenCapture,
    dhash,
    hamming,
)
from .perception.detector import UnitTracker
from .perception.models import (
    CARD_CLS_PATH,
    UI_DET_PATH,
    CardClassifier,
    UiDetector,
    load_trusted_card_count,
)
from .perception.scheduling import (
    FPS_KEYS,
    PerceptionScheduler,
    PerceptionSettings,
    SettingsValidationError,
    legacy_config_warnings,
)
from .perception.screens import ScreenClassifier
from .policy.dispatcher import DeliveryDispatcher, DispatcherConfig
from .policy.safety import (
    OutputSafetyConfig,
    OutputSafetyGuard,
    SafetyCircuitBreaker,
    SafetyConfig,
)
from .runtime.contracts import normalize_event
from .runtime.detectors import DetectorRegistry
from .runtime.health import CaptureHealthSnapshot
from .runtime.match_adapter import MatchTrackerAdapter
from .runtime.scenario import KardsScenarioResolver
from .runtime.situation import (
    SITUATION_FRESH_MAX_AGE_SECONDS,
    build_situation,
    situation_context_text,
    situation_payload,
)
from .runtime.snapshot import FrameMeta, RuntimeSnapshot
from .runtime.timeline import RuntimeTimeline
from .settings import KardsSettings as KardsSettings
from .settings import _message_hook
from .state.model import (
    E_CHATTER,
    E_MATCH_END,
    SCENE_DEFEAT,
    SCENE_MATCH,
    SCENE_MENU,
    SCENE_UNKNOWN,
    SCENE_VICTORY,
    GameEvent,
    MatchState,
)
from .state.tracker import MatchTracker

PLUGIN_ID = "kards_companion"
STATUS_SCHEMA_VERSION = "kards_status_v2"
_LIVE_STATE_TOOL_NAME = "kards_get_live_state"
# 双模型常量：models/ui_det.onnx（界面检测）与 models/card_cls.onnx（卡牌分类）；
# 旧七类检测权重（kards-det）不再被加载。
_MODEL_PATH = UI_DET_PATH  # 兼容别名：仅指向新界面检测模型
_CARD_CLS_MODEL_PATH = CARD_CLS_PATH
_VALID_SCENES = frozenset({SCENE_MENU, SCENE_MATCH, SCENE_VICTORY, SCENE_DEFEAT})

# A's runtime.scheduling is the single source of truth for preset values and
# hard caps.  Order is capture / change / unit / card.
PERCEPTION_FPS_KEYS: tuple[str, ...] = FPS_KEYS
PERCEPTION_FPS_PRESETS: dict[str, dict[str, float]] = {
    name: dict(zip(PERCEPTION_FPS_KEYS, values))
    for name, values in PerceptionSettings.PRESETS.items()
}
PERCEPTION_FPS_CAPS: dict[str, float] = {
    key: float(limits[1])
    for key, limits in PerceptionSettings.LIMITS.items()
    if key in PERCEPTION_FPS_KEYS
}
PERCEPTION_FPS_MINS: dict[str, float] = {
    key: float(limits[0])
    for key, limits in PerceptionSettings.LIMITS.items()
    if key in PERCEPTION_FPS_KEYS
}
PERCEPTION_PRESET_VALUES = frozenset({*PERCEPTION_FPS_PRESETS, "custom"})
PERCEPTION_SETTINGS_ACTION_KEYS = frozenset(PerceptionSettings.CONFIG_KEYS)
# 卡牌任务：裁图短边小于该像素数直接跳过（过小裁图分类不可信）。
CARD_CROP_MIN_SIDE = 40
# A completely static screen must still produce independent UI samples; the
# heartbeat re-runs UiDetector at most ~1s apart and never reuses a frame id.
UI_OBSERVATION_HEARTBEAT_SECONDS = 1.0
# 检测区域 -> 白名单外观键（trusted_cards.json 的 appearance 维度）。
CARD_APPEARANCE_BY_REGION = {"card_hand": "hand_full", "played_card": "zoom_full"}
# UI boxes that can support a scene fact; their confidence is the only real
# scene confidence source (never a fabricated 1.0).
SCENE_SIGNAL_CLASSES = frozenset({
    "frontline", "end_turn_button", "end_turn_button_active",
    "screen_victory", "screen_defeat",
})
# 状态面 recognized_cards 的保留上限。
RECOGNIZED_CARDS_LIMIT = 20

# Config defaults are centralized so the manifest, runtime snapshot and tests
# cannot drift.  Explicit user values always win; these are only fallbacks.
_CONFIG_DEFAULTS: dict[str, Any] = {
    "mode": "companion",
    "dry_run": False,
    "auto_start_monitoring": True,
    "llm_data_consent": True,
    "chatter_interval_seconds": 60.0,
    "cooldown_seconds": 25.0,
    "critical_cooldown_seconds": 5.0,
    "min_priority": 4,
    "do_not_disturb": False,
    "perf_mode": "realtime",
    "tick_ms": 333.0,
    "perception_preset": "balanced",
    "capture_fps": 20.0,
    "change_fps": 10.0,
    "unit_fps": 4.0,
    "card_fps": 6.0,
    "capture_fps_power_saver": 4.0,
    "catalog_enabled": False,
    "catalog_url": "",
    "catalog_refresh_hours": 24.0,
    "vlm_enabled": False,
    "target_lanlan": "",
    "plugin_owned_output_enabled": False,
    "user_chat_quiet_window_seconds": 60.0,
    "battle_output_quiet_window_seconds": 30.0,
    "battle_output_backpressure_seconds": 20.0,
    "reply_max_chars": 28,
    "battle_event_ttl_seconds": 12.0,
    "imported_deck_code": "",
}


def _num(value: Any, default: float, *, minimum: float | None = None,
         maximum: float | None = None) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        out = float(default)
    if minimum is not None:
        out = max(minimum, out)
    if maximum is not None:
        out = min(maximum, out)
    return out


def _integer(value: Any, default: int, *, minimum: int | None = None,
             maximum: int | None = None) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        out = int(default)
    if minimum is not None:
        out = max(minimum, out)
    if maximum is not None:
        out = min(maximum, out)
    return out


def _boolean(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _tick_seconds(cfg: dict[str, Any]) -> float:
    return max(0.15, _num(cfg.get("tick_ms"), 333.0) / 1000.0)


def _legacy_perception_aliases(raw: dict[str, Any]) -> dict[str, Any]:
    """Map provisional/legacy names onto A's canonical scheduling keys.

    已移除功能的旧键（hud_fps/ocr_fps/roi_*/recording_*）不映射、不生效；
    :func:`_log_legacy_config_warnings` 负责把它们记成 warning。
    """
    mapped = dict(raw)
    aliases = {
        "detect_fps": "change_fps",
        "tracker_fps": "unit_fps",
    }
    for old, new in aliases.items():
        if new not in mapped and old in mapped:
            mapped[new] = mapped[old]
    if str(mapped.get("perception_preset") or "").strip().lower() == "low":
        mapped["perception_preset"] = "low_cost"
    return mapped


def _perception_settings_from_config(cfg: dict[str, Any]) -> PerceptionSettings:
    """Parse the canonical settings, failing closed to safe defaults."""
    try:
        return PerceptionSettings.from_config(_legacy_perception_aliases(cfg))
    except SettingsValidationError:
        return PerceptionSettings()
    except Exception:
        return PerceptionSettings()


def _perception_read_model(settings: PerceptionSettings,
                           cfg: dict[str, Any]) -> dict[str, Any]:
    """Serializable read model shared by status, settings and the UI."""
    del cfg  # Settings are the single source of truth; no shadow toggles.
    return {
        "perception_preset": settings.preset,
        "preset": settings.preset,
        "fps": settings.rates,
        "capture_fps": settings.capture_fps,
        "change_fps": settings.change_fps,
        "unit_fps": settings.unit_fps,
        "card_fps": settings.card_fps,
        "evidence_enabled": settings.evidence_enabled,
    }


def _capture_fps(cfg: dict[str, Any]) -> float:
    return float(_perception_settings_from_config(cfg).capture_fps)


def _loop_seconds(cfg: dict[str, Any]) -> float:
    """Poll at least as fast as the capture rate, capped by legacy tick_ms."""
    capture_interval = 1.0 / max(0.5, _capture_fps(cfg))
    return max(0.02, min(_tick_seconds(cfg), capture_interval))


def _result_payload(result: Any) -> Any:
    """Unwrap Ok/Err across SDK objects and the test stub's dict shape."""
    if isinstance(result, dict):
        if result.get("ok") is False:
            return result.get("error", result)
        return result.get("data", result.get("value"))
    for name in ("data", "value"):
        value = getattr(result, name, None)
        if value is not None:
            return value
    return None


def _result_is_error(result: Any) -> bool:
    if isinstance(result, dict):
        return result.get("ok") is False
    for name in ("is_error", "is_err"):
        probe = getattr(result, name, None)
        if callable(probe):
            try:
                if probe():
                    return True
            except Exception:
                pass
    return False


def _store_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    inner = getattr(value, "value", None)
    if inner is not None:
        return inner
    if isinstance(value, dict) and "ok" in value:
        return value.get("data", default)
    return value


def _supports_kwarg(func: Any, name: str) -> bool:
    try:
        return name in inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False


@neko_plugin
class KardsCompanionPlugin(NekoPluginBase):
    """生命周期、感知循环调度与对外 entry/tool 面。"""

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.logger = self.enable_file_logging(
            log_level=(os.environ.get("NEKO_LOG_LEVEL") or "INFO").strip().upper())
        self._cfg: dict[str, Any] = dict(_CONFIG_DEFAULTS)
        self._perception_settings = _perception_settings_from_config(self._cfg)
        self.scheduler = PerceptionScheduler(
            self._perception_settings, clock=time.monotonic)
        self._scheduler_lock = threading.Lock()
        self._monitoring = False
        self._started_on_loop = False
        self._startup_armed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._capture: ScreenCapture | None = None
        self._loop_task: asyncio.Task | None = None
        self._catalog_task: asyncio.Task | None = None
        # 双模型加载层：文件缺失/契约不符时进入显式 unavailable/rejected 状态。
        self.ui_detector = UiDetector(_MODEL_PATH)
        self.card_classifier = CardClassifier(_CARD_CLS_MODEL_PATH)
        self._ui_detect_status = "ok"
        self.screen_classifier = ScreenClassifier()
        self._trusted_card_count = load_trusted_card_count()
        self._unit_tracker = UnitTracker()
        self._unit_lock = threading.Lock()
        self._last_unit_diagnostics: dict[str, Any] = {}
        # Missing/zero capture ids get a unique positive id from this
        # lifecycle-guarded counter, used by both the scheduler and evidence.
        self._capture_frame_seq = 0
        self._deck: ParsedDeck | None = None
        self._applied_catalog_url: str | None = None
        self._events_log: list[dict[str, Any]] = []
        self._last_detector_diagnostics: list[dict[str, Any]] = []
        self._pipeline_started = False
        self._last_start_error = ""
        self._capture_pause_active = False
        self._pause_reason = ""
        self._user_paused = False
        self._last_scene = SCENE_UNKNOWN
        self._classification_valid = False
        self._confirmed_exit_handled = False
        self._pending_rearm: list[GameEvent] = []

        # 卡牌任务跨 tick 状态：最近一次 unit 检测的待分类框与消费水位。
        # _card_lock 保护 pending 队列与 recognized_cards 列表。
        self._card_lock = threading.Lock()
        self._card_consumed_frame = 0
        self._card_pending_frame = 0
        self._card_pending_at = 0.0
        self._card_pending_session = ""
        self._card_pending: list[tuple[str, Any]] = []  # (region, Detection)
        self._card_image: Any = None
        self._recognized_cards: list[dict[str, Any]] = []
        # Latest independent UI-detector sample.  These are source timestamps,
        # never publication time; a static capture kept being consumed must not
        # make the situation look freshly observed.
        self._last_ui_observation_at: float | None = None
        self._last_ui_observation_mono: float | None = None
        self._last_ui_frame_id = 0
        self._last_ui_signals: list[str] = []
        self._last_ui_scene_confidence: float | None = None
        self._ui_observation_failed = False
        self._ui_rebase_pending = False
        # Read-context entry idempotence (session + gate token + fingerprint).
        self._read_context_entered_session = ""
        self._read_context_entered_token = ""
        self._read_context_entered_fingerprint = ""
        # change 任务门控：dHash 指纹与“画面已变化待处理”标志。
        self._change_hash: int | None = None
        self._change_dirty = True
        # capture 实测频率：帧计数差 / 墙钟差。
        self._capture_rate_probe: tuple[float, int] | None = None

        # Lifecycle mutex: guards session ownership (capture/tasks/epoch) and
        # the in-memory pipeline state.  It must NEVER be held across a host
        # push or a gate begin_stop call.
        self._lifecycle_lock = threading.RLock()
        self._epoch = 0
        self._ticks_in_flight = 0
        self._ticks_idle = threading.Event()
        self._ticks_idle.set()
        # Serializes async lifecycle transitions across event loops.  The lock
        # is acquired in an executor so holding it across awaits never blocks
        # the asyncio command loop.
        self._transition_guard = threading.Lock()
        self._session_id = ""
        self.catalog = CardCatalog(self.data_path("cards.json"))

        # Runtime identity: the installed plugin may be ``kards_companion_dev``
        # or any other real id.  Never label a dev install as the formal id.
        self._runtime_id, self._source_identity = self._resolve_runtime_identity()

        self.timeline = RuntimeTimeline()
        self.scenario = KardsScenarioResolver()
        self.tracker = MatchTracker()
        self.match_detector = MatchTrackerAdapter(
            self.tracker,
            chatter_interval_s=60.0,
            source="match_tracker",
            ttl_s=None,
        )
        self.arbiter = CommentaryArbiter(ArbiterConfig())
        self.gate = HostPushGate(
            self.push_message,
            config=HostPushConfig(
                dry_run=False, consent=True, simulated=False,
                authorized_real_test=False),
            now_fn=time.time,
        )
        self.publisher = LiveStatePublisher(
            gate=self.gate,
            logger=self.logger,
            source=self._runtime_id,
            now_fn=time.time,
            refresh_seconds=30.0,
            max_age_seconds=60.0,
        )
        self.breaker = SafetyCircuitBreaker(SafetyConfig(), now_fn=time.time)
        self.read_breaker = SafetyCircuitBreaker(SafetyConfig(), now_fn=time.time)
        self.guard = OutputSafetyGuard(OutputSafetyConfig(), now_fn=time.time)
        self.dispatcher = DeliveryDispatcher(
            self.arbiter,
            gate=self.gate,
            read_publisher=self.publisher,
            breaker=self.breaker,
            read_breaker=self.read_breaker,
            safety_guard=self.guard,
            config=self._dispatcher_config(),
            now_fn=time.time,
            timeline=self.timeline,
            logger=self.logger,
            target_resolver=lambda event: resolve_target_lanlan(self, event),
            on_delivered=self._on_delivered,
        )

        self.detectors = DetectorRegistry()
        self.detectors.add(self.match_detector)
        self._snapshot_seq = 0
        self._previous_snapshot: RuntimeSnapshot | None = None
        self._last_trusted_snapshot: RuntimeSnapshot | None = None
        self._last_capture_health = CaptureHealthSnapshot()

        # Persistence futures are drained on shutdown/stop instead of being
        # fire-and-forget across a closing command loop.
        self._persist_futures: set[Any] = set()
        self._pending_persist: list[dict[str, Any]] = []
        self._persisted_match_keys: set[str] = set()

        # Cross-tick canonical state.  Unit/card work is scheduled independently
        # of classification, so a tick that skips one task must carry the last
        # known value forward instead of feeding a false empty board to the
        # tracker.
        self._pipeline_state = MatchState()

    # ------------------------------------------------------------- identity

    def _resolve_runtime_identity(self) -> tuple[str, str]:
        for value in (getattr(self, "plugin_id", ""),
                      getattr(getattr(self, "ctx", None), "plugin_id", "")):
            text = str(value or "").strip()
            if text:
                return text[:160], "sdk"
        return PLUGIN_ID, "manifest_fallback"

    @property
    def runtime_plugin_id(self) -> str:
        return self._runtime_id

    # ------------------------------------------------------------------ 配置

    def _dispatcher_config(self, *, authorized_real_test: bool = False) -> DispatcherConfig:
        target = clean_target_lanlan(self._cfg.get("target_lanlan"))
        kwargs: dict[str, Any] = {
            "source": self._runtime_id,
            "target_lanlan": target,
            "plugin_owned_output_enabled": _boolean(
                self._cfg.get("plugin_owned_output_enabled"), False),
            "reply_max_chars": _integer(self._cfg.get("reply_max_chars"), 28,
                                        minimum=1, maximum=200),
            "battle_event_ttl_seconds": _num(
                self._cfg.get("battle_event_ttl_seconds"), 12.0,
                minimum=0.0, maximum=120.0),
            "user_chat_quiet_window_seconds": _num(
                self._cfg.get("user_chat_quiet_window_seconds"), 60.0,
                minimum=0.0, maximum=600.0),
            "battle_output_quiet_window_seconds": _num(
                self._cfg.get("battle_output_quiet_window_seconds"), 30.0,
                minimum=0.0, maximum=600.0),
            "battle_output_backpressure_seconds": _num(
                self._cfg.get("battle_output_backpressure_seconds"), 20.0,
                minimum=0.0, maximum=600.0),
            "dry_run": _boolean(self._cfg.get("dry_run"), False),
            "llm_data_consent": _boolean(self._cfg.get("llm_data_consent"), True),
            "simulated": False,
            "authorized_real_test": bool(authorized_real_test),
            "known_targets": (target,) if target else (),
        }
        accepted = {
            field.name for field in getattr(DispatcherConfig, "__dataclass_fields__", {}).values()
        } if hasattr(DispatcherConfig, "__dataclass_fields__") else set(kwargs)
        return DispatcherConfig(**{key: value for key, value in kwargs.items()
                                   if key in accepted})

    def _apply_config(self, cfg: dict[str, Any]) -> None:
        raw = cfg.get("kards") if isinstance(cfg.get("kards"), dict) else cfg
        raw = dict(raw or {})
        normalized: dict[str, Any] = {}
        for key, default in _CONFIG_DEFAULTS.items():
            value = raw.get(key, default)
            if isinstance(default, bool):
                normalized[key] = _boolean(value, default)
            elif isinstance(default, int):
                normalized[key] = _integer(value, default)
            elif isinstance(default, float):
                normalized[key] = _num(value, default)
            else:
                normalized[key] = "" if value is None else str(value)
        normalized["mode"] = str(normalized.get("mode") or "companion").strip().lower()
        if normalized["mode"] not in ("companion", "coach", "silent"):
            normalized["mode"] = "companion"
        normalized["perf_mode"] = str(normalized.get("perf_mode") or "realtime").strip().lower()
        if normalized["perf_mode"] not in ("realtime", "power_saver"):
            normalized["perf_mode"] = "realtime"
        normalized["min_priority"] = _integer(normalized.get("min_priority"), 4,
                                              minimum=1, maximum=9)
        normalized["reply_max_chars"] = _integer(normalized.get("reply_max_chars"), 28,
                                                 minimum=1, maximum=200)
        normalized["target_lanlan"] = clean_target_lanlan(normalized.get("target_lanlan"))

        # OCR/录像时代的旧键只记 warning，不再参与任何生效路径。
        for warning in legacy_config_warnings(raw):
            self.logger.warning("legacy config key ignored: {}", warning)

        perception_cfg = _legacy_perception_aliases(raw)
        try:
            perception = PerceptionSettings.from_config(perception_cfg)
        except SettingsValidationError as exc:
            self.logger.warning("perception settings rejected, using defaults: {}", exc)
            perception = PerceptionSettings()
        normalized["perception_preset"] = perception.preset
        for key in PERCEPTION_FPS_KEYS:
            normalized[key] = float(getattr(perception, key))

        previous = dict(self._cfg)
        self._cfg = normalized
        self._perception_settings = perception
        try:
            with self._scheduler_lock:
                self.scheduler.update(perception)
        except Exception as exc:
            self.logger.warning("perception scheduler update failed: {}: {}",
                                type(exc).__name__, exc)

        self.arbiter.update(ArbiterConfig(
            mode=normalized["mode"],
            do_not_disturb=normalized["do_not_disturb"],
            llm_data_consent=normalized["llm_data_consent"],
            min_priority=normalized["min_priority"],
            cooldown_seconds=_num(normalized.get("cooldown_seconds"), 25.0,
                                  minimum=1.0, maximum=300.0),
            critical_cooldown_seconds=_num(
                normalized.get("critical_cooldown_seconds"), 5.0,
                minimum=1.0, maximum=300.0),
            max_event_age_seconds=_num(
                normalized.get("battle_event_ttl_seconds"), 12.0,
                minimum=0.0, maximum=120.0),
        ))
        self.match_detector.chatter_interval_s = _num(
            normalized.get("chatter_interval_seconds"), 60.0,
            minimum=0.0, maximum=600.0)

        endpoint = str(normalized.get("catalog_url") or "").strip()
        if endpoint != (self._applied_catalog_url or ""):
            try:
                self.catalog = CardCatalog(self.data_path("cards.json"), endpoint=endpoint)
                self._applied_catalog_url = endpoint
            except CatalogError as exc:
                self.logger.warning("catalog_url rejected, keep previous source: {}", exc)

        code = str(normalized.get("imported_deck_code") or "")
        if code:
            try:
                self._deck = parse_deck_code(code)
            except DeckCodeError as exc:
                self.logger.warning("stored deck invalid: {}", exc)

        # One config object drives dispatcher, gate consent/dry state and the
        # publisher's gate view.  Any change purges the pending slot inside C.
        try:
            self.dispatcher.update_config(self._dispatcher_config())
        except Exception as exc:
            self.logger.warning("dispatcher config update failed: {}: {}",
                                type(exc).__name__, exc)

        if self._capture is not None:
            try:
                self._capture.fps = _capture_fps(self._cfg)
            except Exception:
                pass
        target_changed = previous.get("target_lanlan") != normalized.get("target_lanlan")
        read_keys = ("mode", "llm_data_consent", "dry_run", "target_lanlan",
                     *PERCEPTION_FPS_KEYS, "perception_preset")
        read_changed = any(
            previous.get(key) != normalized.get(key) for key in read_keys)
        if self._monitoring and (target_changed or read_changed):
            # Any read-relevant policy/perception change invalidates the local
            # cursor.  The publisher's gate still decides whether a cleanup
            # tombstone may actually be pushed (consent=false / dry-run = no
            # host push), so this never bypasses the hard data policy.
            self._expire_read_context(
                "target_changed" if target_changed else "config_changed",
                token=self.gate.get_token())

    # ------------------------------------------------------------- 感知设置面

    def _measured_fps_payload(self) -> dict[str, Any]:
        """capture/change/unit/card 实测频率（与目标值并列展示）。"""
        with self._scheduler_lock:
            measured = dict(self.scheduler.measured_fps())
        measured["capture"] = self._measured_capture_fps()
        return measured

    def _measured_capture_fps(self) -> float | None:
        """采集实测频率：capture.stats 帧计数差 / 墙钟差。"""
        capture = self._capture
        if capture is None:
            return None
        now = time.monotonic()
        try:
            frames = int(capture.stats.get("frames", 0) or 0)
        except Exception:
            return None
        probe_at, probe_frames = self._capture_rate_probe or (None, None)
        if probe_at is None:
            self._capture_rate_probe = (now, frames)
            return None
        span = now - probe_at
        if span < 1.0:
            return None
        self._capture_rate_probe = (now, frames)
        delta = frames - int(probe_frames or 0)
        if delta <= 0:
            return None
        return round(delta / span, 3)

    @staticmethod
    def _model_status(model: Any, *, role: str) -> dict[str, Any]:
        """读取模型状态；测试桩/异常实现退化为最小诊断视图。"""
        try:
            status = dict(model.status())
        except Exception:
            status = {
                "role": role,
                "state": "unavailable",
                "loaded": False,
                "diagnostics": "status_unavailable",
            }
        status.setdefault("role", role)
        return status

    def _models_payload(self) -> dict[str, Any]:
        """状态面 models 段：双模型加载态 + 类别表版本 + 可信卡数。"""
        ui_status = self._model_status(self.ui_detector, role="ui_detect")
        card_status = self._model_status(self.card_classifier, role="card_classify")
        return {
            "ui_det": ui_status,
            "card_cls": card_status,
            "class_map_versions": {
                "ui_classes": ui_status.get("class_map_version"),
                "card_classes": card_status.get("class_map_version"),
            },
            "trusted_card_count": int(self._trusted_card_count),
        }

    def _recognized_cards_payload(self) -> list[dict[str, Any]]:
        with self._card_lock:
            return [dict(item) for item in self._recognized_cards[-RECOGNIZED_CARDS_LIMIT:]]

    def _perception_settings_payload(self) -> dict[str, Any]:
        settings = self._perception_settings
        with self._scheduler_lock:
            task_snapshot = self.scheduler.snapshot()
        tasks = {}
        for task, payload in task_snapshot.get("tasks", {}).items():
            tasks[task] = {
                "fps": payload.get("fps"),
                "actual": payload.get("actual"),
                "missed": payload.get("missed"),
                "last_duration_ms": payload.get("last_duration_ms"),
                "next_in_ms": payload.get("next_in_ms"),
            }
        return {
            **_perception_read_model(settings, self._cfg),
            "presets": {
                name: dict(values) for name, values in PERCEPTION_FPS_PRESETS.items()
            },
            "caps": dict(PERCEPTION_FPS_CAPS),
            "mins": dict(PERCEPTION_FPS_MINS),
            "scheduler": {
                "settings": settings.to_dict(),
                "tasks": tasks,
                "measured_fps": self._measured_fps_payload(),
            },
        }

    async def _reload_config(self) -> None:
        try:
            cfg = await self.config.dump(timeout=5.0)
        except Exception as exc:
            self.logger.warning("config dump failed: {}: {}", type(exc).__name__, exc)
            return
        self._apply_config(cfg if isinstance(cfg, dict) else {})

    @lifecycle(id="startup")
    async def startup(self, **_):
        await self._reload_config()
        self._startup_armed = True
        # Never start long-lived tasks on the SDK's transient startup loop.
        self._maybe_schedule_auto_start()
        return Ok({"status": "ready", "monitoring": self._monitoring,
                   "runtime_plugin_id": self._runtime_id,
                   "source_identity": self._source_identity})

    @lifecycle(id="config_change")
    async def on_config_change(self, **_):
        await self._reload_config()
        self._maybe_schedule_auto_start()
        return Ok({"status": "reloaded", "mode": self._cfg.get("mode"),
                   "runtime_plugin_id": self._runtime_id})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        await self._stop_pipeline(reason="shutdown", clear_loop=True)
        await self._drain_persist_futures(timeout=3.0)
        return Ok({"status": "shutdown"})

    async def _on_command_loop_start(self) -> None:
        # @lifecycle 走一次性 asyncio.run，常驻任务必须挂在命令循环上。
        self._loop = asyncio.get_running_loop()
        self._started_on_loop = True
        if self._startup_armed and _boolean(self._cfg.get("auto_start_monitoring"), True):
            await self._start_pipeline()

    def _maybe_schedule_auto_start(self) -> None:
        if not (self._startup_armed and _boolean(self._cfg.get("auto_start_monitoring"), True)):
            return
        loop = self._loop
        if loop is None or not loop.is_running():
            # The SDK command-loop hook will start it on the real loop.
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            if not self._monitoring:
                asyncio.create_task(self._start_pipeline())
            return
        try:
            asyncio.run_coroutine_threadsafe(self._start_pipeline(), loop)
        except Exception as exc:
            self.logger.warning("auto-start scheduling failed: {}: {}",
                                type(exc).__name__, exc)

    @_message_hook(id="chat_quiet_window", source="chat")
    async def on_chat_message(self, *, _ctx: Any = None, **kwargs):
        """Remember *when* the user spoke, never *what* they said.

        Only the session target in ``_ctx`` is inspected.  Message text, voice
        transcripts and other payload keys are ignored and never stored.  The
        quiet window applies only when the observed target matches the
        configured target (or no fixed target is configured).
        """
        del kwargs  # Never inspect text/voice content.
        observed = ""
        if isinstance(_ctx, dict):
            observed = str(_ctx.get("lanlan_name") or _ctx.get("target_lanlan") or "")
        else:
            observed = str(getattr(_ctx, "lanlan_name", "")
                           or getattr(_ctx, "target_lanlan", "") or "")
        observed = clean_target_lanlan(observed)
        configured = clean_target_lanlan(self._cfg.get("target_lanlan"))
        applies = not observed or not configured or observed == configured
        now = time.time()
        if applies:
            self.dispatcher.note_user_chat(now=now)
        self.timeline.record("chat", "quiet_window", ts=now,
                             reason="user_chat",
                             detail={"target_present": bool(observed),
                                     "applied": bool(applies)})
        return Ok({"status": "observed", "target_present": bool(observed),
                   "quiet_applied": bool(applies)})

    # ---------------------------------------------------------------- 生命周期

    async def _start_pipeline(self) -> bool:
        await self._acquire_transition()
        try:
            return await self._start_pipeline_serialized()
        finally:
            self._release_transition()

    async def _start_pipeline_serialized(self) -> bool:
        loop = self._loop or asyncio.get_running_loop()
        with self._lifecycle_lock:
            if self._monitoring:
                return True
            self._epoch += 1
            epoch = self._epoch
            self._monitoring = True
            self._pipeline_started = True
            self._last_start_error = ""
            self._loop = loop
            self._user_paused = False
            self._reset_runtime_state_locked(reason="pipeline_start")
            session_id = uuid.uuid4().hex
            self._session_id = session_id
            # reset_session clears the id; set it afterwards by contract.
            self.tracker.set_session_id(session_id)

        # A new session must not inherit the previous host read context.  If the
        # gate cannot reopen, roll back this exact start generation instead of
        # leaving UI-visible monitoring=True while every push is stopped.
        try:
            self.gate.reopen()
        except Exception as exc:
            self.logger.warning("gate reopen failed: {}: {}", type(exc).__name__, exc)
            with self._lifecycle_lock:
                if (self._epoch == epoch and self._monitoring
                        and self._session_id == session_id):
                    self._monitoring = False
                    self._pipeline_started = False
                    self._last_start_error = "gate_reopen"
                    self._reset_runtime_state_locked(reason="start_failed")
                    self._session_id = ""
                    self._last_scene = SCENE_UNKNOWN
                    self._classification_valid = False
                    self._confirmed_exit_handled = False
                    self._capture_pause_active = False
                    self._pause_reason = ""
                    self.timeline.record(
                        "pipeline", "start_failed", ts=time.time(),
                        reason="gate_reopen",
                        detail={"error": type(exc).__name__})
            return False
        self._expire_read_context("pipeline_start", token=self.gate.get_token())

        # 双模型惰性重载：推理期异常把状态打成 error 后，下个会话重试加载；
        # 文件缺失保持 unavailable，等待部署补齐权重。
        if not self.ui_detector.available and self.ui_detector.state in ("error", "unavailable"):
            self.ui_detector = UiDetector(_MODEL_PATH)
        if not self.card_classifier.available and self.card_classifier.state in ("error", "unavailable"):
            self.card_classifier = CardClassifier(_CARD_CLS_MODEL_PATH)
        for model in (self.ui_detector, self.card_classifier):
            if not model.available and model.diagnostics:
                self.logger.warning("model {} not ready ({}): {}",
                                    type(model).__name__, model.state, model.diagnostics)

        capture = ScreenCapture(
            fps=_capture_fps(self._cfg),
            # The producer thread owns the single capture path; the perception
            # loop consumes latest_capture() and never opens a competing grab.
            capture_thread_enabled=True,
        )
        with self._lifecycle_lock:
            if not self._monitoring or self._epoch != epoch:
                capture.stop()
                return False
            self._capture = capture
            capture.start()
            self._loop_task = asyncio.create_task(self._perception_loop(epoch))
            self._catalog_task = asyncio.create_task(self._catalog_loop(epoch))
            self._last_start_error = ""
        self.logger.info("kards companion pipeline started (mode={}, runtime={})",
                         self._cfg.get("mode"), self._runtime_id)
        return True

    async def _stop_pipeline(self, *, reason: str = "monitoring_stopped",
                             clear_loop: bool = False) -> None:
        await self._acquire_transition()
        try:
            await self._stop_pipeline_serialized(reason=reason, clear_loop=clear_loop)
        finally:
            self._release_transition()

    async def _stop_pipeline_serialized(self, *, reason: str,
                                        clear_loop: bool) -> None:
        with self._lifecycle_lock:
            self._monitoring = False
            self._epoch += 1
            stop_epoch = self._epoch
            loop_task, self._loop_task = self._loop_task, None
            catalog_task, self._catalog_task = self._catalog_task, None
            capture, self._capture = self._capture, None
            self._capture_pause_active = True
            self._pause_reason = reason
            self._user_paused = False
            self._last_start_error = ""
            self._pending_rearm = []

        # 1) Close gate admission outside the lifecycle lock.  begin_stop may
        #    wait for a host push already in flight; running it in a worker
        #    keeps the asyncio command loop responsive.
        try:
            await asyncio.to_thread(self.gate.begin_stop, reason)
        except Exception as exc:
            self.logger.warning("gate begin_stop failed: {}: {}", type(exc).__name__, exc)

        # 2) Stop producers, then wait for the worker thread that may still be
        #    inside capture/OCR.  Stale ticks see the new epoch and cannot push.
        for task in (loop_task, catalog_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        await self._wait_for_ticks_idle()

        # 3) Drop queued respond items without touching policy modules.
        self._purge_dispatcher_queue()

        # 4) Expire the read context with the cleanup token minted by
        #    begin_stop.  Consent/dry/stop policy is enforced by the gate.
        try:
            cleanup_token = self.gate.get_token()
        except Exception:
            cleanup_token = None
        self._expire_read_context(reason, token=cleanup_token)

        try:
            await asyncio.to_thread(self.gate.finish_stop)
        except Exception as exc:
            self.logger.warning("gate finish_stop failed: {}: {}", type(exc).__name__, exc)

        with self._lifecycle_lock:
            if self._epoch == stop_epoch and not self._monitoring:
                self._reset_runtime_state_locked(reason="monitoring_stopped")
                self._session_id = ""
                self._last_scene = SCENE_UNKNOWN
                self._classification_valid = False
                self._confirmed_exit_handled = False
                self._capture_pause_active = False
                self._pause_reason = ""
                self.timeline.record("pipeline", "stopped", ts=time.time(),
                                     reason=reason)
                if clear_loop:
                    self._loop = None
                    self._started_on_loop = False
                    self._startup_armed = False

        if capture is not None:
            try:
                await asyncio.to_thread(capture.stop)
            except Exception:
                pass

    async def _acquire_transition(self) -> None:
        await asyncio.get_running_loop().run_in_executor(
            None, self._transition_guard.acquire)

    def _release_transition(self) -> None:
        try:
            self._transition_guard.release()
        except RuntimeError:
            pass

    async def _pause_pipeline(self, reason: str = "user_pause") -> None:
        await self._acquire_transition()
        try:
            await self._pause_pipeline_serialized(reason)
        finally:
            self._release_transition()

    async def _pause_pipeline_serialized(self, reason: str) -> None:
        with self._lifecycle_lock:
            if not self._monitoring:
                return
            if self._user_paused:
                return
            self._user_paused = True
            self._pause_reason = reason
        try:
            self.guard.pause(reason, auto=False)
        except Exception:
            pass
        # Pause/expire admission first; a token minted before this call can no
        # longer push.  Ticks keep running but do no recognition/delivery.
        try:
            await asyncio.to_thread(self.gate.pause, reason)
        except Exception as exc:
            self.logger.warning("gate pause failed: {}: {}", type(exc).__name__, exc)
        await self._wait_for_ticks_idle()
        self._purge_dispatcher_queue()
        try:
            pause_token = self.gate.get_token()
        except Exception:
            pause_token = None
        self._expire_read_context(reason, token=pause_token)
        self.timeline.record("pipeline", "paused", ts=time.time(), reason=reason)

    async def _resume_pipeline(self) -> None:
        await self._acquire_transition()
        try:
            await self._resume_pipeline_serialized()
        finally:
            self._release_transition()

    async def _resume_pipeline_serialized(self) -> None:
        with self._lifecycle_lock:
            if not self._monitoring:
                return
            self._user_paused = False
            self._pending_rearm = list(self.tracker.rearm_uncommitted(now=time.time()))
            if self.tracker.in_match:
                self.tracker.rebase()
                with self._unit_lock:
                    self._unit_tracker.rebase()
        # UI resume is explicit: clear both the manual pause and any automatic
        # failure pause.  There is no automatic half-open leak here.
        for kwargs in ({}, {"auto": True}):
            try:
                self.guard.resume(**kwargs)
            except Exception:
                pass
        try:
            await asyncio.to_thread(self.gate.resume)
        except Exception as exc:
            self.logger.warning("gate resume failed: {}: {}", type(exc).__name__, exc)
        self.timeline.record("pipeline", "resumed", ts=time.time(), reason="user_resume")

    async def _catalog_loop(self, epoch: int | None = None) -> None:
        while self._session_active(epoch):
            if _boolean(self._cfg.get("catalog_enabled"), False):
                updated = await asyncio.to_thread(
                    self.catalog.sync,
                    force=False,
                    max_age_hours=_num(self._cfg.get("catalog_refresh_hours"), 24.0,
                                       minimum=1.0, maximum=720.0))
                if updated:
                    self.logger.info("kards catalog refreshed: {} cards", self.catalog.size())
            await asyncio.sleep(600)

    async def _perception_loop(self, epoch: int | None = None) -> None:
        while self._session_active(epoch):
            await asyncio.sleep(_loop_seconds(self._cfg))
            if not self._session_active(epoch):
                return
            try:
                await asyncio.to_thread(self._run_tick, epoch)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.warning("perception tick failed: {}: {}", type(exc).__name__, exc)

    def _session_active(self, epoch: int | None = None) -> bool:
        # User pause keeps the loop alive; _perceive_once is the gate that
        # skips recognition and delivery while paused.
        with self._lifecycle_lock:
            return (self._monitoring
                    and (epoch is None or self._epoch == epoch))

    def _tick_active_locked(self, epoch: int, capture: Any) -> bool:
        """Caller must hold ``_lifecycle_lock``."""
        return (self._monitoring and not self._user_paused
                and self._epoch == epoch and self._capture is capture)

    def _run_tick(self, epoch: int | None = None) -> None:
        """Thread-side tick wrapper so stop can wait for real completion."""
        # Capture the gate token before taking the lifecycle lock: get_token can
        # wait on the gate commit lock and must never be called under our lock.
        try:
            token = self.gate.get_token()
            token_epoch = int(self.gate.epoch)
        except Exception:
            token = None
            token_epoch = None
        with self._lifecycle_lock:
            if not self._session_active(epoch):
                return
            self._ticks_in_flight += 1
            self._ticks_idle.clear()
        try:
            self._perceive_once(epoch, context_token=token, token_epoch=token_epoch)
        finally:
            with self._lifecycle_lock:
                self._ticks_in_flight = max(0, self._ticks_in_flight - 1)
                if self._ticks_in_flight == 0:
                    self._ticks_idle.set()

    async def _wait_for_ticks_idle(self, timeout: float = 5.0) -> bool:
        if self._ticks_idle.is_set():
            return True
        idle = await asyncio.to_thread(self._ticks_idle.wait, timeout)
        if not idle:
            self.logger.warning(
                "perception tick still in flight after {:.1f}s during stop", timeout)
        return bool(idle)

    # -------------------------------------------------------------- 状态清理

    def _purge_dispatcher_queue(self) -> int:
        """Drop queued respond items through C's public purge API."""
        purge = getattr(self.dispatcher, "purge", None)
        if callable(purge):
            try:
                removed = purge("lifecycle_purge")
                return len(removed) if isinstance(removed, list) else 0
            except Exception as exc:
                self.logger.warning("dispatcher purge failed: {}: {}",
                                    type(exc).__name__, exc)
        return 0

    def _expire_read_context(self, reason: str, *, token: str | None = None) -> bool:
        """Retire the host read/context cursor outside the lifecycle lock.

        The delivery owner's ``expire`` restores/clears both the core live-state
        slot and the context slot.  Main only clears its local enter marker so
        the next allowed tick re-announces capability boundaries; it must not
        send a second restore push itself.
        """
        with self._lifecycle_lock:
            self._read_context_entered_token = ""
            self._read_context_entered_session = ""
            self._read_context_entered_fingerprint = ""
        expire = getattr(self.publisher, "expire", None)
        if not callable(expire):
            return False
        try:
            # Exact frozen API: expire owns both core and context slots.
            return bool(expire(reason=reason, context_token=token))
        except Exception as exc:
            self.logger.warning("publisher expire failed: {}: {}", type(exc).__name__, exc)
            return False

    def _reset_runtime_state_locked(self, *, reason: str = "reset") -> None:
        """Clear every cross-tick read/delivery cache. Caller holds the lock."""
        self._capture_pause_active = True
        self._pause_reason = reason
        self._last_capture_health = CaptureHealthSnapshot(
            status="stopped", reason=reason, reading=False)
        try:
            self.arbiter.reset()
        except Exception:
            pass
        for component in (self.guard, self.breaker, self.read_breaker):
            reset = getattr(component, "reset", None)
            if callable(reset):
                try:
                    reset()
                except Exception:
                    pass
        try:
            self.tracker.reset_session()
        except Exception:
            pass
        try:
            self.scenario.reset()
        except Exception:
            pass
        with self._unit_lock:
            reset_units = getattr(self._unit_tracker, "reset_session", None)
            if callable(reset_units):
                try:
                    reset_units()
                except Exception:
                    pass
            self._last_unit_diagnostics = {}
        self._pipeline_state = MatchState()
        # 场景分类器回到 unknown 基线；卡牌任务丢弃待分类框与识别缓存。
        try:
            self.screen_classifier.invalidate()
        except Exception:
            pass
        with self._card_lock:
            self._card_consumed_frame = 0
            self._card_pending = []
            self._card_pending_frame = 0
            self._card_pending_at = 0.0
            self._card_pending_session = ""
            self._card_image = None
            self._recognized_cards = []
        self._last_ui_observation_at = None
        self._last_ui_observation_mono = None
        self._last_ui_frame_id = 0
        self._last_ui_signals = []
        self._last_ui_scene_confidence = None
        self._ui_observation_failed = False
        self._ui_rebase_pending = False
        self._read_context_entered_session = ""
        self._read_context_entered_token = ""
        self._read_context_entered_fingerprint = ""
        self._change_hash = None
        self._change_dirty = True
        self._capture_rate_probe = None
        self._previous_snapshot = None
        self._last_trusted_snapshot = None
        self._last_detector_diagnostics = []
        self._pending_rearm = []
        self._last_scene = SCENE_UNKNOWN
        self._classification_valid = False
        self._confirmed_exit_handled = False

    # ---------------------------------------------------------------- 感知 tick

    def _ensure_session_locked(self) -> str:
        if not self._session_id:
            self._session_id = uuid.uuid4().hex
            self.tracker.set_session_id(self._session_id)
        return self._session_id

    def _next_capture_frame_id(self, candidate: Any = 0) -> int:
        """Return one positive frame id; caller holds ``_lifecycle_lock``.

        A real capture id is kept as-is (the counter only advances past it so
        a later missing id cannot collide).  Missing/zero/negative ids get a
        fresh monotonic value instead of repeatedly masquerading as frame 0.
        """
        try:
            value = int(candidate)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            if value > self._capture_frame_seq:
                self._capture_frame_seq = value
            return value
        self._capture_frame_seq += 1
        return self._capture_frame_seq

    def _perceive_once(self, epoch: int | None = None, *,
                       context_token: str | None = None,
                       token_epoch: int | None = None) -> None:
        """One perception tick.

        Trusted frame: change 门控（dHash）→ unit 任务（ui_det 检测 + 场景推断
        + 单位轨迹）→ card 任务（card_hand/played_card 裁图分类）→ normalize
        → one scenario update under the lifecycle lock, then all delivery/read
        host calls outside the lock with the token captured at tick entry.

        Untrusted frame: no detector/classifier/tracker, one scenario pause
        update, one delivery purge + read-context expire at the transition.
        """
        if context_token is None:
            try:
                context_token = self.gate.get_token()
                token_epoch = int(self.gate.epoch)
            except Exception:
                context_token = None
                token_epoch = None

        with self._lifecycle_lock:
            current_epoch = self._epoch if epoch is None else int(epoch)
            capture = self._capture
            if (not self._monitoring or self._user_paused
                    or current_epoch != self._epoch or capture is None):
                return
            previous = self._last_trusted_snapshot
            session_id = self._ensure_session_locked()

        # The capture thread is the sole producer.  Consume its latest result;
        # calling capture_frame() while the worker is alive returns that same
        # snapshot and never starts a competing capture.  Fake captures in
        # tests have no latest_capture() and fall back to their one-shot API.
        result = None
        latest_capture = getattr(capture, "latest_capture", None)
        if callable(latest_capture):
            try:
                result = latest_capture()
            except Exception:
                result = None
        if result is None:
            result = capture.capture_frame()
        health = capture.health_snapshot()
        observed_at = time.time()
        if result is not None:
            try:
                captured_at = float(getattr(result, "captured_at", 0.0) or 0.0)
            except (TypeError, ValueError):
                captured_at = 0.0
            if captured_at > 0.0:
                observed_at = captured_at

        with self._lifecycle_lock:
            if not self._tick_active_locked(current_epoch, capture):
                return
            self._last_capture_health = health
            self._snapshot_seq += 1
            seq = self._snapshot_seq

        # RuntimeSnapshot.trusted is the authority: capture.reading + a valid
        # frame + an image.  CaptureResult.trusted is only evidence used by the
        # recorder's window/source check, never the sole recognition gate.
        result_image = getattr(result, "image", None) if result is not None else None
        if result is None or not health.reading or result_image is None:
            reason = health.reason or (
                "frame_unavailable" if result is None else "capture_not_trusted")
            frame_id = 0
            if result is not None:
                try:
                    frame_id = int(getattr(result, "frame_id", 0) or 0)
                except (TypeError, ValueError):
                    frame_id = 0
            frame = FrameMeta(
                frame_id=frame_id or int(health.frame_id or 0),
                captured_at=0.0,
                width=health.width,
                height=health.height,
                backend=health.backend,
                valid=False,
                reason=reason,
            )
            snapshot = RuntimeSnapshot(
                seq=seq, observed_at=observed_at, health=health, frame=frame,
                state=MatchState(), image=None)
            with self._lifecycle_lock:
                if not self._tick_active_locked(current_epoch, capture):
                    return
                self._previous_snapshot = snapshot
                first_pause = not self._capture_pause_active
                self._capture_pause_active = True
                self._pause_reason = reason
                # One scenario pause update per tick; it never confirms exit
                # from unknown frames.
                try:
                    self.scenario.update(snapshot.state, now=observed_at)
                except Exception:
                    pass
                if first_pause:
                    self.timeline.record(
                        "capture", "paused", ts=observed_at, seq=seq,
                        reason=reason,
                        detail={"status": health.status,
                                "backend": health.backend,
                                "frame_age_ms": health.frame_age_ms,
                                "frame_id": int(frame_id or health.frame_id or 0),
                                "reading": False})
            # Host-facing cleanup happens outside the lifecycle lock.  Every
            # untrusted tick retries the publisher's bounded expire backoff
            # with the token captured at tick entry; only the transition tick
            # purges the respond slot.
            if first_pause:
                self._purge_dispatcher_queue()
            self._expire_read_context(reason, token=context_token)
            return

        image = result_image
        raw_frame = result.to_frame_meta(valid=True, reason=health.reason)
        try:
            # Source frame id only.  Health may already describe a newer
            # producer frame; borrowing it here would mislabel this image.
            candidate_frame_id = int(getattr(raw_frame, "frame_id", 0) or 0)
        except (TypeError, ValueError):
            candidate_frame_id = 0

        # Carry the last committed state forward.  HUD/unit/change run on
        # independent deadlines, so a tick that skips a task must not feed an
        # empty board or a None HP value into MatchTracker.
        with self._lifecycle_lock:
            if not self._tick_active_locked(current_epoch, capture):
                return
            # Resolve the id exactly once, under the same lock that owns the
            # tick sequence.  Every scheduler/evidence/unit marker below uses
            # this value.
            frame_id = self._next_capture_frame_id(candidate_frame_id)
            frame = FrameMeta(
                frame_id=frame_id,
                captured_at=observed_at,
                width=raw_frame.width,
                height=raw_frame.height,
                backend=raw_frame.backend,
                valid=True,
                reason=raw_frame.reason,
            )
            base = self._pipeline_state
            state = MatchState(
                scene=base.scene,
                game_number=int(base.game_number or 0),
                in_match=bool(base.in_match),
                my_turn=base.my_turn,
                turn_number=int(base.turn_number or 0),
                my_hq=base.my_hq,
                enemy_hq=base.enemy_hq,
                my_credits=base.my_credits,
                my_credit_cap=base.my_credit_cap,
                hand_count=base.hand_count,
                enemy_hand_count=base.enemy_hand_count,
                units=[unit.copy() for unit in base.units],
                last_order_card=copy.deepcopy(base.last_order_card),
                detected_at=observed_at,
                draft_unsupported=bool(base.draft_unsupported),
            )
            # Runtime cadence marker (also backwards compatible if the shared
            # model does not declare it): a non-unit tick carries last-known
            # units but is not fresh unit evidence.
            state.unit_observation_valid = False
            state.unit_observation_frame = 0

        # ---- change 任务：纯 dHash 帧差门控，不跑任何模型 -------------------
        with self._scheduler_lock:
            change_due = self.scheduler.due("change", frame_id=frame_id)

        if change_due:
            started = time.monotonic()
            fingerprint = dhash(image)
            with self._lifecycle_lock:
                if (self._change_hash is None
                        or hamming(fingerprint, self._change_hash) > 2):
                    self._change_hash = fingerprint
                    # 画面有变化：允许下一次 unit 任务推进检测/场景。
                    self._change_dirty = True
            with self._scheduler_lock:
                self.scheduler.mark_duration(
                    "change", max(0.0, time.monotonic() - started))

        # ---- unit 任务前半：ui_det 检测 → 场景推断（ScreenClassifier） -----
        # 场景必须先于 pre-gate 得到（与旧 change 任务一致），resume 的
        # rebase 才能在首个观测被消费前生效。
        detections: list[Any] | None = None
        unit_due = False
        with self._scheduler_lock:
            unit_fps = max(0.5, float(
                getattr(self._perception_settings, "unit_fps", 4.0) or 4.0))
            heartbeat_s = min(UI_OBSERVATION_HEARTBEAT_SECONDS,
                              max(0.1, 1.0 / unit_fps))
            heartbeat_due = (
                self._last_ui_observation_mono is None
                or time.monotonic() - self._last_ui_observation_mono >= heartbeat_s)
            if self._change_dirty or heartbeat_due:
                unit_due = self.scheduler.due("unit", frame_id=frame_id)
            if (unit_due and not self._change_dirty
                    and (frame_id == self._last_ui_frame_id
                         or (self._last_ui_observation_at is not None
                             and observed_at <= self._last_ui_observation_at))):
                # A repeated capture frame/timestamp is not a second
                # independent UI observation; the 2-frame scene/unit gates
                # must not advance.
                unit_due = False

        if unit_due:
            started = time.monotonic()
            detections = self._detect_ui(image)
            if detections is not None:
                self._last_ui_observation_at = observed_at
                self._last_ui_observation_mono = time.monotonic()
                self._last_ui_frame_id = int(frame_id)
                self._last_ui_signals = [
                    str(getattr(det, "name", "")) for det in detections
                    if str(getattr(det, "name", ""))]
                scene_confs: list[float] = []
                for det in detections:
                    if str(getattr(det, "name", "")) not in SCENE_SIGNAL_CLASSES:
                        continue
                    try:
                        value = float(getattr(det, "conf", 0.0) or 0.0)
                    except (TypeError, ValueError):
                        continue
                    if value == value and 0.0 <= value <= 1.0:
                        scene_confs.append(value)
                self._last_ui_scene_confidence = (
                    round(max(scene_confs), 3) if scene_confs else None)
                self._ui_observation_failed = False
                if self._ui_rebase_pending:
                    # A failed UI observation ended the previous continuous
                    # baseline; the first good sample is a silent baseline.
                    self.tracker.rebase()
                    with self._unit_lock:
                        self._unit_tracker.rebase()
                    self._ui_rebase_pending = False
                try:
                    scene, my_turn = self.screen_classifier.classify(detections)
                except Exception as exc:
                    self.logger.warning("scene classify failed: {}: {}",
                                        type(exc).__name__, exc)
                    scene, my_turn = SCENE_UNKNOWN, None
                state.scene = str(scene or SCENE_UNKNOWN)
                # Keep the classifier's tri-state as-is: unknown stays None,
                # never a fabricated False.
                state.my_turn = (my_turn if isinstance(my_turn, bool)
                                 and state.scene == SCENE_MATCH else None)
                state.in_match = state.scene == SCENE_MATCH
                # card 任务消费最近一次 unit 检测的 card_hand/played_card 框。
                self._stage_card_crops(detections, image, frame_id,
                                       observed_at=observed_at)
                # 本帧已跑过检测；画面再次变化前不重复推理。
                with self._lifecycle_lock:
                    self._change_dirty = False
            else:
                # Model/inference failure is not an empty board.  Let the unit
                # tracker preserve last-known identities with observed=False
                # (so diagnostics stay honest), then invalidate the scene so a
                # stale match cannot keep context_valid/read alive.
                if state.scene == SCENE_MATCH or self.tracker.in_match:
                    unit_diagnostics = self._apply_detector(
                        state, image, None, frame_id=frame_id,
                        observed_at=observed_at,
                        observation=self._observation_evidence(
                            capture, result, health))
                    state.unit_observation_valid = bool(
                        (unit_diagnostics or {}).get("observation_valid", False))
                    state.unit_observation_frame = int(frame_id)
                self._ui_observation_failed = True
                self._ui_rebase_pending = True
                state.scene = SCENE_UNKNOWN
                state.my_turn = None
                state.in_match = False
            with self._scheduler_lock:
                self.scheduler.mark_duration(
                    "unit", max(0.0, time.monotonic() - started) / 2.0)

        # Carried (non-UI) ticks may keep the last **verified** scene only while
        # a real UI sample is within the freshness bound.  A stale/failed UI
        # observation must not let a copied ``base.scene=match`` keep
        # scenario.context_valid true on every capture tick.
        if not (unit_due and detections is not None):
            with self._lifecycle_lock:
                ui_at = self._last_ui_observation_at
                ui_failed = bool(self._ui_observation_failed)
            ui_stale = (ui_failed or ui_at is None
                        or max(0.0, observed_at - float(ui_at))
                        > SITUATION_FRESH_MAX_AGE_SECONDS)
            if ui_stale:
                state.scene = SCENE_UNKNOWN
                state.my_turn = None
                state.in_match = False

        # Pure scene gate runs before the expensive HUD/unit tasks so a resume
        # can rebase both trackers before the first observation is consumed.
        with self._lifecycle_lock:
            if not self._tick_active_locked(current_epoch, capture):
                return
            pre = self.scenario.observe_scene(state, now=observed_at)
        if pre.get("new_match"):
            with self._unit_lock:
                self._unit_tracker.reset_match()
            # No observation may be inherited from the previous match when
            # this tick skips the unit task.
            state.units = []
            with self._card_lock:
                stale_cards = bool(self._recognized_cards) or (
                    bool(self._card_pending)
                    and int(self._card_pending_frame or 0) != int(frame_id))
            if stale_cards:
                self._clear_current_cards("new_match")
        if pre.get("resume"):
            if self.tracker.in_match:
                self.tracker.rebase()
            with self._unit_lock:
                self._unit_tracker.rebase()

        if state.scene == SCENE_MATCH:
            if unit_due:
                started = time.monotonic()
                unit_diagnostics = self._apply_detector(
                    state, image, detections, frame_id=frame_id,
                    observed_at=observed_at,
                    observation=self._observation_evidence(capture, result, health))
                # A unit-due tick is the only tick allowed to advance unit
                # gates.  Model/inference failure yields False here, so the
                # carried last-known units stay silent instead of counting as
                # a missing observation.
                state.unit_observation_valid = bool(
                    (unit_diagnostics or {}).get("observation_valid", False))
                state.unit_observation_frame = int(frame_id)
                with self._scheduler_lock:
                    self.scheduler.mark_duration(
                        "unit", max(0.0, time.monotonic() - started))

            # ---- card 任务：待分类裁图 → card_cls → recognized_cards ------
            with self._scheduler_lock:
                card_due = self.scheduler.due("card", frame_id=frame_id)
            if card_due:
                started = time.monotonic()
                try:
                    self._run_card_task(state, observed_at=observed_at)
                except Exception as exc:
                    self.logger.warning("card task failed: {}: {}",
                                        type(exc).__name__, exc)
                with self._scheduler_lock:
                    self.scheduler.mark_duration(
                        "card", max(0.0, time.monotonic() - started))

        snapshot = RuntimeSnapshot(
            seq=seq, observed_at=observed_at, health=health, frame=frame,
            state=state, image=image)

        with self._lifecycle_lock:
            if not self._tick_active_locked(current_epoch, capture):
                return
            resumed = self._capture_pause_active

            candidates: list[Any] = []
            diagnostics: list[Any] = []
            detector_previous = None if (resumed or pre.get("resume")) else previous
            if pre.get("allow_diff"):
                candidates, diagnostics = self.detectors.update(detector_previous, snapshot)
            # No tracker/diff for menu/draft/unknown.  Terminal frames still
            # allow_diff so a confirmed victory/defeat closes the match.

            events = [normalize_event(item.event, session_id=session_id)
                      for item in candidates]
            # Exactly one scenario advance per tick.
            scenario_name = self.scenario.update(state, events, now=observed_at)
            scene_snapshot = self.scenario.snapshot()
            if pre.get("resume"):
                try:
                    self.scenario.consume_rebase()
                except Exception:
                    pass

            confirmed_exit = bool(scene_snapshot.get("confirmed_exit"))
            terminal_frame = any(event.kind == E_MATCH_END for event in events)
            if confirmed_exit and not self._confirmed_exit_handled:
                # Preserve the session and cumulative game number; only the
                # current match memory is dropped.  This also covers a terminal
                # frame where MatchTracker has already closed _in_match itself.
                if self.tracker.in_match:
                    try:
                        self.tracker.reset_match()
                    except Exception:
                        pass
                with self._unit_lock:
                    reset_match = getattr(self._unit_tracker, "reset_match", None)
                    if callable(reset_match):
                        reset_match()
                state.units = []
                self._clear_current_cards("confirmed_exit")
                self._confirmed_exit_handled = True
            elif pre.get("new_match") or (self.tracker.in_match and not confirmed_exit):
                self._confirmed_exit_handled = False

            if terminal_frame:
                self._clear_current_cards("match_ended")
            if not bool(scene_snapshot.get("context_valid")):
                # An unknown/menu/draft frame invalidates the live facts and
                # drops the match-scoped card faces immediately.
                self._clear_current_cards("scene_invalid")

            if resumed or pre.get("resume"):
                # Conditions that were already true before a pause are
                # re-armed without refreshing their original ts.
                try:
                    rearmed = self.tracker.rearm_uncommitted(now=observed_at)
                except Exception:
                    rearmed = []
                for event in rearmed:
                    events.append(normalize_event(event, session_id=session_id))

            # Stable identity dedupe: the tracker event wins over a re-arm copy.
            deduped: list[GameEvent] = []
            seen: set[str] = set()
            for event in events:
                identity = str((event.details or {}).get("identity")
                               or event.fingerprint())
                if identity in seen:
                    continue
                seen.add(identity)
                deduped.append(event)
            events = deduped

            # One unified view for read/respond/tool, built from the *same*
            # snapshot after tracker/scenario and before any host call.  Source
            # timestamps are capture/UI samples, not this commit time.
            state.situation = build_situation(
                state,
                models=self._models_payload(),
                observations=self._recognized_cards_payload(),
                session_id=session_id,
                frame_id=frame_id,
                observed_at=observed_at,
                now=observed_at,
                context_valid=bool(scene_snapshot.get("context_valid")),
                reason=str(scene_snapshot.get("reason") or ""),
                ui_observed_at=self._last_ui_observation_at,
                events=events,
                unit_diagnostics=self._last_unit_diagnostics,
                observation_valid=state.unit_observation_valid,
                scenario=scenario_name,
                ui_signals=self._last_ui_signals,
                scene_confidence=self._last_ui_scene_confidence,
            )

            # Commit the full state only after the trackers have consumed it.
            # MatchTracker mutates lineage/game_number in place, so this is the
            # canonical baseline for the next scheduled task.
            self._pipeline_state = state
            self._previous_snapshot = snapshot
            self._last_trusted_snapshot = snapshot
            self._last_detector_diagnostics = [
                {"name": item.name,
                 "duration_ms": round(float(item.duration_ms), 3),
                 "outcome": item.outcome,
                 "error": item.error}
                for item in diagnostics
            ]
            for item in diagnostics:
                if item.outcome == "error":
                    self.timeline.record(
                        "detector", "error", ts=observed_at, seq=seq,
                        reason=item.error,
                        detail={"name": item.name,
                                "frame_id": int(frame_id or 0)})

            scene_text = str(state.scene or SCENE_UNKNOWN)
            if scene_text in _VALID_SCENES:
                self._last_scene = scene_text
                self._classification_valid = True
            else:
                # Keep the last confirmed scene for status; unknown/draft do
                # not erase it.
                self._classification_valid = False
            if resumed:
                self._capture_pause_active = False
                self._pause_reason = ""
                self.timeline.record(
                    "capture", "resumed", ts=observed_at, seq=seq,
                    reason="frame_trusted",
                    detail={"status": health.status,
                            "backend": health.backend,
                            "frame_age_ms": health.frame_age_ms,
                            "frame_id": int(frame_id or 0),
                            "reading": True})

        # ---- All host-facing work is outside the lifecycle lock. -------------
        terminal = any(event.kind == E_MATCH_END for event in events)
        invalid_context = not bool(scene_snapshot.get("context_valid"))
        confirmed_exit = bool(scene_snapshot.get("confirmed_exit"))
        if invalid_context or confirmed_exit or terminal:
            # The old respond slot / read context cannot survive a broken or
            # ended scene.  Purge first, then expire with the tick token; a
            # terminal result is still admitted below after the old slot is
            # gone.  Repeated invalid frames retry publisher backoff, never a
            # freshly minted token.
            self._purge_dispatcher_queue()
            reason = ("match_ended" if terminal else
                      ("confirmed_exit" if confirmed_exit else "scene_invalid"))
            self._expire_read_context(reason, token=context_token)
        self._attach_frame_evidence(
            events, frame, session_id=session_id, seq=seq,
            observed_at=observed_at)
        self._record_events(events)
        self._dispatch_candidates(
            events, state, session_id=session_id,
            scenario=scenario_name, context_token=context_token,
            token_epoch=token_epoch, observed_at=observed_at)
        validity = self._context_validity(observed_at)
        if (self.tracker.in_match and validity["valid"]
                and not self._user_paused):
            self._publish_live_state(
                state, session_id=session_id,
                context_token=context_token, token_epoch=token_epoch,
                observed_at=observed_at)

    def _attach_frame_evidence(self, events: list[GameEvent], frame: FrameMeta, *,
                               session_id: str, seq: int,
                               observed_at: float) -> None:
        """Attach bounded, structured evidence; never image pixels or OCR text."""
        if not bool(getattr(self._perception_settings, "evidence_enabled", True)):
            return
        evidence = {
            "frame_id": int(frame.frame_id or 0),
            "captured_at": float(frame.captured_at),
            "session_id": str(session_id or ""),
            "seq": int(seq),
            "observed_at": float(observed_at),
            "backend": str(frame.backend or ""),
            "width": int(frame.width or 0),
            "height": int(frame.height or 0),
        }
        for event in events:
            details = event.details if isinstance(event.details, dict) else {}
            details.setdefault("frame_id", evidence["frame_id"])
            details.setdefault("evidence", dict(evidence))
            event.details = details

    def _detect_ui(self, image) -> list[Any] | None:
        """运行界面检测模型；不可用/失败返回 ``None``（区别于空检测）。"""
        try:
            detections = self.ui_detector.detect(image)
        except Exception as exc:
            self.logger.warning("ui detector step failed: {}: {}",
                                type(exc).__name__, exc)
            self._ui_detect_status = f"detector_error:{type(exc).__name__}"
            return None
        self._ui_detect_status = ("model_unavailable" if detections is None
                                  else "ok")
        return detections

    def _stage_card_crops(self, detections: list[Any], image, frame_id: int,
                          *, observed_at: float | None = None) -> None:
        """unit 任务产出的 card_hand/played_card 框进入待分类队列。

        只暂存最近一帧的框、图像引用、**采集源时间**与所属 session；消费
        水位由 card 任务维护，同一帧不会重复分类、重复计数。消费时绝不把
        “当前 tick 时间”伪造成观测时间。
        """
        pending = [(det.name, det) for det in detections or ()
                   if getattr(det, "name", "") in ("card_hand", "played_card")]
        with self._card_lock:
            self._card_pending = pending
            self._card_pending_frame = int(frame_id)
            self._card_pending_at = float(
                observed_at if observed_at is not None else time.time())
            self._card_pending_session = str(self._session_id or "")
            self._card_image = image

    def _run_card_task(self, state: MatchState, *, observed_at: float) -> None:
        """card 任务：裁图 → card_cls → recognized_cards 区域观察。

        * 裁图短边 < CARD_CROP_MIN_SIDE 直接跳过（过小裁图不可信）；
        * 分类成功（非 unknown）才写入 recognized_cards；
        * played_card 是放大/悬停的牌面区域观察，不代表已经打出，也不携带
          敌我信息：绝不写 last_order_card、绝不产生 E_ORDER_CARD；
        * 每条观察带 session_id / source_frame / **采集源 observed_at** /
          appearance，不伪造单位 instance_id，也不把消费时刻当观测时刻；
        * 白名单为空（trusted_card_count=0）时绝不写入任何卡面身份；
        * 同一 pending 帧只消费一次（帧号高水位），且只消费与当前 session
          一致的暂存帧。
        """
        with self._card_lock:
            pending_frame = int(self._card_pending_frame or 0)
            pending = list(self._card_pending)
            image = self._card_image
            pending_at = float(self._card_pending_at or 0.0) or float(
                observed_at or 0.0)
            pending_session = str(self._card_pending_session or "")
            consumed = int(getattr(self, "_card_consumed_frame", 0))
            current_session = str(self._session_id or "")
        if image is None or not pending or pending_frame <= consumed:
            return
        if pending_session and current_session and pending_session != current_session:
            # A different session's staged frame must never leak into this one.
            return
        if not self.card_classifier.available:
            return
        # Whitelist enforcement belongs to CardClassifier (production empty
        # whitelist -> unknown).  The unified situation builder additionally
        # gates recognized_cards on trusted_card_count, so a fake test
        # classifier may produce observations without weakening production.
        recognized: list[dict[str, Any]] = []
        for index, (region, det) in enumerate(pending):
            try:
                short_side = min(float(det.width()), float(det.height()))
            except Exception:
                continue
            if short_side < CARD_CROP_MIN_SIDE:
                continue
            try:
                crop = det.crop(image)
            except Exception:
                continue
            appearance = CARD_APPEARANCE_BY_REGION.get(region)
            result = self.card_classifier.classify(crop, appearance)
            if result is None:
                break  # 模型推理失败：本帧剩余裁图一并放弃
            if not result.known:
                continue
            recognized.append({
                "observation_id": f"card:{pending_frame}:{index}",
                "session_id": pending_session or current_session,
                "frame_id": pending_frame,
                "source_frame": pending_frame,
                "observed_at": round(float(pending_at), 3),
                "appearance": appearance or region,
                "card_id": result.card_id,
                "name": result.name,
                "region": region,
                "conf": float(result.conf),
            })

        with self._card_lock:
            self._card_consumed_frame = pending_frame
            if recognized:
                self._recognized_cards = (
                    self._recognized_cards + recognized)[-RECOGNIZED_CARDS_LIMIT:]

    def _clear_current_cards(self, reason: str) -> None:
        """Drop match-scoped card faces; a new session/match never inherits them."""
        with self._card_lock:
            had_cards = bool(self._recognized_cards or self._card_pending)
            self._card_pending = []
            self._card_pending_frame = 0
            self._card_pending_at = 0.0
            self._card_pending_session = ""
            self._card_image = None
            self._recognized_cards = []
        if had_cards:
            self.timeline.record("cards", "cleared", ts=time.time(),
                                 reason=str(reason or "cleared")[:80])

    @staticmethod
    def _observation_evidence(capture: Any, result: Any,
                              health: CaptureHealthSnapshot) -> dict[str, Any]:
        """Capture health passed to UnitTracker's absence gate.

        A trusted frame is still not automatically valid *absence* evidence:
        desktop backends need foreground/occlusion proof, while host WGC may
        legitimately read a background window.  UnitTracker owns that decision;
        main only supplies the raw evidence.
        """
        backend = str(
            getattr(result, "source", "")
            or getattr(result, "backend", "")
            or health.backend
            or "")
        foreground: bool | None = None
        if isinstance(getattr(health, "foreground", None), bool):
            foreground = bool(health.foreground)
        elif isinstance(getattr(result, "foreground", None), bool):
            foreground = bool(result.foreground)
        return {
            "occlusion_state": getattr(capture, "occlusion_state", None),
            "foreground": foreground,
            "backend": backend,
            "occluded": None if not health.occluded else True,
            "capture_reading": bool(health.reading),
        }

    def _apply_detector(self, state: MatchState, image, detections: list[Any] | None,
                        *, frame_id: int, observed_at: float | None = None,
                        observation: dict[str, Any] | None = None
                        ) -> dict[str, Any]:
        """Consume the due unit observation and commit its trajectory units.

        ``detections`` 由 unit 任务的 :meth:`_detect_ui` 产出：``None``（模型
        不可用/推理失败）与 ``[]``（真实空棋盘）是不同的证据——前者保留已确认
        的 last-known units（``observed=False``）且不推进丢失计数，后者才推进
        3 帧缺失语义。ROI 区域校准已随 OCR 链路移除。
        """
        source_at = time.time() if observed_at is None else float(observed_at)
        gate = observation or {}
        with self._unit_lock:
            units, diagnostics = self._unit_tracker.update_observations(
                detections,
                image.size,
                frame_id=frame_id,
                observed_at=source_at,
                detector_available=detections is not None,
                observation_valid=detections is not None,
                status=("ok" if detections is not None
                        else getattr(self, "_ui_detect_status",
                                     "model_unavailable")),
                occlusion_state=gate.get("occlusion_state"),
                foreground=gate.get("foreground"),
                backend=str(gate.get("backend") or ""),
                occluded=gate.get("occluded"),
                capture_reading=gate.get("capture_reading"),
            )
            diagnostics = dict(diagnostics)
            diagnostics["captured_at"] = round(source_at, 3)
            self._last_unit_diagnostics = copy.deepcopy(diagnostics)

        for unit in units:
            if not getattr(unit, "first_seen_turn", 0):
                unit.first_seen_turn = int(state.turn_number or 0)
        # 单位身份保持感知/轨迹原样：未绑定 card_id 的单位不得借区域观察命名。
        state.units = units
        return diagnostics

    # -------------------------------------------------------------- 事件分发

    def _record_events(self, events: list[GameEvent]) -> None:
        dry_run = _boolean(self._cfg.get("dry_run"), False)
        for event in events:
            record = event.to_dict()
            self._events_log.append(record)
            del self._events_log[:-30]
            if dry_run:
                self._append_event_log(record)
            elif event.kind == E_MATCH_END:
                self._schedule_persist(event)

    @staticmethod
    def _chatter_is_grounded(state: MatchState) -> bool:
        """Whether the situation contains any verified board/card fact.

        A "盘面平稳" chatter is only allowed when there is a supported unit
        observation (observed or retained) or a fresh card observation.  When
        the UI capabilities cannot verify a board, silence is the honest reply.
        """
        situation = situation_payload(state)
        facts = situation.get("facts") if isinstance(situation, dict) else None
        units = (facts or {}).get("units") or {}
        counts = units.get("counts") or {}
        if units.get("supported") and isinstance(counts, dict):
            try:
                if (int(counts.get("observed", 0))
                        + int(counts.get("retained", 0))) > 0:
                    return True
            except (TypeError, ValueError):
                pass
        if situation.get("recognized_cards"):
            return True
        return False

    def _grounded_candidates(self, candidates: list[GameEvent],
                             state: MatchState) -> list[GameEvent]:
        if self._chatter_is_grounded(state):
            return list(candidates)
        return [event for event in candidates if event.kind != E_CHATTER]

    def _dispatch_candidates(self, candidates: list[GameEvent],
                             state: MatchState, *, session_id: str,
                             scenario: str, context_token: str | None,
                             token_epoch: int | None,
                             observed_at: float) -> None:
        """Run the single dispatcher tick outside the lifecycle lock."""
        target = resolve_target_lanlan(self)
        tick = getattr(self.dispatcher, "tick", None)
        if not callable(tick):
            return
        candidates = self._grounded_candidates(candidates, state)
        # Tracker events are stamped with its own wall clock, which can be a
        # fraction of a millisecond after the capture's observed_at.  Use the
        # newest source timestamp so a fresh event is never labelled future.
        dispatch_now = observed_at
        for event in candidates:
            try:
                dispatch_now = max(dispatch_now, float(getattr(event, "ts", 0.0)))
            except (TypeError, ValueError):
                continue
        # Never upgrade a stale tick to the current token.  The token captured
        # at perception entry is the only one this tick may use.
        if token_epoch is not None:
            try:
                if int(self.gate.epoch) != int(token_epoch):
                    self.timeline.record(
                        "dispatcher", "stale_epoch", ts=observed_at,
                        reason="perception_epoch_changed",
                        detail={"token_epoch": int(token_epoch),
                                "gate_epoch": int(self.gate.epoch)})
                    return
            except Exception:
                pass
        try:
            tick(
                candidates, state,
                scenario=scenario,
                session_id=session_id,
                match_id=self.tracker.game_number,
                target_lanlan=target,
                context_token=context_token,
                now=dispatch_now,
            )
        except TypeError:
            # Transitional compatibility while older C revisions are loading.
            tick(candidates, state, context_token=context_token, now=dispatch_now)
            return
        except Exception as exc:
            self.logger.warning("dispatcher tick failed: {}: {}", type(exc).__name__, exc)
            return

    def _publish_live_state(self, state: MatchState, *, session_id: str,
                            context_token: str | None,
                            token_epoch: int | None,
                            observed_at: float) -> None:
        allowed, _blocked = self._export_allowed()
        if not allowed:
            # consent=false / dry-run / paused / stopped: no enter, no read,
            # and no cleanup push that would bypass the gate policy.
            return
        if token_epoch is not None:
            try:
                if int(self.gate.epoch) != int(token_epoch):
                    return
            except Exception:
                pass
        if not state.situation:
            validity = self._context_validity(observed_at)
            state.situation = build_situation(
                state,
                models=self._models_payload(),
                observations=self._recognized_cards_payload(),
                session_id=session_id,
                frame_id=self._last_ui_frame_id,
                observed_at=observed_at,
                now=observed_at,
                context_valid=bool(validity["valid"]),
                reason=validity["reason"],
                ui_observed_at=self._last_ui_observation_at,
                events=[],
                unit_diagnostics=self._last_unit_diagnostics,
                observation_valid=state.unit_observation_valid,
                scenario=self.scenario.scenario,
                ui_signals=self._last_ui_signals,
                scene_confidence=self._last_ui_scene_confidence,
            )
        target = resolve_target_lanlan(self)
        # Always ask the publisher to enter/refresh the context.  It is
        # idempotent (coalesces unchanged target/session/instructions/TTL) and
        # owns route-change cleanup; the local marker is diagnostic only and
        # must never skip TTL refresh or an active-character change.
        try:
            entered = self.publisher.enter(
                state,
                text=situation_context_text(state.situation),
                target=target,
                session_id=session_id,
                context_token=context_token,
                now=observed_at,
                capabilities=state.situation.get("capabilities")
                if isinstance(state.situation, dict) else None,
            )
        except Exception as exc:
            entered = None
            self.logger.warning("context enter failed: {}: {}",
                                type(exc).__name__, exc)
        entered_ok = bool(getattr(entered, "delivered", False))
        if entered_ok:
            with self._lifecycle_lock:
                self._read_context_entered_token = str(context_token or "")
                self._read_context_entered_session = str(session_id or "")
                self._read_context_entered_fingerprint = str(
                    getattr(entered, "fingerprint", "") or "")
        else:
            # failed/suppressed enter means the host has no current context
            # explanation; do not push a read that pretends it exists.
            return
        text = build_live_state_text(
            state, session_id=session_id).replace(
            '"query_tool":"kards_live_state"',
            f'"query_tool":"{_LIVE_STATE_TOOL_NAME}"',
        )
        try:
            # The dispatcher read channel and this tick share one gate token.
            self.dispatcher.push_read(
                state, text=text, target=target, session_id=session_id,
                context_token=context_token, now=observed_at)
        except Exception as exc:
            self.logger.warning("read publish failed: {}: {}", type(exc).__name__, exc)

    def _push_live_state(self, state: MatchState) -> None:
        """Compatibility helper for callers/tests that publish a live state."""
        try:
            token = self.gate.get_token()
            token_epoch = int(self.gate.epoch)
        except Exception:
            token = None
            token_epoch = None
        with self._lifecycle_lock:
            session_id = self._session_id or self._ensure_session_locked()
        observed_at = time.time()
        if not state.situation:
            # Explicit helper call: the caller supplied a state to publish, so
            # build a coherent manual snapshot instead of letting the publisher
            # reject an empty/context-invalid situation.
            state.situation = build_situation(
                state,
                models=self._models_payload(),
                observations=self._recognized_cards_payload(),
                session_id=session_id,
                frame_id=self._last_ui_frame_id or 0,
                observed_at=observed_at,
                now=observed_at,
                context_valid=True,
                reason="manual_publish",
                ui_observed_at=observed_at,
                unit_diagnostics=self._last_unit_diagnostics,
                observation_valid=state.unit_observation_valid,
                scenario=self.scenario.scenario,
                ui_signals=self._last_ui_signals,
                scene_confidence=self._last_ui_scene_confidence,
            )
        self._publish_live_state(
            state, session_id=session_id, context_token=token,
            token_epoch=token_epoch, observed_at=observed_at)

    def _on_delivered(self, event: GameEvent, state: MatchState,
                      metadata: Any = None) -> None:
        """Called exactly once after the gate reports an explicit acceptance."""
        meta = metadata if isinstance(metadata, dict) else {}
        session = str(meta.get("session_id")
                      or (event.details or {}).get("session_id") or "")
        with self._lifecycle_lock:
            if session and self._session_id and session != self._session_id:
                return
            try:
                self.tracker.mark_delivered(event)
            except Exception:
                pass
        if event.kind == E_MATCH_END and not _boolean(self._cfg.get("dry_run"), False):
            self._schedule_persist(event)

    def _append_event_log(self, record: dict[str, Any]) -> None:
        try:
            path = self.data_path("events.jsonl")
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            self.logger.warning("event log write failed: {}", exc)

    # ---------------------------------------------------------------- 持久化

    def _schedule_persist(self, event: GameEvent) -> None:
        details = event.details or {}
        summary = {
            "game": details.get("game"),
            "result": details.get("result"),
            "played": (details.get("ledger") or {}).get("played", []),
            "died": (details.get("ledger") or {}).get("died", []),
            "ended": (details.get("ledger") or {}).get("ended", time.time()),
            "session_id": details.get("session_id", ""),
        }
        key = f"{summary.get('session_id')}|{summary.get('game')}|{details.get('identity', '')}"
        with self._lifecycle_lock:
            if key in self._persisted_match_keys:
                return
            self._persisted_match_keys.add(key)
            loop = self._loop
        if loop is not None and loop.is_running():
            coroutine = self._persist_summary(summary)
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            try:
                if running is loop:
                    future = asyncio.create_task(coroutine)
                else:
                    future = asyncio.run_coroutine_threadsafe(coroutine, loop)
            except Exception as exc:
                self.logger.warning("persist scheduling failed: {}: {}",
                                    type(exc).__name__, exc)
                coroutine.close()
                with self._lifecycle_lock:
                    self._pending_persist.append(summary)
                return
            with self._lifecycle_lock:
                self._persist_futures.add(future)
            future.add_done_callback(self._persist_done)
            return
        with self._lifecycle_lock:
            self._pending_persist.append(summary)

    def _persist_done(self, future: Any) -> None:
        with self._lifecycle_lock:
            self._persist_futures.discard(future)
        try:
            future.result()
        except Exception as exc:
            self.logger.warning("persist match failed: {}: {}",
                                type(exc).__name__, exc)

    async def _drain_persist_futures(self, timeout: float = 3.0) -> None:
        with self._lifecycle_lock:
            futures = list(self._persist_futures)
            pending = list(self._pending_persist)
            self._pending_persist.clear()
        for summary in pending:
            try:
                await self._persist_summary(summary)
            except Exception as exc:
                self.logger.warning("pending persist failed: {}: {}", type(exc).__name__, exc)
        for future in futures:
            try:
                if isinstance(future, asyncio.Future):
                    await asyncio.wait_for(asyncio.shield(future), timeout)
                else:
                    await asyncio.wait_for(asyncio.wrap_future(future), timeout)
            except Exception as exc:
                self.logger.warning("persist future did not finish: {}: {}",
                                    type(exc).__name__, exc)

    async def _persist_summary(self, summary: dict[str, Any]) -> None:
        store = getattr(self, "store", None)
        if store is None:
            return
        try:
            stored = await store.get("matches", [])
            matches = list(_store_value(stored, []) or [])
            await store.set("matches", (matches + [summary])[-50:])
            stored_records = await store.get("records", {})
            records = dict(_store_value(stored_records, {}) or {})
            tally = dict(records.get("overall", {"won": 0, "lost": 0}))
            key = "won" if summary.get("result") == "won" else "lost"
            tally[key] = int(tally.get(key, 0)) + 1
            records["overall"] = tally
            await store.set("records", records)
        except Exception as exc:
            self.logger.warning("persist match failed: {}: {}", type(exc).__name__, exc)

    async def _persist_match(self, event: GameEvent) -> None:  # compatibility
        details = event.details or {}
        await self._persist_summary({
            "game": details.get("game"),
            "result": details.get("result"),
            "played": (details.get("ledger") or {}).get("played", []),
            "died": (details.get("ledger") or {}).get("died", []),
            "ended": (details.get("ledger") or {}).get("ended", time.time()),
            "session_id": details.get("session_id", ""),
        })

    # ---------------------------------------------------------------- entries

    @ui.action(id="monitor_start", label="开始监控")
    @plugin_entry(id="monitor_start", name="开始监控",
                  description="启动 KARDS 被动读屏监控；无有效帧时识别与解说自动暂停。")
    async def monitor_start(self, **_):
        started = await self._start_pipeline()
        if not started:
            reason = self._last_start_error or "start_failed"
            return Err(SdkError(f"监控启动失败：{reason}"))
        return Ok({"monitoring": self._monitoring, "paused": self._user_paused})

    @ui.action(id="monitor_stop", label="停止监控")
    @plugin_entry(id="monitor_stop", name="停止监控",
                  description="停止监控并让实时读上下文过期。")
    async def monitor_stop(self, **_):
        await self._stop_pipeline(reason="user_stop")
        return Ok({"monitoring": False})

    @ui.action(id="monitor_pause", label="暂停解说")
    @plugin_entry(id="monitor_pause", name="暂停解说",
                  description="暂停识别与推送并让实时读上下文过期；采集/监控保持运行。")
    async def monitor_pause(self, **_):
        await self._pause_pipeline("user_pause")
        return Ok({"monitoring": self._monitoring, "paused": self._user_paused})

    @ui.action(id="monitor_resume", label="恢复解说")
    @plugin_entry(id="monitor_resume", name="恢复解说",
                  description="恢复识别与推送；首帧重新建立基线，不补报暂停期间事件。")
    async def monitor_resume(self, **_):
        await self._resume_pipeline()
        return Ok({"monitoring": self._monitoring, "paused": self._user_paused})

    @ui.action(id="monitor_status", label="刷新状态")
    @plugin_entry(id="monitor_status", name="查询状态",
                  description="返回采集健康/reading、场景上下文、检测器注册表、投递统计与诊断时间线；"
                              "无有效帧或分类不可信时 paused=true，识别与解说暂停。")
    async def monitor_status(self, **_):
        return Ok(self._status_payload())

    @ui.action(id="monitor_diagnostics", label="拉取诊断")
    @plugin_entry(id="monitor_diagnostics", name="诊断信息",
                  description="返回扩展诊断：最近时间线、检测器诊断、gate 回执、投递熔断与最后快照元数据。")
    async def monitor_diagnostics(self, **_):
        payload = self._status_payload()
        payload["timeline"] = self.timeline.as_dict(limit=50)
        snapshot = self._previous_snapshot
        payload["last_snapshot"] = (self._public_snapshot_dict(snapshot)
                                    if snapshot is not None else None)
        payload["gate_records"] = self.gate.recent_records(limit=30)
        payload["simulated_records"] = list(getattr(self.dispatcher, "simulated_records", []) or [])[-20:]
        return Ok(payload)

    @ui.action(id="target_set", label="设置目标")
    @plugin_entry(id="target_set", name="设置目标",
                  description="设置固定目标猫娘；留空=跟随当前角色。绝不伪造角色名。",
                  input_schema={"type": "object", "properties": {
                      "target": {"type": "string"}}})
    async def target_set(self, *, target: Any = "", target_lanlan: Any = None, **_):
        value = clean_target_lanlan(target if target_lanlan is None else target_lanlan)
        await self.config.update({"kards": {"target_lanlan": value}})
        merged = dict(self._cfg)
        merged["target_lanlan"] = value
        self._apply_config({"kards": merged})
        return Ok({"target_lanlan_present": bool(value)})

    @ui.action(id="monitor_test_say", label="连接测试")
    @plugin_entry(id="monitor_test_say", name="连接测试",
                  description="一次真实连接测试：必须显式授权、真实目标、非 dry/consent 关闭；"
                              "回执只表示 SDK 本地提交，不代表 LLM 生成或 TTS 播放。",
                  input_schema={"type": "object", "properties": {
                      "target": {"type": "string"},
                      "text": {"type": "string"},
                      "authorized_real_test": {"type": "boolean"}},
                      "required": ["authorized_real_test"]})
    async def monitor_test_say(self, *, target: Any = "", text: Any = "",
                               authorized_real_test: Any = False, **_):
        if authorized_real_test is not True:
            return Err(SdkError("真实连接测试需要显式 authorized_real_test=true"))
        configured = resolve_target_lanlan(self)
        explicit = clean_target_lanlan(target)
        chosen = explicit or configured
        if not chosen:
            return Err(SdkError("无法确认目标角色，拒绝伪造 target_lanlan"))
        gate = self.gate
        if not gate.is_running:
            return Err(SdkError("gate 未运行（监控已停止/暂停）"))
        if gate.paused:
            return Err(SdkError("gate 已暂停，拒绝真实连接测试"))
        if gate.config.dry_run or gate.config.simulated:
            return Err(SdkError("dry-run 下不会进行真实连接测试"))
        if not gate.config.consent:
            return Err(SdkError("llm_data_consent=false，禁止任何真实推送"))
        guard = getattr(self.dispatcher, "_guard", self.guard)
        verdict = None
        try:
            verdict = guard.allow(critical=False, terminal=False, now=time.time())
        except TypeError:
            try:
                verdict = guard.allow(now=time.time())
            except Exception:
                verdict = None
        if verdict is not None and not bool(getattr(verdict, "allowed", verdict)):
            return Err(SdkError(f"输出安全门拒绝：{getattr(verdict, 'reason', 'safety_paused')}"))

        line = " ".join(str(text or "连接测试：本条只验证本地推送通道。").split())[:80]
        prompt = build_commentary_prompt(
            f"连接测试（非对局，无历史）：{line}", MatchState(),
            mode=self.arbiter.config.mode,
            max_chars=max(1, int(self._cfg.get("reply_max_chars", 28))))
        metadata = {
            "kind": "connection_test",
            "context_type": "kards_connection_test",
            "delivery_intent": "connection_test",
            "battle_reply_contract": "short_tts_line",
            "max_reply_chars": int(self._cfg.get("reply_max_chars", 28)),
            "reply_style_contract": "one_sentence_only",
            "host_callback_contract": "neko.callback.v1",
            "plugin_recommended_reply": True,
            "plugin_owned_output_enabled": bool(
                self._cfg.get("plugin_owned_output_enabled")),
            "match_id": 0,
            "attempt": 1,
            "event_max_age_seconds": float(
                self._cfg.get("battle_event_ttl_seconds", 12.0)),
            "replace_pending_intent": True,
            "interrupt_pending_intent": False,
            "host_capabilities": {
                "replace_pending": "unknown",
                "interrupt_pending": "unknown",
            },
            "non_game": True,
            "history_available": False,
            "no_history": True,
            "local_submission_only": True,
            "sdk_submission_semantics": "immediate_local_submission_not_host_consumption",
            "llm_generated": "unknown",
            "tts_played": "unknown",
            "target_lanlan_present": True,
        }
        # Authorize exactly this call on the gate only.  ``authorized_real_test``
        # does not change the gate epoch, so a legitimate pending battle event
        # is not purged by a diagnostic connection test.
        base_gate_config = gate.config
        try:
            gate.update_config(HostPushConfig(
                dry_run=base_gate_config.dry_run,
                consent=base_gate_config.consent,
                simulated=base_gate_config.simulated,
                max_records=base_gate_config.max_records,
                authorized_real_test=True,
            ))
        except Exception as exc:
            return Err(SdkError(f"无法授权测试：{type(exc).__name__}"))
        try:
            token = self.gate.get_token()
            result: GateResult = self.gate.push(
                "test",
                critical=False,
                terminal=False,
                context_token=token,
                now=time.time(),
                source=self._runtime_id,
                visibility=[],
                ai_behavior="respond",
                parts=[{"type": "text", "text": prompt}],
                priority=5,
                coalesce_key="kards:connection_test",
                target_lanlan=chosen,
                metadata=metadata,
            )
        except Exception as exc:
            return Err(SdkError(f"连接测试失败：{type(exc).__name__}"))
        finally:
            try:
                gate.update_config(HostPushConfig(
                    dry_run=base_gate_config.dry_run,
                    consent=base_gate_config.consent,
                    simulated=base_gate_config.simulated,
                    max_records=base_gate_config.max_records,
                    authorized_real_test=False,
                ))
            except Exception:
                pass
        if result.accepted:
            try:
                guard.mark_output(critical=False, terminal=False, now=time.time())
            except Exception:
                pass
        elif result.rejected and result.retryable:
            try:
                guard.record_failure(now=time.time())
            except Exception:
                pass
        return Ok({
            "accepted": bool(result.accepted),
            "sdk_submission": result.status,
            "status": result.status,
            "reason": result.reason,
            "receipt": result.to_dict(),
            "non_game": True,
            "history": None,
            "llm_generated": "unknown",
            "tts_played": "unknown",
            "target_lanlan_present": True,
            "local_submission_only": True,
        })

    @ui.action(id="mode_set", label="切换模式")
    @plugin_entry(id="mode_set", name="切换模式",
                  description="陪伴 companion / 教练 coach / 静默 silent",
                  input_schema={"type": "object", "properties": {
                      "mode": {"type": "string", "enum": ["companion", "coach", "silent"]}},
                      "required": ["mode"]})
    async def mode_set(self, *, mode: Any = None, **_):
        value = str(mode or "").strip().lower()
        if value not in ("companion", "coach", "silent"):
            return Err(SdkError(f"无效模式 {mode!r}"))
        await self.config.update({"kards": {"mode": value}})
        await self._reload_config()
        return Ok({"mode": value})

    @ui.action(id="dry_run_set", label="切换试运行")
    @plugin_entry(id="dry_run_set", name="切换试运行",
                  description="true=完整模拟仲裁/安全链，但 gate 最终 no-op，只写 events.jsonl。",
                  input_schema={"type": "object", "properties": {
                      "enabled": {"type": "boolean"}}, "required": ["enabled"]})
    async def dry_run_set(self, *, enabled: Any = None, **_):
        flag = bool(enabled) if enabled is not None else not _boolean(
            self._cfg.get("dry_run"), False)
        await self.config.update({"kards": {"dry_run": flag}})
        await self._reload_config()
        return Ok({"dry_run": flag})

    @ui.action(id="perception_settings_set", label="保存感知设置")
    @plugin_entry(id="perception_settings_set", name="感知采样设置",
                  description="严格白名单：采样预设与采集/变化/单位/卡牌四项 FPS。"
                              "未知字段与已移除的 HUD/ROI/录像旧键直接拒绝。",
                  input_schema={
                      "type": "object",
                      "properties": {
                          "perception_preset": {
                              "type": "string",
                              "enum": ["low_cost", "balanced", "quality", "custom"]},
                          "capture_fps": {"type": "number", "minimum": 1, "maximum": 30},
                          "change_fps": {"type": "number", "minimum": 0.5, "maximum": 15},
                          "unit_fps": {"type": "number", "minimum": 0.5, "maximum": 12},
                          "card_fps": {"type": "number", "minimum": 0.5, "maximum": 12},
                      },
                  })
    async def perception_settings_set(self, **kwargs):
        kwargs = {key: value for key, value in kwargs.items()
                  if not str(key).startswith("_")}
        unknown = sorted(set(kwargs) - PERCEPTION_SETTINGS_ACTION_KEYS)
        if unknown:
            return Err(SdkError(f"不支持的设置项：{', '.join(unknown)}"))
        if not kwargs:
            return Err(SdkError("没有可应用的设置项"))
        try:
            patch = PerceptionSettings.validate_changes(
                kwargs, current=self._perception_settings)
        except SettingsValidationError as exc:
            return Err(SdkError(str(exc)))
        if not patch:
            return Err(SdkError("没有可应用的设置项"))
        try:
            await self.config.update({"kards": patch})
        except Exception as exc:
            return Err(SdkError(f"保存感知设置失败：{type(exc).__name__}"))
        merged = dict(self._cfg)
        merged.update(patch)
        self._apply_config({"kards": merged})
        return Ok({
            "updated": sorted(patch),
            "perception_settings": self._perception_settings_payload(),
        })

    @ui.action(id="deck_import", label="导入卡组")
    @plugin_entry(id="deck_import", name="导入卡组",
                  description="粘贴 KARDS 卡组代码（%%xx|…），离线解析并持久化。",
                  input_schema={"type": "object", "properties": {
                      "code": {"type": "string"}}, "required": ["code"]})
    async def deck_import(self, *, code: Any = "", **_):
        try:
            deck = parse_deck_code(str(code or ""))
        except DeckCodeError as exc:
            return Err(SdkError(str(exc)))
        self._deck = deck
        self._cfg["imported_deck_code"] = deck.normalized_code
        await self.config.update({"kards": {"imported_deck_code": deck.normalized_code}})
        return Ok({"main": deck.main_nation, "ally": deck.ally_nation,
                   "cards": len(deck.import_ids)})

    # ---------------------------------------------------------------- 状态面

    def _public_config(self) -> dict[str, Any]:
        """Allowlisted config for UI/diagnostics; never dump arbitrary keys."""
        allowed = (
            "mode", "dry_run", "auto_start_monitoring", "llm_data_consent",
            "chatter_interval_seconds", "cooldown_seconds",
            "critical_cooldown_seconds", "min_priority", "do_not_disturb",
            "perf_mode", "tick_ms", "perception_preset", "capture_fps",
            "change_fps", "unit_fps", "card_fps", "capture_fps_power_saver",
            "catalog_enabled", "catalog_refresh_hours", "vlm_enabled",
            "plugin_owned_output_enabled", "user_chat_quiet_window_seconds",
            "battle_output_quiet_window_seconds",
            "battle_output_backpressure_seconds", "reply_max_chars",
            "battle_event_ttl_seconds",
        )
        out = {key: self._cfg.get(key, _CONFIG_DEFAULTS.get(key))
               for key in allowed}
        out["target_lanlan_present"] = bool(
            clean_target_lanlan(self._cfg.get("target_lanlan")))
        return out

    def _context_validity(self, now: float | None = None) -> dict[str, Any]:
        """Unified trusted-context check for status, tools and read publishing.

        Freshness is measured from the last **new UI-detector sample**, not from
        the capture thread's latest frame.  A static screen keeps producing new
        capture timestamps; without an independent UI heartbeat that must not
        look like a fresh board.
        """
        current = time.time() if now is None else float(now)
        max_age = min(SITUATION_FRESH_MAX_AGE_SECONDS,
                      max(0.5, _tick_seconds(self._cfg) * 3.0))
        with self._lifecycle_lock:
            snapshot = self._last_trusted_snapshot
            monitoring = self._monitoring
            paused = self._user_paused
            session_id = self._session_id
            tracker_session = self.tracker.session_id
            in_match = self.tracker.in_match
            ui_observed_at = self._last_ui_observation_at
            ui_frame_id = int(self._last_ui_frame_id or 0)
            ui_failed = bool(self._ui_observation_failed)
        scene_snapshot = self.scenario.snapshot()
        age_ms: float | None = None
        source_age_ms: float | None = None
        ui_age_ms: float | None = None
        reason = ""
        if not monitoring:
            reason = "monitoring_stopped"
        elif paused:
            reason = "user_paused"
        elif snapshot is None:
            reason = "no_trusted_snapshot"
        elif not snapshot.trusted:
            reason = "snapshot_not_trusted"
        else:
            source_age = max(0.0, current - float(snapshot.observed_at))
            source_age_ms = round(source_age * 1000.0, 1)
            measured_at = (float(ui_observed_at)
                           if ui_observed_at is not None
                           else float(snapshot.observed_at))
            age = max(0.0, current - measured_at)
            age_ms = round(age * 1000.0, 1)
            if ui_observed_at is not None:
                ui_age_ms = age_ms
            if ui_failed:
                reason = "ui_observation_unavailable"
            elif age > max_age:
                reason = "context_stale"
            elif snapshot.state.scene != SCENE_MATCH or not in_match:
                reason = "scene_not_match"
            elif not scene_snapshot.get("context_valid"):
                reason = "scenario_context_invalid"
            elif not session_id or tracker_session != session_id:
                reason = "session_changed"
        return {
            "valid": not reason,
            "reason": reason,
            "age_ms": age_ms,
            "source_age_ms": source_age_ms,
            "ui_age_ms": ui_age_ms,
            "max_age_ms": round(max_age * 1000.0, 1),
            "snapshot_seq": None if snapshot is None else snapshot.seq,
            "ui_frame_id": ui_frame_id or None,
            "session_id": session_id,
        }

    @staticmethod
    def _public_health(health: CaptureHealthSnapshot) -> dict[str, Any]:
        payload = health.to_dict()
        # Capture backends can only report occlusion positively; the current
        # implementations always set False, which must not be rendered as
        # "not occluded".  Publish unknown instead of a false claim.
        payload["occluded"] = None
        payload["occlusion_detection"] = "unavailable"
        return payload

    @classmethod
    def _public_snapshot_dict(cls, snapshot: RuntimeSnapshot) -> dict[str, Any]:
        payload = snapshot.to_public_dict()
        capture = payload.get("capture")
        if isinstance(capture, dict):
            capture["occluded"] = None
            capture["occlusion_detection"] = "unavailable"
        return payload

    def _current_health(self) -> CaptureHealthSnapshot:
        capture = self._capture
        if capture is not None:
            probe = getattr(capture, "health_snapshot", None)
            if callable(probe):
                return probe()
            return self._last_capture_health
        if self._monitoring:
            return CaptureHealthSnapshot(
                status="frame_unavailable", reason="capture_unavailable", reading=False)
        reason = "monitoring_stopped" if self._pipeline_started else "capture_not_started"
        return CaptureHealthSnapshot(status="stopped", reason=reason, reading=False)

    def _capture_payload(self, health: CaptureHealthSnapshot) -> dict[str, Any]:
        payload = self._public_health(health)
        snapshot = self._previous_snapshot
        payload["frame"] = snapshot.frame.to_dict() if snapshot is not None else None
        if snapshot is not None:
            payload["seq"] = snapshot.seq
            payload["observed_at"] = snapshot.observed_at
            payload["trusted"] = snapshot.trusted
        else:
            payload["seq"] = 0
            payload["observed_at"] = None
            payload["trusted"] = False
        validity = self._context_validity()
        payload["context_valid"] = bool(validity["valid"])
        payload["context_reason"] = validity["reason"]
        payload["context_age_ms"] = validity["age_ms"]
        payload["snapshot_seq"] = validity["snapshot_seq"]
        payload["classification_valid"] = self._classification_valid
        payload["user_paused"] = self._user_paused
        return payload

    def _detector_payload(self) -> dict[str, Any]:
        validity = self._context_validity()
        paused = not bool(validity["valid"])
        with self._lifecycle_lock:
            tracker_snapshot = self.tracker.snapshot()
            tracker_view = {
                "in_match": self.tracker.in_match,
                "game_number": self.tracker.game_number,
                "session_id": self.tracker.session_id,
            }
        with self._unit_lock:
            unit_tracker = self._unit_tracker.snapshot()
            unit_last = dict(self._last_unit_diagnostics)
        return {
            "registry": [detector.name for detector in self.detectors.detectors],
            "last": list(self._last_detector_diagnostics),
            "paused": paused,
            "unit_tracker": unit_tracker,
            "unit_last": unit_last,
            "match_tracker": {
                **tracker_view,
                "delivered": tracker_snapshot.get("delivered", 0),
                "rebase_pending": tracker_snapshot.get("rebase_pending", False),
            },
        }

    def _delivery_payload(self) -> dict[str, Any]:
        target = resolve_target_lanlan(self)
        gate_snapshot = self.gate.snapshot()
        guard_snapshot: Any = {}
        try:
            guard_snapshot = self.guard.snapshot()
        except Exception:
            guard_snapshot = {}
        return {
            "stats": self.dispatcher.stats(),
            "pending": self.dispatcher.pending,
            "breaker": self.dispatcher.breaker_snapshot().to_dict(),
            "mode": self.arbiter.config.mode,
            "target_lanlan_present": bool(target),
            "coalesce_key": getattr(self.dispatcher.config, "coalesce_key", ""),
            "reply_max_chars": getattr(self.dispatcher.config, "reply_max_chars", 28),
            "quiet_window": {
                "user_chat_seconds": self._cfg.get("user_chat_quiet_window_seconds"),
                "battle_output_seconds": self._cfg.get("battle_output_quiet_window_seconds"),
                "backpressure_seconds": self._cfg.get("battle_output_backpressure_seconds"),
            },
            "gate": {
                "phase": gate_snapshot.get("phase"),
                "epoch": gate_snapshot.get("epoch"),
                "simulating": gate_snapshot.get("simulating"),
                "paused": gate_snapshot.get("paused"),
                "real_test_used": gate_snapshot.get("real_test_used"),
                "config": gate_snapshot.get("config"),
            },
            "output_guard": guard_snapshot,
            "simulated_records": list(
                getattr(self.dispatcher, "simulated_records", []) or [])[-10:],
        }

    def _pipeline_payload(self, health: CaptureHealthSnapshot) -> dict[str, Any]:
        loop_task = self._loop_task
        scene = self.scenario.snapshot()
        validity = self._context_validity()
        paused = (not bool(health.reading) or self._capture_pause_active
                  or self._user_paused or not bool(validity["valid"]))
        return {
            "monitoring": self._monitoring,
            "started": self._pipeline_started,
            "loop_running": bool(loop_task is not None and not loop_task.done()),
            "paused": paused,
            "pause_reason": (self._pause_reason if not self._user_paused
                             else "user_pause"),
            "start_error": self._last_start_error,
            "user_paused": self._user_paused,
            "tick_seconds": _loop_seconds(self._cfg),
            "capture_fps": _capture_fps(self._cfg),
            "change_fps": self._perception_settings.change_fps,
            "unit_fps": self._perception_settings.unit_fps,
            "card_fps": self._perception_settings.card_fps,
            "mode": self._cfg.get("mode"),
            "dry_run": _boolean(self._cfg.get("dry_run"), False),
            "scenario": scene.get("scenario"),
            "context_valid": bool(validity["valid"]),
            "context_reason": validity["reason"],
            "context_age_ms": validity["age_ms"],
            "confirmed_exit": scene.get("confirmed_exit"),
            "classification_valid": self._classification_valid,
            "session_id": self._session_id,
            "auto_start_monitoring": _boolean(self._cfg.get("auto_start_monitoring"), True),
        }

    def _status_payload(self) -> dict[str, Any]:
        health = self._current_health()
        capture = self._capture_payload(health)
        snapshot = self._previous_snapshot
        scene_snapshot = self.scenario.snapshot()
        capture_backend = (
            self._capture.backend if self._capture is not None else None)
        return {
            "schema_version": STATUS_SCHEMA_VERSION,
            "runtime_plugin_id": self._runtime_id,
            "source_identity": self._source_identity,
            "config": self._public_config(),
            "pipeline": self._pipeline_payload(health),
            "capture": capture,
            "detectors": self._detector_payload(),
            "delivery": self._delivery_payload(),
            "perception_settings": self._perception_settings_payload(),
            "models": self._models_payload(),
            "recognized_cards": self._recognized_cards_payload(),
            "fps_measured": self._measured_fps_payload(),
            "scenario": scene_snapshot,
            "timeline": self.timeline.as_dict(limit=20),
            "last_snapshot": (self._public_snapshot_dict(snapshot)
                              if snapshot is not None else None),
            # ---- 兼容旧运行状态字段 ----
            "monitoring": self._monitoring,
            "window_found": bool(self._capture and self._capture.window),
            "foreground": bool(self._capture and self._capture.foreground),
            "capture_backend": capture_backend,
            "capture_stats": self._capture.stats if self._capture else {},
            "scene": self._last_scene,
            "mode": self._cfg.get("mode"),
            "dry_run": _boolean(self._cfg.get("dry_run"), False),
            "in_match": self.tracker.in_match,
            "game_number": self.tracker.game_number,
            "context_valid": bool(scene_snapshot.get("context_valid")),
            "classification_valid": self._classification_valid,
            "detector_loaded": bool(
                getattr(self.ui_detector, "available",
                        callable(getattr(self.ui_detector, "detect", None)))),
            "catalog": {"ready": self.catalog.ready, "cards": self.catalog.size(),
                        "fetched_at": self.catalog.fetched_at, "error": self.catalog.last_error,
                        "builtin": self.catalog.builtin},
            "deck": None if self._deck is None else {
                "main": self._deck.main_nation, "ally": self._deck.ally_nation,
                "cards": len(self._deck.import_ids)},
            "recent_events": self._events_log[-10:],
        }

    def _export_allowed(self) -> tuple[bool, str]:
        """Hard data-export policy shared by gate-backed tools."""
        if self.gate.phase == "stopped":
            return False, "stopped"
        if self.gate.phase == "stopping":
            return False, "stopping"
        if self.gate.paused:
            return False, "paused"
        if not self.gate.config.consent:
            return False, "consent_required"
        if self.gate.config.dry_run or self.gate.config.simulated:
            return False, "dry_run"
        return True, ""

    # ------------------------------------------------------------- llm tools

    @ui.context(id="dashboard", title="KARDS 陪玩")
    async def dashboard_context(self, **_):
        """Hosted UI context.  ``capture.reading`` / ``pipeline.context_valid``
        are false whenever no trusted frame or classification is available;
        recognition and commentary are paused then, and the UI must not present
        old-frame state as current."""
        status = await self.monitor_status()
        payload = _result_payload(status)
        return {"status": payload if isinstance(payload, dict) else {}}

    @llm_tool(name="kards_get_live_state",
              description="查询 KARDS 统一局势视图：已确认场景、当前/保留/未知侧别单位、"
                          "短时卡面观察及不支持能力；总部血量、资源、当前攻防、回合归属未知，"
                          "不编造。无有效帧/分类、consent 关闭或 dry-run 时返回 paused=true 且 "
                          "state=null，不得用旧画面推断局势。玩家问局势时用。",
              parameters={"type": "object", "properties": {}})
    async def kards_get_live_state(self, **_):
        health = self._current_health()
        validity = self._context_validity()
        allowed, blocked = self._export_allowed()
        usable = bool(allowed and validity["valid"])
        with self._lifecycle_lock:
            snapshot = self._last_trusted_snapshot
            current = (snapshot.state if snapshot is not None
                       else (self.tracker.current or MatchState()))
        situation = situation_payload(current) if usable else None
        if usable and not situation:
            # Same-snapshot fallback for states constructed before the unified
            # builder was wired; never expose raw tracker internals.
            observed_at = (snapshot.observed_at if snapshot is not None
                           else time.time())
            frame_id = (snapshot.frame.frame_id if snapshot is not None else 0)
            situation = build_situation(
                current,
                models=self._models_payload(),
                observations=self._recognized_cards_payload(),
                session_id=self._session_id,
                frame_id=frame_id,
                observed_at=observed_at,
                now=time.time(),
                context_valid=bool(validity["valid"]),
                reason=validity["reason"],
                ui_observed_at=self._last_ui_observation_at,
                unit_diagnostics=self._last_unit_diagnostics,
                observation_valid=current.unit_observation_valid,
                scenario=self.scenario.scenario,
                ui_signals=self._last_ui_signals,
                scene_confidence=self._last_ui_scene_confidence,
            )
        reason = blocked or validity["reason"] or ("" if usable else "not_in_match")
        return {
            "monitoring": self._monitoring,
            "paused": not usable,
            "reason": reason,
            "capture": self._public_health(health),
            "context_valid": bool(validity["valid"]),
            "context_age_ms": validity["age_ms"],
            "source_age_ms": validity.get("source_age_ms"),
            "ui_age_ms": validity.get("ui_age_ms"),
            "snapshot_seq": validity["snapshot_seq"],
            "classification_valid": self._classification_valid,
            "session_id": self._session_id,
            "in_match": bool(getattr(current, "in_match", False)) if usable else False,
            # Unified view only: cards/units/capabilities/limitations come from
            # the same frozen situation used by read/respond.
            "state": situation if usable else None,
            "situation": situation if usable else None,
            # Blocked tools expose no event details, deck or game facts.
            "recent_events": (
                list(((situation or {}).get("facts") or {}).get("events") or [])[:10]
                if usable else []),
            "deck": (None if (not usable or self._deck is None) else {
                "main": self._deck.main_nation, "ally": self._deck.ally_nation}),
            "data_policy": {
                "consent": bool(self.gate.config.consent),
                "dry_run": bool(self.gate.config.dry_run),
                "export_allowed": bool(allowed),
            },
        }

    @llm_tool(name="kards_search_cards",
              description="按卡名/关键词查询 KARDS 卡牌资料（费用、攻防、国家、效果文本）。内置卡牌库离线可用。",
              parameters={"type": "object", "properties": {
                  "query": {"type": "string", "description": "卡名或关键词"}},
                  "required": ["query"]})
    async def kards_search_cards(self, *, query: Any = "", **_):
        if not self.catalog.ready:
            return {"output": {"error": "卡牌库加载失败，请检查插件内置数据文件"}, "is_error": True}
        return {"cards": [c.to_dict() for c in self.catalog.search(str(query or ""), limit=6)]}

    @llm_tool(name="kards_import_deck",
              description="导入玩家粘贴的 KARDS 卡组代码（形如 %%45|xxxxxx;yy;…）。",
              parameters={"type": "object", "properties": {
                  "code": {"type": "string"}}, "required": ["code"]})
    async def kards_import_deck(self, *, code: Any = "", **_):
        result = await self.deck_import(code=code)
        data = _result_payload(result)
        if _result_is_error(result):
            return {"output": data or {"error": "解析失败"}, "is_error": True}
        return {"output": data}

    @llm_tool(name="kards_get_match_history",
              description="查询近期 KARDS 对局记录与胜负统计（复盘用）。consent 关闭或 dry-run 时不返回对局数据。",
              parameters={"type": "object", "properties": {
                  "limit": {"type": "integer", "description": "返回条数，默认 10"}}})
    async def kards_get_match_history(self, *, limit: Any = 10, **_):
        allowed, blocked = self._export_allowed()
        if not allowed:
            return {"matches": [], "records": {}, "blocked": blocked}
        store = getattr(self, "store", None)
        if store is None:
            return {"matches": [], "records": {}}
        stored = await store.get("matches", [])
        matches = list(_store_value(stored, []) or [])
        stored_records = await store.get("records", {})
        records = _store_value(stored_records, {}) or {}
        try:
            count = max(1, min(50, int(limit)))
        except (TypeError, ValueError):
            count = 10
        return {"matches": matches[-count:], "records": records}
