"""前端契约：SceneState / CompanionEvent / CbqConfig。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

IDLE = "IDLE"
IN_GAME = "IN_GAME"
DEGRADED = "DEGRADED"

ALL_SCENARIOS = (IDLE, IN_GAME, DEGRADED)

CAT_LIFECYCLE = "lifecycle"
CAT_VISION = "vision"
CAT_CHATTER = "chatter"
CAT_MATCH = "match"  # 对局阶段/结果（无屏日志信号）


@dataclass(frozen=True)
class EventSpec:
    event_id: str
    category: str
    priority: int
    preempt: bool
    cooldown_seconds: float
    severity_warning: int
    severity_critical: int

    def severity_for(self, level: str) -> int:
        return self.severity_critical if level == "critical" else self.severity_warning


EVENT_CATALOG: dict[str, EventSpec] = {
    "game_started": EventSpec("game_started", CAT_LIFECYCLE, 10, True, -1, 8, 8),
    "game_stopped": EventSpec("game_stopped", CAT_LIFECYCLE, 10, True, -1, 8, 8),
    "enemy_spotted": EventSpec("enemy_spotted", CAT_VISION, 5, False, 8, 3, 4),
    "enemy_nearby": EventSpec("enemy_nearby", CAT_VISION, 7, True, 5, 5, 8),
    "enemy_left_view": EventSpec("enemy_left_view", CAT_VISION, 2, False, 12, 2, 2),
    "multi_threat": EventSpec("multi_threat", CAT_VISION, 6, False, 8, 4, 5),
    # 空闲时主动闲聊（无敌人/低威胁）：最低优先级，只在没有更重要事件时才轮到它
    "chatter": EventSpec("chatter", CAT_CHATTER, 1, False, -1, 1, 1),
    # 无屏日志信号（LogSensor 消费端）
    "character_select": EventSpec("character_select", CAT_MATCH, 4, False, 20.0, 2, 2),
    "match_started": EventSpec("match_started", CAT_MATCH, 3, False, 20.0, 2, 2),
    "player_death": EventSpec("player_death", CAT_MATCH, 8, True, 5.0, 5, 8),
    "match_win": EventSpec("match_win", CAT_MATCH, 6, False, 60.0, 4, 4),
    "match_lose": EventSpec("match_lose", CAT_MATCH, 6, False, 60.0, 3, 3),
}

SCENARIO_GATING: dict[str, frozenset[str]] = {
    IDLE: frozenset({CAT_LIFECYCLE, CAT_MATCH}),
    IN_GAME: frozenset({CAT_LIFECYCLE, CAT_VISION, CAT_CHATTER, CAT_MATCH}),
    # Visual capture may degrade while UE logs still carry reliable phase/result edges.
    DEGRADED: frozenset({CAT_LIFECYCLE, CAT_MATCH}),
}


def category_allowed(scenario: str, category: str) -> bool:
    return category in SCENARIO_GATING.get(scenario, frozenset())


def _clamp(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(v, hi))


def _as_bool(value: Any, default: bool) -> bool:
    """显式解析布尔：避免 bool("false") == True 的字符串陷阱。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    return default


