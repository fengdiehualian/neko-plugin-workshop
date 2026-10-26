"""比较前后两次 MatchState，产出带优先级的事件流，并维护单位/危机履历账本。

纯逻辑层：输入全部是 model.py 的数据类，可用构造快照单测。

跨帧事实门控（宁可晚一帧，不报单帧 OCR 尖峰）：

* HQ：已确认值作 baseline；连续两个有效帧读到同一新值才 commit 差值，
  ``None`` 不推进确认也不回血复位。这样 ``20 -> 2 -> 20`` 不会产生假危机，
  ``21 -> 15 -> 15`` 仍能确认 21→15 的真实重击。
* 单位：首次出现需要两个**不同的真实观测帧**确认（carried state 的同一
  ``last_seen_frame`` 重复 tick 不算第二帧）；缺失只记录 ``unit_lost``
  diagnostics，绝不因漏检生成阵亡事件。``MatchState.unit_observation_valid``
  为 False 时（调度器非 unit-due tick）不推进登场/移动/HP0/漏检门。
  对局开始时的在场单位只做 baseline，不当作刚打出的单位风暴重放。
* 场景切换：非 match 的 menu/draft/unknown 帧暂停 diff，不把暂停帧当新世界；
  只有 victory/defeat 是权威终局。

事件时间统一由注入时钟 ``now_fn`` 产生（测试可控），不使用 GameEvent 的
墙钟默认值。所有事件在源头写入 ``game/turn/session_id/entity/occurrence``，
危机事件额外写 ``band/episode/edge``，供共享契约计算稳定 identity。
"""

from __future__ import annotations

import copy as _copy
import time
from collections import OrderedDict
from dataclasses import replace
from typing import Any, Mapping

from .model import (
    E_CHATTER,
    E_HQ_CRITICAL,
    E_HQ_DAMAGE,
    E_MATCH_END,
    E_MATCH_START,
    E_ORDER_CARD,
    E_TURN_SWITCH,
    E_UNIT_ADVANCED,
    E_UNIT_DIED,
    E_UNIT_PLAYED,
    HQ_CRITICAL_HP,
    HQ_DAMAGE_THRESHOLD,
    LANE_FRONT,
    LANE_SUPPORT,
    SCENE_DEFEAT,
    SCENE_DRAFT,
    SCENE_MATCH,
    SCENE_MENU,
    SCENE_UNKNOWN,
    SCENE_VICTORY,
    SIDE_ENEMY,
    SIDE_MINE,
    BoardUnit,
    GameEvent,
    MatchState,
)

_END_SCENES = {SCENE_VICTORY: "won", SCENE_DEFEAT: "lost"}
_PAUSE_SCENES = frozenset({SCENE_MENU, SCENE_DRAFT, SCENE_UNKNOWN})

# 单位消失多少帧后才认定阵亡（容忍瞬时漏检）
_MISS_GRACE = 3
# 首次出现的单位需要连续多少帧确认（过滤一帧 YOLO 鬼影）
_UNIT_CONFIRM_FRAMES = 2
# HQ 新值需要连续多少帧确认
_HQ_CONFIRM_FRAMES = 2
# my_turn 边沿需要连续多少帧确认（过滤单帧翻转）
_TURN_CONFIRM_FRAMES = 2
# 危机解除播报阈值：> 7 且已确认
_HQ_RECOVERY_HP = 7
# order_card 的最低识别证据
_ORDER_MIN_CONFIDENCE = 0.6
# 轨迹单位的事件侧别门槛：低于此值只保留 unknown 事实，不生成敌我事件。
_SIDE_EVENT_MIN_CONFIDENCE = 0.6
# 丢失诊断/履历有界（无图片、无 OCR 原文）
_UNIT_LOSS_LIMIT = 20
_EVIDENCE_FRAME_LIMIT = 4
_ORDER_PLACEHOLDERS = frozenset({
    "", "?", "??", "unknown", "unknown_card", "played_card", "card",
    "order", "spell", "secret", "unknown_order", "未知", "未知卡",
})

# 兼容旧测试/调用方可能引用的私有名
UNIT_CONFIRM_FRAMES = _UNIT_CONFIRM_FRAMES
UNIT_MISS_GRACE = _MISS_GRACE
HQ_CONFIRM_FRAMES = _HQ_CONFIRM_FRAMES
TURN_CONFIRM_FRAMES = _TURN_CONFIRM_FRAMES
HQ_RECOVERY_HP = _HQ_RECOVERY_HP


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out else default  # NaN -> default


def _unit_id(unit: Any) -> int | None:
    """Stable unit id or None for unusable perception placeholders."""
    try:
        return int(getattr(unit, "instance_id", None))
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _explicit_turn(value: Any) -> bool | None:
    """Only a real bool is turn evidence; anything else stays unknown."""
    return value if isinstance(value, bool) else None


def _unit_last_frame(unit: Any) -> int | None:
    """Frame id of the latest real observation, if the producer supplied one.

    Carried state copies keep the same frame id across non-detector ticks;
    comparing it prevents one sampled detection from satisfying a 2-frame
    gate.  Legacy units without frame metadata keep the old behavior.
    """
    for name in ("last_seen_frame", "first_seen_frame"):
        frame = _safe_int(getattr(unit, name, None))
        if frame is not None:
            return frame
    return None


def _loss_identity(record: Mapping[str, Any]) -> tuple[Any, ...]:
    """Bounded identity used to deduplicate repeated unit_lost records."""
    evidence = record.get("evidence")
    source = evidence if isinstance(evidence, Mapping) else {}
    first = _safe_int(source.get("first_seen"))
    last = _safe_int(source.get("last_seen"))
    instance = _safe_int(record.get("instance_id"))
    if first is None and last is None:
        frame = _safe_int(record.get("frame_id"))
        if frame is not None:
            return ("frame", instance, frame)
        try:
            ts = round(float(record.get("ts", 0.0)), 3)
        except (TypeError, ValueError):
            ts = 0.0
        return ("ts", instance, ts)
    return ("frames", instance, first, last)


def _bounded_position(value: Any) -> dict[str, Any] | None:
    """Keep only side/lane/slot/normalized bbox; drop every other field."""
    if not isinstance(value, Mapping):
        return None
    bbox = value.get("bbox")
    bbox_out = None
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            bbox_out = [round(max(0.0, min(1.0, float(v))), 4) for v in bbox]
        except (TypeError, ValueError):
            bbox_out = None
    return {
        "side": str(value.get("side") or "unknown"),
        "lane": str(value.get("lane") or LANE_SUPPORT),
        "slot": _safe_int(value.get("slot")),
        "bbox": bbox_out,
    }


