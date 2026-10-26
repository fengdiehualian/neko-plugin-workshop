"""Delivery safety primitives.

Two separate layers live here:

``SafetyCircuitBreaker``
    The existing push-outcome breaker (closed/open/half-open probes).  Its
    public contract is unchanged; :meth:`release_probe` was added so a
    coalesced/cancelled probe slot can be returned without recording a
    success or failure.

``OutputSafetyGuard``
    Output pacing and pause state for the arbitrated delivery path.  It is
    deliberately *not* a second breaker: there is no half-open state and no
    recovery timer.  Manual and automatic pauses are independent flags, and
    only an explicit ``resume()`` (or ``reset()``) clears them.

Terminal events bypass output cadence (minimum interval and critical
cooldown) but never a pause.  Terminal once-only semantics belong to the
arbiter/session, not to this guard.

``policy.safety`` imports :class:`Decision` from ``delivery.arbiter``.  The
arbiter never imports this module, so there is no import cycle.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Mapping

from ..delivery.arbiter import Decision

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"
CIRCUIT_STATES: frozenset[str] = frozenset({CLOSED, OPEN, HALF_OPEN})

# OutputSafetyGuard reasons.
ALLOWED = "allowed"
MANUAL_PAUSED = "manual_paused"
AUTO_PAUSED = "auto_paused"
OUTPUT_INTERVAL = "output_interval"
CRITICAL_COOLDOWN = "critical_cooldown"


@dataclass(frozen=True)
class SafetyConfig:
    failure_threshold: int = 3
    recovery_seconds: float = 30.0
    half_open_max_probes: int = 1

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if self.recovery_seconds < 0:
            raise ValueError("recovery_seconds must be >= 0")
        if self.half_open_max_probes < 1:
            raise ValueError("half_open_max_probes must be >= 1")


@dataclass(frozen=True)
class SafetySnapshot:
    state: str
    failures: int
    opened_at: float | None = None
    recovery_at: float | None = None
    probes: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "failures": self.failures,
            "opened_at": self.opened_at,
            "recovery_at": self.recovery_at,
            "probes": self.probes,
        }


class SafetyCircuitBreaker:
    """Thread-safe failure breaker with time-injectable state transitions."""

    def __init__(self, config: SafetyConfig | None = None, *,
                 now_fn=time.monotonic) -> None:
        self.config = config or SafetyConfig()
        self._now = now_fn
        self._lock = threading.RLock()
        self._state = CLOSED
        self._failures = 0
        self._opened_at: float | None = None
        self._probes = 0

    def _current(self, now: float | None) -> float:
        return self._now() if now is None else float(now)

    def _refresh(self, current: float) -> None:
        if (self._state == OPEN and self._opened_at is not None
                and current - self._opened_at >= self.config.recovery_seconds):
            self._state = HALF_OPEN
            self._probes = 0

    def allow(self, *, now: float | None = None) -> bool:
        """Return True when a delivery attempt may proceed.

        In ``half_open`` this reserves one probe slot; the caller must report
        the outcome via :meth:`record_success`/:meth:`record_failure` or return
        an unused reservation with :meth:`release_probe`.
        """
        with self._lock:
            current = self._current(now)
            self._refresh(current)
            if self._state == CLOSED:
                return True
            if self._state == HALF_OPEN:
                if self._probes >= self.config.half_open_max_probes:
                    return False
                self._probes += 1
                return True
            return False

    def release_probe(self) -> None:
        """Return an unused half-open reservation.

        Used when a reserved delivery is cancelled/coalesced before an outcome
        exists.  It never changes the breaker state or failure counters, and
        it cannot create extra probes beyond ``half_open_max_probes``.
        """
        with self._lock:
            if self._state == HALF_OPEN and self._probes > 0:
                self._probes -= 1

    def record_success(self, *, now: float | None = None) -> None:
        with self._lock:
            # Only a probe result (or a closed-state success) may reset the
            # breaker; successful traffic after an explicit trip should not
            # silently bypass the recovery window.
            if self._state == CLOSED or self._state == HALF_OPEN:
                self._state = CLOSED
                self._failures = 0
                self._opened_at = None
                self._probes = 0

    def record_failure(self, *, now: float | None = None) -> None:
        with self._lock:
            current = self._current(now)
            self._refresh(current)
            if self._state == OPEN:
                # Keep the original recovery deadline stable.
                return
            if self._state == HALF_OPEN:
                self._trip(current)
                return
            self._failures += 1
            if self._failures >= self.config.failure_threshold:
                self._trip(current)

    def _trip(self, current: float) -> None:
        self._state = OPEN
        self._opened_at = current
        self._probes = 0

    def reset(self) -> None:
        with self._lock:
            self._state = CLOSED
            self._failures = 0
            self._opened_at = None
            self._probes = 0

    def state(self, *, now: float | None = None) -> str:
        with self._lock:
            self._refresh(self._current(now))
            return self._state

    def snapshot(self, *, now: float | None = None) -> SafetySnapshot:
        with self._lock:
            current = self._current(now)
            self._refresh(current)
            recovery_at = (None if self._opened_at is None
                           else self._opened_at + self.config.recovery_seconds)
            return SafetySnapshot(
                state=self._state,
                failures=self._failures,
                opened_at=self._opened_at,
                recovery_at=recovery_at,
                probes=self._probes,
            )


@dataclass(frozen=True)
class OutputSafetyConfig:
    """Pacing and rolling-failure settings for :class:`OutputSafetyGuard`."""

    failure_threshold: int = 5
    failure_window_seconds: float = 60.0
    min_output_interval_seconds: float = 12.0
    critical_cooldown_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        for name in ("failure_window_seconds", "min_output_interval_seconds",
                     "critical_cooldown_seconds"):
            value = getattr(self, name)
            if not math.isfinite(float(value)) or float(value) < 0:
                raise ValueError(f"{name} must be a finite number >= 0")


def _finite(value: Any, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    return number if math.isfinite(number) else float(fallback)


def _json_time(value: float | None) -> float | None:
    if value is None:
        return None
    number = _finite(value, 0.0)
    return round(number, 3)


class OutputSafetyGuard:
    """Pause state plus a rolling failure window and output clocks.

    There is intentionally no half-open probe state here.  A rolling failure
    burst sets ``auto_paused``; only :meth:`resume` clears it (there is no
    automatic recovery).  ``reset`` is the unconditional lifecycle reset.
    """

    def __init__(self, config: OutputSafetyConfig | None = None, *,
                 now_fn=time.time) -> None:
        self.config = config or OutputSafetyConfig()
        self._now = now_fn
        self._lock = threading.RLock()
        self._manual_paused = False
        self._manual_reason = ""
        self._auto_paused = False
        self._auto_reason = ""
        self._failures: deque[float] = deque(
            maxlen=self.config.failure_threshold)
        self._failure_window_started_at: float | None = None
        self._last_output_at: float | None = None
        self._last_critical_at: float | None = None
        self._terminal_emitted = False

    # ------------------------------------------------------------------ 时钟

    def _current(self, now: float | None) -> float:
        return _finite(self._now() if now is None else now, 0.0)

    def _paused(self) -> bool:
        return self._manual_paused or self._auto_paused

    def _prune_failures(self, current: float) -> None:
        window = max(0.0, _finite(self.config.failure_window_seconds, 0.0))
        if window <= 0.0:
            self._failures.clear()
        else:
            cutoff = current - window
            while self._failures and self._failures[0] < cutoff:
                self._failures.popleft()
        self._failure_window_started_at = (
            self._failures[0] if self._failures else None)

    # ------------------------------------------------------------------ 暂停

    def pause(self, reason: str = "", *, auto: bool = False,
              now: float | None = None) -> None:
        """Set a pause flag.  Pauses never expire on their own.

        ``auto=False`` sets the manual pause; ``auto=True`` sets the rolling
        failure pause.  The two flags are independent; clearing one never
        clears the other.
        """
        with self._lock:
            clean = " ".join(str(reason or "").split())[:120]
            if auto:
                self._auto_paused = True
                self._auto_reason = clean or "auto_paused"
            else:
                self._manual_paused = True
                self._manual_reason = clean

    def resume(self, *, auto: bool = False, now: float | None = None) -> None:
        """Resume one pause channel and clear the rolling failure window.

        ``auto=True`` clears the automatic failure pause; the default clears
        the manual pause.  The flags are independent, so a failure pause needs
        an explicit ``resume(auto=True)`` (or ``reset``).
        """
        with self._lock:
            if auto:
                self._auto_paused = False
                self._auto_reason = ""
            else:
                self._manual_paused = False
                self._manual_reason = ""
            self._failures.clear()
            self._failure_window_started_at = None

    def reset(self) -> None:
        """Clear pauses, failures, output clocks and terminal diagnostics."""
        with self._lock:
            self._manual_paused = False
            self._manual_reason = ""
            self._auto_paused = False
            self._auto_reason = ""
            self._failures.clear()
            self._failure_window_started_at = None
            self._last_output_at = None
            self._last_critical_at = None
            self._terminal_emitted = False

    # ------------------------------------------------------------------ 门控

    def allow(self, *, critical: bool = False, terminal: bool = False,
              now: float | None = None) -> Decision:
        """Judge one output attempt.  This does not reserve a slot."""
        with self._lock:
            current = self._current(now)
            if self._manual_paused:
                return Decision(False, MANUAL_PAUSED)
            if self._auto_paused:
                return Decision(False, AUTO_PAUSED)

            if terminal:
                # Terminal bypasses cadence, never a pause or a hard gate.
                return Decision(True, ALLOWED)

            if critical:
                remaining = self._critical_remaining(current)
                if remaining > 0.0:
                    return Decision(False, CRITICAL_COOLDOWN,
                                    round(remaining, 3))
                return Decision(True, ALLOWED)

            remaining = self._interval_remaining(current)
            if remaining > 0.0:
                return Decision(False, OUTPUT_INTERVAL, round(remaining, 3))
            return Decision(True, ALLOWED)

    def remaining(self, *, now: float | None = None) -> float | None:
        """Seconds until the next normal output; ``None`` while paused."""
        with self._lock:
            if self._paused():
                return None
            return round(self._interval_remaining(self._current(now)), 3)

    # ------------------------------------------------------------------ 记账

    def mark_output(self, *, critical: bool = False, terminal: bool = False,
                    now: float | None = None) -> None:
        """Record an SDK-locally accepted output.  Clears failures, never a pause."""
        with self._lock:
            current = self._current(now)
            self._last_output_at = current
            if critical:
                self._last_critical_at = current
            if terminal:
                self._terminal_emitted = True
            # An accepted local output is a success signal.  It clears the
            # rolling failure window but must not clear pause flags.
            self._failures.clear()
            self._failure_window_started_at = None

    def record_success(self, *, now: float | None = None) -> None:
        """Clear the rolling failure window without touching pause flags."""
        with self._lock:
            self._failures.clear()
            self._failure_window_started_at = None

    def record_failure(self, *, now: float | None = None) -> None:
        """Add a failure to the rolling window; trip auto-pause at threshold."""
        with self._lock:
            if self._auto_paused:
                return
            current = self._current(now)
            self._prune_failures(current)
            self._failures.append(current)
            self._failure_window_started_at = self._failures[0]
            if len(self._failures) >= self.config.failure_threshold:
                self._auto_paused = True
                self._auto_reason = "failure_threshold"

    # ------------------------------------------------------------------ 诊断

    def snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        """JSON-safe diagnostic state; paused remaining is ``None``."""
        with self._lock:
            current = self._current(now)
            self._prune_failures(current)
            paused = self._paused()
            remaining = None if paused else round(
                self._interval_remaining(current), 3)
            if paused:
                reason = (self._manual_reason if self._manual_paused
                          else self._auto_reason)
            else:
                reason = ""
            return {
                "schema_version": "kards_output_safety_v1",
                "paused": paused,
                "pause_reason": reason,
                "manual_paused": self._manual_paused,
                "auto_paused": self._auto_paused,
                "state": "paused" if paused else CLOSED,
                "failures": len(self._failures),
                "failure_threshold": int(self.config.failure_threshold),
                "failure_window_seconds": _finite(
                    self.config.failure_window_seconds, 0.0),
                "failure_window_started_at": _json_time(
                    self._failure_window_started_at),
                "remaining": remaining,
                "min_output_interval_seconds": _finite(
                    self.config.min_output_interval_seconds, 0.0),
                "critical_cooldown_seconds": _finite(
                    self.config.critical_cooldown_seconds, 0.0),
                "last_output_at": _json_time(self._last_output_at),
                "last_critical_at": _json_time(self._last_critical_at),
                "terminal_emitted": self._terminal_emitted,
            }

    # ------------------------------------------------------------------ 克隆

    def output_clock_checkpoint(self) -> dict[str, Any]:
        """JSON-safe checkpoint for a dry-run simulation clone.

        The name is kept for the output-clock API; the checkpoint also carries
        the pause/failure state required by the frozen integration contract.
        """
        with self._lock:
            return {
                "schema_version": "kards_output_clock_v1",
                "last_output_at": _json_time(self._last_output_at),
                "last_critical_at": _json_time(self._last_critical_at),
                "terminal_emitted": self._terminal_emitted,
                "failure_count": len(self._failures),
                "failure_timestamps": [
                    _json_time(ts) for ts in self._failures],
                "failure_window_started_at": _json_time(
                    self._failure_window_started_at),
                "manual_paused": self._manual_paused,
                "auto_paused": self._auto_paused,
                "pause_reason": (self._manual_reason if self._manual_paused
                                 else self._auto_reason),
            }

    def restore(self, checkpoint: Mapping[str, Any] | None) -> None:
        """Restore a full checkpoint (clocks, pause flags, failure window)."""
        with self._lock:
            if not isinstance(checkpoint, Mapping):
                self.restore_output_clock(None)
                self._manual_paused = False
                self._manual_reason = ""
                self._auto_paused = False
                self._auto_reason = ""
                self._failures.clear()
                self._failure_window_started_at = None
                return

            self._last_output_at = self._read_time(
                checkpoint.get("last_output_at"))
            self._last_critical_at = self._read_time(
                checkpoint.get("last_critical_at"))
            if "terminal_emitted" in checkpoint:
                self._terminal_emitted = bool(checkpoint.get("terminal_emitted"))

            self._manual_paused = bool(checkpoint.get("manual_paused"))
            self._auto_paused = bool(checkpoint.get("auto_paused"))
            reason = " ".join(str(checkpoint.get("pause_reason") or "").split())[:120]
            self._manual_reason = reason if self._manual_paused else ""
            self._auto_reason = (reason or "auto_paused") if self._auto_paused else ""

            try:
                count = int(checkpoint.get("failure_count") or 0)
            except (TypeError, ValueError):
                count = 0
            threshold = max(1, int(self.config.failure_threshold))
            count = max(0, min(count, threshold))

            exact = checkpoint.get("failure_timestamps")
            timestamps: list[float] = []
            if isinstance(exact, (list, tuple)):
                for value in exact:
                    parsed = self._read_time(value)
                    if parsed is not None:
                        timestamps.append(parsed)
                # Keep chronological order and the bounded tail.  The
                # timestamps are the authoritative distribution; count-only
                # checkpoints cannot reproduce window pruning.
                timestamps = sorted(timestamps)[-threshold:]

            self._failures.clear()
            if timestamps:
                self._failures.extend(timestamps)
                self._failure_window_started_at = timestamps[0]
            elif count > 0:
                # Legacy count-only checkpoint (or a list with no usable
                # timestamps): restore the bounded count conservatively at
                # the current clock so a still-valid window is not
                # prematurely discarded.
                anchor = self._current(None)
                self._failures.extend([anchor] * count)
                self._failure_window_started_at = anchor
            else:
                self._failure_window_started_at = None

    def restore_output_clock(self, checkpoint: Mapping[str, Any] | None) -> None:
        """Restore only output clocks; pauses and failures are untouched."""
        with self._lock:
            if not isinstance(checkpoint, Mapping):
                self._last_output_at = None
                self._last_critical_at = None
                self._terminal_emitted = False
                return
            self._last_output_at = self._read_time(
                checkpoint.get("last_output_at"))
            self._last_critical_at = self._read_time(
                checkpoint.get("last_critical_at"))
            if "terminal_emitted" in checkpoint:
                self._terminal_emitted = bool(checkpoint.get("terminal_emitted"))

    def fork(self) -> "OutputSafetyGuard":
        """Return an independent copy (config, pauses, failures and clocks)."""
        clone = OutputSafetyGuard(self.config, now_fn=self._now)
        with self._lock:
            clone._manual_paused = self._manual_paused
            clone._manual_reason = self._manual_reason
            clone._auto_paused = self._auto_paused
            clone._auto_reason = self._auto_reason
            clone._failures = deque(self._failures,
                                    maxlen=self.config.failure_threshold)
            clone._failure_window_started_at = self._failure_window_started_at
            clone._last_output_at = self._last_output_at
            clone._last_critical_at = self._last_critical_at
            clone._terminal_emitted = self._terminal_emitted
        return clone

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _read_time(value: Any) -> float | None:
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number >= 0.0 else None

    def _interval_remaining(self, current: float) -> float:
        if self._last_output_at is None:
            return 0.0
        interval = max(0.0, _finite(
            self.config.min_output_interval_seconds, 0.0))
        return max(0.0, interval - (current - self._last_output_at))

    def _critical_remaining(self, current: float) -> float:
        if self._last_critical_at is None:
            return 0.0
        cooldown = max(0.0, _finite(
            self.config.critical_cooldown_seconds, 0.0))
        return max(0.0, cooldown - (current - self._last_critical_at))


__all__ = [
    "AUTO_PAUSED",
    "CIRCUIT_STATES",
    "CLOSED",
    "CRITICAL_COOLDOWN",
    "HALF_OPEN",
    "MANUAL_PAUSED",
    "OPEN",
    "OUTPUT_INTERVAL",
    "OutputSafetyConfig",
    "OutputSafetyGuard",
    "SafetyCircuitBreaker",
    "SafetyConfig",
    "SafetySnapshot",
]
