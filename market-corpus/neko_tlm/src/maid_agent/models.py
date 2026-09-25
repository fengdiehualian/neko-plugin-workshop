"""In-memory action records with generation and sequence ordering guards."""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Tuple

ACTIVE_STATUSES = frozenset({
    "PENDING",
    "RUNNING",
    "CANCEL_REQUESTED",
    "TERMINATING",
})

TERMINAL_STATUSES = frozenset({
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "SUPERSEDED",
    "TIMEOUT",
})


def _upper(value: Any, default: str = "") -> str:
    text = str(value or default).strip()
    return text.upper() if text else default


@dataclass
class ActionRecord:
    action_id: str
    maid_id: str = ""
    generation: int = 0
    sequence: int = -1
    kind: str = ""
    status: str = "PENDING"
    stage: str = ""
    progress: Optional[float] = None
    end_reason: str = ""
    result: Dict[str, Any] = field(default_factory=dict)
    detail: Dict[str, Any] = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    def as_dict(self) -> Dict[str, Any]:
        data = {
            "action_id": self.action_id,
            "maid_id": self.maid_id,
            "generation": self.generation,
            "sequence": self.sequence,
            "kind": self.kind,
            "status": self.status,
            "stage": self.stage,
            "end_reason": self.end_reason,
            "result": self.result,
            "detail": self.detail,
            "warnings": list(self.warnings),
        }
        if self.progress is not None:
            data["progress"] = self.progress
        return data


class ActionTracker:
    """Tracks action snapshots and rejects stale server events."""

    def __init__(self):
        self._records: Dict[str, ActionRecord] = {}

    def get(self, action_id: str) -> Optional[ActionRecord]:
        return self._records.get(str(action_id or ""))

    def values(self) -> Iterable[ActionRecord]:
        return tuple(self._records.values())

    def active(self, maid_id: Optional[str] = None) -> list:
        maid_id = str(maid_id or "")
        return [
            record for record in self._records.values()
            if record.active and (not maid_id or record.maid_id == maid_id)
        ]

    def latest_active(self, maid_id: Optional[str] = None) -> Optional[ActionRecord]:
        records = self.active(maid_id)
        if not records:
            return None
        return max(records, key=lambda item: (item.updated_at, item.generation, item.sequence))

    def apply(self, payload: Dict[str, Any]) -> Tuple[Optional[ActionRecord], bool]:
        payload = dict(payload or {})
        action_id = str(payload.get("action_id") or "").strip()
        if not action_id:
            return None, False

        generation = self._as_int(payload.get("generation"), 0)
        sequence = self._as_int(payload.get("sequence"), -1)
        current = self._records.get(action_id)

        if current is not None:
            if generation < current.generation:
                return current, False
            if generation == current.generation:
                # Responses may omit sequence. They may enrich the current snapshot,
                # but never allow a duplicate/older event to produce feedback again.
                if sequence >= 0 and sequence <= current.sequence:
                    return current, False
                if current.terminal and _upper(payload.get("status"), current.status) not in TERMINAL_STATUSES:
                    return current, False

        if current is None or generation > current.generation:
            current = ActionRecord(action_id=action_id, generation=generation)
            self._records[action_id] = current

        current.maid_id = str(payload.get("maid_id") or current.maid_id)
        current.generation = generation
        if sequence >= 0:
            current.sequence = sequence
        current.kind = str(payload.get("kind") or current.kind)
        current.status = _upper(payload.get("status"), current.status or "PENDING")
        current.stage = str(payload.get("stage") or current.stage)
        if "progress" in payload:
            current.progress = self._progress(payload.get("progress"))
        current.end_reason = _upper(payload.get("end_reason"), current.end_reason)
        result = payload.get("result")
        if isinstance(result, dict):
            current.result = dict(result)
        detail = payload.get("detail")
        if isinstance(detail, dict):
            current.detail = dict(detail)
        warnings = payload.get("warnings")
        if isinstance(warnings, list):
            current.warnings = list(warnings)
        current.updated_at = time.time()
        current.raw = payload
        return current, True

    def mark_server_state_lost(self, record: ActionRecord) -> Tuple[ActionRecord, bool]:
        sequence = max(record.sequence + 1, 0)
        return self.apply({
            **record.as_dict(),
            "sequence": sequence,
            "status": "FAILED",
            "stage": "RECONCILING",
            "end_reason": "SERVER_STATE_LOST",
            "result": {"message": "The server no longer has this action"},
        })

    @staticmethod
    def _as_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _progress(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return max(0.0, min(1.0, number))
