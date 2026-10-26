"""KARDS 结构化状态与事件的数据模型。

纯数据层：不 import NEKO SDK、不依赖宿主库，保证可单测。
"""

import copy as _copy
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------- 场景常量

SCENE_MENU = "menu"
SCENE_DRAFT = "draft"        # 换牌/轮抓阶段
SCENE_MATCH = "match"
SCENE_VICTORY = "victory"
SCENE_DEFEAT = "defeat"
SCENE_UNKNOWN = "unknown"

SIDE_MINE = "mine"
SIDE_ENEMY = "enemy"
SIDE_NEUTRAL = "neutral"
# Explicit uncertainty: never inferred from geometry or smoothed from the
# previous frame.  Unknown-side units are tracked but must not produce
# mine/enemy events.
SIDE_UNKNOWN = "unknown"

LANE_SUPPORT = "support"
LANE_FRONT = "front"

# Bounds for trajectory evidence (no images, no raw OCR, JSON-safe only).
POSITION_HISTORY_LIMIT = 20
EVIDENCE_FRAME_LIMIT = 4

# Volatile keys excluded from semantic fingerprints.  This is the single
# shared specification: delivery.publisher may import :func:`semantic_value`
# instead of maintaining a second recursive normalizer.
SEMANTIC_VOLATILE_KEYS = frozenset({
    "detected_at", "observed_at", "ui_observed_at", "captured_at", "ts",
    "frame_id", "source_frame", "last_seen_frame", "first_seen_frame", "seq",
    "age_ms", "source_age_ms", "ui_age_ms", "built_at", "published_at",
    "expires_at", "next_expire_at", "last_seen_at", "first_seen_at",
    "duration_ms", "last_ms", "next_in_ms", "age_s",
})