def _bounded_evidence(raw: Any, *, fallback_before: Any = None,
                      fallback_after: Any = None,
                      first_seen: int | None = None,
                      last_seen: int | None = None,
                      confirm_count: int = 0,
                      confidence: float = 0.0) -> dict[str, Any]:
    """Normalize confirmation evidence to the frozen, image-free shape."""
    source = raw if isinstance(raw, Mapping) else {}
    frames_raw = source.get("frame_ids")
    frames: list[int] = []
    if isinstance(frames_raw, (list, tuple)):
        for value in frames_raw:
            number = _safe_int(value)
            if number is not None:
                frames.append(number)
    first = _safe_int(source.get("first_seen"))
    last = _safe_int(source.get("last_seen"))
    try:
        count = int(source.get("confirm_count", confirm_count))
    except (TypeError, ValueError):
        count = int(confirm_count)
    try:
        conf = float(source.get("confidence", confidence))
    except (TypeError, ValueError):
        conf = float(confidence)
    if not (conf == conf) or conf < 0.0:
        conf = 0.0
    return {
        "before": _bounded_position(
            source.get("before") if "before" in source else fallback_before),
        "after": _bounded_position(
            source.get("after") if "after" in source else fallback_after),
        "frame_ids": frames[-_EVIDENCE_FRAME_LIMIT:],
        "first_seen": first if first is not None else first_seen,
        "last_seen": last if last is not None else last_seen,
        "confirm_count": max(0, count),
        "confidence": round(min(1.0, conf), 3),
    }


