"""Lightweight runtime observability reused from the War Thunder plugin."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import Any

_OUTPUT_STAGES = {
    "dispatcher_dry_run",
    "dispatcher_pushed",
    "dispatcher_failed",
    "dispatcher_suppressed",
    "context_pushed",
    "context_failed",
    "test_say_pushed",
    "test_say_blocked",
    "test_say_failed",
}
_ACTIVITY_STAGES = _OUTPUT_STAGES | {
    "arbiter_allowed",
    "arbiter_cooldown",
    "arbiter_scenario_gated",
    "arbiter_preempted",
    "arbiter_dropped",
    "arbiter_suppressed",
}
_ACTIVITY_KEYS = (
    "seq",
    "ts",
    "stage",
    "outcome",
    "reason",
    "event_id",
    "edge",
    "level",
    "dry_run",
    "pushed",
)
_CORE_KEYS = (
    "event_id",
    "edge",
    "scenario",
    "priority",
    "level",
    "dry_run",
    "safety_status",
    "kind",
    "ai_behavior",
    "visibility",
    "pushed",
)
_DELIVERY_KEYS = (
    "target_lanlan",
    "coalesce_key",
    "event_ts",
    "event_age_seconds",
    "event_max_age_seconds",
    "event_expires_at",
    "companion_reply_contract",
    "reply_contract",
    "max_reply_chars",
    "reply_max_chars",
    "response_module_hint",
    "plugin_recommended_reply",
    "plugin_owned_output",
    "replace_pending",
    "interrupt_pending",
    "reply_style_contract",
    "dialogue_policy_owner",
    "plugin_dialogue_policy",
    "host_callback_contract_version",
    "delivery_ttl_seconds",
    "delivery_intent",
    "interrupt_policy",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_record(data: dict[str, Any]) -> dict[str, Any]:
    # Never persist raw frames, payloads, prompts, or host message objects.
    banned = {"raw_payload", "prompt", "payload", "scene_state", "push_message"}
    return {key: value for key, value in data.items() if key not in banned and value is not None}


class RuntimeTimeline:
    """In-memory summaries; full bounded records are opt-in."""

    def __init__(self, *, observability_enabled: bool = False, max_events: int = 100) -> None:
        self.enabled = bool(observability_enabled)
        self.max_events = max(1, int(max_events or 100))
        self._records: deque[dict[str, Any]] = deque(maxlen=self.max_events)
        self._activity_records: deque[dict[str, Any]] = deque(maxlen=20)
        self._seq = 0
        self._lock = Lock()
        self._last_event: dict[str, Any] | None = None
        self._last_decision: dict[str, Any] | None = None
        self._last_output_status: dict[str, Any] | None = None
        self._last_tick_at: str | None = None

    def configure(self, *, observability_enabled: bool, max_events: int = 100) -> None:
        with self._lock:
            limit = max(1, int(max_events or 100))
            old_records = list(self._records)[-limit:]
            self.enabled = bool(observability_enabled)
            self.max_events = limit
            self._records = deque(old_records, maxlen=limit)

    def mark_tick(self) -> None:
        with self._lock:
            self._last_tick_at = _now_iso()

    def record_decision(
        self,
        *,
        event_id: str | None,
        stage: str,
        outcome: str,
        reason: str,
        scenario: str | None = None,
        safety_status: str | None = None,
        dry_run: bool | None = None,
    ) -> None:
        record = _clean_record(
            {
                "ts": _now_iso(),
                "event_id": event_id,
                "stage": stage,
                "outcome": outcome,
                "reason": reason,
                "scenario": scenario,
                "safety_status": safety_status,
                "dry_run": dry_run,
            }
        )
        with self._lock:
            if (
                self._last_decision
                and self._last_decision.get("outcome") == "allowed"
                and record.get("stage") == "arbiter_preempted"
            ):
                return
            self._last_decision = dict(record)
            if event_id:
                self._last_event = {"event_id": event_id}

    def record_stage(self, *, stage: str, outcome: str, reason: str, **metadata: Any) -> None:
        try:
            record = _clean_record(
                {
                    "seq": self._seq + 1,
                    "ts": _now_iso(),
                    "stage": stage,
                    "outcome": outcome,
                    "reason": reason,
                    **{key: metadata.get(key) for key in _CORE_KEYS},
                    **{key: metadata.get(key) for key in _DELIVERY_KEYS},
                    "message": metadata.get("safe_summary"),
                }
            )
            with self._lock:
                self._seq += 1
                record["seq"] = self._seq
                if record.get("event_id"):
                    self._last_event = {
                        "event_id": record.get("event_id"),
                        "edge": record.get("edge"),
                        "level": record.get("level"),
                    }
                if stage in _OUTPUT_STAGES:
                    self._last_output_status = {
                        key: value
                        for key, value in record.items()
                        if key in {"stage", "outcome", "reason", *_CORE_KEYS, *_DELIVERY_KEYS}
                    }
                if self.enabled:
                    self._records.append(record)
                if stage in _ACTIVITY_STAGES:
                    self._activity_records.append(
                        {key: record[key] for key in _ACTIVITY_KEYS if key in record}
                    )
        except Exception:
            # Observability must never break the output path.
            return

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "last_tick_at": self._last_tick_at,
                "last_event": dict(self._last_event) if self._last_event else None,
                "last_decision": dict(self._last_decision) if self._last_decision else None,
                "last_output_status": (
                    dict(self._last_output_status) if self._last_output_status else None
                ),
                "recent_activity": [dict(item) for item in self._activity_records],
                "recent_timeline": [dict(item) for item in self._records] if self.enabled else [],
            }


def arbiter_chain_to_observe_records(
    chain: list[dict[str, Any]], *, scenario: str
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in chain:
        outcome = str(item.get("outcome") or "")
        reason = str(item.get("reason") or "unknown")
        stage = "arbiter_dropped"
        normalized_outcome = outcome or "dropped"
        normalized_reason = reason
        if outcome == "spoken":
            stage = "arbiter_allowed"
            normalized_outcome = "allowed"
            normalized_reason = "selected"
        elif reason == "cooldown":
            stage = "arbiter_cooldown"
            normalized_reason = "cooldown_active"
        elif reason.startswith("scenario_gated"):
            stage = "arbiter_scenario_gated"
            normalized_reason = "scenario_gated"
        elif reason == "lost_to_preempt":
            stage = "arbiter_preempted"
            normalized_reason = "preempted_by_critical"
        elif outcome == "suppressed":
            stage = "arbiter_suppressed"
        records.append(
            _clean_record(
                {
                    "stage": stage,
                    "outcome": normalized_outcome,
                    "reason": normalized_reason,
                    "event_id": item.get("event_id"),
                    "level": item.get("level"),
                    "scenario": scenario,
                }
            )
        )
    return records
