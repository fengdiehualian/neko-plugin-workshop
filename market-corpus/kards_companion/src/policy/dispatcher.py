"""Single-slot, TTL-aware delivery dispatcher.

``read`` and ``respond`` are deliberately separate channels:

* respond candidates go through the arbiter, the output safety guard, the
  quiet/backpressure policy and finally the shared ``HostPushGate``;
* read/live-state context is delegated to a ``LiveStatePublisher`` that uses
  the same gate but its own circuit breaker and never consumes commentary
  pacing or marks a respond event delivered.

The dispatcher owns exactly **one** pending battle event and makes at most one
final host attempt per tick.  All candidates in a tick are evaluated together
by ``arbiter.select`` (the highest one wins, not merely the first), then the
winner is compared with any pending retry.  Event timestamps and expiry are
preserved across retries: ``now + ttl`` is never recomputed.
"""

from __future__ import annotations

import copy
import math
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..adapters.target import (
    TargetResolution,
    clean_target_lanlan,
    resolve_target,
)
from ..delivery.arbiter import CommentaryArbiter, Decision
from ..delivery.contracts import (
    REASON_CONSENT_REQUIRED,
    REASON_DRY_RUN,
    REASON_PAUSED,
    REASON_STALE_CONTEXT,
    REASON_STALE_EPOCH,
    REASON_STOPPED,
    REASON_STOPPING,
    REASON_TRANSPORT_UNAVAILABLE,
    GateResult,
    HostPushConfig,
    HostPushGate,
)
from ..delivery.publisher import (
    PublishStatus,
    build_commentary_prompt,
    build_live_state_text,
    situation_payload,
)
from ..runtime.contracts import (
    CATEGORY_CRITICAL,
    IN_MATCH,
    LEVEL_CRITICAL,
    event_edge,
    event_identity,
    event_level,
    event_max_age_seconds,
    event_spec,
    normalize_event,
)
from ..state.model import E_MATCH_END, GameEvent, MatchState
from .safety import SafetyCircuitBreaker, SafetySnapshot

_UNSET = object()

_INTERRUPT_MODES = frozenset({"allow_interrupt", "critical_only", "no_interrupt"})

# Denials that will not become allowed by waiting inside this TTL.
_DROP_REASONS = frozenset({
    "passive_state_event", "below_min_priority", "semantic_duplicate",
    "category_not_allowed", "scenario_category_blocked", "once_per_match",
    "duplicate", "mode_silent", "do_not_disturb", "llm_consent_required",
    "critical_only", "not_own_critical", "event_expired", "event_in_future",
    "invalid_timestamp", "lost_in_window",
})

# Reasons that are hard policy suppression, not transport failures.
_SUPPRESS_REASONS = frozenset({
    REASON_CONSENT_REQUIRED, REASON_STOPPED, REASON_STOPPING,
    REASON_PAUSED, "stopped", "stopping", "paused",
})

_READ_LIKE_EVENT_KINDS = frozenset({
    "unit_played", "unit_died", "unit_advanced", "order_card_seen",
    E_MATCH_END,
})

# Local source-freshness rejections from the publisher: not transport failures.
_STALE_SOURCE_REASONS = frozenset({
    "source_expired", "source_invalid", "context_invalid",
    "situation_invalid",
})


@dataclass(frozen=True)
class DispatcherConfig:
    """Delivery policy.

    ``max_queue`` is retained for old callers; the production path is always a
    single pending slot.
    """

    max_queue: int = 1
    ttl_seconds: float | None = None
    battle_event_ttl_seconds: float = 12.0
    critical_priority_at: int | None = None
    max_attempts: int = 2
    source: str = "kards_companion"
    target_lanlan: str = ""
    plugin_owned_output_enabled: bool = False
    reply_max_chars: int = 28
    coalesce_key: str = "kards:battle_event"

    # War Thunder-style quiet / backpressure windows.
    user_chat_quiet_window_seconds: float = 60.0
    battle_output_quiet_window_seconds: float = 30.0
    battle_output_backpressure_seconds: float = 20.0
    interrupt_mode: str = "allow_interrupt"

    # Hard policy shared with the gate.
    dry_run: bool = False
    llm_data_consent: bool = True
    simulated: bool = False
    authorized_real_test: bool = False

    max_simulated_records: int = 200
    known_targets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_queue < 1:
            raise ValueError("max_queue must be >= 1")
        if self.ttl_seconds is not None and self.ttl_seconds < 0:
            raise ValueError("ttl_seconds must be >= 0")
        if self.battle_event_ttl_seconds < 0:
            raise ValueError("battle_event_ttl_seconds must be >= 0")
        if self.critical_priority_at is not None and self.critical_priority_at < 1:
            raise ValueError("critical_priority_at must be >= 1")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.reply_max_chars < 1:
            raise ValueError("reply_max_chars must be >= 1")
        if not str(self.coalesce_key).strip():
            raise ValueError("coalesce_key must be non-empty")
        if self.interrupt_mode not in _INTERRUPT_MODES:
            raise ValueError(f"interrupt_mode must be one of {sorted(_INTERRUPT_MODES)}")
        if self.max_simulated_records < 0:
            raise ValueError("max_simulated_records must be >= 0")
        for name in ("user_chat_quiet_window_seconds",
                     "battle_output_quiet_window_seconds",
                     "battle_output_backpressure_seconds"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")
        object.__setattr__(self, "known_targets",
                           tuple(str(v) for v in self.known_targets if str(v)))


@dataclass(frozen=True)
class SubmitResult:
    accepted: bool
    reason: str
    queue_depth: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "queue_depth": self.queue_depth,
        }


@dataclass(frozen=True)
class DispatchOutcome:
    event: GameEvent
    status: str
    reason: str = ""
    attempts: int = 0

    def to_dict(self) -> dict[str, object]:
        """Serializable diagnostic form; never includes prompt or OCR text."""
        return {
            "kind": self.event.kind,
            "priority": self.event.priority,
            "status": self.status,
            "reason": self.reason,
            "attempts": self.attempts,
        }


@dataclass
class _Pending:
    event: GameEvent
    state: MatchState
    scenario: str
    session_id: str
    match_id: Any
    target_lanlan: str
    route_reason: str
    enqueued_at: float
    expires_at: float
    event_expires_at: float
    order: int = 0
    attempts: int = 0
    generation: int = 0
    # Captured when the tick admitted the item.  Never refreshed at attempt
    # time: a stop/reopen must invalidate an old tick's token.
    token: str = ""
    critical: bool = False
    terminal: bool = False

    @property
    def identity(self) -> str:
        details = getattr(self.event, "details", None)
        if isinstance(details, Mapping) and details.get("identity"):
            return str(details["identity"])
        return event_identity(self.event)


def _copy_state(state: MatchState) -> MatchState:
    try:
        return copy.deepcopy(state)
    except Exception:
        try:
            return state.copy()  # type: ignore[attr-defined]
        except Exception:
            return state


def _copy_event(event: GameEvent) -> GameEvent:
    try:
        return copy.deepcopy(event)
    except Exception:
        details = dict(getattr(event, "details", None) or {})
        return GameEvent(kind=event.kind, priority=event.priority,
                         summary=event.summary, ts=event.ts, details=details)


def _clean_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


def _decision_allowed(decision: Any) -> bool:
    if decision is None:
        return True
    return bool(getattr(decision, "allowed", decision))


def _decision_reason(decision: Any) -> str:
    if decision is None:
        return ""
    return str(getattr(decision, "reason", "") or "")