def semantic_value(value: Any) -> Any:
    """Recursively normalize a public payload for semantic comparison.

    Volatile timestamps/frame ids/ages are dropped, mappings are key-sorted and
    tuples become lists.  Functional changes (capability boundaries, observed
    false, units, cards) are preserved and therefore change the fingerprint.
    """
    if isinstance(value, dict):
        return {
            str(key): semantic_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in SEMANTIC_VOLATILE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [semantic_value(item) for item in value]
    return value


def semantic_fingerprint(value: Any) -> str:
    """Stable SHA-256 over :func:`semantic_value` (JSON-safe by construction)."""
    encoded = json.dumps(
        semantic_value(value), ensure_ascii=True, sort_keys=True,
        separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

# ---------------------------------------------------------------- 事件定义

E_MATCH_START = "match_start"
E_TURN_SWITCH = "turn_switch"
E_HQ_DAMAGE = "hq_damage"          # 单回合掉血 >= 阈值
E_HQ_CRITICAL = "hq_critical"      # HQ <= 5（按危机档位去重）
E_MATCH_END = "match_end"
E_UNIT_PLAYED = "unit_played"
E_UNIT_DIED = "unit_died"
E_UNIT_ADVANCED = "unit_advanced"  # 支援线 -> 前线
E_ORDER_CARD = "order_card_seen"   # 抓到的指令/法术卡
E_CHATTER = "chatter"              # 平稳期主动点评

HQ_DAMAGE_THRESHOLD = 5
HQ_CRITICAL_HP = 5

_NATION_NAMES = {
    "Germany": "德国", "Britain": "英国", "Japan": "日本", "Soviet": "苏联",
    "USA": "美国", "France": "法国", "Italy": "意大利", "Poland": "波兰",
    "Finland": "芬兰", "Anzac": "澳新",
}


@dataclass
class BoardUnit:
    """棋盘上一个被跟踪的单位实例。"""

    instance_id: int
    side: str = SIDE_NEUTRAL
    lane: str = LANE_SUPPORT
    attack: int | None = None
    health: int | None = None
    max_health_seen: int | None = None
    cost: int | None = None
    card_id: str | None = None      # 识别出的卡（来自打出时刻/匹配）
    card_name: str | None = None
    name_confidence: float = 0.0    # 0=纯猜测以下方描述式称呼
    nation: str | None = None
    first_seen_turn: int = 0
    lineage: list[str] = field(default_factory=list)  # 履历（"登场"、"推进前线"…）

    # ---------------------------------------------------------- 轨迹跟踪
    # 全部带默认值：旧调用 BoardUnit(instance_id=..., side=..., lane=...)
    # 不需要修改。新字段只由 perception.detector.UnitTracker 的
    # update_observations 填充；bbox 为归一化 xyxy（0..1），绝不含图片或 OCR。
    slot: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    side_confidence: float = 0.0
    lane_confidence: float = 0.0
    detection_confidence: float = 0.0
    first_seen_frame: int | None = None
    last_seen_frame: int | None = None
    confirm_count: int = 0
    miss_count: int = 0
    observed: bool = False
    trajectory: bool = False          # True = produced by update_observations
    track_confirmed: bool = False     # 2-frame entry/move gate already passed
    enter_evidence: dict[str, Any] | None = None
    move_evidence: dict[str, Any] | None = None
    position_history: list[dict[str, Any]] = field(default_factory=list)
    observed_at: float = 0.0

    def describe(self) -> str:
        """低置信度时允许的最小事实描述。"""
        bits: list[str] = []
        if self.cost is not None:
            bits.append(f"{self.cost}费")
        if self.nation:
            bits.append(_NATION_NAMES.get(self.nation, self.nation))
        if self.attack is not None and self.health is not None:
            bits.append(f"{self.attack}/{self.health}")
        who = self.card_name if (self.card_name and self.name_confidence >= 0.6) else "".join(bits) or "单位"
        return who

    def position_snapshot(self) -> dict[str, Any]:
        """Bounded, JSON-safe position used by evidence before/after.

        Never includes pixels, crops or OCR text.  ``bbox`` is normalized
        xyxy in 0..1 coordinate space.
        """
        bbox = None
        if self.bbox is not None:
            try:
                bbox = [round(float(v), 4) for v in self.bbox]
            except (TypeError, ValueError):
                bbox = None
        return {
            "side": self.side,
            "lane": self.lane,
            "slot": self.slot,
            "bbox": bbox,
        }

    def to_public_dict(self) -> dict[str, Any]:
        known = bool(self.card_name and self.name_confidence >= 0.6)
        out: dict[str, Any] = {
            "id": self.instance_id,
            "side": self.side,
            "lane": self.lane,
            "at": self.attack,
            "hp": self.health,
        }
        if self.cost is not None:
            out["cost"] = self.cost
        # Legacy constructors keep the exact previous public shape unless they
        # already supplied position metadata.
        if self.slot is not None:
            out["slot"] = int(self.slot)
        if self.bbox is not None:
            try:
                out["bbox"] = [round(float(v), 4) for v in self.bbox]
            except (TypeError, ValueError):
                pass
        if self.trajectory:
            # Trajectory units always carry observation provenance.  A unit
            # that was not observed on the current valid frame is retained
            # history: its identity must never read as the current board.
            observed = bool(self.observed)
            out["observed"] = observed
            out["current"] = observed
            out["last_seen_frame"] = (
                int(self.last_seen_frame)
                if self.last_seen_frame is not None else None)
            out["observed_at"] = round(float(self.observed_at or 0.0), 3)
            out["detection_conf"] = round(
                float(self.detection_confidence or 0.0), 3)
            out["track_confirmed"] = bool(self.track_confirmed)
            if known:
                if observed:
                    out["name"] = self.card_name
                    out["card"] = self.card_id
                else:
                    out["last_known_name"] = self.card_name
                    if self.card_id:
                        out["last_known_card"] = self.card_id
            elif observed:
                out["desc"] = self.describe()
        elif known:
            out["name"] = self.card_name
            out["card"] = self.card_id
        else:
            out["desc"] = self.describe()
        return out

    def copy(self) -> "BoardUnit":
        nested = ("lineage", "enter_evidence", "move_evidence", "position_history")
        clone = BoardUnit(**{k: v for k, v in self.__dict__.items() if k not in nested})
        clone.lineage = list(self.lineage)
        clone.enter_evidence = _copy.deepcopy(self.enter_evidence)
        clone.move_evidence = _copy.deepcopy(self.move_evidence)
        clone.position_history = _copy.deepcopy(self.position_history)
        return clone


@dataclass
class MatchState:
    """一次感知 tick 产出的完整快照。"""

    scene: str = SCENE_UNKNOWN
    game_number: int = 0
    in_match: bool = False
    # Tri-state: True/False only from an explicit, trustworthy turn signal.
    # None means unknown/unobserved and must never be coerced to False.
    my_turn: bool | None = None
    turn_number: int = 0
    my_hq: int | None = None
    enemy_hq: int | None = None
    my_credits: int | None = None
    my_credit_cap: int | None = None
    hand_count: int | None = None
    enemy_hand_count: int | None = None
    units: list[BoardUnit] = field(default_factory=list)
    last_order_card: dict[str, Any] | None = None   # {"name","side","ts","confidence"}
    # Unit-task cadence marker.  ``None`` keeps legacy per-tick unit diff for
    # old callers; a scheduled main sets True only on a detector observation
    # tick and False on carried-state ticks so entry/move/miss gates do not
    # advance from the same sampled frame.
    unit_observation_valid: bool | None = None
    unit_observation_frame: int | None = None
    detected_at: float = field(default_factory=time.time)
    # 竞技场/轮抓未支持标记
    draft_unsupported: bool = False
    # Unified read/respond/tool view (``runtime.situation.build_situation``).
    # Empty keeps every legacy public shape byte-compatible.
    situation: dict[str, Any] = field(default_factory=dict)

    def units_of(self, side: str) -> list[BoardUnit]:
        return [u for u in self.units if u.side == side]

    def to_public_dict(self) -> dict[str, Any]:
        payload = {
            "in_match": self.in_match,
            "my_turn": self.my_turn,
            "turn": self.turn_number,
            "my_hq": self.my_hq,
            "enemy_hq": self.enemy_hq,
            "my_credits": self.my_credits,
            "hand": self.hand_count,
            "enemy_hand": self.enemy_hand_count,
            "mine": [u.to_public_dict() for u in self.units_of(SIDE_MINE)],
            "enemy": [u.to_public_dict() for u in self.units_of(SIDE_ENEMY)],
            "last_order": self.last_order_card,
            "game_number": self.game_number,
        }
        # Legacy callers built states without a situation view; do not add the
        # key until a real per-tick view exists.
        if self.situation:
            payload["situation"] = self.situation
        return payload

    def semantic_fingerprint(self) -> str:
        """Shared semantic hash (same normalizer as delivery.publisher).

        Volatile frame/time/age fields are excluded; capability changes and
        ``observed=false`` transitions remain functional and change the hash.
        """
        return semantic_fingerprint(self.to_public_dict())


@dataclass
class GameEvent:
    kind: str
    priority: int                  # 1-9，越高越重要
    summary: str
    ts: float = field(default_factory=time.time)
    details: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        key = {"kind": self.kind}
        for name in sorted(self.details):
            if name in ("ts", "at", "observed_at"):
                continue
            key[name] = self.details[name]
        encoded = json.dumps(key, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "priority": self.priority, "summary": self.summary,
                "ts": self.ts, "details": self.details, "fp": self.fingerprint()}
