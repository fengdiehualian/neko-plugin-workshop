"""Crash-safe orchestration of declarative high-level maid skills."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from typing import Any, Dict, Mapping, Optional

from .base import (
    Blocked,
    Complete,
    Fail,
    SkillDefinition,
    SkillRun,
    StartAction,
    action_fingerprint,
)
from .checkpoint import SkillCheckpointStore

ACTION_TERMINAL_STATUSES = frozenset({
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "SUPERSEDED",
    "TIMEOUT",
})
REPLACEMENT_CANCEL_TIMEOUT = 10.0
BLOCKED_NOTIFICATION_RETRY_DELAYS = (0.5, 1.5, 5.0)


class SkillRunner:
    """Runs one checkpointed high-level skill per maid.

    The WebSocket poll loop calls :meth:`on_action_event`.  That callback only
    checkpoints terminal child state and schedules ``_drive``; it never waits
    for another Minecraft request, avoiding a poll-loop/request deadlock.
    """

    def __init__(self, plugin, action_client, checkpoint_dir, feedback=None):
        self.plugin = plugin
        self.action_client = action_client
        self.feedback = feedback
        self.store = SkillCheckpointStore(checkpoint_dir)
        self._definitions: Dict[str, SkillDefinition] = {}
        self._runs: Dict[str, SkillRun] = {}
        self._claims: Dict[str, str] = {}
        self._maid_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._drive_tasks: Dict[str, asyncio.Task] = {}
        self._feedback_tasks: set[asyncio.Task] = set()
        self._notification_retry_tasks: Dict[str, asyncio.Task] = {}
        self._notification_retry_counts: Dict[str, int] = {}
        self._terminal_events: Dict[str, asyncio.Event] = {}
        self._starts_inflight: set[str] = set()
        self._start_events: Dict[str, asyncio.Event] = {}
        self._starting_maids: set[str] = set()
        self._closed = False
        register = getattr(self.action_client, "register_event_consumer", None)
        if callable(register):
            register(self)

    def register(self, definition: SkillDefinition) -> None:
        name = str(getattr(definition, "name", "") or "").strip().lower()
        version = int(getattr(definition, "version", 0) or 0)
        if not name or version < 1:
            raise ValueError("Skill definitions require a name and positive version")
        current = self._definitions.get(name)
        if current is not None and current is not definition:
            raise ValueError(f"Skill definition already registered: {name}")
        self._definitions[name] = definition

    async def load(self):
        loaded = await self.store.load_all()
        self._runs = {run.skill_id: run for run in loaded}
        self._claims.clear()
        for run in loaded:
            self._event_for(run.skill_id)
            if run.current_action_id and not self._fingerprint_valid(run):
                run.status = "BLOCKED"
                run.last_failure_reason = "CHECKPOINT_FINGERPRINT_MISMATCH"
                run.result = {
                    "message": "Child action checkpoint fingerprint does not match its request"
                }
                run.blocked_notification_revision = 0
                await self._persist(run)
            elif run.current_action_id:
                self._remember_claim(run)
        return [run.as_dict() for run in loaded]

    async def start(
        self,
        skill_name,
        maid_id,
        args,
        skill_id=None,
        replace_existing=True,
    ):
        self._ensure_open()
        name = str(skill_name or "").strip().lower()
        maid_id = str(maid_id or "").strip()
        if not maid_id:
            raise ValueError("maid_id is required")
        normalized = self.normalize_args(name, args)
        definition = self._definitions[name]
        canonical_id = self._canonical_skill_id(skill_id or str(uuid.uuid4()))

        lock = self._maid_locks[maid_id]
        async with lock:
            if maid_id in self._starting_maids:
                raise RuntimeError(f"A skill start is already in progress for maid {maid_id}")
            if canonical_id in self._runs:
                existing = self._runs[canonical_id]
                if (existing.skill_name == name and existing.maid_id == maid_id
                        and existing.args == normalized):
                    return existing.as_dict()
                raise ValueError("skill_id already exists with different parameters")
            occupied = self._latest_nonterminal_for_maid(maid_id)
            if occupied is not None and not replace_existing:
                raise RuntimeError(
                    f"Maid {maid_id} already has active skill {occupied.skill_id}"
                )
            self._starting_maids.add(maid_id)

        try:
            if occupied is not None:
                cancelled = await self.cancel(
                    skill_id=occupied.skill_id, maid_id=maid_id
                )
                if cancelled and cancelled.get("status") == "CANCEL_REQUESTED":
                    try:
                        await asyncio.wait_for(
                            self._event_for(occupied.skill_id).wait(),
                            timeout=REPLACEMENT_CANCEL_TIMEOUT,
                        )
                    except asyncio.TimeoutError as exc:
                        raise RuntimeError(
                            "Timed out waiting for the existing skill child to terminate"
                        ) from exc
                terminal_old = self._runs.get(occupied.skill_id)
                if terminal_old is not None and not terminal_old.terminal:
                    raise RuntimeError(
                        "Existing skill did not reach a terminal state"
                    )
            async with lock:
                # A different caller may have completed a start while the old
                # child cancellation was in flight.
                conflict = self._latest_nonterminal_for_maid(maid_id)
                if conflict is not None:
                    raise RuntimeError(
                        f"Maid {maid_id} already has active skill {conflict.skill_id}"
                    )
                now = time.time()
                run = SkillRun(
                    skill_id=canonical_id,
                    maid_id=maid_id,
                    skill_name=name,
                    skill_version=int(definition.version),
                    args=dict(normalized),
                    created_at=now,
                    updated_at=now,
                )
                self._runs[run.skill_id] = run
                self._event_for(run.skill_id)
                try:
                    definition.initialize(run)
                    run.status = "RUNNING"
                except Exception as exc:
                    run.status = "FAILED"
                    run.last_failure_reason = "SKILL_INITIALIZATION_FAILED"
                    run.result = {"message": str(exc)}
                await self._persist(run)
        finally:
            async with lock:
                self._starting_maids.discard(maid_id)

        if not run.terminal:
            await self._ensure_drive(run.skill_id)
        else:
            await self._notify_finished(run)
        return run.as_dict()

    async def cancel(self, skill_id="", maid_id=""):
        skill_id = str(skill_id or "").strip()
        maid_id = str(maid_id or "").strip()
        run = self._runs.get(skill_id) if skill_id else self._latest_nonterminal_for_maid(maid_id)
        if run is None:
            raise ValueError("No matching active skill")
        lock = self._maid_locks[run.maid_id]
        async with lock:
            run = self._runs.get(run.skill_id)
            if run is None or run.terminal:
                return run.as_dict() if run else None
            run.status = "CANCEL_REQUESTED"
            child_action_id = run.current_action_id
            start_inflight = child_action_id in self._starts_inflight
            await self._persist(run)

        if start_inflight:
            try:
                await asyncio.wait_for(
                    self._start_event(child_action_id).wait(),
                    timeout=REPLACEMENT_CANCEL_TIMEOUT,
                )
            except asyncio.TimeoutError as exc:
                raise RuntimeError(
                    "Timed out waiting for the child action start request to settle"
                ) from exc

            async with lock:
                run = self._runs.get(run.skill_id)
                if run is None or run.terminal:
                    return run.as_dict() if run else None
                if run.pending_terminal:
                    snapshot = run.as_dict()
                    self._ensure_drive_background(run.skill_id)
                    return snapshot
                child_action_id = run.current_action_id

        cancel_result = None
        if child_action_id:
            cancel_result = await self.action_client.cancel_action(
                child_action_id, maid_id=run.maid_id
            )

        async with lock:
            run = self._runs[run.skill_id]
            if run.terminal:
                return run.as_dict()
            if (child_action_id and cancel_result is not None
                    and not self._start_accepted(cancel_result)):
                run.status = "WAITING_ACTION"
                run.last_failure_reason = str(
                    cancel_result.get("error_code") or "CANCEL_REJECTED"
                )
                await self._persist(run)
                raise RuntimeError(
                    str(cancel_result.get("error") or "Child action cancellation failed")
                )
            if child_action_id:
                # Java owns child termination and lease cleanup.  The skill
                # becomes CANCELLED only after maid_action_finished arrives.
                return run.as_dict()
            if not run.terminal:
                run.status = "CANCELLED"
                run.last_failure_reason = "REQUESTED"
                run.result = {
                    "message": "Skill cancellation requested",
                    "child_cancel": dict(cancel_result or {}),
                }
                await self._persist(run)
            snapshot = run.as_dict()
        await self._notify_finished(run)
        return snapshot

    async def reconcile(self):
        """Adopt live children and resume deterministic checkpoint states."""
        if self._closed:
            return {"success": False, "error": "SkillRunner is closed"}
        if hasattr(self.plugin, "connected") and not self.plugin.connected:
            return {"success": False, "error": "Not connected to Minecraft"}

        active_items = await self.action_client.list_active_actions(maid_id="")
        active = {
            str(item.get("action_id") or ""): dict(item)
            for item in active_items if isinstance(item, Mapping)
            and str(item.get("action_id") or "")
        }
        adopted = []
        recovered = []
        lost = []
        unresolved = []
        scheduled = []

        for run in list(self._runs.values()):
            if run.status == "BLOCKED":
                if run.blocked_notification_revision <= 0:
                    scheduled.append(self._ensure_drive(run.skill_id))
                continue
            if run.terminal:
                continue
            if run.pending_terminal:
                scheduled.append(self._ensure_drive(run.skill_id))
                continue
            action_id = run.current_action_id
            if not action_id:
                scheduled.append(self._ensure_drive(run.skill_id))
                continue
            if not self._fingerprint_valid(run):
                lock = self._maid_locks[run.maid_id]
                async with lock:
                    current = self._runs.get(run.skill_id)
                    if current and not current.terminal:
                        current.status = "BLOCKED"
                        current.last_failure_reason = "CHECKPOINT_FINGERPRINT_MISMATCH"
                        current.result = {
                            "message": "Child action checkpoint fingerprint does not match its request"
                        }
                        current.blocked_notification_revision = 0
                        await self._persist(current)
                scheduled.append(self._ensure_drive(run.skill_id))
                continue
            self._remember_claim(run)
            snapshot = active.get(action_id)
            if snapshot is None:
                snapshot = await self.action_client.get_action_status(action_id)
            if snapshot and self._is_action_terminal(snapshot):
                await self.on_action_event(
                    "maid_action_finished", snapshot, dict(snapshot)
                )
                task = self._drive_tasks.get(run.skill_id)
                if task is not None:
                    scheduled.append(task)
                recovered.append(action_id)
                continue
            if snapshot and bool(snapshot.get("_query_error")):
                unresolved.append(action_id)
                continue
            if snapshot:
                lock = self._maid_locks[run.maid_id]
                async with lock:
                    current = self._runs.get(run.skill_id)
                    if current and current.current_action_id == action_id:
                        current.status = "WAITING_ACTION"
                        current.current_action_generation = self._integer(
                            snapshot.get("generation"),
                            current.current_action_generation,
                        )
                        await self._persist(current)
                        adopted.append(action_id)
                continue
            if run.status == "STARTING_ACTION" and run.current_action_request:
                # The checkpoint precedes the request. Reusing the same action ID
                # and fingerprint is the only safe automatic resubmission.
                scheduled.append(self._ensure_drive(run.skill_id))
                continue
            synthetic = {
                "action_id": action_id,
                "maid_id": run.maid_id,
                "generation": run.current_action_generation,
                "sequence": 0,
                "kind": run.current_action_request.get("kind", ""),
                "status": "FAILED",
                "stage": "RECONCILING",
                "end_reason": "SERVER_STATE_LOST",
                "result": {"message": "The server no longer has this child action"},
            }
            await self.on_action_event("maid_action_finished", synthetic, synthetic)
            task = self._drive_tasks.get(run.skill_id)
            if task is not None:
                scheduled.append(task)
            lost.append(action_id)

        if scheduled:
            await asyncio.gather(*scheduled, return_exceptions=True)
        return {
            "success": True,
            "adopted": adopted,
            "recovered": recovered,
            "lost": lost,
            "unresolved": unresolved,
            "skills": self.list_skills(),
        }

    async def close(self):
        self._closed = True
        unregister = getattr(self.action_client, "unregister_event_consumer", None)
        if callable(unregister):
            unregister(self)
        tasks = [task for task in self._drive_tasks.values() if not task.done()]
        tasks.extend(task for task in self._feedback_tasks if not task.done())
        tasks.extend(
            task for task in self._notification_retry_tasks.values()
            if not task.done()
        )
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._start_events.clear()
        self._starts_inflight.clear()
        # Every transition is synchronously checkpointed.  Deliberately do not
        # cancel current_action_id: Java remains authoritative across restart.

    def get_status(self, skill_id):
        run = self._runs.get(str(skill_id or "").strip())
        return run.as_dict() if run else None

    def registered_skills(self):
        """Return the public, immutable names accepted by ``start``."""
        return tuple(sorted(self._definitions))

    def normalize_args(self, skill_name, args):
        """返回与 ``start`` 使用相同的规范化参数。"""
        name = str(skill_name or "").strip().lower()
        definition = self._definitions.get(name)
        if definition is None:
            raise ValueError(f"Unknown skill: {name or '<empty>'}")
        return definition.normalize_args(dict(args or {}))

    async def wait_terminal(self, skill_id, timeout=10.0):
        """Wait for one durable Skill terminal without polling checkpoints."""
        canonical = str(skill_id or "").strip()
        run = self._runs.get(canonical)
        if run is None:
            return None
        if not run.terminal:
            await asyncio.wait_for(
                self._event_for(canonical).wait(), timeout=float(timeout)
            )
        current = self._runs.get(canonical)
        return current.as_dict() if current else None

    def list_skills(self, maid_id="", include_terminal=True):
        maid_id = str(maid_id or "").strip()
        runs = [
            run for run in self._runs.values()
            if (not maid_id or run.maid_id == maid_id)
            and (include_terminal or not run.terminal)
        ]
        runs.sort(key=lambda item: (item.updated_at, item.created_at), reverse=True)
        return [run.as_dict() for run in runs]

    def claims(self, action_id):
        return self._claims.get(str(action_id or "").strip())

    async def on_action_event(self, event_type, record_snapshot, raw_payload):
        """Checkpoint a claimed terminal and return without Minecraft I/O."""
        snapshot = self._snapshot(record_snapshot)
        action_id = str(snapshot.get("action_id") or "")
        skill_id = self._claims.get(action_id)
        if not skill_id:
            return False
        run = self._runs.get(skill_id)
        if run is None:
            return False
        if not self._is_action_terminal(snapshot):
            lock = self._maid_locks[run.maid_id]
            async with lock:
                run = self._runs.get(skill_id)
                if run is None or run.current_action_id != action_id:
                    return True
                generation = self._integer(snapshot.get("generation"), 0)
                if (run.current_action_generation > 0 and generation > 0
                        and generation < run.current_action_generation):
                    return True
                generation_changed = (
                    generation > 0
                    and generation > run.current_action_generation
                )
                if generation_changed:
                    run.current_action_generation = generation
                progress_changed = self._observe_child_progress(run, snapshot)
                changed = generation_changed or progress_changed
                if changed:
                    await self._persist(run)
                progress_snapshot = run.as_dict()
            callback = getattr(self.feedback, "progress", None)
            if callable(callback):
                progress_snapshot["child_action"] = snapshot
                task = asyncio.create_task(callback(progress_snapshot))
                self._feedback_tasks.add(task)
                task.add_done_callback(self._feedback_task_done)
            return True

        lock = self._maid_locks[run.maid_id]
        async with lock:
            run = self._runs.get(skill_id)
            if run is None or run.current_action_id != action_id:
                return True
            generation = self._integer(snapshot.get("generation"), 0)
            if (run.current_action_generation > 0 and generation > 0
                    and generation < run.current_action_generation):
                return True
            if run.pending_terminal:
                return True
            run.pending_terminal = snapshot
            if generation > 0:
                run.current_action_generation = generation
            await self._persist(run)

        self._ensure_drive_background(skill_id)
        return True

    def _observe_child_progress(
        self, run: SkillRun, snapshot: Mapping[str, Any]
    ) -> bool:
        """Project Java autonomous-mining telemetry into the Skill checkpoint."""
        if str(snapshot.get("kind") or "").lower() != "autonomous_mining":
            return False
        detail = snapshot.get("detail")
        detail = dict(detail) if isinstance(detail, Mapping) else {}
        changed = False
        reported_count = self._integer(detail.get("collected_count"), -1)
        if reported_count >= 0 and reported_count > run.collected_count:
            run.collected_count = reported_count
            changed = True
        stage = str(snapshot.get("stage") or detail.get("phase") or "")
        previous_stage = str(run.result.get("stage") or "")
        if stage and stage != previous_stage:
            run.result["stage"] = stage
            changed = True
        if detail and detail != run.result.get("java_progress"):
            run.result["java_progress"] = detail
            changed = True
        if run.result.get("execution_mode") != "autonomous":
            run.result["execution_mode"] = "autonomous"
            run.result["planner_owner"] = "java"
            changed = True
        return changed

    async def _drive(self, skill_id: str):
        while not self._closed:
            run = self._runs.get(skill_id)
            if run is None:
                return
            lock = self._maid_locks[run.maid_id]
            action_request = None
            feedback_kind = None

            async with lock:
                run = self._runs.get(skill_id)
                if run is None:
                    return
                if run.status == "BLOCKED":
                    if run.blocked_notification_revision <= 0:
                        feedback_kind = "blocked"
                    else:
                        return
                elif run.terminal:
                    return
                elif run.pending_terminal:
                    self._forget_claim(run.current_action_id, run.skill_id)
                    terminal = dict(run.pending_terminal)
                    child_status = str(terminal.get("status") or "").upper()
                    cancel_requested = run.status == "CANCEL_REQUESTED"
                    if (cancel_requested
                            or child_status in {"CANCELLED", "SUPERSEDED"}):
                        self._clear_child(run)
                        run.status = "CANCELLED"
                        run.decision_required = False
                        run.decision_context = {}
                        run.last_failure_reason = str(
                            terminal.get("end_reason")
                            or ("REQUESTED" if cancel_requested
                                else child_status)
                        ).upper()
                        run.result = {
                            "message": "Skill stopped after child cancellation or supersession",
                            "child_terminal": terminal,
                        }
                        await self._persist(run)
                        feedback_kind = "finished"
                        directive = None
                        outcome = "finished"
                    else:
                        directive = self._next_directive(run, terminal)
                        self._clear_child(run)
                        outcome = await self._apply_directive_locked(run, directive)
                    if outcome == "action":
                        action_request = dict(run.current_action_request)
                    elif outcome in {"blocked", "finished"}:
                        feedback_kind = outcome
                    elif outcome == "continue":
                        continue
                    else:
                        return
                elif run.status == "STARTING_ACTION":
                    if run.current_action_id in self._starts_inflight:
                        return
                    if not run.current_action_request:
                        run.status = "FAILED"
                        run.last_failure_reason = "START_CHECKPOINT_INCOMPLETE"
                        run.result = {"message": "Missing current_action_request"}
                        await self._persist(run)
                        feedback_kind = "finished"
                    else:
                        self._starts_inflight.add(run.current_action_id)
                        self._start_event(run.current_action_id).clear()
                        action_request = dict(run.current_action_request)
                elif run.status in {"PENDING", "RUNNING"}:
                    directive = self._next_directive(run, None)
                    outcome = await self._apply_directive_locked(run, directive)
                    if outcome == "action":
                        action_request = dict(run.current_action_request)
                    elif outcome in {"blocked", "finished"}:
                        feedback_kind = outcome
                    elif outcome == "continue":
                        continue
                    else:
                        return
                else:
                    return

            if feedback_kind:
                if feedback_kind == "blocked":
                    await self._notify_blocked(run)
                else:
                    await self._notify_finished(run)
                return
            if action_request is None:
                return

            action_id = str(action_request.get("action_id") or "")
            try:
                response = await self._start_child(run, action_request)
            finally:
                # ``runner.start`` may be wrapped in a caller deadline.  If that
                # deadline cancels this drive task while the transport is
                # waiting, cancellation must never leave the skill permanently
                # marked as an in-flight STARTING_ACTION.
                self._starts_inflight.discard(action_id)
                self._start_event(action_id).set()
            async with lock:
                current = self._runs.get(skill_id)
                if current is None or current.current_action_id != action_id:
                    continue
                # A terminal event may have reached the poll loop before the
                # start response coroutine resumed.
                if current.pending_terminal:
                    continue
                if self._start_accepted(response):
                    cancel_requested = current.status == "CANCEL_REQUESTED"
                    generation = self._integer(response.get("generation"), 0)
                    if generation > 0:
                        current.current_action_generation = generation
                    if self._is_action_terminal(response):
                        current.pending_terminal = dict(response)
                    else:
                        current.status = (
                            "CANCEL_REQUESTED"
                            if cancel_requested else "WAITING_ACTION"
                        )
                    await self._persist(current)
                    if not current.pending_terminal:
                        return
                    continue
                if self._start_outcome_uncertain(response):
                    current.status = (
                        "CANCEL_REQUESTED"
                        if current.status == "CANCEL_REQUESTED"
                        else "STARTING_ACTION"
                    )
                    current.last_failure_reason = str(
                        response.get("error_code") or "START_REQUEST_UNCERTAIN"
                    )
                    await self._persist(current)
                    self._remember_claim(current)
                    return
                current.pending_terminal = self._rejected_snapshot(current, response)
                await self._persist(current)

    async def _apply_directive_locked(self, run: SkillRun, directive):
        if isinstance(directive, StartAction):
            request = directive.request_payload()
            action_id = str(uuid.uuid4())
            request["action_id"] = action_id
            run.status = "STARTING_ACTION"
            run.current_action_id = action_id
            run.current_action_generation = 0
            run.current_action_request = request
            run.current_action_fingerprint = action_fingerprint(
                run.maid_id, action_id, request
            )
            run.pending_terminal = {}
            run.decision_required = False
            run.decision_context = {}
            run.blocked_notification_revision = 0
            await self._persist(run)
            self._claims[action_id] = run.skill_id
            self._starts_inflight.add(action_id)
            self._start_event(action_id).clear()
            return "action"
        if isinstance(directive, Complete):
            run.status = "SUCCEEDED"
            run.result = dict(directive.result or {})
            run.warnings = list(directive.warnings or ())
            run.last_failure_reason = ""
            run.decision_required = False
            run.decision_context = {}
            await self._persist(run)
            return "finished"
        if isinstance(directive, Blocked):
            run.status = "BLOCKED"
            run.last_failure_reason = str(directive.reason or "BLOCKED")
            run.result = dict(directive.result or {})
            run.warnings = list(directive.warnings or ())
            run.decision_required = bool(
                run.result.get("decision_required", True)
            )
            context = run.result.get("decision")
            run.decision_context = (
                dict(context) if isinstance(context, Mapping) else {}
            )
            run.blocked_notification_revision = 0
            await self._persist(run)
            logger = getattr(self.plugin, "logger", None)
            if logger is not None:
                logger.info(
                    "[MaidSkill] classified BLOCKED skill_id=%s reason=%s "
                    "revision=%s",
                    run.skill_id, run.last_failure_reason, run.revision,
                )
            return "blocked"
        if isinstance(directive, Fail):
            run.status = "FAILED"
            run.last_failure_reason = str(directive.reason or "FAILED")
            run.result = dict(directive.result or {})
            run.warnings = list(directive.warnings or ())
            run.decision_required = False
            run.decision_context = {}
            await self._persist(run)
            return "finished"
        raise TypeError(f"Unsupported skill directive: {type(directive).__name__}")

    def _next_directive(self, run: SkillRun, terminal_snapshot):
        definition = self._definitions.get(run.skill_name)
        if definition is None:
            return Blocked(
                "SKILL_DEFINITION_MISSING",
                {"message": f"Skill definition is not registered: {run.skill_name}"},
            )
        if int(definition.version) != run.skill_version:
            return Blocked(
                "SKILL_VERSION_MISMATCH",
                {
                    "message": "Checkpoint skill version does not match definition",
                    "checkpoint_version": run.skill_version,
                    "definition_version": int(definition.version),
                },
            )
        try:
            return definition.next_directive(run, terminal_snapshot)
        except Exception as exc:
            return Fail("SKILL_DEFINITION_ERROR", {"message": str(exc)})

    async def _start_child(self, run: SkillRun, request: Mapping[str, Any]):
        try:
            return self._snapshot(await self.action_client.start_action(
                action_id=str(request.get("action_id") or run.current_action_id),
                maid_id=run.maid_id,
                kind=str(request.get("kind") or ""),
                args=dict(request.get("args") or {}),
                timeout_ms=int(request.get("timeout_ms", 0)),
                replace_existing=bool(request.get("replace_existing", True)),
                owner_id=run.skill_id,
                feedback_policy="internal",
            ))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return {
                "success": False,
                "accepted": False,
                "error_code": "REQUEST_FAILED",
                "error": str(exc),
            }

    async def _notify_blocked(self, run: SkillRun):
        if run.blocked_notification_revision > 0:
            return
        callback = getattr(self.feedback, "blocked", None)
        if callable(callback):
            await callback(run.as_dict())
        lock = self._maid_locks[run.maid_id]
        async with lock:
            current = self._runs.get(run.skill_id)
            if (current is not None and current.status == "BLOCKED"
                    and current.blocked_notification_revision <= 0):
                current.blocked_notification_revision = current.revision + 1
                await self._persist(current)

    async def _notify_finished(self, run: SkillRun):
        callback = getattr(self.feedback, "finished", None)
        if callable(callback):
            await callback(run.as_dict())

    def _feedback_task_done(self, task: asyncio.Task):
        self._feedback_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            pass

    async def _persist(self, run: SkillRun):
        run.revision += 1
        run.updated_at = time.time()
        await self.store.save(run)
        if run.terminal:
            self._event_for(run.skill_id).set()

    async def _ensure_drive(self, skill_id: str):
        task = self._drive_tasks.get(skill_id)
        if task is None or task.done():
            task = self._create_drive_task(skill_id)
        return await task

    def _ensure_drive_background(self, skill_id: str):
        task = self._drive_tasks.get(skill_id)
        if task is None or task.done():
            task = self._create_drive_task(skill_id)
        return task

    def _create_drive_task(self, skill_id: str):
        task = asyncio.create_task(self._drive(skill_id))
        self._drive_tasks[skill_id] = task
        task.add_done_callback(
            lambda completed, key=skill_id: self._drive_task_done(key, completed)
        )
        return task

    def _drive_task_done(self, skill_id: str, task: asyncio.Task):
        if task.cancelled():
            return
        try:
            exception = task.exception()
        except (asyncio.CancelledError, Exception):
            return
        run = self._runs.get(skill_id)
        if exception is None:
            if run is None or run.blocked_notification_revision > 0:
                self._notification_retry_counts.pop(skill_id, None)
            return

        logger = getattr(self.plugin, "logger", None)
        if logger is not None:
            logger.error(
                "[MaidSkill] background drive failed for %s: %s",
                skill_id, exception,
            )
        if (self._closed or run is None or run.status != "BLOCKED"
                or run.blocked_notification_revision > 0):
            return
        attempt = self._notification_retry_counts.get(skill_id, 0)
        if attempt >= len(BLOCKED_NOTIFICATION_RETRY_DELAYS):
            if logger is not None:
                logger.error(
                    "[MaidSkill] blocked feedback retries exhausted "
                    "skill_id=%s attempts=%s",
                    skill_id, attempt,
                )
            return
        existing = self._notification_retry_tasks.get(skill_id)
        if existing is not None and not existing.done():
            return
        self._notification_retry_counts[skill_id] = attempt + 1
        if logger is not None:
            logger.warning(
                "[MaidSkill] scheduling blocked feedback retry "
                "skill_id=%s attempt=%s delay=%ss",
                skill_id, attempt + 1,
                BLOCKED_NOTIFICATION_RETRY_DELAYS[attempt],
            )
        retry = asyncio.create_task(self._retry_blocked_notification(
            skill_id, BLOCKED_NOTIFICATION_RETRY_DELAYS[attempt]
        ))
        self._notification_retry_tasks[skill_id] = retry
        retry.add_done_callback(
            lambda completed, key=skill_id:
            self._notification_retry_done(key, completed)
        )

    async def _retry_blocked_notification(self, skill_id: str, delay: float):
        await asyncio.sleep(delay)
        run = self._runs.get(skill_id)
        if (self._closed or run is None or run.status != "BLOCKED"
                or run.blocked_notification_revision > 0):
            return
        current = asyncio.current_task()
        if self._notification_retry_tasks.get(skill_id) is current:
            self._notification_retry_tasks.pop(skill_id, None)
        self._ensure_drive_background(skill_id)

    def _notification_retry_done(self, skill_id: str, task: asyncio.Task):
        if self._notification_retry_tasks.get(skill_id) is task:
            self._notification_retry_tasks.pop(skill_id, None)
        if task.cancelled():
            return
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            pass

    def _remember_claim(self, run: SkillRun):
        if not run.current_action_id:
            return
        self._claims[run.current_action_id] = run.skill_id
        claim = getattr(self.action_client, "claim_action", None)
        if callable(claim):
            claim(
                run.current_action_id,
                run.skill_id,
                feedback_policy="internal",
            )

    def _event_for(self, skill_id: str) -> asyncio.Event:
        event = self._terminal_events.get(skill_id)
        if event is None:
            event = asyncio.Event()
            self._terminal_events[skill_id] = event
        return event

    def _start_event(self, action_id: str) -> asyncio.Event:
        event = self._start_events.get(action_id)
        if event is None:
            event = asyncio.Event()
            self._start_events[action_id] = event
        return event

    @staticmethod
    def _fingerprint_valid(run: SkillRun) -> bool:
        if not run.current_action_id:
            return True
        if not run.current_action_request or not run.current_action_fingerprint:
            return False
        expected = action_fingerprint(
            run.maid_id,
            run.current_action_id,
            run.current_action_request,
        )
        return expected == run.current_action_fingerprint

    def _forget_claim(self, action_id: str, skill_id: str):
        if not action_id:
            return
        if self._claims.get(action_id) == skill_id:
            self._claims.pop(action_id, None)
        self._start_events.pop(action_id, None)
        self._starts_inflight.discard(action_id)
        release = getattr(self.action_client, "release_action", None)
        if callable(release):
            release(action_id, skill_id)

    @staticmethod
    def _clear_child(run: SkillRun):
        run.current_action_id = ""
        run.current_action_generation = 0
        run.current_action_fingerprint = ""
        run.current_action_request = {}
        run.pending_terminal = {}
        run.status = "RUNNING"

    def _latest_nonterminal_for_maid(self, maid_id: str) -> Optional[SkillRun]:
        if not maid_id:
            return None
        runs = [
            run for run in self._runs.values()
            if run.maid_id == maid_id and not run.terminal
        ]
        return max(runs, key=lambda item: item.updated_at) if runs else None

    @staticmethod
    def _snapshot(value: Any) -> Dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        converter = getattr(value, "as_dict", None)
        if callable(converter):
            converted = converter()
            return dict(converted) if isinstance(converted, Mapping) else {}
        return {}

    @staticmethod
    def _integer(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _is_action_terminal(snapshot: Mapping[str, Any]) -> bool:
        return str(snapshot.get("status") or "").upper() in ACTION_TERMINAL_STATUSES

    @staticmethod
    def _start_accepted(response: Mapping[str, Any]) -> bool:
        return bool(response.get("success", response.get("accepted", False))) \
            and bool(response.get("accepted", True))

    @staticmethod
    def _start_outcome_uncertain(response: Mapping[str, Any]) -> bool:
        return str(response.get("error_code") or "").upper() in {
            "REQUEST_FAILED", "NOT_CONNECTED", "TIMEOUT",
        }

    @staticmethod
    def _rejected_snapshot(run: SkillRun, response: Mapping[str, Any]):
        reason = str(
            response.get("error_code") or response.get("end_reason")
            or "ACTION_REJECTED"
        ).upper()
        message = str(response.get("error") or response.get("message") or reason)
        return {
            "action_id": run.current_action_id,
            "maid_id": run.maid_id,
            "generation": run.current_action_generation,
            "sequence": 0,
            "kind": run.current_action_request.get("kind", ""),
            "status": "FAILED",
            "stage": "START_REJECTED",
            "end_reason": reason,
            "result": {"message": message, "start_response": dict(response)},
        }

    @staticmethod
    def _canonical_skill_id(value: Any) -> str:
        text = str(value or "").strip().lower()
        try:
            parsed = uuid.UUID(text)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("skill_id must be a UUID") from exc
        canonical = str(parsed)
        if text != canonical:
            raise ValueError("skill_id must use canonical UUID form")
        return canonical

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError("SkillRunner is closed")
