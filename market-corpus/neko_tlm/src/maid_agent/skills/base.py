"""Pure-Python contracts for checkpointed maid skills.

This module intentionally has no dependency on the N.E.K.O plugin SDK.  Skill
definitions can therefore be unit-tested without importing the plugin entrypoint.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Protocol, Sequence, Union

SCHEMA_VERSION = 1

ACTIVE_SKILL_STATUSES = frozenset({
    "PENDING",
    "RUNNING",
    "STARTING_ACTION",
    "WAITING_ACTION",
    "CANCEL_REQUESTED",
})

TERMINAL_SKILL_STATUSES = frozenset({
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "BLOCKED",
})


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _position(value: Any) -> Optional[Dict[str, int]]:
    if not isinstance(value, Mapping):
        return None
    try:
        return {axis: int(value[axis]) for axis in ("x", "y", "z")}
    except (KeyError, TypeError, ValueError):
        return None


@dataclass
class SkillRun:
    """Serializable state for one high-level skill execution."""

    skill_id: str
    maid_id: str
    skill_name: str
    args: Dict[str, Any]
    schema_version: int = SCHEMA_VERSION
    skill_version: int = 1
    collected_count: int = 0
    origin_pos: Optional[Dict[str, int]] = None
    current_pos: Optional[Dict[str, int]] = None
    main_direction: str = ""
    main_segment_index: int = 0
    branch_index: int = 0
    tried_directions_at_current: list[str] = field(default_factory=list)
    status: str = "PENDING"
    current_action_id: str = ""
    current_action_generation: int = 0
    current_action_fingerprint: str = ""
    revision: int = 0
    blocked_notification_revision: int = 0
    last_failure_reason: str = ""
    # A BLOCKED skill is terminal in v1.  Java may attach a structured
    # decision request, but there is intentionally no in-place resume protocol
    # until the server implements and advertises one.  The LLM must start a new
    # skill with adjusted goal/preferences after player confirmation.
    decision_required: bool = False
    decision_context: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    result: Dict[str, Any] = field(default_factory=dict)
    warnings: list[Any] = field(default_factory=list)
    # The full request is required to recover a crash after the STARTING_ACTION
    # checkpoint but before the idempotent start response is received.
    current_action_request: Dict[str, Any] = field(default_factory=dict)
    # A terminal child snapshot is persisted before the definition consumes it.
    # Replaying it after a process crash is therefore deterministic.
    pending_terminal: Dict[str, Any] = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_SKILL_STATUSES

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_SKILL_STATUSES

    def as_dict(self) -> Dict[str, Any]:
        can_cancel = self.active
        return {
            "schema_version": self.schema_version,
            "skill_id": self.skill_id,
            "maid_id": self.maid_id,
            "skill_name": self.skill_name,
            "skill_version": self.skill_version,
            "args": dict(self.args),
            "collected_count": self.collected_count,
            "origin_pos": dict(self.origin_pos) if self.origin_pos else None,
            "current_pos": dict(self.current_pos) if self.current_pos else None,
            "main_direction": self.main_direction,
            "main_segment_index": self.main_segment_index,
            "branch_index": self.branch_index,
            "tried_directions_at_current": list(self.tried_directions_at_current),
            "status": self.status,
            "current_action_id": self.current_action_id,
            "current_action_generation": self.current_action_generation,
            "current_action_fingerprint": self.current_action_fingerprint,
            "revision": self.revision,
            "blocked_notification_revision": self.blocked_notification_revision,
            "last_failure_reason": self.last_failure_reason,
            "decision_required": bool(self.decision_required),
            "decision_context": dict(self.decision_context),
            "control_capabilities": {
                "cancel": can_cancel,
                "pause": False,
                "resume": False,
                "submit_decision": False,
                "decision_mode": (
                    "restart_with_adjusted_parameters"
                    if self.status == "BLOCKED" and self.decision_required
                    else "none"
                ),
                "protocol_gate": "pause_resume_decision_protocol_not_registered",
            },
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "result": dict(self.result),
            "warnings": list(self.warnings),
            "current_action_request": dict(self.current_action_request),
            "pending_terminal": dict(self.pending_terminal),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SkillRun":
        data = dict(value or {})
        schema_version = int(data.get("schema_version", 0))
        if schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported skill checkpoint schema_version={schema_version}"
            )
        return cls(
            schema_version=schema_version,
            skill_id=str(data.get("skill_id") or ""),
            maid_id=str(data.get("maid_id") or ""),
            skill_name=str(data.get("skill_name") or ""),
            skill_version=max(1, int(data.get("skill_version", 1))),
            args=_mapping(data.get("args")),
            collected_count=max(0, int(data.get("collected_count", 0))),
            origin_pos=_position(data.get("origin_pos")),
            current_pos=_position(data.get("current_pos")),
            main_direction=str(data.get("main_direction") or ""),
            main_segment_index=max(0, int(data.get("main_segment_index", 0))),
            branch_index=max(0, int(data.get("branch_index", 0))),
            tried_directions_at_current=[
                str(item) for item in data.get("tried_directions_at_current", [])
                if str(item)
            ],
            status=str(data.get("status") or "PENDING").upper(),
            current_action_id=str(data.get("current_action_id") or ""),
            current_action_generation=max(
                0, int(data.get("current_action_generation", 0))
            ),
            current_action_fingerprint=str(
                data.get("current_action_fingerprint") or ""
            ),
            revision=max(0, int(data.get("revision", 0))),
            blocked_notification_revision=max(
                0, int(data.get("blocked_notification_revision", 0))
            ),
            last_failure_reason=str(data.get("last_failure_reason") or ""),
            decision_required=bool(
                data.get(
                    "decision_required",
                    str(data.get("status") or "").upper() == "BLOCKED",
                )
            ),
            decision_context=_mapping(data.get("decision_context")),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            result=_mapping(data.get("result")),
            warnings=list(data.get("warnings", []))
            if isinstance(data.get("warnings", []), list) else [],
            current_action_request=_mapping(data.get("current_action_request")),
            pending_terminal=_mapping(data.get("pending_terminal")),
        )


@dataclass(frozen=True)
class StartAction:
    kind: str
    args: Mapping[str, Any]
    timeout_ms: int = 0
    replace_existing: bool = True

    def request_payload(self) -> Dict[str, Any]:
        return {
            "kind": str(self.kind or "").strip().lower(),
            "args": dict(self.args or {}),
            "timeout_ms": int(self.timeout_ms),
            "replace_existing": bool(self.replace_existing),
        }


@dataclass(frozen=True)
class Complete:
    result: Mapping[str, Any] = field(default_factory=dict)
    warnings: Sequence[Any] = field(default_factory=tuple)


@dataclass(frozen=True)
class Blocked:
    reason: str
    result: Mapping[str, Any] = field(default_factory=dict)
    warnings: Sequence[Any] = field(default_factory=tuple)


@dataclass(frozen=True)
class Fail:
    reason: str
    result: Mapping[str, Any] = field(default_factory=dict)
    warnings: Sequence[Any] = field(default_factory=tuple)


SkillDirective = Union[StartAction, Complete, Blocked, Fail]


class SkillDefinition(Protocol):
    name: str
    version: int

    def normalize_args(self, raw: Mapping[str, Any]) -> Dict[str, Any]: ...

    def initialize(self, run: SkillRun) -> None: ...

    def next_directive(
        self,
        run: SkillRun,
        terminal_snapshot: Optional[Mapping[str, Any]],
    ) -> SkillDirective: ...


class ActionClient(Protocol):
    async def start_action(
        self,
        *,
        action_id: str,
        maid_id: str,
        kind: str,
        args: Mapping[str, Any],
        timeout_ms: int,
        replace_existing: bool,
        owner_id: str,
        feedback_policy: str,
    ) -> Mapping[str, Any]: ...

    async def cancel_action(
        self, action_id: str, *, maid_id: str = ""
    ) -> Mapping[str, Any]: ...

    async def get_action_status(
        self, action_id: str
    ) -> Optional[Mapping[str, Any]]: ...

    async def list_active_actions(
        self, *, maid_id: str = ""
    ) -> Sequence[Mapping[str, Any]]: ...


class SkillFeedback(Protocol):
    async def progress(self, run_snapshot: Mapping[str, Any]) -> None: ...

    async def blocked(self, run_snapshot: Mapping[str, Any]) -> None: ...

    async def finished(self, run_snapshot: Mapping[str, Any]) -> None: ...


def action_fingerprint(
    maid_id: str, action_id: str, request: Mapping[str, Any]
) -> str:
    canonical = json.dumps(
        {
            "maid_id": str(maid_id),
            "action_id": str(action_id),
            **dict(request or {}),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