def _decision_retry_after(decision: Any) -> float:
    try:
        value = float(getattr(decision, "retry_after", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, value)


class DeliveryDispatcher:
    """Single-slot dispatcher; call from the perception tick thread."""

    def __init__(
        self,
        arbiter: CommentaryArbiter,
        *,
        push_message: Callable[..., Any] | None = None,
        gate: HostPushGate | None = None,
        read_publisher: Any = None,
        breaker: SafetyCircuitBreaker | None = None,
        read_breaker: SafetyCircuitBreaker | None = None,
        safety_guard: Any = None,
        config: DispatcherConfig | None = None,
        now_fn: Callable[[], float] = time.time,
        timeline: Any = None,
        prompt_builder: Callable[[str, MatchState], str] | None = None,
        logger: Any = None,
        target_resolver: Callable[[GameEvent], Any] | None = None,
        on_delivered: Callable[[GameEvent, MatchState, Mapping[str, Any]], None] | None = None,
        known_targets: Iterable[str] | Callable[[], Iterable[str]] | None = None,
    ) -> None:
        self.config = config or DispatcherConfig()
        self._arbiter = arbiter
        self._gate = gate
        if self._gate is None:
            self._gate = HostPushGate(
                push_message,
                config=HostPushConfig(
                    dry_run=self.config.dry_run,
                    consent=self.config.llm_data_consent,
                    simulated=self.config.simulated,
                    authorized_real_test=self.config.authorized_real_test,
                ),
                now_fn=now_fn,
            )
        elif push_message is not None:
            # The injected gate is authoritative: a second raw push callable
            # would bypass consent/stop/epoch checks.
            self._raw_push_ignored = True
        self._read_publisher = read_publisher
        self._breaker = breaker or SafetyCircuitBreaker(now_fn=now_fn)
        # Read and respond breakers are independent; a failing read channel
        # must not block battle call-outs and vice versa.
        self._read_breaker = read_breaker or SafetyCircuitBreaker(now_fn=now_fn)
        self._guard = safety_guard
        self._target_resolver = target_resolver
        self._on_delivered = on_delivered
        self._now = now_fn
        self._timeline = timeline
        self._logger = logger
        self._known_targets = (tuple(str(v) for v in known_targets if str(v))
                               if known_targets is not None
                               and not callable(known_targets)
                               else (known_targets if callable(known_targets)
                                     else self.config.known_targets))
        self._prompt = prompt_builder or (
            lambda summary, state, event=None, now=None: build_commentary_prompt(
                summary, state, event=event,
                mode=getattr(getattr(self._arbiter, "config", None),
                             "mode", "companion"),
                max_chars=self.config.reply_max_chars, now=now))
        self._lock = threading.RLock()
        self._pending: _Pending | None = None
        self._state: MatchState | None = None
        self._scenario = IN_MATCH
        self._session_id = ""
        self._match_id: Any = ""
        self._target = ""
        self._generation = 0
        self._seq = 0
        self._last_user_chat_at = 0.0
        self._last_chat_by_target: dict[str, float] = {}
        self._last_output_at = 0.0
        self._last_output_by_target: dict[str, float] = {}
        self._sim_arbiter: Any = None
        self._sim_guard: Any = None
        self._sim_arbiter_is_real = False
        self._sim_guard_is_real = False
        self._simulated_records: list[dict[str, Any]] = []
        self._counters: dict[str, int] = {
            "queued": 0, "submitted": 0, "simulated": 0, "deferred": 0,
            "waiting": 0, "dropped": 0, "expired": 0, "replaced": 0,
            "purged": 0, "retried": 0, "failed": 0, "unconfirmed": 0,
            "suppressed": 0, "route_unavailable": 0, "quiet_window": 0,
            "passive_context": 0, "read_submitted": 0, "read_coalesced": 0,
            "read_failed": 0, "read_suppressed": 0, "read_unconfirmed": 0,
            "read_stale": 0,
        }
        self._refresh_config_locked()

    # ------------------------------------------------------------------ config

    def _refresh_config_locked(self) -> None:
        arbiter_config = getattr(self._arbiter, "config", None)
        configured = (self.config.critical_priority_at
                      if self.config.critical_priority_at is not None
                      else getattr(arbiter_config, "critical_priority_at", 8))
        try:
            self._critical_at = int(configured or 8)
        except (TypeError, ValueError):
            self._critical_at = 8
        self._delivery_ttl = float(
            self.config.battle_event_ttl_seconds
            if self.config.ttl_seconds is None
            else self.config.ttl_seconds)

    def _gate_config_locked(self) -> HostPushConfig:
        """Project the dispatcher policy onto the shared gate.

        The dispatcher's config is authoritative for dry/consent because
        ``update_config`` is the hot-reload path; ``max_records`` stays a
        gate-local diagnostic limit.
        """
        gate_config = getattr(self._gate, "config", None)
        base = gate_config if isinstance(gate_config, HostPushConfig) else HostPushConfig()
        return HostPushConfig(
            dry_run=bool(self.config.dry_run),
            consent=bool(self.config.llm_data_consent),
            simulated=bool(self.config.simulated),
            max_records=base.max_records,
            authorized_real_test=bool(self.config.authorized_real_test),
        )

    def update_config(self, config: DispatcherConfig) -> list[DispatchOutcome]:
        """Hot-reload configuration; any route/dry/consent change purges pending."""
        if not isinstance(config, DispatcherConfig):
            raise TypeError("config must be DispatcherConfig")
        with self._lock:
            self.config = config
            self._refresh_config_locked()
            # ``update_config`` always invalidates admissions: the gate token
            # may have changed even when only an unrelated field was edited,
            # and an old pending item must not be carried into a new policy
            # generation (doc: "任一变更立即 purge pending").
            self._generation += 1
            outcomes = self._purge_locked("config_changed", self._now())
            updater = getattr(self._gate, "update_config", None)
            if callable(updater):
                try:
                    updater(self._gate_config_locked())
                except Exception:
                    pass
            simulated_now = self._is_simulated()
            if simulated_now and self._sim_arbiter is None:
                self._ensure_simulation_forks_locked()
            if not simulated_now:
                self._sim_arbiter = None
                self._sim_guard = None
                self._sim_arbiter_is_real = False
                self._sim_guard_is_real = False
                self._simulated_records.clear()
            return outcomes

    def note_user_chat(self, *, now: float | None = None,
                       target_lanlan: str = "", is_voice: bool = False) -> None:
        """Record chat timing only; message text is never stored."""
        current = self._now() if now is None else float(now)
        target = clean_target_lanlan(target_lanlan)
        with self._lock:
            self._last_user_chat_at = current
            self._last_chat_by_target[target or ""] = current
            # ``is_voice`` is accepted for the host contract; it does not widen
            # or shrink the quiet window.

    @property
    def last_user_chat_at(self) -> float:
        with self._lock:
            return self._last_user_chat_at

    @property
    def target_lanlan(self) -> str:
        with self._lock:
            if self._target:
                return self._target
            return clean_target_lanlan(self.config.target_lanlan)

    # ------------------------------------------------------------------ state

    @property
    def pending(self) -> int:
        with self._lock:
            return 1 if self._pending is not None else 0

    @property
    def pending_event(self) -> GameEvent | None:
        with self._lock:
            return self._pending.event if self._pending is not None else None

    @property
    def arbiter(self) -> CommentaryArbiter:
        return self._arbiter

    @property
    def gate(self) -> HostPushGate:
        return self._gate

    @property
    def breaker(self) -> SafetyCircuitBreaker:
        return self._breaker

    @property
    def read_breaker(self) -> SafetyCircuitBreaker:
        return self._read_breaker

    @property
    def simulated_records(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(record) for record in self._simulated_records]

    def stats(self) -> dict[str, int]:
        with self._lock:
            data = dict(self._counters)
            data["pending"] = 1 if self._pending is not None else 0
            return data

    def breaker_snapshot(self, *, now: float | None = None) -> SafetySnapshot:
        current = self._now() if now is None else float(now)
        return self._breaker.snapshot(now=current)

    def read_breaker_snapshot(self, *, now: float | None = None) -> SafetySnapshot:
        current = self._now() if now is None else float(now)
        return self._read_breaker.snapshot(now=current)

    def set_context(self, *, scenario: str | None = None,
                    session_id: str | None = None,
                    match_id: Any | None = None,
                    target_lanlan: Any = _UNSET) -> None:
        """Update the default route.  Empty target is a real value, not "unset"."""
        with self._lock:
            changed = False
            if scenario is not None and str(scenario) != self._scenario:
                self._scenario = str(scenario)
                changed = True
            if session_id is not None and str(session_id) != self._session_id:
                self._session_id = str(session_id)
                changed = True
            if match_id is not None and match_id != self._match_id:
                self._match_id = match_id
                changed = True
            if target_lanlan is not _UNSET:
                target = clean_target_lanlan(target_lanlan)
                if target != self._target:
                    self._target = target
                    changed = True
            if changed:
                self._generation += 1
                self._purge_locked("context_changed", self._now())

    def set_state(self, state: MatchState | None) -> None:
        with self._lock:
            self._state = None if state is None else _copy_state(state)

    # ------------------------------------------------------------------ helpers

    def _gate_token(self) -> str:
        getter = getattr(self._gate, "get_token", None)
        if callable(getter):
            try:
                return str(getter())
            except Exception:
                return ""
        return str(getattr(self._gate, "token", "") or "")

    def _is_simulated(self) -> bool:
        if self.config.dry_run or self.config.simulated:
            return True
        return bool(getattr(self._gate, "is_simulating", False))

    def _ensure_simulation_forks_locked(self) -> None:
        if self._sim_arbiter is not None:
            return
        fork = getattr(self._arbiter, "fork", None)
        if callable(fork):
            try:
                self._sim_arbiter = fork()
                self._sim_arbiter_is_real = False
            except Exception:
                self._sim_arbiter = None
        if self._sim_arbiter is None:
            try:
                self._sim_arbiter = copy.deepcopy(self._arbiter)
                self._sim_arbiter_is_real = False
            except Exception:
                # Never simulate by mutating the real ledger.  Selecting is
                # pure, so using the real arbiter for selection without marking
                # is a safe fallback.
                self._sim_arbiter = self._arbiter
                self._sim_arbiter_is_real = True
        if self._guard is not None:
            guard_fork = getattr(self._guard, "fork", None)
            if callable(guard_fork):
                try:
                    self._sim_guard = guard_fork()
                    self._sim_guard_is_real = False
                except Exception:
                    self._sim_guard = None
            if self._sim_guard is None:
                try:
                    self._sim_guard = copy.deepcopy(self._guard)
                    self._sim_guard_is_real = False
                except Exception:
                    self._sim_guard = self._guard
                    self._sim_guard_is_real = True

    def _effective_arbiter(self) -> Any:
        if self._is_simulated():
            self._ensure_simulation_forks_locked()
            return self._sim_arbiter or self._arbiter
        return self._arbiter

    def _effective_guard(self) -> Any:
        if self._guard is None:
            return None
        if self._is_simulated():
            self._ensure_simulation_forks_locked()
            return self._sim_guard
        return self._guard

    def _record(self, stage: str, outcome: str, reason: str, current: float,
                item: _Pending | None = None, event: GameEvent | None = None,
                **detail: Any) -> None:
        if self._timeline is None:
            return
        payload: dict[str, Any] = {}
        source_event = item.event if item is not None else event
        if source_event is not None:
            details = source_event.details if isinstance(source_event.details, Mapping) else {}
            payload.update(
                kind=source_event.kind,
                priority=source_event.priority,
                identity=str(details.get("identity") or
                             event_identity(source_event))[:160],
                edge=event_edge(source_event),
                level=event_level(source_event),
                match_id=details.get("game", 0),
            )
        if item is not None:
            payload["attempts"] = item.attempts
        payload["queue_depth"] = 1 if self._pending is not None else 0
        for key, value in detail.items():
            # Only bounded structured values ever reach the timeline.
            if isinstance(value, (str, int, float, bool)) or value is None:
                payload[key] = value
            elif isinstance(value, (list, tuple)) and len(value) <= 8:
                payload[key] = [str(v)[:60] for v in value]
        try:
            self._timeline.record(stage, outcome, ts=current, reason=reason,
                                  detail=payload)
        except Exception:
            pass

    def _record_simulation(self, item: _Pending, status: str, reason: str,
                           current: float) -> None:
        if self.config.max_simulated_records <= 0:
            return
        details = item.event.details if isinstance(item.event.details, Mapping) else {}
        self._simulated_records.append({
            "kind": item.event.kind,
            "priority": int(item.event.priority),
            "reason": _clean_text(reason, 64),
            "status": _clean_text(status, 32),
            "identity": str(details.get("identity")
                            or event_identity(item.event))[:160],
            "timestamp": round(current, 3),
            "target_present": bool(item.target_lanlan),
        })
        overflow = len(self._simulated_records) - self.config.max_simulated_records
        if overflow > 0:
            del self._simulated_records[:overflow]

    def _purge_locked(self, reason: str, current: float) -> list[DispatchOutcome]:
        item = self._pending
        self._pending = None
        if item is None:
            return []
        self._counters["purged"] += 1
        clean_reason = _clean_text(reason, 120) or "purged"
        self._record("dispatcher", "purged", clean_reason, current, item=item)
        return [DispatchOutcome(item.event, "purged", clean_reason, item.attempts)]

    # ------------------------------------------------------------------ selection

    @staticmethod
    def _is_terminal_event(event: GameEvent) -> bool:
        spec = event_spec(event)
        return bool(spec.terminal or spec.category == "terminal"
                    or event.kind == E_MATCH_END)

    @staticmethod
    def _is_critical_event(event: GameEvent) -> bool:
        spec = event_spec(event)
        return (spec.category == CATEGORY_CRITICAL and bool(spec.preempt)
                and event_level(event) == LEVEL_CRITICAL)

    def _rank(self, event: GameEvent, order: int = 0) -> tuple[int, int, int, float, int]:
        return (
            1 if self._is_terminal_event(event) else 0,
            1 if self._is_critical_event(event) else 0,
            int(event.priority),
            -float(event.ts or 0.0),
            -int(order),
        )

    def _select(self, events: list[GameEvent], state: MatchState,
                scenario: str, current: float) -> tuple[GameEvent | None,
                                                        list[Any]]:
        arbiter = self._effective_arbiter()
        selector = getattr(arbiter, "select", None)
        if callable(selector):
            try:
                selection = selector(events, state, scenario=scenario, now=current)
            except TypeError:
                try:
                    selection = selector(events, state, now=current)
                except Exception:
                    selection = None
            except Exception:
                selection = None
            if selection is not None:
                selected = getattr(selection, "event", None)
                decisions = list(getattr(selection, "decisions", ()) or ())
                return selected, decisions
        # Fallback for old arbiters: evaluate each item and keep the highest.
        decisions = []
        best: GameEvent | None = None
        best_rank: tuple[int, int, int, float, int] | None = None
        for index, event in enumerate(events):
            decision = self._decide(event, state, scenario, current)
            decisions.append(decision)
            if not _decision_allowed(decision):
                continue
            rank = self._rank(event, index)
            if best_rank is None or rank > best_rank:
                best, best_rank = event, rank
        return best, decisions

    def _decide(self, event: GameEvent, state: MatchState, scenario: str,
                current: float) -> Any:
        arbiter = self._effective_arbiter()
        try:
            return arbiter.decide(event, state, scenario=scenario, now=current)
        except TypeError:
            try:
                return arbiter.decide(event, state, now=current)
            except Exception:
                return Decision(True, "allowed")

    # ------------------------------------------------------------------ route

    def _resolve_route(self, event: GameEvent, explicit_target: Any,
                       session_id: str) -> TargetResolution:
        details = event.details if isinstance(event.details, Mapping) else {}
        event_target = clean_target_lanlan(
            details.get("target_lanlan") or details.get("lanlan_name"))
        if event_target:
            return TargetResolution(event_target, "explicit_event")
        event_ctx = details.get("_ctx")
        if event_ctx is not None:
            resolved = resolve_target(None, {"details": {"_ctx": event_ctx}},
                                      environ={})
            if resolved.target:
                return TargetResolution(resolved.target, "explicit_event")
        if explicit_target is not _UNSET:
            target = clean_target_lanlan(explicit_target)
            if target:
                return TargetResolution(target, "explicit_tick")
            # An explicit empty target means "use the host fallback"; it is not
            # an absent argument and must not silently fall back to config.
            return TargetResolution("", "explicit_empty")
        if self._target_resolver is not None:
            try:
                value = self._target_resolver(event)
            except Exception:
                value = ""
            if isinstance(value, TargetResolution):
                if value.target or value.ambiguous:
                    return value
            else:
                target = clean_target_lanlan(value)
                if target:
                    return TargetResolution(target, "host_resolver")
        if self._target:
            return TargetResolution(self._target, "context")
        if self.config.target_lanlan:
            return TargetResolution(clean_target_lanlan(self.config.target_lanlan),
                                    "config")
        known = self._known_targets() if callable(self._known_targets) else self._known_targets
        try:
            known_values = [clean_target_lanlan(v) for v in (known or ())]
        except Exception:
            known_values = []
        known_values = [v for v in known_values if v]
        unique = tuple(dict.fromkeys(known_values))
        if len(unique) == 1:
            return TargetResolution(unique[0], "single_known_target", unique)
        if len(unique) > 1:
            return TargetResolution("", "ambiguous", unique)
        return TargetResolution("", "empty")

    def _expires_at(self, event: GameEvent, current: float) -> float:
        event_ts = float(getattr(event, "ts", 0.0) or 0.0)
        max_age = event_max_age_seconds(event, self._delivery_ttl)
        if event_ts > 0 and event_ts <= current:
            # Source timestamps are preserved; retries never refresh TTL.
            return event_ts + max_age
        if event_ts > current:
            # A wall-clock default event compared with a synthetic injected
            # clock is not a future fact; avoid granting a huge TTL.
            return current + max_age
        return current + max_age

    # ------------------------------------------------------------------ admission

    def _make_pending(self, event: GameEvent, state: MatchState, *,
                      scenario: str, session_id: str, match_id: Any,
                      route: TargetResolution, current: float,
                      order: int = 0, token: str = "") -> _Pending:
        expires_at = self._expires_at(event, current)
        return _Pending(
            event=_copy_event(event),
            state=_copy_state(state),
            scenario=scenario,
            session_id=session_id,
            match_id=match_id,
            target_lanlan=route.target,
            route_reason=route.reason,
            enqueued_at=current,
            expires_at=expires_at,
            event_expires_at=expires_at,
            order=order,
            generation=self._generation,
            token=token or self._gate_token(),
            critical=self._is_critical_event(event),
            terminal=self._is_terminal_event(event),
        )

    def _should_replace(self, pending: _Pending, candidate: _Pending) -> tuple[bool, str]:
        if candidate.identity == pending.identity:
            return False, "already_pending"
        if candidate.terminal and not pending.terminal:
            return True, "terminal_replaces_ordinary"
        if pending.terminal:
            return False, "pending_terminal"
        if candidate.critical and not pending.critical:
            return True, "critical_preempts"
        if pending.critical and not candidate.critical:
            return False, "pending_critical"
        if self._rank(candidate.event) > self._rank(pending.event):
            return True, "replaced_higher_priority"
        return False, "pending_higher_priority"

    def submit(self, event: GameEvent, state: MatchState, *,
               now: float | None = None,
               scenario: str | None = None,
               target_lanlan: Any = _UNSET,
               context_token: str | None = None) -> SubmitResult:
        """Legacy admission API; ``queue_depth`` is always 0 or 1."""
        current = self._now() if now is None else float(now)
        with self._lock:
            self._state = _copy_state(state)
            use_scenario = self._scenario if scenario is None else str(scenario)
            normalized = normalize_event(event, session_id=self._session_id)
            decision = self._decide(normalized, state, use_scenario, current)
            if not _decision_allowed(decision):
                self._counters["dropped"] += 1
                return SubmitResult(False, _decision_reason(decision) or "rejected",
                                    self.pending)
            route = self._resolve_route(normalized, target_lanlan, self._session_id)
            if route.ambiguous:
                self._counters["route_unavailable"] += 1
                return SubmitResult(False, "route_unavailable", self.pending)
            if self._expires_at(normalized, current) <= current:
                self._counters["expired"] += 1
                return SubmitResult(False, "ttl_expired", self.pending)
            use_match = (self._match_id
                         if self._match_id not in ("", None)
                         else (getattr(state, "game_number", "") or ""))
            self._scenario = use_scenario
            self._match_id = use_match
            candidate = self._make_pending(
                normalized, state, scenario=use_scenario,
                session_id=self._session_id, match_id=use_match,
                route=route, current=current, order=self._seq,
                token=(context_token if context_token is not None
                       else self._gate_token()))
            self._seq += 1
            if self._pending is None:
                self._pending = candidate
                self._counters["queued"] += 1
                return SubmitResult(True, "queued", 1)
            replace, reason = self._should_replace(self._pending, candidate)
            if replace:
                old = self._pending
                self._pending = candidate
                self._counters["replaced"] += 1
                self._record("dispatcher", "replaced", reason, current,
                             item=old, replacement_kind=candidate.event.kind)
                return SubmitResult(True, reason, 1)
            return SubmitResult(False, reason, 1)

    # ------------------------------------------------------------------ tick

    def tick(self, candidates: Iterable[GameEvent], state: MatchState | None = None, *,
             scenario: str | None = None, session_id: str | None = None,
             match_id: Any | None = None, target_lanlan: Any = None,
             context_token: str | None = None,
             now: float | None = None) -> list[DispatchOutcome]:
        """Evaluate a batch, keep the highest single slot, attempt at most once.

        ``target_lanlan=None`` means "not provided" (use context/config);
        an explicit ``""`` means "host fallback" and never falls through to a
        stale configured target.
        """
        current = self._now() if now is None else float(now)
        explicit_target = _UNSET if target_lanlan is None else target_lanlan
        callback: tuple[GameEvent, MatchState, dict[str, Any]] | None = None
        with self._lock:
            if state is not None:
                self._state = _copy_state(state)
            use_state = self._state or MatchState()
            use_scenario = self._scenario if scenario is None else str(scenario)
            use_session = (self._session_id if session_id is None
                           else str(session_id))
            if match_id is not None:
                use_match = match_id
            elif self._match_id not in ("", None):
                use_match = self._match_id
            else:
                use_match = getattr(use_state, "game_number", "") or ""
            # Persist derived context so drain() can validate the same route.
            self._scenario = use_scenario
            self._session_id = use_session
            self._match_id = use_match

            simulated = self._is_simulated()
            if simulated:
                self._ensure_simulation_forks_locked()
            # Bind the epoch/token captured by the *perception tick entry*.
            # A caller that passes ``context_token`` is an older tick and must
            # not be silently upgraded to the current gate token; the gate will
            # reject it after a stop/reopen.  Only an explicit None captures the
            # current token here.
            tick_token = (context_token if context_token is not None
                          else self._gate_token())

            outcomes: list[DispatchOutcome] = []
            pending = self._pending
            if pending is not None:
                if (pending.session_id != use_session
                        or pending.match_id != use_match
                        or pending.scenario != use_scenario
                        or pending.generation != self._generation):
                    self._pending = None
                    self._counters["purged"] += 1
                    self._record("dispatcher", "purged", "context_changed",
                                 current, item=pending)
                    outcomes.append(DispatchOutcome(
                        pending.event, "purged", "context_changed",
                        pending.attempts))
                    pending = None
                elif (pending.token and tick_token
                      and pending.token != tick_token):
                    # The gate epoch/context changed (pause, stop/reopen or a
                    # hot config update).  An item admitted under the old
                    # token must not be delivered under the new one.
                    self._pending = None
                    self._counters["purged"] += 1
                    self._record("dispatcher", "purged",
                                 "context_token_changed", current,
                                 item=pending)
                    outcomes.append(DispatchOutcome(
                        pending.event, "purged", "context_token_changed",
                        pending.attempts))
                    pending = None
                elif (explicit_target is not _UNSET
                      and clean_target_lanlan(explicit_target)
                      != pending.target_lanlan):
                    # The tick's route changed even though set_context() was
                    # not called; never deliver an item to its old target.
                    self._pending = None
                    self._counters["purged"] += 1
                    self._record("dispatcher", "purged", "target_changed",
                                 current, item=pending)
                    outcomes.append(DispatchOutcome(
                        pending.event, "purged", "target_changed",
                        pending.attempts))
                    pending = None
                elif pending.expires_at <= current:
                    self._pending = None
                    self._counters["expired"] += 1
                    self._record("dispatcher", "expired", "ttl_expired",
                                 current, item=pending)
                    outcomes.append(DispatchOutcome(
                        pending.event, "expired", "ttl_expired",
                        pending.attempts))
                    pending = None

            raw_candidates = list(candidates or ())
            events: list[GameEvent] = []
            for raw in raw_candidates:
                if not isinstance(raw, GameEvent):
                    continue
                events.append(normalize_event(raw, session_id=use_session))
            selected, decisions = self._select(
                events, use_state, use_scenario, current) if events else (None, [])

            selected_index = None
            if selected is not None:
                for index, event in enumerate(events):
                    if event is selected:
                        selected_index = index
                        break

            # If select() denied everything, a time-based denial (cooldown) may
            # still be worth waiting for until the event's own TTL.
            wait_candidate: GameEvent | None = None
            if selected is None and events:
                best_rank = None
                for index, event in enumerate(events):
                    decision = decisions[index] if index < len(decisions) else None
                    if _decision_allowed(decision):
                        continue
                    retry = _decision_retry_after(decision)
                    reason = _decision_reason(decision)
                    if retry <= 0 and reason not in {"cooldown", "critical_cooldown"}:
                        continue
                    expires = self._expires_at(event, current)
                    if expires <= current:
                        continue
                    if retry > 0 and current + retry >= expires:
                        continue
                    rank = self._rank(event, index)
                    if best_rank is None or rank > best_rank:
                        wait_candidate, _, best_rank = event, decision, rank
                if wait_candidate is not None:
                    selected = wait_candidate
                    selected_index = events.index(wait_candidate)

            handled_early: set[int] = set()
            if selected is not None:
                chosen_decision = (decisions[selected_index]
                                   if selected_index is not None
                                   and selected_index < len(decisions) else None)
                route = self._resolve_route(selected, explicit_target, use_session)
                if route.ambiguous:
                    self._counters["route_unavailable"] += 1
                    self._record("dispatcher", "dropped", "route_unavailable",
                                 current, event=selected)
                    outcomes.append(DispatchOutcome(
                        selected, "dropped", "route_unavailable"))
                    handled_early.add(id(selected))
                    selected = None
                else:
                    expires = self._expires_at(selected, current)
                    if expires <= current:
                        self._counters["expired"] += 1
                        outcomes.append(DispatchOutcome(
                            selected, "expired", "ttl_expired"))
                        handled_early.add(id(selected))
                        selected = None
                    else:
                        candidate = self._make_pending(
                            selected, use_state, scenario=use_scenario,
                            session_id=use_session, match_id=use_match,
                            route=route, current=current,
                            order=self._seq, token=tick_token)
                        self._seq += 1
                        if pending is None:
                            self._pending = candidate
                            pending = candidate
                            self._counters["queued"] += 1
                            self._record("dispatcher", "queued",
                                         _decision_reason(chosen_decision)
                                         or "selected", current, item=candidate)
                        elif candidate.identity == pending.identity:
                            if candidate.target_lanlan != pending.target_lanlan:
                                # The same fact was re-detected for a
                                # different route; the old binding is invalid.
                                old = pending
                                self._pending = candidate
                                pending = candidate
                                self._counters["replaced"] += 1
                                self._record("dispatcher", "replaced",
                                             "target_changed", current,
                                             item=old)
                                outcomes.append(DispatchOutcome(
                                    old.event, "replaced", "target_changed",
                                    old.attempts))
                            else:
                                # Keep the original ts/expiry; only refresh
                                # the retained public state.
                                pending.state = candidate.state
                        else:
                            replace, reason = self._should_replace(pending, candidate)
                            if replace:
                                old = self._pending or pending
                                self._pending = candidate
                                pending = candidate
                                self._counters["replaced"] += 1
                                self._record("dispatcher", "replaced", reason,
                                             current, item=old,
                                             replacement_kind=candidate.event.kind)
                                outcomes.append(DispatchOutcome(
                                    old.event, "replaced", reason, old.attempts))
                            else:
                                self._counters["dropped"] += 1
                                self._record("dispatcher", "dropped", reason,
                                             current, item=candidate)
                                outcomes.append(DispatchOutcome(
                                    candidate.event, "dropped", reason))

            # One outcome entry for every candidate that did not become the
            # selected/replaced item.  Decisions stay in input order.
            handled = set(handled_early)
            if selected is not None:
                handled.add(id(selected))
            if self._pending is not None:
                handled.add(id(self._pending.event))
            for index, event in enumerate(events):
                if id(event) in handled:
                    continue
                decision = decisions[index] if index < len(decisions) else None
                reason = _decision_reason(decision) or "not_selected"
                retry = _decision_retry_after(decision)
                if retry > 0:
                    status = "deferred"
                    self._counters["deferred"] += 1
                else:
                    status = "dropped"
                    self._counters["dropped"] += 1
                self._record("dispatcher", status, reason, current, event=event)
                outcomes.append(DispatchOutcome(event, status, reason))

            if self._pending is not None:
                delivered, delivery_outcomes, callback = self._deliver_locked(
                    current, simulated)
                outcomes.extend(delivery_outcomes)

        if callback is not None and self._on_delivered is not None:
            event, state_snapshot, metadata = callback
            try:
                self._on_delivered(event, state_snapshot, metadata)
            except Exception as exc:
                # Accepted by the host: a post-acceptance callback error must
                # never trigger a resend.
                self._warn("kards on_delivered failed: {}", exc)
        return outcomes

    def drain(self, *, now: float | None = None,
              limit: int | None = None,
              context_token: str | None = None) -> list[DispatchOutcome]:
        """Retry the single pending slot; production max is one per call."""
        state = None
        with self._lock:
            if self._state is not None:
                state = _copy_state(self._state)
            scenario = self._scenario
            session = self._session_id
            match = self._match_id
        return self.tick([], state, scenario=scenario, session_id=session,
                         match_id=match, context_token=context_token,
                         now=now) if state is not None else \
            self.tick([], MatchState(), scenario=scenario, session_id=session,
                      match_id=match, context_token=context_token, now=now)

    # ------------------------------------------------------------------ delivery

    def _warn(self, message: str, exc: Exception | None = None) -> None:
        if self._logger is not None:
            try:
                self._logger.warning(message, exc)
            except Exception:
                pass

    def _guard_allow(self, guard: Any, *, critical: bool, terminal: bool,
                     current: float) -> Any:
        if guard is None:
            return None
        try:
            return guard.allow(critical=critical, terminal=terminal, now=current)
        except TypeError:
            try:
                return guard.allow(now=current)
            except Exception:
                return None
        except Exception:
            return None

    def _guard_mark_output(self, guard: Any, *, critical: bool,
                           terminal: bool, current: float) -> None:
        if guard is None:
            return
        marker = getattr(guard, "mark_output", None)
        if not callable(marker):
            return
        try:
            marker(critical=critical, terminal=terminal, now=current)
        except TypeError:
            try:
                marker(now=current)
            except Exception:
                pass
        except Exception:
            pass

    def _guard_record_failure(self, guard: Any, current: float) -> None:
        if guard is None:
            return
        recorder = getattr(guard, "record_failure", None)
        if callable(recorder):
            try:
                recorder(now=current)
            except TypeError:
                try:
                    recorder()
                except Exception:
                    pass
            except Exception:
                pass

    def _quiet_reason(self, item: _Pending, current: float) -> tuple[str, bool]:
        """Return ``(reason, hard_drop)`` for the current quiet policy."""
        mode = self.config.interrupt_mode
        target = item.target_lanlan
        is_critical = item.critical
        is_terminal = item.terminal

        if mode == "critical_only" and not (is_critical or is_terminal):
            return "critical_only", True

        base = ""
        chat_at = self._last_chat_by_target.get(target)
        if chat_at is None:
            chat_at = self._last_chat_by_target.get("")
        if chat_at is None:
            chat_at = self._last_user_chat_at
        if (self.config.user_chat_quiet_window_seconds > 0 and chat_at
                and current - chat_at < self.config.user_chat_quiet_window_seconds):
            base = "user_chat_quiet_window"
        if not base:
            output_at = self._last_output_by_target.get(target)
            if output_at is None:
                output_at = self._last_output_by_target.get("")
            if output_at is None:
                output_at = self._last_output_at
            if (self.config.battle_output_quiet_window_seconds > 0 and output_at
                    and current - output_at
                    < self.config.battle_output_quiet_window_seconds):
                base = "battle_output_quiet_window"
            elif (self.config.battle_output_backpressure_seconds > 0 and output_at
                  and current - output_at
                  < self.config.battle_output_backpressure_seconds):
                base = "battle_output_backpressure"
        if not base:
            return "", False
        if mode == "no_interrupt":
            # Even a terminal event must not interrupt the user.
            return f"{base}_no_interrupt", False
        if is_critical or is_terminal:
            # Critical/terminal may interrupt only in allow_interrupt or
            # critical_only; ``no_interrupt`` already returned above.
            return "", False
        return base, False

    def _recommended_reply(self, event: GameEvent) -> str:
        details = event.details if isinstance(event.details, Mapping) else {}
        for key in ("recommended_reply", "reply", "short_reply", "tts_line"):
            value = details.get(key)
            if not isinstance(value, str):
                continue
            # A line that does not fit the one-sentence budget is not a
            # trustworthy short reply; omit it instead of truncating words.
            text = _clean_text(value, self.config.reply_max_chars + 1)
            if text and len(text) <= self.config.reply_max_chars:
                return text
        return ""

    def _make_prompt(self, item: _Pending, current: float) -> str:
        """Call the prompt builder with event context, tolerating old callers."""
        event = item.event
        attempts: tuple[dict[str, Any], ...] = (
            {"event": event, "now": current},
            {"event": event},
            {},
        )
        for extra in attempts:
            try:
                return str(self._prompt(event.summary, item.state, **extra))
            except TypeError:
                continue
        return str(self._prompt(event.summary, item.state))

    @staticmethod
    def _state_source_time(state: MatchState,
                           current: float) -> tuple[float | None, str, bool]:
        """Real source observation time for the queued state snapshot."""
        try:
            payload = situation_payload(state)
        except Exception:
            payload = {}
        source = payload.get("source") if isinstance(payload, Mapping) else None
        if isinstance(source, Mapping):
            for key in ("ui_observed_at", "observed_at"):
                try:
                    value = float(source.get(key))
                except (TypeError, ValueError):
                    continue
                if value > 0 and math.isfinite(value):
                    valid = value <= current + 0.001
                    return value, f"situation.source.{key}", valid
        try:
            value = float(getattr(state, "detected_at", 0.0) or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0 and math.isfinite(value):
            return value, "state.detected_at", value <= current + 0.001
        return None, "missing", False

    def _build_payload(self, item: _Pending, current: float
                       ) -> tuple[dict[str, Any], dict[str, Any]]:
        event = item.event
        level = event_level(event)
        edge = event_edge(event)
        details = event.details if isinstance(event.details, Mapping) else {}
        identity = str(details.get("identity") or event_identity(event))
        event_ts = float(getattr(event, "ts", 0.0) or 0.0)
        event_time_valid = event_ts > 0 and event_ts <= current + 0.001
        event_age = (max(0.0, current - event_ts)
                     if event_ts > 0 and event_ts <= current else 0.0)
        event_max_age = event_max_age_seconds(event, self._delivery_ttl)
        # The admission deadline is the original source deadline; retries never
        # refresh it.
        event_expires = item.expires_at
        # TTL is the *current* remaining delivery time, not the source max age.
        remaining = max(0.0, item.expires_at - current)
        priority = max(1, min(9, int(event.priority)))
        recommended = self._recommended_reply(event)
        plugin_owned = bool(self.config.plugin_owned_output_enabled and recommended)
        # The host contract is a single battle-event replace slot.  Only a
        # confirmed own-side critical preempts (interrupts) the host.
        replace_pending = True
        interrupt_pending = bool(item.critical)
        target = item.target_lanlan
        state_ts, state_origin, state_valid = self._state_source_time(
            item.state, current)
        state_age = (max(0.0, current - state_ts)
                     if state_ts is not None and state_valid else None)
        output_mode = ("plugin_owned_visible_chat" if plugin_owned
                       else "host_respond")

        callback_contract = {
            "version": "neko.callback.v1",
            "kind": "realtime_cue",
            "delivery": {
                "coalesce_key": self.config.coalesce_key,
                "replace_pending": replace_pending,
                "interrupt_pending": interrupt_pending,
                "priority": priority,
                # Source deadline, never the remaining TTL.
                "expires_at": round(event_expires, 3),
                # Source event max age spec.
                "max_age_seconds": round(event_max_age, 3),
                "ttl_remaining_seconds": round(remaining, 3),
            },
            "freshness": {
                "event_ts": round(event_ts, 3) if event_ts > 0 else None,
                "event_age_seconds": round(event_age, 3),
                "event_max_age_seconds": round(event_max_age, 3),
                "event_expires_at": round(event_expires, 3),
                "event_time_valid": bool(event_time_valid),
            },
            "target": {"lanlan": target},
        }
        metadata: dict[str, Any] = {
            "plugin": "kards",
            "event_id": identity[:160],
            "edge": edge,
            "level": level,
            "domain": "kards",
            "coalesce": self.config.coalesce_key,
            "coalesce_key": self.config.coalesce_key,
            "kind": event.kind,
            "delivery_intent": "commentary",
            "delivery_mode": output_mode,
            "priority": priority,
            "match_id": details.get("game", item.match_id),
            "session_id": item.session_id,
            "attempt": item.attempts + 1,
            "replace_pending": replace_pending,
            "interrupt_pending": interrupt_pending,
            "interrupt_battle_event": interrupt_pending,
            "replace_pending_intent": True,
            "live_reply_contract": "short_tts_line",
            "plugin_recommended_reply": recommended,
            # Legacy host alias for the recommended line.
            "recommended_reply": recommended,
            "plugin_owned_output": plugin_owned,
            "plugin_owned_output_enabled": plugin_owned,
            "reply_style_contract": "one_sentence_only",
            "domain_prompt_contract": "kards_commentary_v1",
            "response_module_hint": ("plugin_owned_blind_chat" if plugin_owned
                                     else "host_llm_commentary"),
            "dialogue_policy_owner": "plugin",
            "plugin_dialogue_policy": {
                "owner": "plugin",
                "pace": "plugin",
                "persona": "host",
                "reply_style": "short_commentary",
                "output_mode": output_mode,
            },
            "plugin_quiet_window_policy": {
                "owner": "plugin",
                "mode": self.config.interrupt_mode,
                "user_chat_quiet_window_seconds": round(
                    float(self.config.user_chat_quiet_window_seconds), 3),
                "battle_output_quiet_window_seconds": round(
                    float(self.config.battle_output_quiet_window_seconds), 3),
                "battle_output_backpressure_seconds": round(
                    float(self.config.battle_output_backpressure_seconds), 3),
                "critical_interrupts": interrupt_pending,
            },
            # Legacy host reader alias (connection-test metadata uses it too).
            "battle_reply_contract": "short_tts_line",
            "max_reply_chars": int(self.config.reply_max_chars),
            "host_callback_contract_version": "neko.callback.v1",
            "host_callback_contract": callback_contract,
            "event_ts": round(event_ts, 3) if event_ts > 0 else None,
            "event_time_valid": bool(event_time_valid),
            "event_age_seconds": round(event_age, 3),
            "event_max_age_seconds": round(event_max_age, 3),
            "event_expires_at": round(event_expires, 3),
            "state_observed_at": round(state_ts, 3) if state_ts is not None else None,
            "state_age_seconds": round(state_age, 3) if state_age is not None else None,
            "state_source_origin": state_origin,
            "state_time_valid": bool(state_valid),
            "state_source": "event_time_snapshot",
            "delivery_expires_at": round(item.expires_at, 3),
            # Current remaining delivery TTL (source max age is separate).
            "delivery_ttl_seconds": round(remaining, 3),
            "target_lanlan_present": bool(target),
            # Capability is genuinely unknown; do not pretend false/true.
            "host_capabilities": {
                "replace_pending": "unknown",
                "interrupt_pending": "unknown",
                "blind_visible_chat": "unknown",
            },
            "host_capabilities_source": "unknown",
            "plugin_source": self.config.source,
            "local_submission_only": True,
            "sdk_submission_semantics":
                "immediate_local_submission_not_host_consumption",
            "llm_generated": "unknown",
            "tts_played": "unknown",
        }
        if target:
            metadata["target_lanlan"] = target
        if plugin_owned:
            # Opt-in blind visible chat: the plugin already owns the validated
            # line, so the host must not generate/invoke the LLM for it.
            body = recommended
            ai_behavior = "blind"
            visibility = ["chat"]
        else:
            body = self._make_prompt(item, current)
            ai_behavior = "respond"
            visibility = []
        kwargs: dict[str, Any] = {
            "source": self.config.source,
            "visibility": visibility,
            "ai_behavior": ai_behavior,
            "parts": [{"type": "text", "text": body}],
            "priority": priority,
            "coalesce_key": self.config.coalesce_key,
            "metadata": metadata,
        }
        if target:
            kwargs["target_lanlan"] = target
        return kwargs, metadata

    def _read_downgrade_locked(self, item: _Pending, quiet_reason: str,
                               current: float) -> bool:
        """Quiet unit/result text becomes passive read context, not a chatting ack."""
        if self._read_publisher is None:
            return False
        if item.event.kind not in _READ_LIKE_EVENT_KINDS:
            return False
        if not self._read_breaker.allow(now=current):
            return False
        try:
            # The read channel carries the event-time state snapshot, not a
            # summary relabelled as a "current" observation.
            text = build_live_state_text(
                item.state, session_id=item.session_id)
        except Exception:
            text = _clean_text(item.event.summary, 160) or f"KARDS:{item.event.kind}"
        try:
            result = self._read_publisher.publish(
                item.state, text=text, target=item.target_lanlan,
                context_token=item.token, now=current)
        except Exception as exc:
            self._read_breaker.record_failure(now=current)
            self._warn("kards passive-context push failed: {}", exc)
            return False
        status = str(getattr(result, "status", "") or "").lower()
        if status == PublishStatus.SUBMITTED.value:
            self._read_breaker.record_success(now=current)
            self._counters["passive_context"] += 1
            self._record("dispatcher", "passive_context", quiet_reason,
                         current, item=item)
            return True
        if status == PublishStatus.COALESCED.value:
            # No host push happened; return the half-open probe.
            self._release_probe(self._read_breaker)
            self._counters["passive_context"] += 1
            self._record("dispatcher", "passive_context", quiet_reason,
                         current, item=item)
            return True
        if status == PublishStatus.SUPPRESSED.value:
            self._release_probe(self._read_breaker)
            return False
        if status == PublishStatus.FAILED.value:
            reason = _clean_text(getattr(result, "reason", ""), 64)
            if reason in {"legacy_unconfirmed", "unknown",
                          "unconfirmed_receipt"}:
                # Unconfirmed is terminal, not a transport failure.
                self._release_probe(self._read_breaker)
            elif reason in _STALE_SOURCE_REASONS:
                # A stale/invalid source is a local rejection: no host attempt,
                # no breaker failure.
                self._release_probe(self._read_breaker)
                self._counters["read_stale"] += 1
            else:
                self._read_breaker.record_failure(now=current)
            return False
        if bool(result):
            self._read_breaker.record_success(now=current)
            self._counters["passive_context"] += 1
            self._record("dispatcher", "passive_context", quiet_reason,
                         current, item=item)
            return True
        self._read_breaker.record_failure(now=current)
        return False

    def _deliver_locked(self, current: float, simulated: bool
                        ) -> tuple[bool, list[DispatchOutcome],
                                   tuple[GameEvent, MatchState, dict[str, Any]] | None]:
        item = self._pending
        if item is None:
            return False, [], None
        assert item is not None

        def drop(status: str, reason: str) -> tuple[bool, list[DispatchOutcome], None]:
            self._pending = None
            self._counters[status if status in self._counters else "dropped"] += 1
            self._record("dispatcher", status, reason, current, item=item)
            return False, [DispatchOutcome(item.event, status, reason,
                                           item.attempts)], None

        def defer(reason: str) -> tuple[bool, list[DispatchOutcome], None]:
            self._counters["deferred"] += 1
            self._record("dispatcher", "deferred", reason, current, item=item)
            return False, [DispatchOutcome(item.event, "deferred", reason,
                                           item.attempts)], None

        # 1. Re-check arbitration at delivery time: a burst admitted in the
        # same tick must still respect cooldown/semantic windows.
        decision = self._decide(item.event, item.state, item.scenario, current)
        if not _decision_allowed(decision):
            reason = _decision_reason(decision) or "rejected"
            retry = _decision_retry_after(decision)
            if reason in _DROP_REASONS or reason in _SUPPRESS_REASONS:
                if reason in _SUPPRESS_REASONS:
                    self._counters["suppressed"] += 1
                return drop("dropped", reason)
            if retry > 0 and current + retry >= item.expires_at:
                return drop("expired", "would_expire_before_retry")
            return defer(reason)

        # 2. Safety guard (pause/pacing) then transport breaker.
        guard = self._effective_guard()
        if guard is not None:
            verdict = self._guard_allow(
                guard, critical=item.critical, terminal=item.terminal,
                current=current)
            if not _decision_allowed(verdict):
                reason = _decision_reason(verdict) or "safety_paused"
                retry = _decision_retry_after(verdict)
                if retry > 0 and current + retry >= item.expires_at:
                    return drop("expired", "would_expire_before_retry")
                return defer(reason)
        if not simulated and not self._breaker.allow(now=current):
            return defer("safety_circuit_open")

        # 3. Quiet/backpressure policy.
        quiet_reason, hard_drop = self._quiet_reason(item, current)
        if quiet_reason:
            if hard_drop:
                # No respond host attempt happens here.
                self._release_probe(self._breaker)
                self._counters["quiet_window"] += 1
                return drop("dropped", quiet_reason)
            if self._read_downgrade_locked(item, quiet_reason, current):
                # The output went to the read channel, not respond.
                self._release_probe(self._breaker)
                self._pending = None
                return False, [DispatchOutcome(item.event, "passive_context",
                                               quiet_reason, item.attempts)], None
            # No host attempt happens here, so give back a reserved half-open
            # breaker probe if the transport breaker handed one out.
            self._release_probe(self._breaker)
            # Keep one waiting candidate, but only if it can still be valid.
            self._counters["quiet_window"] += 1
            self._counters["waiting"] += 1
            self._record("dispatcher", "quiet_window", quiet_reason,
                         current, item=item)
            return False, [DispatchOutcome(item.event, "quiet_window",
                                           quiet_reason, item.attempts)], None

        # 4. Build the bounded, privacy-safe payload and push through the gate.
        try:
            kwargs, metadata = self._build_payload(item, current)
        except Exception as exc:
            # Local construction failed before any host call: release the
            # transport probe so a half-open breaker is not stuck.
            self._release_probe(self._breaker)
            self._guard_record_failure(self._effective_guard(), current)
            return drop("failed", f"prompt_error:{type(exc).__name__}")

        result: GateResult = self._gate.push(
            "respond",
            critical=item.critical,
            terminal=item.terminal,
            # Strictly the token bound at admission; never the current one.
            context_token=item.token,
            simulate=bool(simulated),
            now=current,
            **kwargs,
        )

        if result.simulated:
            # Full decision chain ran; never touch the real arbiter/guard.
            self._release_probe(self._breaker)
            if not self._sim_arbiter_is_real:
                try:
                    self._effective_arbiter().mark_submitted(item.event, now=current)
                except Exception:
                    pass
            if not self._sim_guard_is_real:
                self._guard_mark_output(self._effective_guard(),
                                        critical=item.critical,
                                        terminal=item.terminal, current=current)
            self._counters["simulated"] += 1
            self._record_simulation(item, "simulated", REASON_DRY_RUN, current)
            self._record("dispatcher", "simulated", REASON_DRY_RUN,
                         current, item=item)
            self._pending = None
            return False, [DispatchOutcome(item.event, "simulated",
                                           REASON_DRY_RUN, item.attempts)], None

        if result.accepted:
            try:
                self._arbiter.mark_submitted(item.event, now=current)
            except Exception:
                pass
            self._guard_mark_output(self._guard, critical=item.critical,
                                    terminal=item.terminal, current=current)
            self._breaker.record_success(now=current)
            self._last_output_at = current
            self._last_output_by_target[item.target_lanlan or ""] = current
            self._counters["submitted"] += 1
            self._record("dispatcher", "submitted", "push_confirmed",
                         current, item=item)
            self._pending = None
            callback = (item.event, _copy_state(item.state), metadata)
            return True, [DispatchOutcome(item.event, "submitted",
                                          "push_confirmed",
                                          item.attempts + 1)], callback

        if result.status == "legacy_unconfirmed" or result.status == "unknown":
            # No host confirmation: stop here to avoid duplicate output, but
            # never mark the event as submitted or call on_delivered.
            self._pending = None
            self._counters["unconfirmed"] += 1
            self._release_probe(self._breaker)
            status = "unconfirmed" if result.status == "legacy_unconfirmed" else "unknown"
            self._record("dispatcher", status, result.status, current, item=item)
            return False, [DispatchOutcome(item.event, status, result.status,
                                           item.attempts + 1)], None

        if result.reason in _SUPPRESS_REASONS:
            self._pending = None
            self._counters["suppressed"] += 1
            self._release_probe(self._breaker)
            self._record("dispatcher", "suppressed", result.reason,
                         current, item=item)
            return False, [DispatchOutcome(item.event, "suppressed",
                                           result.reason, item.attempts)], None

        if result.reason == REASON_TRANSPORT_UNAVAILABLE:
            # Misconfigured gate (no host callable): no host attempt happened.
            self._pending = None
            self._release_probe(self._breaker)
            self._guard_record_failure(self._effective_guard(), current)
            self._counters["failed"] += 1
            self._record("dispatcher", "failed", result.reason,
                         current, item=item)
            return False, [DispatchOutcome(item.event, "failed",
                                           result.reason, item.attempts)], None

        if result.reason in {REASON_STALE_EPOCH, REASON_STALE_CONTEXT}:
            # The tick that prepared this item belongs to an old epoch.
            self._pending = None
            self._counters["dropped"] += 1
            self._release_probe(self._breaker)
            self._record("dispatcher", "stale_epoch", result.reason,
                         current, item=item)
            return False, [DispatchOutcome(item.event, "stale_epoch",
                                           result.reason, item.attempts)], None

        # Transport / explicit rejection: retry only while the original TTL
        # still has room; attempts never extend the expiry.
        self._counter_failure(item, current)
        item.attempts += 1
        if item.attempts < self.config.max_attempts and item.expires_at > current:
            self._counters["retried"] += 1
            self._record("dispatcher", "retry", result.reason, current, item=item)
            return False, [DispatchOutcome(item.event, "retry",
                                           result.reason, item.attempts)], None
        self._pending = None
        self._counters["failed"] += 1
        self._record("dispatcher", "failed", result.reason, current, item=item)
        return False, [DispatchOutcome(item.event, "failed", result.reason,
                                       item.attempts)], None

    def _counter_failure(self, item: _Pending, current: float) -> None:
        self._guard_record_failure(self._effective_guard(), current)
        try:
            self._breaker.record_failure(now=current)
        except Exception:
            pass

    @staticmethod
    def _release_probe(breaker: SafetyCircuitBreaker) -> None:
        """Return an unused half-open reservation without recording an outcome."""
        release = getattr(breaker, "release_probe", None)
        if callable(release):
            try:
                release()
            except Exception:
                pass

    # ------------------------------------------------------------------ read

    def push_read(self, state: MatchState, *, text: str, target: str = "",
                  target_lanlan: str = "", session_id: str = "",
                  context_token: str | None = None,
                  source_observed_at: float | None = None,
                  now: float | None = None) -> bool:
        """Push passive live-state text without consuming respond pacing.

        The read channel has its own breaker; gate consent/dry/stop still apply
        inside the publisher/gate.
        """
        current = self._now() if now is None else float(now)
        route = (clean_target_lanlan(target_lanlan)
                 or clean_target_lanlan(target))
        with self._lock:
            if self._read_publisher is None:
                self._record("read", "failed", "read_channel_unavailable", current)
                return False
            if not self._read_breaker.allow(now=current):
                self._record("read", "circuit_open", "safety_open", current)
                return False
            try:
                publish_kwargs: dict[str, Any] = {
                    "text": text, "target": route, "session_id": session_id,
                    "context_token": context_token, "now": current,
                }
                if source_observed_at is not None:
                    publish_kwargs["source_observed_at"] = source_observed_at
                result = self._read_publisher.publish(state, **publish_kwargs)
            except Exception as exc:
                self._read_breaker.record_failure(now=current)
                self._counters["read_failed"] += 1
                self._record("read", "failed", f"read_push_exception:{type(exc).__name__}",
                             current)
                return False
            status = str(getattr(result, "status", "") or "").lower()
            if status == PublishStatus.SUBMITTED.value:
                self._read_breaker.record_success(now=current)
                self._counters["read_submitted"] += 1
                self._record("read", "submitted", "read_push_confirmed", current)
                return True
            if status == PublishStatus.COALESCED.value:
                # No host push happened; do not keep a half-open probe.
                self._release_probe(self._read_breaker)
                self._counters["read_coalesced"] += 1
                self._record("read", "coalesced", "read_state_coalesced", current)
                return True
            if status == PublishStatus.SUPPRESSED.value:
                self._release_probe(self._read_breaker)
                self._counters["read_suppressed"] += 1
                self._record("read", "suppressed",
                             _clean_text(getattr(result, "reason", ""), 64)
                             or "read_suppressed", current)
                return False
            if status == PublishStatus.FAILED.value:
                reason = _clean_text(getattr(result, "reason", ""), 64)
                if reason in {"legacy_unconfirmed", "unknown",
                              "unconfirmed_receipt"}:
                    # Unconfirmed is terminal and must not trip the read
                    # breaker as if the transport had failed.
                    self._release_probe(self._read_breaker)
                    self._counters["read_unconfirmed"] += 1
                    self._record("read", "unconfirmed", reason, current)
                    return False
                if reason in _STALE_SOURCE_REASONS:
                    # Local source rejection: no host attempt happened.
                    self._release_probe(self._read_breaker)
                    self._counters["read_stale"] += 1
                    self._record("read", "stale", reason, current)
                    return False
                self._read_breaker.record_failure(now=current)
                self._counters["read_failed"] += 1
                self._record("read", "failed", reason
                             or "read_push_rejected", current)
                return False
            if bool(result):
                self._read_breaker.record_success(now=current)
                self._counters["read_submitted"] += 1
                self._record("read", "submitted", "legacy_read_accepted", current)
                return True
            self._read_breaker.record_failure(now=current)
            self._counters["read_failed"] += 1
            self._record("read", "failed", "read_push_rejected", current)
            return False

    def purge(self, reason: str = "purged", *,
              now: float | None = None) -> list[DispatchOutcome]:
        """Drop the single pending event without attempting delivery."""
        with self._lock:
            current = self._now() if now is None else float(now)
            clean_reason = _clean_text(reason, 120) or "purged"
            return self._purge_locked(clean_reason, current)


__all__ = [
    "DeliveryDispatcher", "DispatchOutcome", "DispatcherConfig", "SubmitResult",
]
