"""Live task status and Codex-style steering for BrowserSkill runs."""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from .models import BrowserTaskResult

SteeringMode = Literal["append", "replace", "cancel"]


@dataclass(frozen=True, slots=True)
class SteeringUpdate:
    revision: int
    mode: SteeringMode
    requirement: str
    user_request: str = ""
    created_at: float = field(default_factory=time.monotonic)


@dataclass(slots=True)
class BrowserTaskControl:
    conversation_id: str
    goal: str
    original_request: str
    initial_request: str = ""
    started_at: float = field(default_factory=time.monotonic)
    stage: str = "starting"
    message: str = "浏览器任务正在启动"
    step: int = 0
    action_limit: int = 0
    progress: float = 0.0
    current_url: str = ""
    current_action: str = ""
    revision: int = 0
    applied_revision: int = 0
    last_update_mode: str = ""
    active: bool = True
    terminal_status: str = ""
    terminal_summary: str = ""
    continuation_available: bool = False
    session_decision_required: bool = False
    session_state: str = ""
    _pending: deque[SteeringUpdate] = field(default_factory=deque)
    _last_submitted: SteeringUpdate | None = None

    def __post_init__(self) -> None:
        if not self.initial_request:
            self.initial_request = self.original_request

    @property
    def cancel_requested(self) -> bool:
        return any(update.mode == "cancel" for update in self._pending)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def submit(
        self,
        mode: SteeringMode,
        requirement: str = "",
        *,
        user_request: str = "",
    ) -> tuple[SteeringUpdate, bool]:
        text = re.sub(r"\s+", " ", str(requirement or "")).strip()[:8000]
        raw_text = re.sub(r"\s+", " ", str(user_request or "")).strip()[:8000]
        if mode != "cancel" and not text:
            raise ValueError("requirement is required for append/replace steering")
        latest = self._last_submitted
        if (
            latest is not None
            and latest.mode == mode
            and latest.requirement.casefold() == text.casefold()
            and latest.user_request.casefold() == raw_text.casefold()
            and time.monotonic() - latest.created_at <= 20.0
        ):
            return latest, True
        self.revision += 1
        update = SteeringUpdate(
            revision=self.revision,
            mode=mode,
            requirement=text,
            user_request=raw_text,
        )
        self._pending.append(update)
        self._last_submitted = update
        self.last_update_mode = mode
        return update, False

    def matches_known_request(self, requirement: str) -> bool:
        normalized = re.sub(r"\s+", " ", str(requirement or "")).strip().casefold()
        if not normalized:
            return False
        candidates = (self.initial_request, self.original_request, self.goal)
        if any(re.sub(r"\s+", " ", value).strip().casefold() == normalized for value in candidates):
            return True
        return bool(
            self._last_submitted
            and self._last_submitted.requirement.casefold() == normalized
            and time.monotonic() - self._last_submitted.created_at <= 20.0
        )

    def consume_updates(self) -> list[SteeringUpdate]:
        updates = list(self._pending)
        self._pending.clear()
        return updates

    def mark_applied(self, update: SteeringUpdate, *, goal: str, original_request: str) -> None:
        self.goal = goal
        self.original_request = original_request
        self.applied_revision = max(self.applied_revision, update.revision)
        self.last_update_mode = update.mode

    def update_progress(
        self,
        *,
        stage: str | None = None,
        message: str | None = None,
        step: int | None = None,
        action_limit: int | None = None,
        progress: float | None = None,
    ) -> None:
        if stage is not None:
            self.stage = str(stage)[:80]
        if message is not None:
            self.message = re.sub(r"\s+", " ", str(message)).strip()[:500]
        if step is not None:
            self.step = max(0, int(step))
        if action_limit is not None:
            self.action_limit = max(0, int(action_limit))
        if progress is not None:
            self.progress = min(1.0, max(0.0, float(progress)))

    def update_action(self, action: str) -> None:
        self.current_action = str(action or "")[:80]

    def update_url(self, url: str) -> None:
        self.current_url = _safe_url(url)

    def status(self) -> dict[str, object]:
        return {
            "active": self.active,
            "stage": self.stage,
            "message": self.message,
            "step": self.step,
            "action_limit": self.action_limit,
            "current_url": self.current_url,
            "current_action": self.current_action,
            "goal": self.goal[:4000],
            "goal_revision": self.revision,
            "applied_revision": self.applied_revision,
            "pending_updates": len(self._pending),
            "last_update_mode": self.last_update_mode,
            "waiting_for_user": self.stage == "waiting_for_user",
            "elapsed_seconds": max(0, int(time.monotonic() - self.started_at)),
            "can_steer": self.active,
            "terminal_status": self.terminal_status,
            "continuation_available": self.continuation_available,
            "session_decision_required": self.session_decision_required,
            "session_state": self.session_state,
            "summary": self.terminal_summary or self.message,
        }


