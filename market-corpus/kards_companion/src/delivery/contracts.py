"""Unified final outlet for every host push.

All delivery channels (``read`` / ``respond`` / ``context`` / ``expire`` /
``test``) must pass through one :class:`HostPushGate`.  The gate is the only
place that calls the host push callable, so it is also the only place that can
guarantee:

* ``llm_data_consent=False`` produces **zero** real pushes, including
  tombstones (``expire``) and connection tests;
* ``dry_run`` runs the complete arbitration/safety chain but the final push is
  a no-op and never updates real delivery bookkeeping;
* lifecycle stop is ordered: a push already inside the host call may finish,
  but new pushes (including terminal events) are refused;
* stale ticks cannot push after a stop/reopen cycle because every push carries
  an epoch/context token that is checked inside the same commit lock;
* receipts are normalized honestly: an explicit ``submitted/ok == False`` is a
  rejection, ``None`` (legacy SDK without a receipt) is ``legacy_unconfirmed``
  and never reported as host-accepted, and an unknown/missing receipt shape is
  ``unknown`` and must not be retried automatically.

Locking contract
----------------
``push`` holds the gate's commit ``RLock`` while the host callable runs, so a
push that already entered the host can complete.  ``begin_stop`` acquires the
same lock and therefore waits for that in-flight call.

The plugin lifecycle lock must **never** be held while calling this gate (or
waiting for ``begin_stop``): the documented stop sequence is
``lock: close admission -> unlock -> begin_stop() -> expire outside lock ->
finish_stop() -> lock: reset``.  Holding the lifecycle lock across a gate call
can deadlock with a push callback that needs the lifecycle lock.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Callable

GATE_INTENTS: tuple[str, ...] = ("read", "respond", "context", "expire", "test")

STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
STATUS_LEGACY_UNCONFIRMED = "legacy_unconfirmed"
STATUS_UNKNOWN = "unknown"
# Simulation is not a host result; it is reported separately from receipts.
STATUS_SIMULATED = "simulated"

PHASE_RUNNING = "running"
PHASE_STOPPING = "stopping"
PHASE_STOPPED = "stopped"

# Reasons used across the gate and dispatcher; kept stable for diagnostics.
REASON_CONSENT_REQUIRED = "consent_required"
REASON_DRY_RUN = "dry_run"
REASON_SIMULATED = "simulated"
REASON_STOPPED = "stopped"
REASON_STOPPING = "stopping"
REASON_PAUSED = "paused"
REASON_STALE_EPOCH = "stale_epoch"
REASON_STALE_CONTEXT = "stale_context"
REASON_INVALID_INTENT = "invalid_intent"
REASON_TEST_NOT_AUTHORIZED = "test_not_authorized"
REASON_TEST_AUTHORIZATION_USED = "test_authorization_used"
REASON_TRANSPORT = "transport"
REASON_HOST_REJECTED = "host_rejected"
REASON_LEGACY_NO_RECEIPT = "legacy_no_receipt"
REASON_RECEIPT_UNKNOWN = "receipt_unknown"
REASON_BACKPRESSURE = "backpressure"
REASON_TRANSPORT_UNAVAILABLE = "transport_unavailable"


def _clean_reason(value: Any, default: str = "") -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:120] or default


@dataclass(frozen=True)
class HostPushConfig:
    """Gate policy.  Credentials/consent are hard gates, never advisory."""

    dry_run: bool = False
    consent: bool = True
    simulated: bool = False
    max_records: int = 128
    authorized_real_test: bool = False

    def __post_init__(self) -> None:
        if self.max_records < 0:
            raise ValueError("max_records must be >= 0")


@dataclass(frozen=True)
class GateResult:
    """Outcome of one final-outlet attempt.

    ``allowed`` says the gate admitted the intent (a dry-run simulation is
    admitted).  ``submitted`` is true only for an explicit host acceptance.
    ``simulated`` says no host call happened.  ``status`` describes the receipt
    class: ``accepted`` / ``rejected`` / ``legacy_unconfirmed`` / ``unknown``,
    or ``simulated`` for a no-op simulation.
    """

    allowed: bool
    submitted: bool
    simulated: bool
    receipt: Any
    status: str
    reason: str = ""
    intent: str = ""
    epoch: int = 0

    @property
    def accepted(self) -> bool:
        return self.status == STATUS_ACCEPTED

    @property
    def legacy_unconfirmed(self) -> bool:
        return self.status == STATUS_LEGACY_UNCONFIRMED

    @property
    def unknown(self) -> bool:
        return self.status == STATUS_UNKNOWN

    @property
    def rejected(self) -> bool:
        return self.status == STATUS_REJECTED

    @property
    def confirmed(self) -> bool:
        return self.status == STATUS_ACCEPTED

    @property
    def retryable(self) -> bool:
        """Only explicit transport rejections are worth an automatic retry.

        ``legacy_unconfirmed`` and ``unknown`` are deliberately terminal to
        avoid duplicate host output; ``stale_*``/``stopped``/consent are not
        transport failures.
        """
        return self.status == STATUS_REJECTED and self.reason in {
            REASON_TRANSPORT, REASON_BACKPRESSURE, REASON_TRANSPORT_UNAVAILABLE}

    def __bool__(self) -> bool:
        return self.submitted

    def to_dict(self) -> dict[str, object]:
        """Bounded JSON-safe diagnostic form; never includes kwargs/prompt."""
        return {
            "allowed": self.allowed,
            "submitted": self.submitted,
            "simulated": self.simulated,
            "status": self.status,
            "reason": self.reason,
            "intent": self.intent,
            "epoch": self.epoch,
            "receipt_type": type(self.receipt).__name__,
        }


def _explicit_bool(value: Any) -> bool | None:
    """Only genuinely boolean-like values count as an explicit receipt."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    return None