@dataclass
class CbqConfig:
    enabled: bool = True
    dry_run: bool = True
    data_layer_url: str = "http://127.0.0.1:8212"
    data_layer_auto_start: bool = True
    assistant_directory: str = ""
    data_layer_startup_timeout_seconds: float = 45.0
    data_layer_shutdown_timeout_seconds: float = 5.0
    poll_interval_seconds: float = 0.05
    http_timeout_seconds: float = 1.5
    global_rate_limit_seconds: float = 6.0
    critical_preempt_cooldown_seconds: float = 4.0
    output_backpressure_seconds: float = 6.0
    output_event_max_age_seconds: float = 8.0
    vision_output_event_max_age_seconds: float = 3.0
    safety_auto_stop_enabled: bool = True
    safety_window_seconds: float = 60.0
    safety_failure_limit: int = 5
    require_game_process: bool = True
    observability_enabled: bool = False
    data_layer_infer_mode: str = "auto"  # auto | yolo | stub
    data_layer_infer_backend: str = "auto"  # auto | torch | onnx
    data_layer_device: str = "auto"  # auto | cpu | cuda | cuda:N
    data_layer_infer_interval_seconds: float = 0.04  # 数据层推理节流（越小识别帧率越高）
    data_layer_stale_targets_seconds: float = 1.5  # 无进展推理看门狗秒数
    data_layer_weights: str = ""  # empty → encrypted release model, then development ONNX fallback
    # 无屏日志信号源：留空自动定位 Saved/Logs；填绝对路径则精确指定
    data_layer_log_path: str = ""
    # 画面状态识别：disabled | heuristic | ocr | hybrid | templates
    data_layer_classifier: str = "disabled"
    data_layer_classifier_template_dir: str = ""
    data_layer_classifier_confirm: int = 3
    # OCR 在后台执行；间隔从完成时刻起算，并由硬超时限制单次耗时。
    data_layer_ocr_interval_seconds: float = 0.5
    data_layer_ocr_timeout_seconds: float = 3.0
    # 第三人称视角：过滤疑似玩家自身角色的检测框（近距离时自身大且偏下/侧）
    self_filter_enabled: bool = True
    # 空闲闲聊：视野内无敌人/低威胁且距上次闲聊达到该秒数时，猫娘才主动说一句
    chatter_min_interval_seconds: float = 45.0
    # 有敌人（进入战斗/威胁升高）时，闲聊冷却重置为多少秒，避免刚开打还在搭话
    chatter_fight_cooldown_seconds: float = 20.0

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "CbqConfig":
        raw = dict(data or {})
        infer_mode = str(raw.get("data_layer_infer_mode") or "auto").strip().lower()
        if infer_mode not in {"auto", "yolo", "stub"}:
            infer_mode = "auto"
        infer_backend = str(raw.get("data_layer_infer_backend") or "auto").strip().lower()
        if infer_backend not in {"auto", "torch", "onnx"}:
            infer_backend = "auto"
        infer_device = str(raw.get("data_layer_device") or "auto").strip().lower()
        if not infer_device:
            infer_device = "auto"
        classifier = str(raw.get("data_layer_classifier") or "disabled").strip().lower()
        if classifier not in {"disabled", "heuristic", "ocr", "hybrid", "templates"}:
            classifier = "disabled"
        return cls(
            enabled=_as_bool(raw.get("enabled"), True),
            dry_run=_as_bool(raw.get("dry_run"), True),
            data_layer_url=str(raw.get("data_layer_url") or "http://127.0.0.1:8212").rstrip("/"),
            data_layer_auto_start=_as_bool(raw.get("data_layer_auto_start"), True),
            assistant_directory=str(raw.get("assistant_directory") or "").strip(),
            data_layer_startup_timeout_seconds=_clamp(
                raw.get("data_layer_startup_timeout_seconds"), 45.0, 3.0, 180.0
            ),
            data_layer_shutdown_timeout_seconds=_clamp(
                raw.get("data_layer_shutdown_timeout_seconds"), 5.0, 0.1, 60.0
            ),
            poll_interval_seconds=_clamp(raw.get("poll_interval_seconds"), 0.05, 0.02, 5.0),
            http_timeout_seconds=_clamp(raw.get("http_timeout_seconds"), 1.5, 0.2, 10.0),
            global_rate_limit_seconds=_clamp(raw.get("global_rate_limit_seconds"), 6.0, 0.0, 600.0),
            critical_preempt_cooldown_seconds=_clamp(
                raw.get("critical_preempt_cooldown_seconds"), 4.0, 0.0, 120.0
            ),
            output_backpressure_seconds=_clamp(raw.get("output_backpressure_seconds"), 6.0, 0.0, 300.0),
            output_event_max_age_seconds=_clamp(raw.get("output_event_max_age_seconds"), 8.0, 0.0, 120.0),
            vision_output_event_max_age_seconds=_clamp(
                raw.get("vision_output_event_max_age_seconds"), 3.0, 0.0, 30.0
            ),
            safety_auto_stop_enabled=_as_bool(raw.get("safety_auto_stop_enabled"), True),
            safety_window_seconds=_clamp(raw.get("safety_window_seconds"), 60.0, 5.0, 3600.0),
            safety_failure_limit=int(_clamp(raw.get("safety_failure_limit"), 5, 1, 100)),
            require_game_process=_as_bool(raw.get("require_game_process"), True),
            observability_enabled=_as_bool(raw.get("observability_enabled"), False),
            data_layer_infer_mode=infer_mode,
            data_layer_infer_backend=infer_backend,
            data_layer_device=infer_device,
            data_layer_infer_interval_seconds=_clamp(
                raw.get("data_layer_infer_interval_seconds"), 0.04, 0.01, 1.0
            ),
            data_layer_stale_targets_seconds=_clamp(
                raw.get("data_layer_stale_targets_seconds"), 1.5, 0.1, 10.0
            ),
            data_layer_weights=str(raw.get("data_layer_weights") or ""),
            data_layer_log_path=str(raw.get("data_layer_log_path") or ""),
            data_layer_classifier=classifier,
            data_layer_classifier_template_dir=str(
                raw.get("data_layer_classifier_template_dir") or ""
            ),
            data_layer_classifier_confirm=int(
                _clamp(raw.get("data_layer_classifier_confirm"), 3, 1, 30)
            ),
            data_layer_ocr_interval_seconds=_clamp(
                raw.get("data_layer_ocr_interval_seconds"), 1.0, 0.1, 5.0
            ),
            data_layer_ocr_timeout_seconds=_clamp(
                raw.get("data_layer_ocr_timeout_seconds"), 3.0, 0.5, 5.0
            ),
            self_filter_enabled=_as_bool(raw.get("self_filter_enabled"), True),
            chatter_min_interval_seconds=_clamp(
                raw.get("chatter_min_interval_seconds"), 45.0, 10.0, 600.0
            ),
            chatter_fight_cooldown_seconds=_clamp(
                raw.get("chatter_fight_cooldown_seconds"), 20.0, 0.0, 300.0
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SceneState:
    connected: bool = False
    data_layer_version: str = ""
    data_layer_instance_id: str = ""
    scenario: str = IDLE
    timestamp: float = 0.0
    frame_seq: int = 0
    event_seq: int = 0
    game_running: bool = False
    game_region: str = "unknown"
    process_name: str | None = None
    session_phase: str = "idle"
    session_active: bool = False
    degraded: bool = False
    degrade_reason: str | None = None
    flags: dict[str, bool] = field(default_factory=dict)
    awareness: dict[str, Any] = field(default_factory=dict)
    classifier: dict[str, Any] = field(default_factory=dict)
    model: dict[str, Any] = field(default_factory=dict)
    targets: list[dict[str, Any]] = field(default_factory=list)
    events_tail: list[dict[str, Any]] = field(default_factory=list)
    events_truncated: bool = False
    infer_mode: str = "unknown"
    age_seconds: float | None = None
    fps_estimate: float | None = None
    infer_ms: float | None = None

    def flag(self, name: str) -> bool:
        return bool(self.flags.get(name))


@dataclass
class CompanionEvent:
    event_id: str
    level: str = "warning"
    edge: str = "enter"
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0

    @property
    def spec(self) -> EventSpec:
        return EVENT_CATALOG[self.event_id]

    @property
    def category(self) -> str:
        return self.spec.category

    @property
    def priority(self) -> int:
        return self.spec.priority

    @property
    def preempt_eligible(self) -> bool:
        return self.spec.preempt and (self.level == "critical" or self.event_id in ("game_started", "game_stopped"))