class BrowserTaskController:
    _MAX_LAST_CONTROLS = 512

    def __init__(self) -> None:
        self._active: dict[str, BrowserTaskControl] = {}
        self._last: dict[str, BrowserTaskControl] = {}

    def _remember_last(self, control: BrowserTaskControl) -> None:
        self._last.pop(control.conversation_id, None)
        self._last[control.conversation_id] = control
        while len(self._last) > self._MAX_LAST_CONTROLS:
            self._last.pop(next(iter(self._last)), None)

    @staticmethod
    def key(conversation_id: str | None) -> str:
        return str(conversation_id or "").strip() or "<one-shot>"

    def start(
        self,
        *,
        conversation_id: str | None,
        goal: str,
        original_request: str,
        action_limit: int = 0,
    ) -> BrowserTaskControl:
        key = self.key(conversation_id)
        control = BrowserTaskControl(
            conversation_id=key,
            goal=str(goal or "").strip(),
            original_request=str(original_request or goal or "").strip(),
            action_limit=max(0, int(action_limit)),
        )
        self._active[key] = control
        return control

    def get_active(self, conversation_id: str | None) -> BrowserTaskControl | None:
        key = self.key(conversation_id)
        control = self._active.get(key)
        if control is not None:
            return control
        if not str(conversation_id or "").strip() and len(self._active) == 1:
            return next(iter(self._active.values()))
        return None

    def finish(self, control: BrowserTaskControl, result: BrowserTaskResult) -> None:
        control.active = False
        control.stage = "completed" if result.success else result.status
        control.progress = 1.0
        control.terminal_status = result.status
        control.terminal_summary = result.summary[:2000]
        control.continuation_available = result.continuation_available
        control.session_decision_required = result.session_decision_required
        control.session_state = result.session_state
        control.current_action = ""
        control.update_url(result.current_url or control.current_url)
        if self._active.get(control.conversation_id) is control:
            self._active.pop(control.conversation_id, None)
        self._remember_last(control)

    def finish_cancelled(self, control: BrowserTaskControl) -> None:
        control.active = False
        control.stage = "cancelled"
        control.terminal_status = "cancelled"
        control.terminal_summary = "浏览器任务已取消"
        control.current_action = ""
        if self._active.get(control.conversation_id) is control:
            self._active.pop(control.conversation_id, None)
        self._remember_last(control)

    def finish_failed(self, control: BrowserTaskControl, summary: str) -> None:
        control.active = False
        control.stage = "failed"
        control.terminal_status = "failed"
        control.terminal_summary = str(summary or "浏览器任务失败")[:2000]
        control.current_action = ""
        if self._active.get(control.conversation_id) is control:
            self._active.pop(control.conversation_id, None)
        self._remember_last(control)

    def status(self, conversation_id: str | None) -> dict[str, object]:
        control = self.get_active(conversation_id)
        if control is None:
            control = self._last.get(self.key(conversation_id))
        if control is None:
            return {
                "active": False,
                "stage": "idle",
                "message": "当前聊天没有 BrowserSkill 任务",
                "can_steer": False,
                "pending_updates": 0,
            }
        return control.status()


def _safe_url(url: str) -> str:
    try:
        parsed = urlsplit(str(url or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        host = parsed.hostname
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path or "/", "", ""))[:4096]
    except (TypeError, ValueError):
        return ""
