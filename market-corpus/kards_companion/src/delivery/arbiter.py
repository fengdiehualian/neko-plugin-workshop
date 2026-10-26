"""Commentary arbitration for KARDS.

The arbiter owns *selection policy*, not the delivery slot.  ``select`` is a
pure batch operation: it evaluates a candidate batch, ranks the allowed
candidates (terminal -> confirmed own-side critical preempt -> canonical
priority -> newest timestamp -> input order) and returns at most one winner.
It never records a submission.  The single commit point remains
:meth:`CommentaryArbiter.mark_submitted`, called by the dispatcher only after
the SDK locally accepted the delivery.

All time arithmetic uses the injected ``now_fn`` (or an explicit ``now``).
Only event freshness uses ``GameEvent.ts``; a wall-clock default timestamp is
treated as unknown when the caller supplies a synthetic clock domain.

No module in ``delivery`` imports ``policy.safety``; the reverse dependency
(``policy.safety`` imports :class:`Decision`) keeps the graph acyclic.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, replace
from typing import Any, Iterable, Mapping

from ..runtime.contracts import (
    CATEGORY_CRITICAL,
    CATEGORY_PRESSURE,
    CRITICAL,
    EDGE_RECOVERY,
    EDGE_UPGRADE,
    ENDED,
    IN_MATCH,
    OPENING,
    OUT_OF_BATTLE,
    PRESSURE,
    category_allowed,
    event_edge,
    event_identity,
    event_level,
    event_max_age_seconds,
    event_spec,
)
from ..state.model import (
    E_MATCH_START,
    HQ_CRITICAL_HP,
    SCENE_DEFEAT,
    SCENE_DRAFT,
    SCENE_MATCH,
    SCENE_MENU,
    SCENE_VICTORY,
    SIDE_ENEMY,
    SIDE_MINE,
    GameEvent,
    MatchState,
)

# Reasons kept for compatibility with the existing dispatcher/timeline.
ALLOWED = "allowed"
MODE_SILENT = "mode_silent"
DO_NOT_DISTURB = "do_not_disturb"
LLM_CONSENT_REQUIRED = "llm_consent_required"
PASSIVE_STATE_EVENT = "passive_state_event"  # reserved; no longer used by match_start
BELOW_MIN_PRIORITY = "below_min_priority"
SEMANTIC_DUPLICATE = "semantic_duplicate"
COOLDOWN = "cooldown"
# New stable reasons.
CATEGORY_NOT_ALLOWED = "category_not_allowed"
ONCE_PER_MATCH = "once_per_match"
EVENT_EXPIRED = "event_expired"
EVENT_IN_FUTURE = "event_in_future"
INVALID_TIMESTAMP = "invalid_timestamp"
LOST_IN_WINDOW = "lost_in_window"

_SUBMITTED_LIMIT = 512
_KIND_CLOCK_LIMIT = 256
_ONCE_LIMIT = 512

_LEVEL_RANK = {
    "info": 0,
    "notice": 1,
    "warning": 2,
    "critical": 3,
    "terminal": 4,
}


@dataclass
class ArbiterConfig:
    mode: str = "companion"            # companion | coach | silent
    do_not_disturb: bool = False
    llm_data_consent: bool = True
    min_priority: int = 4
    cooldown_seconds: float = 25.0
    critical_cooldown_seconds: float = 5.0
    critical_priority_at: int = 8
    # Freshness: per-kind spec age is intersected with this value.
    max_event_age_seconds: float = 12.0
    # Identity is stable enough to suppress polling duplicates, but only in a
    # short window; the old 120 s "same semantics" kill is intentionally gone.
    duplicate_window_seconds: float = 3.0


@dataclass(frozen=True)
class Decision:
    """Arbiter verdict with a machine-readable reason.

    ``retry_after`` is only meaningful for time-based rejections.  It is
    ``0.0`` for permanent rejections (silent, DND, category, TTL, once,
    priority) and for allowed decisions.
    """

    allowed: bool
    reason: str
    retry_after: float = 0.0

    @property
    def denied(self) -> bool:
        return not self.allowed

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "retry_after": self.retry_after,
        }


@dataclass(frozen=True)
class Selection:
    """Result of a batch select.  ``decisions`` follows input order."""

    event: GameEvent | None
    decisions: list[Decision]

    def to_dict(self) -> dict[str, object]:
        return {
            "event": None if self.event is None else self.event.to_dict(),
            "decisions": [decision.to_dict() for decision in self.decisions],
        }


@dataclass(frozen=True)
class _Submitted:
    identity: str
    kind: str
    session: str
    game: str
    level: str
    edge: str
    ts: float

    def to_dict(self) -> dict[str, object]:
        return {
            "identity": self.identity,
            "kind": self.kind,
            "session": self.session,
            "game": self.game,
            "level": self.level,
            "edge": self.edge,
            "ts": self.ts,
        }


def _finite(value: Any, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    return number if math.isfinite(number) else float(fallback)


def _bounded_remaining(remaining: float) -> float:
    """A JSON-safe, non-negative retry hint."""
    value = _finite(remaining, 0.0)
    return round(value, 3) if value > 0.0 else 0.0


def _safe_component(value: Any, default: str = "-") -> str:
    if value is None:
        return default
    text = " ".join(str(value).replace("\x00", " ").split())
    text = text.replace("|", "_")[:64].strip()
    return text or default


def _event_ts(event: GameEvent) -> float:
    return _finite(getattr(event, "ts", 0.0), 0.0)


def _is_own_critical_preempt(event: GameEvent) -> bool:
    """Only a confirmed own-side critical crisis may preempt.

    ``event_spec`` already clears ``preempt`` for enemy/unknown sides, and the
    side lookup only trusts ``mine``/``enemy``.  Requiring both the flag and a
    critical level keeps a custom event from opting into preemption.
    """
    spec = event_spec(event)
    return bool(spec.preempt) and spec.category == CATEGORY_CRITICAL


def _is_critical(spec, cfg: ArbiterConfig) -> bool:
    if spec.terminal:
        return False
    return (
        spec.category == CATEGORY_CRITICAL
        or spec.preempt
        or int(spec.priority) >= int(cfg.critical_priority_at)
    )


class CommentaryArbiter:
    """Priority gate, per-kind timing and a bounded submitted ledger.

    The ledger is intentionally bounded (512 recent submissions).  It is used
    for short-window duplicate suppression, ``once_per_match`` and diagnostics
    only; a new event never waits forever behind an old identity.
    """

    def __init__(self, config: ArbiterConfig, *, now_fn=time.time) -> None:
        self.config = config
        self._now = now_fn
        self._lock = threading.RLock()
        self._last_at = 0.0
        self._last_critical_at = 0.0
        self._submitted: OrderedDict[str, _Submitted] = OrderedDict()
        self._once: OrderedDict[str, float] = OrderedDict()
        self._kind_last: OrderedDict[str, tuple[float, int]] = OrderedDict()

    # ------------------------------------------------------------------ 配置

    def update(self, config: ArbiterConfig) -> None:
        with self._lock:
            self.config = config

    def reset(self) -> None:
        with self._lock:
            self._last_at = 0.0
            self._last_critical_at = 0.0
            self._submitted.clear()
            self._once.clear()
            self._kind_last.clear()

    # ------------------------------------------------------------------ 门控

    def allow(self, event: GameEvent, state: MatchState, *,
              scenario: str | None = None,
              now: float | None = None) -> bool:
        """Backward-compatible boolean gate."""
        return self.decide(
            event, state, scenario=scenario, now=now).allowed

    def decide(self, event: GameEvent, state: MatchState, *,
               scenario: str | None = None,
               now: float | None = None) -> Decision:
        """Evaluate one event without writing the submitted ledger."""
        cfg = self.config
        current = _finite(self._now() if now is None else now, 0.0)

        # User preference / privacy gates come first and are never bypassed,
        # not even by terminal events.
        if cfg.mode == "silent":
            return Decision(False, MODE_SILENT)
        if cfg.do_not_disturb:
            return Decision(False, DO_NOT_DISTURB)
        if not cfg.llm_data_consent:
            return Decision(False, LLM_CONSENT_REQUIRED)

        spec = event_spec(event)

        # Freshness: reject NaN/inf/future timestamps and stale events.  A
        # missing (<= 0) timestamp cannot be aged and is left to the caller.
        timestamp_reason = self._timestamp_reason(event, current, cfg)
        if timestamp_reason is not None:
            return Decision(False, timestamp_reason)

        scenario_value = (
            scenario if scenario is not None
            else self._infer_scenario(event, state, spec)
        )
        if not category_allowed(scenario_value, spec.category):
            return Decision(False, CATEGORY_NOT_ALLOWED)

        if spec.priority < cfg.min_priority:
            return Decision(False, BELOW_MIN_PRIORITY)

        session, game = self._match_key(event)
        if spec.once_per_match and self._once_seen(spec.kind, session, game):
            return Decision(False, ONCE_PER_MATCH)

        if spec.terminal:
            # Terminal bypasses pacing (global/per-kind cooldown and the
            # short duplicate window) but still passed every hard gate above.
            return Decision(True, ALLOWED)

        identity = event_identity(event)
        level = event_level(event)
        edge = event_edge(event)

        with self._lock:
            duplicate_remaining = self._duplicate_remaining(
                identity, current, cfg)
            if duplicate_remaining > 0.0:
                return Decision(
                    False, SEMANTIC_DUPLICATE,
                    _bounded_remaining(duplicate_remaining))

            kind_remaining = self._kind_remaining(
                event, spec, level, edge, current, session, game)
            global_remaining = self._global_remaining(spec, current, cfg)
            remaining = max(kind_remaining, global_remaining)
            if remaining > 0.0:
                return Decision(False, COOLDOWN, _bounded_remaining(remaining))

        return Decision(True, ALLOWED)

    # ------------------------------------------------------------------ 批量选择

    def select(self, events: Iterable[GameEvent], state: MatchState, *,
               scenario: str,
               now: float | None = None) -> Selection:
        """Return at most one winner and one decision per input event.

        Pure: no submitted ledger, cooldown clock or selection state is
        written.  All decisions are evaluated against the same ``now``.
        """
        current = _finite(self._now() if now is None else now, 0.0)
        items = list(events)
        unwrapped = [self._unwrap(item) for item in items]
        decisions = [
            self.decide(event, state, scenario=scenario, now=current)
            for event in unwrapped
        ]

        winner = -1
        best_key: tuple[int, int, int, float, int] | None = None
        for index, (event, decision) in enumerate(zip(unwrapped, decisions)):
            if not decision.allowed:
                continue
            spec = event_spec(event)
            key = (
                0 if spec.terminal else 1,
                0 if _is_own_critical_preempt(event) else 1,
                -int(spec.priority),
                -_event_ts(event),
                index,
            )
            if best_key is None or key < best_key:
                best_key = key
                winner = index

        if winner < 0:
            return Selection(None, decisions)

        final = [
            decision if (decision.allowed and index == winner)
            else (Decision(False, LOST_IN_WINDOW) if decision.allowed
                  else decision)
            for index, decision in enumerate(decisions)
        ]
        return Selection(unwrapped[winner], final)

    # ------------------------------------------------------------------ 提交

    def mark_submitted(self, event: GameEvent, *,
                       now: float | None = None) -> None:
        """Record an SDK-locally accepted delivery.  Only commit point."""
        current = _finite(self._now() if now is None else now, 0.0)
        spec = event_spec(event)
        identity = event_identity(event)
        session, game = self._match_key(event)
        record = _Submitted(
            identity=identity,
            kind=spec.kind,
            session=session,
            game=game,
            level=event_level(event),
            edge=event_edge(event),
            ts=current,
        )
        with self._lock:
            if identity in self._submitted:
                del self._submitted[identity]
            self._submitted[identity] = record
            while len(self._submitted) > _SUBMITTED_LIMIT:
                self._submitted.popitem(last=False)

            if spec.once_per_match:
                key = self._once_key(spec.kind, session, game)
                if key in self._once:
                    del self._once[key]
                self._once[key] = current
                while len(self._once) > _ONCE_LIMIT:
                    self._once.popitem(last=False)

            kind_key = self._kind_clock_key(event, spec, session, game)
            if kind_key in self._kind_last:
                del self._kind_last[kind_key]
            self._kind_last[kind_key] = (
                current, _level_rank(event_level(event)))
            while len(self._kind_last) > _KIND_CLOCK_LIMIT:
                self._kind_last.popitem(last=False)

            self._last_at = current
            if _is_critical(spec, self.config):
                self._last_critical_at = current

    # ------------------------------------------------------------------ 克隆

    def snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        """JSON-safe diagnostic state (no GameEvent/OCR/prompt content).

        The bounded ledger is summarised: only the 30 most recent identities
        are exposed, so a tick-time snapshot stays small and safe to log.
        """
        with self._lock:
            recent = list(self._submitted.values())[-30:]
            return {
                "schema_version": "kards_arbiter_v1",
                "config": self._config_dict(),
                "last_submitted_at": _finite(self._last_at, 0.0),
                "last_critical_at": _finite(self._last_critical_at, 0.0),
                "submitted_count": len(self._submitted),
                "recent_submitted": [record.to_dict() for record in recent],
                "once_count": len(self._once),
                "recent_once": list(self._once.keys())[-30:],
            }

    def fork(self, reset: bool = False) -> "CommentaryArbiter":
        """Copy config and ledger for a dry-run simulation clone.

        ``reset=True`` keeps the same configuration and clock but starts with
        an empty ledger/cooldown state.
        """
        clone = CommentaryArbiter(replace(self.config), now_fn=self._now)
        if reset:
            return clone
        with self._lock:
            clone._last_at = self._last_at
            clone._last_critical_at = self._last_critical_at
            clone._submitted = OrderedDict(self._submitted)
            clone._once = OrderedDict(self._once)
            clone._kind_last = OrderedDict(self._kind_last)
        return clone

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _unwrap(item: Any) -> GameEvent:
        event = getattr(item, "event", None)
        return event if isinstance(event, GameEvent) else item

    def _config_dict(self) -> dict[str, Any]:
        data = asdict(self.config)
        for key, value in data.items():
            if isinstance(value, float):
                data[key] = _finite(value, 0.0)
        return data

    @staticmethod
    def _timestamp_reason(event: GameEvent, current: float,
                          cfg: ArbiterConfig) -> str | None:
        raw = getattr(event, "ts", 0.0)
        try:
            ts = float(raw)
        except (TypeError, ValueError):
            return INVALID_TIMESTAMP
        if not math.isfinite(ts):
            return INVALID_TIMESTAMP
        if ts <= 0.0:
            # No usable timestamp.  The arbiter cannot age the event, so the
            # dispatcher's queue TTL remains the explicit freshness fallback.
            return None

        if ts > current:
            return EVENT_IN_FUTURE

        max_age = event_max_age_seconds(
            event, _finite(cfg.max_event_age_seconds, 12.0))
        if max_age > 0.0 and current - ts > max_age + 1e-9:
            return EVENT_EXPIRED
        return None

    @staticmethod
    def _match_key(event: GameEvent) -> tuple[str, str]:
        details = getattr(event, "details", None)
        details = details if isinstance(details, Mapping) else {}
        session = _safe_component(details.get("session_id"), "")
        game_value = details.get("game", details.get("game_number", 0))
        game = _safe_component(game_value, "0")
        return session, game

    @staticmethod
    def _kind_clock_key(event: GameEvent, spec, session: str,
                        game: str) -> str:
        details = getattr(event, "details", None)
        details = details if isinstance(details, Mapping) else {}
        side = str(details.get("side") or "").strip().lower()
        side = side if side in (SIDE_MINE, SIDE_ENEMY) else "-"
        return f"{session}|{game}|{spec.kind}|{side}"

    @staticmethod
    def _once_key(kind: str, session: str, game: str) -> str:
        return f"{session}|{game}|{kind}"

    def _once_seen(self, kind: str, session: str, game: str) -> bool:
        key = self._once_key(kind, session, game)
        with self._lock:
            return key in self._once

    def _duplicate_remaining(self, identity: str, current: float,
                             cfg: ArbiterConfig) -> float:
        record = self._submitted.get(identity)
        if record is None:
            return 0.0
        window = max(0.0, _finite(cfg.duplicate_window_seconds, 0.0))
        if window <= 0.0:
            return 0.0
        elapsed = current - record.ts
        if elapsed < 0.0:
            # A clock rollback must not create a permanent suppression.
            return 0.0
        return window - elapsed

    def _kind_remaining(self, event: GameEvent, spec, level: str, edge: str,
                        current: float, session: str,
                        game: str) -> float:
        key = self._kind_clock_key(event, spec, session, game)
        state = self._kind_last.get(key)
        if state is None:
            return 0.0
        last_ts, last_rank = state
        cooldown = max(0.0, _finite(spec.cooldown_seconds, 0.0))
        if cooldown <= 0.0:
            return 0.0
        elapsed = current - last_ts
        if elapsed < 0.0:
            return 0.0
        remaining = cooldown - elapsed
        if remaining <= 0.0:
            return 0.0
        # Escalation is new information: a higher band may jump the previous
        # band's per-kind clock.  The independent global critical clock still
        # guards the actual critical rhythm (5 s by default).
        if _level_rank(level) > last_rank:
            return 0.0
        if edge in (EDGE_UPGRADE, EDGE_RECOVERY):
            return 0.0
        return remaining

    def _global_remaining(self, spec, current: float,
                          cfg: ArbiterConfig) -> float:
        critical = _is_critical(spec, cfg)
        # Critical pacing has its own clock: an ordinary output must never
        # consume the chance to call out a sudden own-side crisis inside the
        # short event TTL.
        anchor = self._last_critical_at if critical else self._last_at
        if anchor <= 0.0:
            return 0.0
        cooldown = (cfg.critical_cooldown_seconds
                    if critical else cfg.cooldown_seconds)
        cooldown = max(0.0, _finite(cooldown, 0.0))
        if cooldown <= 0.0:
            return 0.0
        elapsed = current - anchor
        if elapsed < 0.0:
            return 0.0
        return cooldown - elapsed

    @staticmethod
    def _infer_scenario(event: GameEvent, state: MatchState, spec) -> str:
        if spec.terminal or state.scene in (SCENE_VICTORY, SCENE_DEFEAT):
            return ENDED
        if spec.kind == E_MATCH_START:
            return OPENING
        if state.in_match:
            if state.my_hq is not None and state.my_hq <= HQ_CRITICAL_HP:
                return CRITICAL
            if spec.category == CATEGORY_CRITICAL:
                return CRITICAL
            if spec.category == CATEGORY_PRESSURE:
                return PRESSURE
            if state.turn_number <= 1:
                return OPENING
            return IN_MATCH
        if state.scene in (SCENE_MENU, SCENE_DRAFT):
            return OUT_OF_BATTLE
        if state.scene == SCENE_MATCH:
            return OPENING
        # An unknown/untrusted state must not resurrect the legacy
        # "opening is muted" behaviour for scenario-less callers.
        return IN_MATCH


def _level_rank(level: str) -> int:
    return _LEVEL_RANK.get(str(level or "").strip().lower(), -1)


__all__ = [
    "ALLOWED",
    "BELOW_MIN_PRIORITY",
    "CATEGORY_NOT_ALLOWED",
    "COOLDOWN",
    "CommentaryArbiter",
    "DO_NOT_DISTURB",
    "Decision",
    "EVENT_EXPIRED",
    "EVENT_IN_FUTURE",
    "INVALID_TIMESTAMP",
    "LLM_CONSENT_REQUIRED",
    "LOST_IN_WINDOW",
    "MODE_SILENT",
    "ONCE_PER_MATCH",
    "PASSIVE_STATE_EVENT",
    "SEMANTIC_DUPLICATE",
    "Selection",
    "ArbiterConfig",
]