def normalize_receipt(receipt: Any) -> tuple[str, str]:
    """Return ``(status, reason)`` for a host receipt.

    Rules:

    * an explicit false ``submitted`` / ``ok`` (or ``is_ok()`` false) wins;
    * an explicit true value is an acceptance;
    * ``None`` is the legacy SDK "no receipt" case and is **not** accepted;
    * ``{}`` / unknown objects carry no explicit truth and are ``unknown``.
    """
    if receipt is True:
        return STATUS_ACCEPTED, "sdk_local_submitted"
    if receipt is False:
        return STATUS_REJECTED, REASON_HOST_REJECTED
    values: dict[str, bool] = {}
    is_ok_value: bool | None = None
    if isinstance(receipt, Mapping):
        for name in ("submitted", "ok"):
            if name in receipt:
                flag = _explicit_bool(receipt.get(name))
                if flag is not None:
                    values[name] = flag
    else:
        for name in ("submitted", "ok"):
            if hasattr(receipt, name):
                try:
                    flag = _explicit_bool(getattr(receipt, name))
                except Exception:
                    flag = None
                if flag is not None:
                    values[name] = flag
        is_ok = getattr(receipt, "is_ok", None)
        if callable(is_ok):
            try:
                is_ok_value = _explicit_bool(is_ok())
            except Exception:
                is_ok_value = None

    if any(flag is False for flag in values.values()) or is_ok_value is False:
        return STATUS_REJECTED, REASON_HOST_REJECTED
    if any(flag is True for flag in values.values()) or is_ok_value is True:
        return STATUS_ACCEPTED, "sdk_local_submitted"
    if receipt is None:
        return STATUS_LEGACY_UNCONFIRMED, REASON_LEGACY_NO_RECEIPT
    return STATUS_UNKNOWN, REASON_RECEIPT_UNKNOWN


