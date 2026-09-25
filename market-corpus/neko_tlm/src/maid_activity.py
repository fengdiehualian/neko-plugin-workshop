"""Unified, server-reconciled orchestration for maid activities.

The director is deliberately a Python orchestration layer.  Minecraft remains
authoritative for low-level actions and TLM task state, while ``SkillRunner``
remains authoritative for checkpointed skills.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
import uuid
from collections import OrderedDict, defaultdict
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional

from . import task_resolver
from .maid_agent.models import TERMINAL_STATUSES as ACTION_TERMINAL_STATUSES
from .maid_agent.skills.base import TERMINAL_SKILL_STATUSES

SWITCH_POLICIES = frozenset({
    "cancel_then_switch",
    "after_current",
    "reject_if_busy",
})
ACTIVITY_TYPES = frozenset({"tlm_task", "agent_action", "skill", "idle"})
_INTERNAL_ACTIVITY_TYPES = ACTIVITY_TYPES | {"preserve_tlm"}


class MaidActivityDirector:
    """Serialize and reconcile activity changes independently for each maid.

    ``request_id`` is the idempotency key.  Reusing it with identical input
    returns the same operation snapshot; reusing it with different input is an
    error.  Idempotency is intentionally process-local, matching deferred
    ``after_current`` transitions, which are also process-local in v1.
    """

    def __init__(
        self,
        plugin,
        *,
        action_service=None,
        skill_runner=None,
        status_provider: Optional[Callable[[str], Awaitable[Mapping[str, Any]]]] = None,
        task_switcher: Optional[
            Callable[[str, str], Awaitable[Mapping[str, Any]]]
        ] = None,
        poll_interval: float = 0.25,
        transition_timeout: float = 10.0,
        request_timeout: float = 5.0,
        idle_task: str = "idle",
        idempotency_limit: int = 256,
    ):
        self.plugin = plugin
        self._injected_action_service = action_service
        self._injected_skill_runner = skill_runner
        self._status_provider = status_provider
        self._task_switcher = task_switcher
        self.poll_interval = max(0.01, float(poll_interval))
        self.transition_timeout = max(0.1, float(transition_timeout))
        self.request_timeout = max(0.1, float(request_timeout))
        self.idle_task = str(idle_task or "idle").strip()
        self.idempotency_limit = max(8, int(idempotency_limit))
        self._maid_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._operations: "OrderedDict[tuple[str, str], Dict[str, Any]]" = OrderedDict()
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._watchers: Dict[str, asyncio.Task] = {}

    async def get_activity(self, maid_id: str = "") -> Dict[str, Any]:
        maid_id = self._resolve_maid_id(maid_id)
        if not maid_id:
            return self._error("NO_MAID_ASSIGNED", "No maid assigned")

        maid_status, status_error = await self._get_maid_status(maid_id)
        active_actions, action_error = await self._query_active_actions(maid_id)
        active_skills = self._active_skills(maid_id)
        active_actions, active_skills = self._fold_skill_children(
            active_actions, active_skills
        )
        primary = self._primary_activity(maid_status, active_actions, active_skills)
        result = {
            "success": status_error is None and action_error is None,
            "maid_id": maid_id,
            "activity": primary,
            "tlm_task": self._tlm_projection(
                maid_status, suppressed=bool(active_actions or active_skills)
            ),
            "active_actions": active_actions,
            "active_skills": active_skills,
            "busy": bool(active_actions or active_skills),
        }
        pending = self._pending.get(maid_id)
        if pending is not None:
            result["pending_transition"] = self._public_pending(pending)
        if status_error is not None:
            result["status_error"] = status_error
        if action_error is not None:
            result["action_query_error"] = action_error
        return result

    async def get_capabilities(self, maid_id: str = "") -> Dict[str, Any]:
        maid_id = self._resolve_maid_id(maid_id)
        if not maid_id:
            return self._error("NO_MAID_ASSIGNED", "No maid assigned")
        maid_status, status_error = await self._get_maid_status(maid_id)
        tasks = self._normalize_tasks(maid_status.get("available_tasks", []))

        service = self._action_service
        registry = getattr(service, "registry", None)
        kinds = sorted(getattr(registry, "SUPPORTED_KINDS", ()) or ())

        runner = self._skill_runner
        registered = getattr(runner, "registered_skills", None)
        skills = sorted(str(name) for name in registered()) if callable(registered) else []
        result = {
            "success": status_error is None,
            "maid_id": maid_id,
            "activity_types": sorted(ACTIVITY_TYPES),
            "switch_policies": sorted(SWITCH_POLICIES),
            "tlm_tasks": tasks,
            "agent_actions": kinds,
            "skills": skills,
            "supports_after_current": True,
            "after_current_persistence": "process_memory",
        }
        if status_error is not None:
            result["status_error"] = status_error
        return result

    async def execute_body_mutation(
        self,
        mutation: Callable[[], Awaitable[Any]],
        *,
        maid_id: str = "",
        operation: str = "body_control",
    ) -> Dict[str, Any]:
        """Run one legacy body-control command only while no controller owns the maid.

        Follow/sit/schedule and hand changes overlap fields protected by Java's
        body and hand leases.  Serializing their actual transport call under
        the same per-maid lock prevents a check-then-act race with activity
        starts, while rejecting mutation during an existing controller avoids
        deterministic USER_OVERRIDE/HAND_CONFLICT termination.
        """
        maid_id = self._resolve_maid_id(maid_id)
        if not maid_id:
            return self._error("NO_MAID_ASSIGNED", "No maid assigned")
        lock = self._maid_locks[maid_id]
        async with lock:
            pending = self._pending.get(maid_id)
            if pending is not None:
                return self._error(
                    "PENDING_TRANSITION_EXISTS",
                    "Body control is blocked while an activity switch is queued",
                    operation=operation,
                    pending_transition=self._public_pending(pending),
                )
            current = await self.get_activity(maid_id)
            if not current.get("success", False):
                return self._error(
                    "ACTIVITY_STATE_UNAVAILABLE",
                    "Cannot safely mutate maid body state",
                    operation=operation,
                    current_activity=current,
                )
            if current.get("busy"):
                return self._error(
                    "MAID_BUSY",
                    "Stop the current Skill/Agent action before changing body state",
                    operation=operation,
                    current_activity=current,
                )
            try:
                value = mutation()
                if inspect.isawaitable(value):
                    value = await value
            except Exception as exc:
                return self._error(
                    "BODY_MUTATION_FAILED", str(exc), operation=operation
                )
            return {"success": True, "operation": operation, "result": value}

    async def set_activity(
        self,
        activity: Mapping[str, Any],
        *,
        maid_id: str = "",
        switch_policy: str = "cancel_then_switch",
        request_id: str = "",
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        maid_id = self._resolve_maid_id(maid_id)
        if not maid_id:
            return self._error("NO_MAID_ASSIGNED", "No maid assigned")
        try:
            target = self._normalize_target(activity)
            policy = self._normalize_policy(switch_policy)
            operation_timeout = self._normalize_timeout(timeout)
        except (TypeError, ValueError) as exc:
            return self._error("INVALID_ACTIVITY_ARGUMENTS", str(exc))

        request_id = str(request_id or uuid.uuid4()).strip()
        operation_key = (maid_id, request_id)
        fingerprint = self._fingerprint(maid_id, target, policy, operation_timeout)
        lock = self._maid_locks[maid_id]
        async with lock:
            prior = self._operations.get(operation_key)
            if prior is not None:
                if prior["fingerprint"] != fingerprint:
                    return self._error(
                        "IDEMPOTENCY_CONFLICT",
                        "request_id was already used with different activity parameters",
                        request_id=request_id,
                    )
                self._operations.move_to_end(operation_key)
                return dict(prior["result"])

            existing_pending = self._pending.get(maid_id)
            if existing_pending is not None:
                if policy == "cancel_then_switch":
                    self._supersede_pending_transition(existing_pending, request_id)
                else:
                    return self._remember(
                        operation_key,
                        fingerprint,
                        self._error(
                            "PENDING_TRANSITION_EXISTS",
                            "This maid already has an after_current transition pending",
                            request_id=request_id,
                            pending_transition=self._public_pending(existing_pending),
                        ),
                    )

            current = await self.get_activity(maid_id)
            if not current.get("success", False):
                return self._remember(
                    operation_key,
                    fingerprint,
                    self._error(
                        "ACTIVITY_STATE_UNAVAILABLE",
                        "Cannot safely arbitrate activity while authoritative state is unavailable",
                        request_id=request_id,
                        current_activity=current,
                    ),
                )
            if self._target_is_current(target, current):
                return self._remember(
                    operation_key,
                    fingerprint,
                    {
                        "success": True,
                        "request_id": request_id,
                        "status": "ALREADY_ACTIVE",
                        "maid_id": maid_id,
                        "target": target,
                        "final_activity": current,
                    },
                )

            busy = bool(current.get("active_actions") or current.get("active_skills"))
            if busy and policy == "reject_if_busy":
                return self._remember(
                    operation_key,
                    fingerprint,
                    self._error(
                        "MAID_BUSY",
                        "Maid has an active Agent action or Skill",
                        request_id=request_id,
                        current_activity=current,
                    ),
                )

            if busy and policy == "after_current":
                pending = {
                    "request_id": request_id,
                    "operation_key": operation_key,
                    "fingerprint": fingerprint,
                    "maid_id": maid_id,
                    "target": target,
                    "status": "WAITING_CURRENT",
                    "created_at": time.time(),
                    "timeout": operation_timeout,
                }
                self._pending[maid_id] = pending
                result = {
                    "success": True,
                    "accepted": True,
                    "request_id": request_id,
                    "status": "QUEUED_AFTER_CURRENT",
                    "maid_id": maid_id,
                    "target": target,
                    "current_activity": current,
                }
                self._remember(operation_key, fingerprint, result)
                watcher = asyncio.create_task(self._watch_after_current(pending))
                self._watchers[maid_id] = watcher
                watcher.add_done_callback(
                    lambda task, mid=maid_id: self._watcher_done(mid, task)
                )
                return dict(result)

            deadline = time.monotonic() + operation_timeout
            try:
                result = await asyncio.wait_for(
                    self._execute_transition(
                        maid_id, target, policy, request_id, deadline
                    ),
                    timeout=operation_timeout,
                )
            except asyncio.TimeoutError:
                result = self._error(
                    "ACTIVITY_SWITCH_TIMEOUT",
                    "Activity transition exceeded its overall deadline",
                    request_id=request_id,
                    final_activity=await self.get_activity(maid_id),
                )
            except Exception as exc:
                result = self._error(
                    "ACTIVITY_SWITCH_FAILED",
                    str(exc),
                    request_id=request_id,
                    final_activity=await self.get_activity(maid_id),
                )
            return self._remember(operation_key, fingerprint, result)

    async def stop(
        self,
        *,
        maid_id: str = "",
        switch_policy: str = "cancel_then_switch",
        request_id: str = "",
        timeout: Optional[float] = None,
        switch_to_idle: bool = True,
    ) -> Dict[str, Any]:
        return await self.set_activity(
            (
                {"type": "idle", "task": self.idle_task}
                if switch_to_idle else {"type": "preserve_tlm"}
            ),
            maid_id=maid_id,
            switch_policy=switch_policy,
            request_id=request_id,
            timeout=timeout,
        )

    async def close(self) -> None:
        watchers = [task for task in self._watchers.values() if not task.done()]
        for task in watchers:
            task.cancel()
        if watchers:
            await asyncio.gather(*watchers, return_exceptions=True)
        self._watchers.clear()
        self._pending.clear()
        # The plugin object can be restarted/reconnected in the same process.
        # Idempotency results describe the previous lifecycle's authoritative
        # state and must not be replayed after close.
        self._operations.clear()

    async def _execute_transition(
        self,
        maid_id: str,
        target: Dict[str, Any],
        policy: str,
        request_id: str,
        deadline: float,
    ) -> Dict[str, Any]:
        before = await self.get_activity(maid_id)
        if not before.get("success", False):
            return self._error(
                "ACTIVITY_STATE_UNAVAILABLE",
                "Cannot safely transition while authoritative state is unavailable",
                request_id=request_id,
                current_activity=before,
            )
        if policy == "reject_if_busy" and before.get("busy"):
            return self._error(
                "MAID_BUSY",
                "Maid became busy before the target could be started",
                request_id=request_id,
                current_activity=before,
            )
        if policy == "cancel_then_switch" and before.get("busy"):
            cancellation = await self._cancel_current(maid_id, before)
            if not cancellation["success"]:
                return self._error(
                    "CANCEL_FAILED",
                    "Failed to request cancellation of the current activity",
                    request_id=request_id,
                    cancellation=cancellation,
                    final_activity=await self.get_activity(maid_id),
                )
            reconciled = await self._wait_for_controllers_terminal(
                maid_id,
                cancellation["action_ids"],
                cancellation["skill_ids"],
                deadline,
            )
            if not reconciled["success"]:
                return self._error(
                    "ACTIVITY_SWITCH_TIMEOUT",
                    "Timed out waiting for Agent/Skill terminal state and lease cleanup",
                    request_id=request_id,
                    reconciliation=reconciled,
                    final_activity=await self.get_activity(maid_id),
                )

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return self._error(
                "ACTIVITY_SWITCH_TIMEOUT",
                "Activity transition deadline expired before starting the target",
                request_id=request_id,
                final_activity=await self.get_activity(maid_id),
            )
        started = await self._start_target(maid_id, target, remaining)
        final = await self.get_activity(maid_id)
        if not started.get("success"):
            return self._error(
                str(started.get("error_code") or "TARGET_START_FAILED"),
                str(started.get("error") or "Failed to start target activity"),
                request_id=request_id,
                target_result=started,
                final_activity=final,
            )
        if not self._target_is_current(target, final, start_result=started):
            terminal = await self._started_target_terminal(target, started)
            if terminal is not None:
                return {
                    "success": True,
                    "accepted": True,
                    "request_id": request_id,
                    "status": "COMPLETED_DURING_START",
                    "maid_id": maid_id,
                    "target": target,
                    "target_result": started,
                    "terminal_activity": terminal,
                    "final_activity": final,
                }
            return self._error(
                "ACTIVITY_VERIFY_FAILED",
                "Target accepted but final authoritative activity does not match",
                request_id=request_id,
                target_result=started,
                final_activity=final,
            )
        return {
            "success": True,
            "accepted": True,
            "request_id": request_id,
            "status": "ACTIVE",
            "maid_id": maid_id,
            "target": target,
            "target_result": started,
            "final_activity": final,
        }

    async def _started_target_terminal(self, target, start_result):
        """Distinguish a fast terminal from a failed post-start verification."""
        target_type = str(target.get("type") or "")
        if target_type == "agent_action":
            action_id = str(
                start_result.get("action_id") or target.get("action_id") or ""
            )
            service = self._action_service
            if not action_id or service is None:
                return None
            snapshot = await service.get_action_status(action_id)
            if (snapshot and not snapshot.get("_query_error")
                    and self._action_terminal(snapshot)):
                return {"type": "agent_action", **dict(snapshot)}
            return None
        if target_type == "skill":
            skill_id = str(
                start_result.get("skill_id") or target.get("skill_id") or ""
            )
            runner = self._skill_runner
            snapshot = runner.get_status(skill_id) if runner and skill_id else None
            if snapshot and self._skill_terminal(snapshot):
                return {"type": "skill", **dict(snapshot)}
        return None

    async def _cancel_current(
        self, maid_id: str, current: Mapping[str, Any]
    ) -> Dict[str, Any]:
        actions = [dict(item) for item in current.get("active_actions", [])]
        skills = [dict(item) for item in current.get("active_skills", [])]
        action_ids = {
            str(item.get("action_id") or "") for item in actions
            if str(item.get("action_id") or "")
        }
        skill_ids = {
            str(item.get("skill_id") or "") for item in skills
            if str(item.get("skill_id") or "")
        }
        skill_child_ids = {
            str(item.get("current_action_id") or "") for item in skills
            if str(item.get("current_action_id") or "")
        }
        action_ids.update(skill_child_ids)
        errors = []
        runner = self._skill_runner
        if runner is not None:
            for skill_id in sorted(skill_ids):
                try:
                    await runner.cancel(skill_id=skill_id, maid_id=maid_id)
                except ValueError:
                    snapshot = runner.get_status(skill_id)
                    if not self._skill_terminal(snapshot):
                        errors.append({"skill_id": skill_id, "error": "Skill not found"})
                except Exception as exc:
                    errors.append({"skill_id": skill_id, "error": str(exc)})

        service = self._action_service
        if service is not None:
            for action_id in sorted(action_ids - skill_child_ids):
                try:
                    result = await service.cancel_action(action_id, maid_id=maid_id)
                except Exception as exc:
                    errors.append({"action_id": action_id, "error": str(exc)})
                    continue
                if not result.get("success", False):
                    snapshot = await service.get_action_status(action_id)
                    if not self._action_terminal(snapshot):
                        errors.append({"action_id": action_id, "result": result})
        return {
            "success": not errors,
            "action_ids": sorted(action_ids),
            "skill_ids": sorted(skill_ids),
            "errors": errors,
        }

    async def _wait_for_controllers_terminal(
        self,
        maid_id: str,
        action_ids,
        skill_ids,
        deadline: float,
    ) -> Dict[str, Any]:
        known_actions = set(action_ids)
        known_skills = set(skill_ids)
        last = {}
        last_skill_reconcile = 0.0
        while True:
            actions, action_query_error = await self._query_active_actions(maid_id)
            known_actions.update(
                str(item.get("action_id") or "") for item in actions
                if str(item.get("action_id") or "")
            )
            skills = self._active_skills(maid_id)
            known_skills.update(
                str(item.get("skill_id") or "") for item in skills
                if str(item.get("skill_id") or "")
            )

            action_states = {}
            service = self._action_service
            for action_id in sorted(known_actions):
                snapshot = (
                    await service.get_action_status(action_id)
                    if service is not None else None
                )
                action_states[action_id] = snapshot
            runner = self._skill_runner
            skill_states = {
                skill_id: runner.get_status(skill_id) if runner is not None else None
                for skill_id in sorted(known_skills)
            }
            active_action_ids = {
                str(item.get("action_id") or "") for item in actions
            }
            unresolved_actions = [
                action_id for action_id, snapshot in action_states.items()
                if action_id in active_action_ids
                or bool(snapshot and snapshot.get("_query_error"))
                or (snapshot is not None and not self._action_terminal(snapshot))
            ]
            unresolved_skills = [
                skill_id for skill_id, snapshot in skill_states.items()
                if not self._skill_terminal(snapshot)
            ]
            now = time.monotonic()
            child_states_terminal = all(
                snapshot is None or self._action_terminal(snapshot)
                for snapshot in action_states.values()
            )
            if (unresolved_skills and child_states_terminal and runner is not None
                    and now - last_skill_reconcile >= max(0.25, self.poll_interval * 4)):
                reconcile = getattr(runner, "reconcile", None)
                if callable(reconcile):
                    try:
                        await reconcile()
                    except Exception:
                        pass
                    last_skill_reconcile = now
                    skill_states = {
                        skill_id: runner.get_status(skill_id)
                        for skill_id in sorted(known_skills)
                    }
                    unresolved_skills = [
                        skill_id for skill_id, snapshot in skill_states.items()
                        if not self._skill_terminal(snapshot)
                    ]
            last = {
                "active_actions": actions,
                "active_skills": skills,
                "action_states": action_states,
                "skill_states": skill_states,
                "unresolved_action_ids": unresolved_actions,
                "unresolved_skill_ids": unresolved_skills,
            }
            if action_query_error is not None:
                last["action_query_error"] = action_query_error
            if (action_query_error is None and not unresolved_actions
                    and not unresolved_skills and not actions and not skills):
                return {"success": True, **last}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"success": False, "error_code": "TIMEOUT", **last}
            await asyncio.sleep(min(self.poll_interval, remaining))

    async def _start_target(
        self, maid_id: str, target: Dict[str, Any], remaining: float
    ) -> Dict[str, Any]:
        activity_type = target["type"]
        if activity_type in {"tlm_task", "idle"}:
            task = target.get("task") or self.idle_task
            return await self._switch_tlm_task(maid_id, task, remaining)
        if activity_type == "preserve_tlm":
            return {"success": True, "preserved_tlm_task": True}
        if activity_type == "agent_action":
            service = self._action_service
            if service is None:
                return self._error(
                    "MAID_AGENT_UNAVAILABLE", "MaidActionService is unavailable"
                )
            action_id = target.get("action_id") or str(uuid.uuid4())
            try:
                result = await asyncio.wait_for(
                    service.start_action(
                        action_id=action_id,
                        maid_id=maid_id,
                        kind=target["kind"],
                        args=target.get("args", {}),
                        timeout_ms=target.get("timeout_ms"),
                        replace_existing=False,
                    ),
                    timeout=max(0.01, remaining),
                )
            except asyncio.TimeoutError:
                return self._error("TIMEOUT", "Agent action start timed out")
            return dict(result or {})
        runner = self._skill_runner
        if runner is None:
            return self._error("SKILL_RUNNER_UNAVAILABLE", "SkillRunner is unavailable")
        try:
            result = await asyncio.wait_for(
                runner.start(
                    skill_name=target["skill"],
                    maid_id=maid_id,
                    args=target.get("args", {}),
                    skill_id=target.get("skill_id"),
                    replace_existing=False,
                ),
                timeout=max(0.01, remaining),
            )
        except asyncio.TimeoutError:
            return self._error("TIMEOUT", "Skill start timed out")
        except Exception as exc:
            return self._error("SKILL_START_FAILED", str(exc))
        snapshot = dict(result or {})
        return {"success": True, **snapshot}

    async def _watch_after_current(self, pending: Dict[str, Any]) -> None:
        maid_id = pending["maid_id"]
        request_id = pending["request_id"]
        # after_current does not impose a deadline on the current activity.  Its
        # timeout applies once the maid becomes idle and target activation starts.
        try:
            unavailable_since = None
            last_skill_reconcile = 0.0
            while True:
                activity = await self.get_activity(maid_id)
                if activity.get("success") and not activity.get("busy"):
                    break
                if not activity.get("success"):
                    unavailable_since = unavailable_since or time.monotonic()
                    if time.monotonic() - unavailable_since >= pending["timeout"]:
                        result = self._error(
                            "ACTIVITY_STATE_UNAVAILABLE",
                            "Authoritative activity state stayed unavailable while waiting",
                            request_id=request_id,
                            current_activity=activity,
                        )
                        self._pending.pop(maid_id, None)
                        operation = self._operations.get(pending["operation_key"])
                        if operation is not None:
                            operation["result"] = result
                        return
                else:
                    unavailable_since = None
                    runner = self._skill_runner
                    now = time.monotonic()
                    if (activity.get("active_skills") and runner is not None
                            and now - last_skill_reconcile
                            >= max(0.25, self.poll_interval * 4)):
                        reconcile = getattr(runner, "reconcile", None)
                        if callable(reconcile):
                            try:
                                await reconcile()
                            except Exception:
                                pass
                            last_skill_reconcile = now
                await asyncio.sleep(self.poll_interval)
            lock = self._maid_locks[maid_id]
            async with lock:
                current = self._pending.get(maid_id)
                if current is not pending:
                    return
                pending["status"] = "STARTING_TARGET"
                deadline = time.monotonic() + pending["timeout"]
                try:
                    result = await asyncio.wait_for(
                        self._execute_transition(
                            maid_id,
                            pending["target"],
                            "reject_if_busy",
                            request_id,
                            deadline,
                        ),
                        timeout=pending["timeout"],
                    )
                except asyncio.TimeoutError:
                    result = self._error(
                        "ACTIVITY_SWITCH_TIMEOUT",
                        "Deferred activity activation exceeded its deadline",
                        request_id=request_id,
                        final_activity=await self.get_activity(maid_id),
                    )
                self._pending.pop(maid_id, None)
                operation_key = pending["operation_key"]
                operation = self._operations.get(operation_key)
                if operation is not None:
                    operation["result"] = result
                    self._operations.move_to_end(operation_key)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result = self._error(
                "AFTER_CURRENT_FAILED", str(exc), request_id=request_id
            )
            self._pending.pop(maid_id, None)
            operation = self._operations.get(pending["operation_key"])
            if operation is not None:
                operation["result"] = result

    async def _switch_tlm_task(
        self, maid_id: str, requested_task: str, remaining: float
    ) -> Dict[str, Any]:
        if self._task_switcher is not None:
            try:
                raw = await asyncio.wait_for(
                    self._task_switcher(maid_id, requested_task),
                    timeout=max(0.01, remaining),
                )
            except asyncio.TimeoutError:
                return self._error("TIMEOUT", "TLM task switch timed out")
            return self._normalize_callback_result(raw)

        status, error = await self._get_maid_status(maid_id)
        if error is not None:
            return self._error("STATUS_REQUEST_FAILED", str(error))
        available = status.get("available_tasks", [])
        resolved = task_resolver.resolve_task_name(requested_task, available)
        if not resolved:
            return self._error(
                "TASK_NOT_AVAILABLE",
                "Requested TLM task does not match an available task",
                requested_task=requested_task,
                available_tasks=self._normalize_tasks(available),
            )
        response = await self._send_request(
            {
                "type": "command_maid",
                "data": {
                    "maid_id": maid_id,
                    "command": "switch_task",
                    "args": {"task": resolved},
                },
            },
            min(self.request_timeout, max(0.01, remaining)),
        )
        if response.get("type") == "error":
            return self._error(
                "TASK_SWITCH_FAILED",
                str(response.get("data", {})),
                requested_task=requested_task,
                matched_task_id=resolved,
            )
        data = self._payload(response)
        if data.get("success") is False:
            return self._error(
                "TASK_SWITCH_FAILED",
                str(data.get("error") or "Task switch failed"),
                requested_task=requested_task,
                matched_task_id=resolved,
                response=data,
            )

        deadline = time.monotonic() + max(0.01, remaining)
        final_status = {}
        while time.monotonic() < deadline:
            final_status, verify_error = await self._get_maid_status(maid_id)
            if verify_error is None and self._task_matches(
                final_status.get("task"), resolved
            ):
                return {
                    "success": True,
                    "verified": True,
                    "requested_task": requested_task,
                    "matched_task_id": resolved,
                    "current_task": final_status.get("task", ""),
                }
            await asyncio.sleep(
                min(self.poll_interval, max(0, deadline - time.monotonic()))
            )
        return self._error(
            "TASK_SWITCH_VERIFY_FAILED",
            "Task switch response was not confirmed by maid status",
            requested_task=requested_task,
            matched_task_id=resolved,
            current_task=final_status.get("task", ""),
        )

    async def _get_maid_status(self, maid_id: str):
        try:
            if self._status_provider is not None:
                raw = await asyncio.wait_for(
                    self._status_provider(maid_id), timeout=self.request_timeout
                )
            else:
                raw = await self._send_request(
                    {"type": "get_maid_status"}, self.request_timeout
                )
        except (asyncio.TimeoutError, Exception) as exc:
            return {}, {"error_code": "STATUS_REQUEST_FAILED", "error": str(exc)}
        if not isinstance(raw, Mapping):
            return {}, {"error_code": "INVALID_STATUS_RESPONSE"}
        if raw.get("type") == "error":
            return {}, dict(raw.get("data", {}))
        if "task" in raw or "available_tasks" in raw:
            maid = dict(raw)
        else:
            maids = self._payload(raw).get("maids", [])
            maid = next(
                (dict(item) for item in maids
                 if isinstance(item, Mapping) and str(item.get("id") or "") == maid_id),
                {},
            )
        if not maid:
            return {}, {
                "error_code": "MAID_NOT_IN_STATUS",
                "error": "Assigned maid was not present in status response",
            }
        cache = getattr(self.plugin, "_maid_status_cache", None)
        if isinstance(cache, dict):
            cache[maid_id] = maid
        return maid, None

    async def _query_active_actions(self, maid_id: str):
        service = self._action_service
        if service is None:
            return [], None
        try:
            strict_query = getattr(service, "query_active_actions", None)
            if callable(strict_query):
                result = await strict_query(maid_id=maid_id)
                if not result.get("success", False):
                    return [], {
                        "error_code": str(
                            result.get("error_code") or "ACTION_QUERY_FAILED"
                        ),
                        "error": result.get("error", "Action query failed"),
                    }
                items = result.get("actions", [])
            else:
                items = await service.list_active_actions(maid_id=maid_id)
        except Exception as exc:
            return [], {
                "error_code": "ACTION_QUERY_FAILED",
                "error": str(exc),
            }
        actions = [
            dict(item) for item in items or []
            if isinstance(item, Mapping)
            and str(item.get("status") or "").upper() not in ACTION_TERMINAL_STATUSES
        ]
        return actions, None

    def _active_skills(self, maid_id: str):
        runner = self._skill_runner
        if runner is None:
            return []
        try:
            items = runner.list_skills(maid_id=maid_id, include_terminal=False)
        except Exception:
            return []
        return [
            dict(item) for item in items or []
            if isinstance(item, Mapping) and not self._skill_terminal(item)
        ]

    @property
    def _action_service(self):
        return self._injected_action_service or getattr(
            self.plugin, "_maid_action_service", None
        )

    @property
    def _skill_runner(self):
        return self._injected_skill_runner or getattr(
            self.plugin, "_skill_runner", None
        )

    async def _send_request(self, request: Dict[str, Any], timeout: float):
        sender = getattr(self.plugin, "_send_request", None)
        if not callable(sender):
            return {"type": "error", "data": {"error": "Request transport unavailable"}}
        try:
            value = sender(request, timeout=timeout)
            if inspect.isawaitable(value):
                return await asyncio.wait_for(value, timeout=max(0.01, timeout))
            return value
        except asyncio.TimeoutError:
            return {"type": "error", "data": {"error": "Request timed out"}}
        except Exception as exc:
            return {"type": "error", "data": {"error": str(exc)}}

    def _resolve_maid_id(self, maid_id: str) -> str:
        maid_id = str(maid_id or "").strip()
        if maid_id:
            return maid_id
        resolver = getattr(self.plugin, "_resolve_maid_id", None)
        return str(resolver() or "").strip() if callable(resolver) else ""

    @staticmethod
    def _normalize_target(activity: Mapping[str, Any]) -> Dict[str, Any]:
        if not isinstance(activity, Mapping):
            raise TypeError("activity must be an object")
        activity_type = str(activity.get("type") or "").strip().lower()
        if activity_type not in _INTERNAL_ACTIVITY_TYPES:
            raise ValueError(
                "activity.type must be tlm_task, agent_action, skill or idle"
            )
        if activity_type == "preserve_tlm":
            return {"type": "preserve_tlm"}
        if activity_type in {"tlm_task", "idle"}:
            task = str(activity.get("task") or ("idle" if activity_type == "idle" else "")).strip()
            if not task:
                raise ValueError("tlm_task requires task")
            return {"type": activity_type, "task": task}
        if activity_type == "agent_action":
            kind = str(activity.get("kind") or "").strip().lower()
            if not kind:
                raise ValueError("agent_action requires kind")
            args = activity.get("args", {})
            if not isinstance(args, Mapping):
                raise TypeError("agent_action.args must be an object")
            target = {"type": activity_type, "kind": kind, "args": dict(args)}
            if activity.get("action_id"):
                target["action_id"] = str(activity["action_id"])
            if "timeout_ms" in activity and activity.get("timeout_ms") is not None:
                target["timeout_ms"] = int(activity["timeout_ms"])
            return target
        name = str(activity.get("skill") or activity.get("name") or "").strip().lower()
        if not name:
            raise ValueError("skill activity requires skill")
        args = activity.get("args", {})
        if not isinstance(args, Mapping):
            raise TypeError("skill.args must be an object")
        target = {"type": "skill", "skill": name, "args": dict(args)}
        if activity.get("skill_id"):
            target["skill_id"] = str(activity["skill_id"])
        return target

    @staticmethod
    def _normalize_policy(value: str) -> str:
        policy = str(value or "cancel_then_switch").strip().lower()
        if policy not in SWITCH_POLICIES:
            raise ValueError(
                "switch_policy must be cancel_then_switch, after_current or reject_if_busy"
            )
        return policy

    def _normalize_timeout(self, value: Optional[float]) -> float:
        if value is None:
            return self.transition_timeout
        timeout = float(value)
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        return timeout

    @staticmethod
    def _target_is_current(target, current, start_result=None):
        target_type = target.get("type")
        if target_type == "preserve_tlm":
            return not bool(
                current.get("active_actions") or current.get("active_skills")
            )
        if target_type in {"tlm_task", "idle"}:
            if current.get("active_actions") or current.get("active_skills"):
                return False
            current_task = current.get("tlm_task", {}).get("id", "")
            return MaidActivityDirector._task_matches(current_task, target.get("task"))
        if target_type == "agent_action":
            expected_id = str(
                (start_result or {}).get("action_id") or target.get("action_id") or ""
            )
            if expected_id:
                return any(
                    str(item.get("action_id") or "") == expected_id
                    for item in current.get("active_actions", [])
                )
            expected_kind = str(target.get("kind") or "")
            expected_args = dict(target.get("args") or {})
            return any(
                str(item.get("kind") or "") == expected_kind
                and dict(item.get("args") or {}) == expected_args
                for item in current.get("active_actions", [])
            )
        expected_id = str(
            (start_result or {}).get("skill_id") or target.get("skill_id") or ""
        )
        if expected_id:
            return any(
                str(item.get("skill_id") or "") == expected_id
                for item in current.get("active_skills", [])
            )
        for item in current.get("active_skills", []):
            if (str(item.get("skill_name") or "") == str(target.get("skill") or "")
                    and dict(item.get("args") or {}) == dict(target.get("args") or {})):
                return True
        return False

    @staticmethod
    def _primary_activity(maid, actions, skills):
        if skills:
            item = skills[0]
            return {
                "type": "skill",
                "skill_id": item.get("skill_id", ""),
                "skill": item.get("skill_name", ""),
                "status": item.get("status", ""),
            }
        if actions:
            item = actions[0]
            return {
                "type": "agent_action",
                "action_id": item.get("action_id", ""),
                "kind": item.get("kind", ""),
                "status": item.get("status", ""),
            }
        task = str(maid.get("task") or "")
        return {
            "type": "idle" if task.split(":")[-1].lower() == "idle" else "tlm_task",
            "task": task,
        }

    @staticmethod
    def _tlm_projection(maid, *, suppressed=False):
        task = str(maid.get("task") or "")
        name = ""
        for item in maid.get("available_tasks", []) or []:
            if isinstance(item, Mapping) and str(item.get("id") or "") == task:
                name = str(item.get("name") or "")
                break
        return {
            "id": task,
            "name": name,
            "suppressed": bool(suppressed),
            "suppression_reason": "body_controller_active" if suppressed else "",
        }

    @staticmethod
    def _fold_skill_children(actions, skills):
        """Fold Skill-owned child actions into their owning Skill snapshot."""
        by_id = {
            str(item.get("action_id") or ""): dict(item)
            for item in actions
            if str(item.get("action_id") or "")
        }
        child_ids = set()
        folded_skills = []
        for item in skills:
            folded = dict(item)
            child_id = str(folded.get("current_action_id") or "")
            child = by_id.get(child_id)
            if child is not None:
                folded["child_action"] = child
                child_ids.add(child_id)
            folded_skills.append(folded)
        standalone = [
            dict(item) for item in actions
            if str(item.get("action_id") or "") not in child_ids
        ]
        return standalone, folded_skills

    @staticmethod
    def _normalize_tasks(items):
        result = []
        for item in items or []:
            if isinstance(item, Mapping):
                task_id = str(item.get("id") or "")
                name = str(item.get("name") or "")
            else:
                task_id, name = str(item or ""), ""
            if task_id or name:
                result.append({"id": task_id, "name": name})
        return result

    @staticmethod
    def _task_matches(current: Any, expected: Any) -> bool:
        current = str(current or "").strip().lower()
        expected = str(expected or "").strip().lower()
        return bool(current and expected) and (
            current == expected or current.split(":")[-1] == expected.split(":")[-1]
        )

    @staticmethod
    def _action_terminal(snapshot) -> bool:
        if snapshot is None:
            return True
        return str(snapshot.get("status") or "").upper() in ACTION_TERMINAL_STATUSES

    @staticmethod
    def _skill_terminal(snapshot) -> bool:
        if snapshot is None:
            return True
        return str(snapshot.get("status") or "").upper() in TERMINAL_SKILL_STATUSES

    @staticmethod
    def _payload(message):
        data = (message or {}).get("data", {}) if isinstance(message, Mapping) else {}
        return dict(data) if isinstance(data, Mapping) else {}

    @staticmethod
    def _normalize_callback_result(raw):
        if not isinstance(raw, Mapping):
            return MaidActivityDirector._error(
                "TASK_SWITCH_FAILED", "Task switcher returned an invalid response"
            )
        if "is_error" in raw:
            output = raw.get("output", {})
            result = dict(output) if isinstance(output, Mapping) else {"error": str(output)}
            if raw.get("is_error"):
                return {
                    "success": False,
                    "error_code": str(raw.get("error") or "TASK_SWITCH_FAILED"),
                    **result,
                }
            return {"success": True, **result}
        return dict(raw)

    @staticmethod
    def _fingerprint(maid_id, target, policy, timeout):
        encoded = json.dumps(
            {
                "maid_id": maid_id,
                "target": target,
                "switch_policy": policy,
                "timeout": timeout,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _remember(self, request_id, fingerprint, result):
        result = dict(result)
        self._operations[request_id] = {
            "fingerprint": fingerprint,
            "result": result,
        }
        self._operations.move_to_end(request_id)
        while len(self._operations) > self.idempotency_limit:
            pinned = {
                item.get("operation_key") for item in self._pending.values()
            }
            evictable = next(
                (key for key in self._operations if key not in pinned), None
            )
            if evictable is None:
                break
            self._operations.pop(evictable, None)
        return dict(result)

    @staticmethod
    def _public_pending(pending):
        return {
            key: pending[key]
            for key in ("request_id", "maid_id", "target", "status", "created_at")
            if key in pending
        }

    def _watcher_done(self, maid_id: str, task: asyncio.Task) -> None:
        if self._watchers.get(maid_id) is task:
            self._watchers.pop(maid_id, None)
        if task.cancelled():
            return
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            pass

    def _supersede_pending_transition(
        self, pending: Dict[str, Any], replacing_request_id: str
    ) -> None:
        maid_id = str(pending.get("maid_id") or "")
        self._pending.pop(maid_id, None)
        watcher = self._watchers.pop(maid_id, None)
        if watcher is not None and not watcher.done():
            watcher.cancel()
        operation = self._operations.get(pending.get("operation_key"))
        if operation is not None:
            operation["result"] = self._error(
                "SUPERSEDED",
                "Queued after_current transition was replaced by a new immediate request",
                request_id=pending.get("request_id", ""),
                replaced_by_request_id=replacing_request_id,
            )

    @staticmethod
    def _error(code: str, message: str, **details):
        return {
            "success": False,
            "error_code": str(code or "REQUEST_FAILED"),
            "error": str(message or "Request failed"),
            **details,
        }


__all__ = [
    "ACTIVITY_TYPES",
    "SWITCH_POLICIES",
    "MaidActivityDirector",
]