class MatchTracker:
    def __init__(
        self,
        *,
        hq_damage_threshold: int = HQ_DAMAGE_THRESHOLD,
        hq_critical_hp: int = HQ_CRITICAL_HP,
        now_fn=time.time,
        unit_confirm_frames: int = _UNIT_CONFIRM_FRAMES,
        unit_miss_grace: int = _MISS_GRACE,
        hq_confirm_frames: int = _HQ_CONFIRM_FRAMES,
        turn_confirm_frames: int = _TURN_CONFIRM_FRAMES,
        order_min_confidence: float = _ORDER_MIN_CONFIDENCE,
        session_id: str = "",
        delivered_limit: int = 256,
    ) -> None:
        self.hq_damage_threshold = int(hq_damage_threshold)
        self.hq_critical_hp = int(hq_critical_hp)
        self._now = now_fn
        self._unit_confirm_frames = max(1, int(unit_confirm_frames))
        self._unit_miss_grace = max(1, int(unit_miss_grace))
        self._hq_confirm_frames = max(1, int(hq_confirm_frames))
        self._turn_confirm_frames = max(1, int(turn_confirm_frames))
        self.order_min_confidence = float(order_min_confidence)
        self._delivered_limit = max(1, int(delivered_limit))
        self._session_id = str(session_id or "")

        self._prev: MatchState | None = None
        self._game_number = 0
        self._in_match = False
        self._known: dict[int, BoardUnit] = {}
        self._miss: dict[int, int] = {}
        # (candidate, seen_frames, last_observation_frame)
        self._pending_units: dict[int, tuple[BoardUnit, int, int | None]] = {}
        # Observation-loss diagnostics only.  Absence never becomes unit_died;
        # only a reliable observed HP=0 (two frames) may do that.
        self._unit_losses: list[dict[str, Any]] = []
        self._loss_keys: set[tuple[Any, ...]] = set()
        self._announced_units: set[int] = set()
        self._zero_hp: dict[int, int] = {}
        self._zero_hp_frame: dict[int, int | None] = {}
        self._zero_hp_announced: set[int] = set()
        # Last unit-observation frame actually consumed by the unit diff.
        self._last_unit_observation_frame: int | None = None
        self._crisis_band: dict[str, int] = {SIDE_MINE: 0, SIDE_ENEMY: 0}
        self._crisis_episode: dict[str, int | None] = {SIDE_MINE: None, SIDE_ENEMY: None}
        self._crisis_announced: dict[str, bool] = {SIDE_MINE: False, SIDE_ENEMY: False}
        self._episode_seq = 0
        self._hq_confirmed: dict[str, int | None] = {SIDE_MINE: None, SIDE_ENEMY: None}
        self._hq_candidate: dict[str, int | None] = {SIDE_MINE: None, SIDE_ENEMY: None}
        self._hq_candidate_frames: dict[str, int] = {SIDE_MINE: 0, SIDE_ENEMY: 0}
        self._turn = 0
        self._confirmed_my_turn: bool | None = None
        self._turn_pending: bool | None = None
        self._turn_pending_frames = 0
        self._last_turn_observation_frame: int | None = None
        self._occurrence: dict[tuple[str, str, str], int] = {}
        self._delivered: OrderedDict[str, None] = OrderedDict()
        self._condition_events: dict[tuple[str, str], GameEvent] = {}
        self._last_notable_ts: float = 0.0
        self._rebase_pending = False
        self._rebaseline_next_match = False
        self._rearm_max_age_s = 12.0
        self.match_ledger: dict[str, Any] | None = None  # 当前局统计

    # ------------------------------------------------------------- 只读状态

    @property
    def in_match(self) -> bool:
        return self._in_match

    @property
    def game_number(self) -> int:
        return self._game_number

    @property
    def current(self) -> MatchState | None:
        return self._prev

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def delivered_count(self) -> int:
        return len(self._delivered)

    def set_session_id(self, session_id: str) -> None:
        self._session_id = str(session_id or "")

    def confirmed_hq(self, side: str) -> int | None:
        """当前已确认 HQ（未达到两帧确认时为 None/baseline）。"""
        return self._hq_confirmed.get(side)

    def is_delivered(self, event: GameEvent) -> bool:
        return self._identity_of(self._unwrap_event(event)) in self._delivered

    def current_ledger(self) -> dict[str, Any] | None:
        return None if self.match_ledger is None else dict(self.match_ledger)

    def recent_unit_losses(self, limit: int = 20) -> list[dict[str, Any]]:
        """Bounded observation-loss diagnostics; no death claim, no images."""
        count = max(1, int(limit))
        return _copy.deepcopy(self._unit_losses[-count:])

    def unit_diagnostics(self) -> dict[str, Any]:
        """Structured unit-tracking facts for the main integration agent."""
        return {
            "known": len(self._known),
            "pending": len(self._pending_units),
            "misses": {str(uid): int(value)
                       for uid, value in sorted(self._miss.items())},
            "losses": self.recent_unit_losses(_UNIT_LOSS_LIMIT),
            "announced": len(self._announced_units),
            "hp_zero_pending": {str(uid): int(value)
                                for uid, value in sorted(self._zero_hp.items())},
            "last_observation_frame": self._last_unit_observation_frame,
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "session_id": self._session_id,
            "game_number": self._game_number,
            "in_match": self._in_match,
            "turn": self._turn,
            "units": len(self._known),
            "pending_units": len(self._pending_units),
            "unit_losses": len(self._unit_losses),
            "crisis_band": dict(self._crisis_band),
            "confirmed_hq": dict(self._hq_confirmed),
            "delivered": len(self._delivered),
            "rebase_pending": self._rebase_pending or self._rebaseline_next_match,
        }

    # ------------------------------------------------------------- 生命周期

    def reset_session(self) -> None:
        """原地清空全部 session/局/单位/危机/投递账本，局号归零。"""
        self._prev = None
        self._game_number = 0
        self._in_match = False
        self._known.clear()
        self._miss.clear()
        self._pending_units.clear()
        self._unit_losses.clear()
        self._loss_keys.clear()
        self._announced_units.clear()
        self._zero_hp.clear()
        self._zero_hp_frame.clear()
        self._zero_hp_announced.clear()
        self._last_unit_observation_frame = None
        self._crisis_band = {SIDE_MINE: 0, SIDE_ENEMY: 0}
        self._crisis_episode = {SIDE_MINE: None, SIDE_ENEMY: None}
        self._crisis_announced = {SIDE_MINE: False, SIDE_ENEMY: False}
        self._episode_seq = 0
        self._hq_confirmed = {SIDE_MINE: None, SIDE_ENEMY: None}
        self._hq_candidate = {SIDE_MINE: None, SIDE_ENEMY: None}
        self._hq_candidate_frames = {SIDE_MINE: 0, SIDE_ENEMY: 0}
        self._turn = 0
        self._confirmed_my_turn = None
        self._turn_pending = None
        self._turn_pending_frames = 0
        self._last_turn_observation_frame = None
        self._occurrence.clear()
        self._delivered.clear()
        self._condition_events.clear()
        self._last_notable_ts = 0.0
        self._rebase_pending = False
        self._rebaseline_next_match = False
        self._session_id = ""
        self.match_ledger = None

    def reset_match(self) -> None:
        """只清当前局：保留 session、累计局号和投递去重账本。"""
        self._prev = None
        self._in_match = False
        self._known.clear()
        self._miss.clear()
        self._pending_units.clear()
        self._unit_losses.clear()
        self._loss_keys.clear()
        self._announced_units.clear()
        self._zero_hp.clear()
        self._zero_hp_frame.clear()
        self._zero_hp_announced.clear()
        self._last_unit_observation_frame = None
        self._crisis_band = {SIDE_MINE: 0, SIDE_ENEMY: 0}
        self._crisis_episode = {SIDE_MINE: None, SIDE_ENEMY: None}
        self._crisis_announced = {SIDE_MINE: False, SIDE_ENEMY: False}
        self._hq_confirmed = {SIDE_MINE: None, SIDE_ENEMY: None}
        self._hq_candidate = {SIDE_MINE: None, SIDE_ENEMY: None}
        self._hq_candidate_frames = {SIDE_MINE: 0, SIDE_ENEMY: 0}
        self._turn = 0
        self._confirmed_my_turn = None
        self._turn_pending = None
        self._turn_pending_frames = 0
        self._last_turn_observation_frame = None
        self._condition_events.clear()
        self._rebase_pending = False
        self._rebaseline_next_match = False
        self.match_ledger = None

    def rebase(self) -> None:
        """把下一帧（或下一次回到 match 的首帧）当作 baseline，不产生 diff。"""
        self._rebase_pending = True
        self._rebaseline_next_match = False

    # ------------------------------------------------------------- 主入口

    def update(self, state: MatchState) -> list[GameEvent]:
        now = self._now()
        events: list[GameEvent] = []
        scene = str(state.scene or SCENE_UNKNOWN)

        if self._rebase_pending:
            self._rebase_pending = False
            if scene in _END_SCENES:
                # 结果仍权威：正常关闭，不因 rebase 丢终局。
                if self._in_match:
                    events.extend(self._close_match(state, now))
                self._prev = state
                self._rebaseline_next_match = False
                if events:
                    self._last_notable_ts = now
                return events
            if self._in_match:
                if scene == SCENE_MATCH:
                    self._apply_baseline(state)
                    return []
                # 暂停场景：把下一次回到 match 的首帧继续视为 baseline。
                self._rebaseline_next_match = True
                state.game_number = self._game_number
                return []

        if self._rebaseline_next_match:
            if scene == SCENE_MATCH and self._in_match:
                self._rebaseline_next_match = False
                self._apply_baseline(state)
                return []
            if scene in _END_SCENES:
                self._rebaseline_next_match = False
                if self._in_match:
                    events.extend(self._close_match(state, now))
                self._prev = state
                if events:
                    self._last_notable_ts = now
                return events
            if not self._in_match:
                self._rebaseline_next_match = False

        if scene in _END_SCENES:
            if self._in_match:
                events.extend(self._close_match(state, now))
            self._prev = state
        elif scene == SCENE_MATCH:
            if not self._in_match:
                events.append(self._start_match(state, now))
            elif self._prev is not None and self._prev.scene == SCENE_MATCH:
                events.extend(self._diff_match(self._prev, state, now))
            else:
                # 从暂停场景恢复但调用方没有 rebase：安静建 baseline，不补报 gap。
                self._apply_baseline(state)
            if self._in_match:
                state.game_number = self._game_number
            # 所有可信 match 帧都必须更新 last-match baseline；否则下一帧会
            # 重复 diff（unit_played/order/turn 风暴）并让暂停帧污染 diff。
            self._prev = state
        else:
            # 非 match 的可信帧暂停业务 diff；保持上一 match 帧的 _prev，
            # 这样 unknown/menu/draft 不会造成 turn/HQ/单位 diff 或阵亡。
            if self._in_match:
                state.game_number = self._game_number
            else:
                self._prev = state

        if events:
            self._last_notable_ts = now
        return events

    def maybe_chatter(self, chatter_interval_s: float) -> GameEvent | None:
        if chatter_interval_s <= 0 or not self._in_match:
            return None
        now = self._now()
        if now - self._last_notable_ts < chatter_interval_s:
            return None
        state = self._prev
        if state is None or state.scene != SCENE_MATCH:
            return None
        self._last_notable_ts = now
        details = {
            "game": self._game_number,
            "turn": self._turn,
            "session_id": self._session_id,
            "entity": "chatter",
        }
        details["occurrence"] = self._next_occurrence(E_CHATTER, "", "chatter")
        return GameEvent(
            kind=E_CHATTER,
            priority=5,
            summary="局势平稳，适合简短点评一下盘面。",
            ts=now,
            details=details,
        )

    # ------------------------------------------------------------- 投递账本

    def mark_delivered(self, event: GameEvent | None) -> None:
        """按 event_identity 记录已投递，供 rearm 去重；有界 FIFO。"""
        if event is None:
            return
        event = self._unwrap_event(event)
        identity = self._identity_of(event)
        if identity in self._delivered:
            self._delivered.move_to_end(identity)
            return
        self._delivered[identity] = None
        while len(self._delivered) > self._delivered_limit:
            self._delivered.popitem(last=False)

    def rearm_uncommitted(self, *, now: float | None = None) -> list[GameEvent]:
        """返回仍成立、未过期、未投递的条件事件（当前为 HQ 危机）。

        保留原 ``ts``/episode，不刷新 TTL；离散事件（match_start/end、
        unit_*、turn/order）永不重放。新 episode 会有自己的新事件。
        """
        at = self._now() if now is None else float(now)
        try:
            from ..runtime.contracts import event_max_age_seconds, event_spec
        except Exception:  # pragma: no cover - 纯 state 环境下的保守回退
            return []
        out: list[GameEvent] = []
        for (kind, side), event in list(self._condition_events.items()):
            if kind != E_HQ_CRITICAL:
                continue
            if self._crisis_band.get(side, 0) <= 0:
                continue
            if not event_spec(event).wants_recovery:
                continue
            if self.is_delivered(event):
                continue
            limit = event_max_age_seconds(event, self._rearm_max_age_s)
            age = max(0.0, at - float(event.ts))
            if limit > 0 and age > limit:
                continue
            out.append(replace(event, details=dict(event.details)))
        out.sort(key=lambda item: (float(item.ts), int(item.priority)))
        return out

    # ------------------------------------------------------------- 局事件

    def _start_match(self, state: MatchState, now: float) -> GameEvent:
        self._in_match = True
        self._game_number += 1
        state.game_number = self._game_number
        known: dict[int, BoardUnit] = {}
        for unit in state.units:
            uid = _unit_id(unit)
            if uid is not None:
                known[uid] = unit
        self._known = known
        self._miss.clear()
        self._pending_units.clear()
        self._unit_losses.clear()
        self._loss_keys.clear()
        # Units already on board at match start are baseline; they must not
        # retroactively announce play events.
        self._announced_units = set(known)
        self._zero_hp.clear()
        self._zero_hp_frame.clear()
        self._zero_hp_announced.clear()
        self._last_unit_observation_frame = None
        self._crisis_band = {SIDE_MINE: 0, SIDE_ENEMY: 0}
        self._crisis_episode = {SIDE_MINE: None, SIDE_ENEMY: None}
        self._crisis_announced = {SIDE_MINE: False, SIDE_ENEMY: False}
        self._hq_confirmed = {SIDE_MINE: None, SIDE_ENEMY: None}
        self._hq_candidate = {SIDE_MINE: None, SIDE_ENEMY: None}
        self._hq_candidate_frames = {SIDE_MINE: 0, SIDE_ENEMY: 0}
        # 首帧已确认值直接作为 baseline（不播报），避免开局多等两帧。
        for side, value in ((SIDE_MINE, state.my_hq), (SIDE_ENEMY, state.enemy_hq)):
            hp = _as_int(value)
            self._hq_confirmed[side] = hp
            self._sync_band_without_event(side, hp)
        observed_turn = _as_int(state.turn_number)
        self._turn = max(1, observed_turn or 1)
        self._confirmed_my_turn = _explicit_turn(state.my_turn)
        self._turn_pending = None
        self._turn_pending_frames = 0
        self._last_turn_observation_frame = None
        self._condition_events.clear()
        self.match_ledger = {
            "game": self._game_number,
            "played": [],
            "died": [],
            # Observation losses are NOT deaths; keep them in a separate
            # structured ledger so outcome summaries never overclaim.
            "lost": [],
            "started": now,
        }
        self._last_notable_ts = now
        return self._make_event(
            E_MATCH_START, 5, "新的一局开始了！", now,
            entity="match", side=None,
        )

    def _close_match(self, state: MatchState, now: float) -> list[GameEvent]:
        result = _END_SCENES[state.scene]
        state.game_number = self._game_number
        events: list[GameEvent] = []
        if self.match_ledger is not None:
            self.match_ledger.update({
                "result": result,
                "ended": now,
                "my_hq": state.my_hq,
                "enemy_hq": state.enemy_hq,
            })
            events.append(self._make_event(
                E_MATCH_END,
                9,
                "胜利！" if result == "won" else "这局输了。",
                now,
                entity="match",
                side=None,
                extra={
                    "result": result,
                    "ledger": dict(self.match_ledger),
                },
            ))
        self._in_match = False
        self._known.clear()
        self._miss.clear()
        self._pending_units.clear()
        self._unit_losses.clear()
        self._loss_keys.clear()
        self._announced_units.clear()
        self._zero_hp.clear()
        self._zero_hp_frame.clear()
        self._zero_hp_announced.clear()
        self._last_unit_observation_frame = None
        self._crisis_band = {SIDE_MINE: 0, SIDE_ENEMY: 0}
        self._crisis_episode = {SIDE_MINE: None, SIDE_ENEMY: None}
        self._crisis_announced = {SIDE_MINE: False, SIDE_ENEMY: False}
        self._hq_confirmed = {SIDE_MINE: None, SIDE_ENEMY: None}
        self._hq_candidate = {SIDE_MINE: None, SIDE_ENEMY: None}
        self._hq_candidate_frames = {SIDE_MINE: 0, SIDE_ENEMY: 0}
        self._condition_events.clear()
        self.match_ledger = None
        return events

    def _apply_baseline(self, state: MatchState) -> None:
        """静默接受当前帧为 baseline；单位 lineage/名称继承保留。"""
        known: dict[int, BoardUnit] = {}
        for unit in state.units:
            uid = _unit_id(unit)
            if uid is None:
                continue
            old = self._known.get(uid)
            if old is not None:
                self._inherit_unit(unit, old)
                if unit.lineage:
                    unit.lineage = list(old.lineage) + [
                        item for item in unit.lineage if item not in old.lineage
                    ]
                else:
                    unit.lineage = list(old.lineage)
            known[uid] = unit
        self._known = known
        self._miss.clear()
        self._pending_units.clear()
        # Rebase baseline units are not "newly played" after a pause.
        self._announced_units.update(known)
        self._zero_hp.clear()
        self._zero_hp_frame.clear()
        baseline_frame = _safe_int(state.unit_observation_frame)
        self._last_unit_observation_frame = (
            None if baseline_frame is None or baseline_frame <= 0
            else baseline_frame)

        for side, value in ((SIDE_MINE, state.my_hq), (SIDE_ENEMY, state.enemy_hq)):
            hp = _as_int(value)
            self._hq_confirmed[side] = hp
            self._hq_candidate[side] = None
            self._hq_candidate_frames[side] = 0
            band = self._band_for(hp)
            self._crisis_band[side] = band
            if band <= 0:
                self._crisis_episode[side] = None
                self._crisis_announced[side] = False
                self._condition_events.pop((E_HQ_CRITICAL, side), None)
            elif self._crisis_episode[side] is None:
                # baseline 已在危机中：保留条件，但不制造 gap 事件。
                self._crisis_episode[side] = self._new_episode()
                self._crisis_announced[side] = False

        observed_turn = _as_int(state.turn_number)
        if observed_turn is not None and observed_turn > self._turn:
            self._turn = observed_turn
        elif self._turn <= 0:
            self._turn = max(1, observed_turn or 1)
        # rebase baseline 同步回合态；下一帧不会把暂停前的旧回合当 diff。
        # Unknown stays None instead of being inherited as an explicit False.
        self._confirmed_my_turn = _explicit_turn(state.my_turn)
        self._turn_pending = None
        self._turn_pending_frames = 0
        self._last_turn_observation_frame = None
        self._prev = state
        self._last_notable_ts = self._now()
        if self._in_match:
            state.game_number = self._game_number

    # ------------------------------------------------------------- 盘中差异

    def _diff_match(self, prev: MatchState, state: MatchState, now: float) -> list[GameEvent]:
        events: list[GameEvent] = []
        state.game_number = self._game_number

        observed_turn = _as_int(state.turn_number)
        advanced = observed_turn is not None and observed_turn > self._turn
        if advanced:
            self._turn = observed_turn
        switched, confirmed_turn = self._confirm_turn(state)
        if switched and not advanced:
            self._turn = max(self._turn + 1, observed_turn or 0)
        elif self._turn <= 0:
            self._turn = 1

        if switched:
            who = "你的回合" if confirmed_turn else "对方回合"
            events.append(self._make_event(
                E_TURN_SWITCH, 4, f"{who}开始了。", now,
                entity="turn", side=SIDE_MINE if confirmed_turn else SIDE_ENEMY,
                extra={"my_turn": confirmed_turn},
            ))

        events.extend(self._hq_diff(state, now))
        # ``unit_observation_valid=False`` means this tick carries the last
        # verified unit snapshot but no fresh detector observation.  Unit
        # entry/move/HP0/miss gates must not advance from carried state.  A
        # repeated explicit observation frame is also treated as carried.
        observation_frame = _safe_int(state.unit_observation_frame)
        if observation_frame is not None and observation_frame <= 0:
            observation_frame = None  # missing-capture-id sentinel
        skip_units = state.unit_observation_valid is False
        if (not skip_units and state.unit_observation_valid is True
                and observation_frame is not None
                and observation_frame == self._last_unit_observation_frame):
            skip_units = True
        if not skip_units:
            events.extend(self._unit_diff(state, now))
            if state.unit_observation_valid is True and observation_frame is not None:
                self._last_unit_observation_frame = observation_frame
        events.extend(self._order_diff(prev, state, now))
        return events

    def _confirm_turn(self, state: MatchState) -> tuple[bool, bool | None]:
        """Independent-observation my_turn edge confirmation.

        Returns ``(committed, confirmed_value)``.  ``None`` is unknown: it
        neither advances nor resets a pending edge, and it never becomes an
        explicit False.  A carried tick (``unit_observation_valid=False``) or
        a repeated detector frame is not independent evidence, so one sampled
        frame cannot satisfy the two-observation gate by ticking twice.
        """
        raw = state.my_turn
        if not isinstance(raw, bool):
            # 缺读/未知不推进边沿，也不回退已确认值。
            self._turn_pending = None
            self._turn_pending_frames = 0
            return False, self._confirmed_my_turn
        if state.unit_observation_valid is False:
            # Carried state is not a new observation.
            return False, self._confirmed_my_turn
        observation_frame = _safe_int(state.unit_observation_frame)
        if observation_frame is not None and observation_frame > 0:
            if observation_frame == self._last_turn_observation_frame:
                return False, self._confirmed_my_turn
            self._last_turn_observation_frame = observation_frame

        value = raw
        if value == self._confirmed_my_turn:
            self._turn_pending = None
            self._turn_pending_frames = 0
            return False, self._confirmed_my_turn
        if self._turn_pending == value:
            self._turn_pending_frames += 1
        else:
            self._turn_pending = value
            self._turn_pending_frames = 1
        if self._turn_pending_frames >= self._turn_confirm_frames:
            self._confirmed_my_turn = value
            self._turn_pending = None
            self._turn_pending_frames = 0
            return True, value
        return False, self._confirmed_my_turn

    # ------------------------------------------------------------- HQ 确认

    def _hq_diff(self, state: MatchState, now: float) -> list[GameEvent]:
        events: list[GameEvent] = []
        for side in (SIDE_MINE, SIDE_ENEMY):
            raw = _as_int(state.my_hq if side == SIDE_MINE else state.enemy_hq)
            if raw is None:
                # 缺读：不推进确认、不回血、不解除危机。
                self._hq_candidate[side] = None
                self._hq_candidate_frames[side] = 0
                continue

            confirmed = self._hq_confirmed[side]
            if confirmed is None:
                if self._hq_candidate[side] == raw:
                    self._hq_candidate_frames[side] += 1
                else:
                    self._hq_candidate[side] = raw
                    self._hq_candidate_frames[side] = 1
                if self._hq_candidate_frames[side] >= self._hq_confirm_frames:
                    self._hq_confirmed[side] = raw
                    self._hq_candidate[side] = None
                    self._hq_candidate_frames[side] = 0
                    self._sync_band_without_event(side, raw)
                continue

            if raw == confirmed:
                self._hq_candidate[side] = None
                self._hq_candidate_frames[side] = 0
                continue

            if self._hq_candidate[side] == raw:
                self._hq_candidate_frames[side] += 1
            else:
                self._hq_candidate[side] = raw
                self._hq_candidate_frames[side] = 1
            if self._hq_candidate_frames[side] < self._hq_confirm_frames:
                continue

            old = confirmed
            self._hq_confirmed[side] = raw
            self._hq_candidate[side] = None
            self._hq_candidate_frames[side] = 0
            events.extend(self._commit_hq_value(side, old, raw, now))
        return events

    def _commit_hq_value(self, side: str, old: int, new: int, now: float) -> list[GameEvent]:
        events: list[GameEvent] = []
        loss = max(0, old - new)
        new_band = self._band_for(new)

        if loss >= self.hq_damage_threshold:
            if side == SIDE_MINE:
                summary = f"我方总部被重击，掉了{loss}点血，现在只剩{new}。"
                priority = 7
            else:
                summary = f"对面总部被砸掉{loss}点，只剩{new}了！"
                priority = 5
            events.append(self._make_event(
                E_HQ_DAMAGE, priority, summary, now,
                side=side, entity="hq", band=new_band,
                extra={"loss": loss, "hp": new},
            ))

        old_band = int(self._crisis_band.get(side, 0))
        if new_band > old_band:
            self._crisis_band[side] = new_band
            episode = self._crisis_episode.get(side)
            if episode is None:
                episode = self._new_episode()
                self._crisis_episode[side] = episode
            if not self._crisis_announced.get(side, False):
                # 静默 baseline 后第一次真正播报仍是 enter。
                edge = "enter"
            else:
                edge = "upgrade"
            if side == SIDE_MINE:
                summary = f"我方总部只剩{new}点血了，危险！"
                priority = 8
            else:
                summary = f"对面总部只有{new}点了，冲啊！"
                priority = 6
            event = self._make_event(
                E_HQ_CRITICAL, priority, summary, now,
                side=side, entity="hq", band=new_band, edge=edge,
                extra={"hp": new, "episode": episode},
            )
            events.append(event)
            self._condition_events[(E_HQ_CRITICAL, side)] = event
            self._crisis_announced[side] = True
        elif new_band < old_band:
            if new_band == 0:
                old_episode = self._crisis_episode.get(side)
                if (old_band > 0 and new > _HQ_RECOVERY_HP
                        and self._crisis_announced.get(side, False)):
                    events.append(self._make_event(
                        E_HQ_CRITICAL, 4,
                        "我方总部恢复到安全线以上。" if side == SIDE_MINE
                        else "对面总部回到安全线以上。",
                        now,
                        side=side, entity="hq", band=0, edge="recovery",
                        extra={
                            "hp": new,
                            "episode": old_episode,
                            "previous_band": old_band,
                        },
                    ))
                self._crisis_band[side] = 0
                self._crisis_episode[side] = None
                self._crisis_announced[side] = False
                self._condition_events.pop((E_HQ_CRITICAL, side), None)
            else:
                # 2 -> 1 档不降价播报，但仍属同一危机 episode。
                self._crisis_band[side] = new_band
        return events

    def _sync_band_without_event(self, side: str, hp: int | None) -> None:
        band = self._band_for(hp)
        self._crisis_band[side] = band
        if band <= 0:
            self._crisis_episode[side] = None
            self._crisis_announced[side] = False
            self._condition_events.pop((E_HQ_CRITICAL, side), None)
        elif self._crisis_episode[side] is None:
            self._crisis_episode[side] = self._new_episode()
            # 静默 baseline 危机不播报；后续回血也不报 recovery。
            self._crisis_announced[side] = False

    def _band_for(self, hp: int | None) -> int:
        if hp is None:
            return 0
        if hp <= 2:
            return 2
        if hp <= self.hq_critical_hp:
            return 1
        return 0

    # ------------------------------------------------------------- 单位

    def _unit_diff(self, state: MatchState, now: float) -> list[GameEvent]:
        events: list[GameEvent] = []
        current: dict[int, BoardUnit] = {}
        for unit in state.units:
            uid = _unit_id(unit)
            if uid is None:
                continue
            current[uid] = unit

        for uid, unit in current.items():
            old = self._known.get(uid)
            if old is not None:
                self._inherit_unit(unit, old)
                if old.lane == LANE_SUPPORT and unit.lane == LANE_FRONT:
                    unit.lineage.append("推进到前线")
                    advanced = self._unit_advanced_event(unit, now, old=old)
                    if advanced is not None:
                        events.append(advanced)
                elif old.lane == LANE_FRONT and unit.lane == LANE_SUPPORT:
                    # 反向移动是撤回，绝不是“推进”。
                    unit.lineage.append("撤回支援线")
                if unit.lineage:
                    unit.lineage = list(old.lineage) + [
                        item for item in unit.lineage if item not in old.lineage
                    ]
                else:
                    unit.lineage = list(old.lineage)
                self._known[uid] = unit
                self._miss.pop(uid, None)
                self._pending_units.pop(uid, None)
                # A unit tracked with unknown side is announced only once its
                # side is trustworthy; an unknown side never emits 敌我 event.
                played = self._announce_unit(unit, now)
                if played is not None:
                    events.append(played)
                died = self._hp_zero_event(unit, now)
                if died is not None:
                    events.append(died)
                continue

            pending = self._pending_units.get(uid)
            if bool(getattr(unit, "track_confirmed", False)):
                # Trajectory tracker already passed the 2-frame entry gate.
                self._pending_units.pop(uid, None)
                if pending is not None:
                    confirmed_unit, _frames, _last_frame = pending
                    self._inherit_unit(unit, confirmed_unit)
                    if unit.lineage:
                        unit.lineage = list(confirmed_unit.lineage) + list(unit.lineage)
                    else:
                        unit.lineage = list(confirmed_unit.lineage)
                self._known[uid] = unit
                self._miss.pop(uid, None)
                played = self._announce_unit(unit, now)
                if played is not None:
                    events.append(played)
                died = self._hp_zero_event(unit, now)
                if died is not None:
                    events.append(died)
                continue

            observation_frame = _unit_last_frame(unit)
            if pending is None:
                self._pending_units[uid] = (unit.copy(), 1, observation_frame)
                continue
            confirmed_unit, frames, last_frame = pending
            if (observation_frame is not None and last_frame is not None
                    and observation_frame == last_frame):
                # Same sampled detection carried across ticks; it is not a
                # second observation and must not satisfy the 2-frame gate.
                self._pending_units[uid] = (confirmed_unit, frames, last_frame)
                continue
            frames += 1
            if frames < self._unit_confirm_frames:
                self._pending_units[uid] = (confirmed_unit, frames, observation_frame)
                continue
            del self._pending_units[uid]
            self._inherit_unit(unit, confirmed_unit)
            if unit.lineage:
                unit.lineage = list(confirmed_unit.lineage) + list(unit.lineage)
            else:
                unit.lineage = list(confirmed_unit.lineage)
            self._known[uid] = unit
            played = self._announce_unit(unit, now)
            if played is not None:
                events.append(played)
            died = self._hp_zero_event(unit, now)
            if died is not None:
                events.append(died)

        for uid, old in list(self._known.items()):
            if uid in current:
                continue
            misses = self._miss.get(uid, 0) + 1
            if misses >= self._unit_miss_grace:
                self._miss.pop(uid, None)
                self._known.pop(uid, None)
                # Absence only proves loss of observation, not death.  Record
                # bounded structured evidence and emit no unit_died event.
                self._record_unit_loss(old, now, misses=misses)
            else:
                # 三帧宽限内保留在 _known，下一帧仍能继承 lineage/名字。
                self._miss[uid] = misses

        for uid in list(self._pending_units):
            if uid not in current:
                del self._pending_units[uid]
        return events

    # -------------------------------------------------- unit event helpers

    @staticmethod
    def _side_event_ready(unit: BoardUnit) -> bool:
        """Whether this frame may attribute an event to mine/enemy.

        Trajectory units additionally require current side confidence; an
        unknown or fallback-derived side is never smoothed into an event.
        Legacy manually-constructed units keep the old permissive behavior.
        """
        side = str(getattr(unit, "side", "") or "")
        if side not in (SIDE_MINE, SIDE_ENEMY):
            return False
        if bool(getattr(unit, "trajectory", False)):
            try:
                confidence = float(getattr(unit, "side_confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            if confidence < _SIDE_EVENT_MIN_CONFIDENCE:
                return False
        return True

    def _unit_evidence(self, unit: BoardUnit, kind: str,
                       *, before_fallback: dict[str, Any] | None = None
                       ) -> dict[str, Any]:
        """Return the frozen evidence shape for enter/move/lost/hp_zero.

        ``first_seen``/``last_seen`` are frame ids; timestamps stay in
        ``position_history``.  Evidence is bounded to four frame ids and
        contains no images, crops or OCR text.
        """
        first_seen = _safe_int(getattr(unit, "first_seen_frame", None))
        last_seen = _safe_int(getattr(unit, "last_seen_frame", None))
        try:
            confirm_count = int(getattr(unit, "confirm_count", 0) or 0)
        except (TypeError, ValueError):
            confirm_count = 0
        if confirm_count <= 0:
            confirm_count = self._unit_confirm_frames
        try:
            confidence = float(getattr(unit, "detection_confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence <= 0.0:
            try:
                confidence = float(getattr(unit, "name_confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
        history = getattr(unit, "position_history", None)
        frame_ids: list[int] = []
        if isinstance(history, (list, tuple)):
            for item in history:
                if isinstance(item, Mapping):
                    frame = _safe_int(item.get("frame_id"))
                    if frame is not None:
                        frame_ids.append(frame)
        if not frame_ids:
            frame_ids = [frame for frame in (first_seen, last_seen)
                         if frame is not None]
        after = unit.position_snapshot()
        if kind == "lost":
            before, after_value = after, None
            raw = None
        elif kind == "move":
            before = before_fallback
            after_value = after
            raw = getattr(unit, "move_evidence", None)
        else:
            before = None
            after_value = after
            raw = (getattr(unit, "enter_evidence", None)
                   if kind == "enter" else None)
        evidence = _bounded_evidence(
            raw,
            fallback_before=before,
            fallback_after=after_value,
            first_seen=first_seen,
            last_seen=last_seen,
            confirm_count=confirm_count,
            confidence=confidence,
        )
        # ``frame_ids`` from the tracker is already bounded; keep the unit's
        # own frame sequence when evidence was synthesized locally.
        if not evidence["frame_ids"]:
            evidence["frame_ids"] = frame_ids[-_EVIDENCE_FRAME_LIMIT:]
        return evidence

    def _announce_unit(self, unit: BoardUnit, now: float) -> GameEvent | None:
        uid = _unit_id(unit)
        if uid is None or uid in self._announced_units:
            return None
        if not self._side_event_ready(unit):
            return None
        if not any("登场" in line for line in unit.lineage):
            unit.lineage.append(f"登场（{unit.describe()}）")
        self._announced_units.add(uid)
        if self.match_ledger is not None:
            self.match_ledger["played"].append(unit.describe())
        return self._make_event(
            E_UNIT_PLAYED, 5,
            f"{'你' if unit.side == SIDE_MINE else '对手'}展开了{unit.describe()}。",
            now,
            side=unit.side, entity=uid,
            extra={
                "lane": unit.lane,
                "name": unit.card_name if unit.name_confidence >= 0.6 else None,
                "evidence": self._unit_evidence(unit, "enter"),
            },
        )

    def _unit_advanced_event(self, unit: BoardUnit, now: float,
                             *, old: BoardUnit | None = None
                             ) -> GameEvent | None:
        if not self._side_event_ready(unit):
            return None
        before = old.position_snapshot() if old is not None else None
        return self._make_event(
            E_UNIT_ADVANCED, 4,
            f"{unit.describe()}顶到了前线。", now,
            side=unit.side, entity=_unit_id(unit),
            extra={
                "lane": unit.lane,
                "unit": unit.describe(),
                "evidence": self._unit_evidence(
                    unit, "move", before_fallback=before),
            },
        )

    def _hp_zero_event(self, unit: BoardUnit, now: float) -> GameEvent | None:
        """Reliable HP=0 evidence is the only absence-independent death."""
        uid = _unit_id(unit)
        if uid is None:
            return None
        hp = _as_int(unit.health)
        if hp != 0:
            self._zero_hp.pop(uid, None)
            self._zero_hp_frame.pop(uid, None)
            return None
        if uid in self._zero_hp_announced:
            return None
        observation_frame = _unit_last_frame(unit)
        if (observation_frame is not None
                and self._zero_hp_frame.get(uid) == observation_frame):
            # Re-reading the same carried detection is not a second HP=0
            # observation.
            return None
        self._zero_hp_frame[uid] = observation_frame
        count = self._zero_hp.get(uid, 0) + 1
        self._zero_hp[uid] = count
        if count < 2 or not self._side_event_ready(unit):
            return None
        self._zero_hp_announced.add(uid)
        evidence = self._unit_evidence(unit, "hp_zero")
        if self.match_ledger is not None:
            self.match_ledger["died"].append(unit.describe())
        return self._make_event(
            E_UNIT_DIED, 6,
            f"{'我方的' if unit.side == SIDE_MINE else '对面的'}{unit.describe()}阵亡了。",
            now,
            side=unit.side, entity=uid,
            extra={
                "unit": unit.describe(),
                "cause": "hp_zero",
                "evidence": evidence,
            },
        )

    def _record_unit_loss(self, unit: BoardUnit, now: float, *, misses: int) -> None:
        """Record observation loss; never claim death and never emit an event."""
        evidence = self._unit_evidence(unit, "lost")
        record = {
            "kind": "unit_lost",
            "instance_id": _unit_id(unit),
            "side": unit.side,
            "lane": unit.lane,
            "slot": getattr(unit, "slot", None),
            "ts": now,
            "game": self._game_number,
            "turn": self._turn,
            "miss_count": int(misses),
            "reason": "missing_frames",
            "evidence": evidence,
        }
        key = _loss_identity(record)
        if key in self._loss_keys:
            return
        self._loss_keys.add(key)
        self._unit_losses.append(record)
        if len(self._unit_losses) > _UNIT_LOSS_LIMIT:
            del self._unit_losses[:-_UNIT_LOSS_LIMIT]
        if self.match_ledger is not None:
            lost = self.match_ledger.setdefault("lost", [])
            lost.append(dict(record))
            if len(lost) > _UNIT_LOSS_LIMIT:
                del lost[:-_UNIT_LOSS_LIMIT]

    @staticmethod
    def _inherit_unit(dst: BoardUnit, src: BoardUnit) -> None:
        if dst.card_name is None and src.card_name is not None:
            dst.card_name = src.card_name
            dst.card_id = src.card_id
            dst.name_confidence = src.name_confidence
        elif dst.card_id is None and src.card_id is not None:
            dst.card_id = src.card_id
        if dst.nation is None and src.nation is not None:
            dst.nation = src.nation
        if dst.cost is None and src.cost is not None:
            dst.cost = src.cost
        if dst.attack is None and src.attack is not None:
            dst.attack = src.attack
            dst.max_health_seen = src.max_health_seen
        if dst.health is None and src.health is not None:
            dst.health = src.health
            dst.max_health_seen = src.max_health_seen
        if not dst.first_seen_turn and src.first_seen_turn:
            dst.first_seen_turn = src.first_seen_turn

    # ------------------------------------------------------------- Order

    def _order_diff(self, prev: MatchState, state: MatchState, now: float) -> list[GameEvent]:
        order = state.last_order_card
        if not isinstance(order, dict):
            return []
        if self._order_key(order) == self._order_key(prev.last_order_card):
            return []

        side = str(order.get("side") or "").strip().lower()
        if side not in (SIDE_MINE, SIDE_ENEMY):
            return []
        name = str(order.get("name") or "").strip()
        confidence = _as_float(order.get("confidence"), 0.0)
        if not name or name.lower() in _ORDER_PLACEHOLDERS:
            return []
        if confidence < self.order_min_confidence:
            return []

        side_word = "对手" if side == SIDE_ENEMY else "你"
        card_id = order.get("card_id") or order.get("id")
        extra: dict[str, Any] = {
            "name": name,
            "confidence": round(confidence, 2),
        }
        if card_id:
            extra["card_id"] = str(card_id)
        return [self._make_event(
            E_ORDER_CARD, 5, f"{side_word}打出了{name}。", now,
            side=side, entity=str(card_id) if card_id else "order",
            extra=extra,
        )]

    @staticmethod
    def _order_key(order: Any) -> tuple[Any, ...] | None:
        if not isinstance(order, dict):
            return None
        return (
            order.get("ts"),
            order.get("side"),
            order.get("name"),
            order.get("card_id") or order.get("id"),
        )

    # ------------------------------------------------------------- 事件工具

    def _new_episode(self) -> int:
        self._episode_seq += 1
        return self._episode_seq

    @staticmethod
    def _unwrap_event(event: Any) -> GameEvent:
        """Accept either a GameEvent or a CandidateEvent-like wrapper."""
        if hasattr(event, "kind") and hasattr(event, "details"):
            return event
        inner = getattr(event, "event", None)
        return inner if inner is not None else event

    def _next_occurrence(self, kind: str, side: str | None, entity: Any) -> int:
        key = (str(kind), str(side or ""), str(entity if entity is not None else ""))
        value = self._occurrence.get(key, 0) + 1
        self._occurrence[key] = value
        return value

    def _identity_of(self, event: GameEvent) -> str:
        try:
            from ..runtime.contracts import event_identity
        except Exception:  # pragma: no cover
            return "|".join((
                str(getattr(event, "kind", "")),
                repr(sorted((getattr(event, "details", {}) or {}).items())),
            ))
        return event_identity(event)

    def _make_event(
        self,
        kind: str,
        priority: int,
        summary: str,
        now: float,
        *,
        side: str | None = None,
        entity: Any = None,
        band: int | None = None,
        edge: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> GameEvent:
        details: dict[str, Any] = {
            "game": self._game_number,
            "turn": self._turn,
            "session_id": self._session_id,
        }
        if side is not None:
            details["side"] = side
        if entity is not None:
            details["entity"] = str(entity)
        if band is not None:
            details["band"] = int(band)
        if edge is not None:
            details["edge"] = edge
        details["occurrence"] = self._next_occurrence(kind, side, entity)
        if extra:
            details.update(extra)
        return GameEvent(kind=kind, priority=int(priority), summary=summary,
                         ts=now, details=details)