class HostPushGate:
    """Thread-safe, epoch-guarded single host push outlet."""

    def __init__(self, push_message: Callable[..., Any] | None, *,
                 config: HostPushConfig | None = None,
                 now_fn: Callable[[], float] = time.time) -> None:
        self._push = push_message
        self._config = config or HostPushConfig()
        self._now = now_fn
        self._lock = threading.RLock()
        self._epoch = 1
        self._token = self._new_token(self._epoch)
        self._phase = PHASE_RUNNING
        self._stop_reason = ""
        self._pause_reason = ""
        self._real_test_used = False
        self._records: list[dict[str, Any]] = []
        self._seq = 0

    # -------------------------------------------------------------- helpers

    @staticmethod
    def _new_token(epoch: int) -> str:
        return f"{int(epoch)}:{uuid.uuid4().hex}"

    def _now_seconds(self, now: float | None) -> float:
        return self._now() if now is None else float(now)

    def _invalidate_locked(self) -> None:
        self._epoch += 1
        self._token = self._new_token(self._epoch)

    def _record_locked(self, *, intent: str, status: str, reason: str,
                       current: float, simulated: bool, submitted: bool,
                       critical: bool, terminal: bool) -> None:
        if self._config.max_records <= 0:
            return
        self._seq += 1
        self._records.append({
            "seq": self._seq,
            "ts": round(current, 3),
            "intent": intent[:24],
            "status": status[:24],
            "reason": _clean_reason(reason)[:64],
            "epoch": self._epoch,
            "simulated": bool(simulated),
            "submitted": bool(submitted),
            "critical": bool(critical),
            "terminal": bool(terminal),
        })
        # Bounded: only structured diagnostics, never kwargs/prompt/OCR.
        overflow = len(self._records) - self._config.max_records
        if overflow > 0:
            del self._records[:overflow]

    def _result(self, *, allowed: bool, submitted: bool, simulated: bool,
                receipt: Any, status: str, reason: str, intent: str) -> GateResult:
        return GateResult(
            allowed=bool(allowed),
            submitted=bool(submitted),
            simulated=bool(simulated),
            receipt=receipt,
            status=status,
            reason=_clean_reason(reason)[:120],
            intent=intent,
            epoch=self._epoch,
        )

    # -------------------------------------------------------------- state

    @property
    def config(self) -> HostPushConfig:
        with self._lock:
            return self._config

    @property
    def epoch(self) -> int:
        with self._lock:
            return self._epoch

    @property
    def token(self) -> str:
        with self._lock:
            return self._token

    def get_token(self) -> str:
        return self.token

    @property
    def phase(self) -> str:
        with self._lock:
            return self._phase

    @property
    def paused(self) -> bool:
        with self._lock:
            return bool(self._pause_reason)

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._phase == PHASE_RUNNING

    @property
    def is_simulating(self) -> bool:
        with self._lock:
            return bool(self._config.dry_run or self._config.simulated)

    def update_config(self, config: HostPushConfig) -> str:
        """Hot-reload gate policy.

        Changing consent/dry-run/simulation invalidates the current epoch so a
        tick that captured the old token cannot push under the new policy.
        Returns the new context token.
        """
        if not isinstance(config, HostPushConfig):
            raise TypeError("config must be HostPushConfig")
        with self._lock:
            old = self._config
            self._config = config
            if (old.dry_run != config.dry_run
                    or old.consent != config.consent
                    or old.simulated != config.simulated):
                self._invalidate_locked()
            # Each explicit false -> true transition is a fresh one-shot grant;
            # an authorization that stays true still only allows one real test.
            if not old.authorized_real_test and config.authorized_real_test:
                self._real_test_used = False
            return self._token

    def pause(self, reason: str = "paused") -> None:
        """Block read/respond/context/test; expire remains available.

        ``terminal`` never bypasses this.  Pausing invalidates outstanding
        tokens so a push prepared before the pause is refused.
        """
        with self._lock:
            self._pause_reason = _clean_reason(reason, "paused")
            self._invalidate_locked()

    def resume(self) -> None:
        with self._lock:
            self._pause_reason = ""
            self._invalidate_locked()

    def begin_stop(self, reason: str = "stopped") -> None:
        """Close normal admission and wait for an in-flight host push.

        Runs while holding the gate commit lock; the lifecycle lock must be
        released before calling this.  ``expire`` stays available until
        :meth:`finish_stop`.
        """
        with self._lock:
            if self._phase == PHASE_STOPPED:
                # finish_stop is final until reopen(); a late begin_stop must
                # not silently re-open the expire channel.
                return
            self._stop_reason = _clean_reason(reason, "stopped")
            self._phase = PHASE_STOPPING
            self._invalidate_locked()

    def finish_stop(self) -> None:
        """Close every intent, including ``expire``."""
        with self._lock:
            self._phase = PHASE_STOPPED
            self._invalidate_locked()

    def reopen(self) -> int:
        """Restart admission with a brand new epoch/token.

        A lifecycle restart begins a fresh session, so the previous session's
        pause flag is cleared.  Call :meth:`pause` again if the new session
        should also start paused.
        """
        with self._lock:
            self._phase = PHASE_RUNNING
            self._stop_reason = ""
            self._pause_reason = ""
            self._invalidate_locked()
            return self._epoch

    # -------------------------------------------------------------- push

    def push(self, intent: str, *, critical: bool = False,
             terminal: bool = False, context_token: str | None = None,
             expected_epoch: int | None = None, simulate: bool = False,
             now: float | None = None, **kwargs: Any) -> GateResult:
        """Run one final-outlet attempt.

        ``context_token`` / ``expected_epoch`` are validated *inside* the
        commit lock; a callback that checks synchronization outside the gate
        can race with stop/reopen and is not a substitute.

        ``simulate=True`` is used by the dispatcher's dry-run path when it owns
        a dry configuration but the injected gate is still live.  It forces a
        no-op simulation and never reaches the host.
        """
        intent_text = str(intent or "").strip().lower()[:24]
        force_simulation = bool(simulate)
        current = self._now_seconds(now)

        with self._lock:
            if intent_text not in GATE_INTENTS:
                self._record_locked(
                    intent=intent_text, status=STATUS_REJECTED,
                    reason=REASON_INVALID_INTENT, current=current,
                    simulated=False, submitted=False, critical=critical,
                    terminal=terminal)
                return self._result(
                    allowed=False, submitted=False, simulated=False,
                    receipt=None, status=STATUS_REJECTED,
                    reason=REASON_INVALID_INTENT, intent=intent_text)

            # Token checks first: a stale tick must never push under a new
            # epoch, not even an expire tombstone.
            if expected_epoch is not None:
                try:
                    epoch_ok = int(expected_epoch) == self._epoch
                except (TypeError, ValueError):
                    epoch_ok = False
                if not epoch_ok:
                    self._record_locked(
                        intent=intent_text, status=STATUS_REJECTED,
                        reason=REASON_STALE_EPOCH, current=current,
                        simulated=False, submitted=False, critical=critical,
                        terminal=terminal)
                    return self._result(
                        allowed=False, submitted=False, simulated=False,
                        receipt=None, status=STATUS_REJECTED,
                        reason=REASON_STALE_EPOCH, intent=intent_text)
            if context_token is not None:
                token_ok = str(context_token) == self._token
                if not token_ok:
                    self._record_locked(
                        intent=intent_text, status=STATUS_REJECTED,
                        reason=REASON_STALE_CONTEXT, current=current,
                        simulated=False, submitted=False, critical=critical,
                        terminal=terminal)
                    return self._result(
                        allowed=False, submitted=False, simulated=False,
                        receipt=None, status=STATUS_REJECTED,
                        reason=REASON_STALE_CONTEXT, intent=intent_text)

            # Lifecycle/phrase policy: terminal only skips pacing, never these.
            if self._phase == PHASE_STOPPED:
                self._record_locked(
                    intent=intent_text, status=STATUS_REJECTED,
                    reason=REASON_STOPPED, current=current, simulated=False,
                    submitted=False, critical=critical, terminal=terminal)
                return self._result(
                    allowed=False, submitted=False, simulated=False,
                    receipt=None, status=STATUS_REJECTED,
                    reason=REASON_STOPPED, intent=intent_text)
            if self._phase == PHASE_STOPPING and intent_text != "expire":
                self._record_locked(
                    intent=intent_text, status=STATUS_REJECTED,
                    reason=REASON_STOPPING, current=current, simulated=False,
                    submitted=False, critical=critical, terminal=terminal)
                return self._result(
                    allowed=False, submitted=False, simulated=False,
                    receipt=None, status=STATUS_REJECTED,
                    reason=REASON_STOPPING, intent=intent_text)
            if self._pause_reason and intent_text != "expire":
                self._record_locked(
                    intent=intent_text, status=STATUS_REJECTED,
                    reason=REASON_PAUSED, current=current, simulated=False,
                    submitted=False, critical=critical, terminal=terminal)
                return self._result(
                    allowed=False, submitted=False, simulated=False,
                    receipt=None, status=STATUS_REJECTED,
                    reason=REASON_PAUSED, intent=intent_text)

            # Consent is a hard zero-push gate for *every* intent, including
            # expire and test.  It is checked before dry-run so a driver can
            # never mistake suppression for a successful simulated push.
            if not self._config.consent:
                self._record_locked(
                    intent=intent_text, status=STATUS_REJECTED,
                    reason=REASON_CONSENT_REQUIRED, current=current,
                    simulated=False, submitted=False, critical=critical,
                    terminal=terminal)
                return self._result(
                    allowed=False, submitted=False, simulated=False,
                    receipt=None, status=STATUS_REJECTED,
                    reason=REASON_CONSENT_REQUIRED, intent=intent_text)

            simulating = (force_simulation or self._config.dry_run
                          or self._config.simulated)

            if intent_text == "test":
                if simulating:
                    self._record_locked(
                        intent=intent_text, status=STATUS_SIMULATED,
                        reason=REASON_DRY_RUN if not force_simulation
                        else REASON_SIMULATED,
                        current=current, simulated=True, submitted=False,
                        critical=critical, terminal=terminal)
                    return self._result(
                        allowed=True, submitted=False, simulated=True,
                        receipt=None, status=STATUS_SIMULATED,
                        reason=(REASON_DRY_RUN if not force_simulation
                                else REASON_SIMULATED),
                        intent=intent_text)
                if not self._config.authorized_real_test:
                    # Default policy is a simulation, never an unauthorized
                    # real connection test.
                    self._record_locked(
                        intent=intent_text, status=STATUS_REJECTED,
                        reason=REASON_TEST_NOT_AUTHORIZED, current=current,
                        simulated=True, submitted=False, critical=critical,
                        terminal=terminal)
                    return self._result(
                        allowed=False, submitted=False, simulated=True,
                        receipt=None, status=STATUS_REJECTED,
                        reason=REASON_TEST_NOT_AUTHORIZED, intent=intent_text)
                if self._real_test_used:
                    self._record_locked(
                        intent=intent_text, status=STATUS_REJECTED,
                        reason=REASON_TEST_AUTHORIZATION_USED, current=current,
                        simulated=False, submitted=False, critical=critical,
                        terminal=terminal)
                    return self._result(
                        allowed=False, submitted=False, simulated=False,
                        receipt=None, status=STATUS_REJECTED,
                        reason=REASON_TEST_AUTHORIZATION_USED,
                        intent=intent_text)
                # Consume the one-shot before the host call: a transport error
                # must not grant unlimited retries of a real test.
                self._real_test_used = True

            if simulating:
                self._record_locked(
                    intent=intent_text, status=STATUS_SIMULATED,
                    reason=(REASON_SIMULATED if force_simulation
                            else REASON_DRY_RUN),
                    current=current, simulated=True, submitted=False,
                    critical=critical, terminal=terminal)
                return self._result(
                    allowed=True, submitted=False, simulated=True,
                    receipt=None, status=STATUS_SIMULATED,
                    reason=(REASON_SIMULATED if force_simulation
                            else REASON_DRY_RUN),
                    intent=intent_text)

            if self._push is None:
                self._record_locked(
                    intent=intent_text, status=STATUS_REJECTED,
                    reason=REASON_TRANSPORT_UNAVAILABLE, current=current,
                    simulated=False, submitted=False, critical=critical,
                    terminal=terminal)
                return self._result(
                    allowed=False, submitted=False, simulated=False,
                    receipt=None, status=STATUS_REJECTED,
                    reason=REASON_TRANSPORT_UNAVAILABLE, intent=intent_text)

            try:
                receipt = self._push(**kwargs)
            except Exception:
                self._record_locked(
                    intent=intent_text, status=STATUS_REJECTED,
                    reason=REASON_TRANSPORT, current=current, simulated=False,
                    submitted=False, critical=critical, terminal=terminal)
                return self._result(
                    allowed=True, submitted=False, simulated=False,
                    receipt=None, status=STATUS_REJECTED,
                    reason=REASON_TRANSPORT, intent=intent_text)

            status, reason = normalize_receipt(receipt)
            submitted = status == STATUS_ACCEPTED
            self._record_locked(
                intent=intent_text, status=status, reason=reason,
                current=current, simulated=False, submitted=submitted,
                critical=critical, terminal=terminal)
            return self._result(
                allowed=True, submitted=submitted, simulated=False,
                receipt=receipt, status=status, reason=reason,
                intent=intent_text)

    def __call__(self, intent: str = "respond", **kwargs: Any) -> GateResult:
        """Compatibility callable; explicit intents are preferred."""
        return self.push(intent, **kwargs)

    # -------------------------------------------------------------- inspect

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "config": asdict(self._config),
                "epoch": self._epoch,
                "phase": self._phase,
                "stop_reason": self._stop_reason,
                "pause_reason": self._pause_reason,
                "paused": bool(self._pause_reason),
                "simulating": bool(self._config.dry_run or self._config.simulated),
                "real_test_used": self._real_test_used,
                "records": [dict(record) for record in self._records],
            }

    def recent_records(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            if limit <= 0:
                return []
            return [dict(record) for record in self._records[-limit:]]


__all__ = [
    "GATE_INTENTS", "GateResult", "HostPushConfig", "HostPushGate",
    "PHASE_RUNNING", "PHASE_STOPPED", "PHASE_STOPPING",
    "REASON_BACKPRESSURE", "REASON_CONSENT_REQUIRED", "REASON_DRY_RUN",
    "REASON_HOST_REJECTED", "REASON_INVALID_INTENT", "REASON_LEGACY_NO_RECEIPT",
    "REASON_PAUSED", "REASON_RECEIPT_UNKNOWN", "REASON_SIMULATED",
    "REASON_STALE_CONTEXT", "REASON_STALE_EPOCH", "REASON_STOPPED",
    "REASON_STOPPING", "REASON_TEST_AUTHORIZATION_USED",
    "REASON_TEST_NOT_AUTHORIZED", "REASON_TRANSPORT",
    "REASON_TRANSPORT_UNAVAILABLE", "STATUS_ACCEPTED", "STATUS_LEGACY_UNCONFIRMED",
    "STATUS_REJECTED", "STATUS_SIMULATED", "STATUS_UNKNOWN", "normalize_receipt",
]
