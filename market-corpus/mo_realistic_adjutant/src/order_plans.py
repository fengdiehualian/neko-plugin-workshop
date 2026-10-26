from __future__ import annotations

import copy
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any


OrderExecutor = Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]]
TransitionHandler = Callable[[dict[str, Any], dict[str, Any], str, str], None]
ActionValidator = Callable[[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]]


class OrderPlanError(ValueError):
    pass


class OrderPlanEngine:
    """Dependency-aware, non-blocking executor for compound battlefield orders."""

    TERMINAL = {"succeeded", "failed", "skipped", "cancelled"}
    ACTIVE = {"queued", "running", "waiting", "retrying"}

    def __init__(
        self,
        executor: OrderExecutor,
        *,
        clock=time.monotonic,
        max_orders_per_tick: int = 8,
        on_transition: TransitionHandler | None = None,
        validator: ActionValidator | None = None,
    ) -> None:
        self._executor = executor
        self._clock = clock
        self._max_orders_per_tick = max(1, int(max_orders_per_tick))
        self._on_transition = on_transition
        self._validator = validator
        self._plans: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def create(
        self,
        name: str,
        orders: list[dict[str, Any]],
        *,
        duration_seconds: int | None = None,
    ) -> dict[str, Any]:
        normalized = self._validate_orders(orders)
        now = self._clock()
        plan_id = uuid.uuid4().hex[:12]
        plan = {
            "plan_id": plan_id,
            "name": str(name or "复合作战计划").strip()[:80],
            "status": "queued",
            "created_at": now,
            "expires_at": (
                now + max(1, int(duration_seconds))
                if duration_seconds is not None else None
            ),
            "orders": normalized,
            "commands_issued": 0,
            "last_tick_at": None,
            "last_error": None,
        }
        with self._lock:
            self._plans[plan_id] = plan
        return self.public_plan(plan)

    def tick(self, state: dict[str, Any]) -> dict[str, int]:
        now = self._clock()
        counts = {"plans": 0, "orders": 0, "succeeded": 0, "failed": 0}
        with self._lock:
            for plan in self._plans.values():
                if plan["status"] in self.TERMINAL:
                    continue
                previous_states = {
                    item["order_id"]: str(item["status"])
                    for item in plan["orders"]
                }
                counts["plans"] += 1
                plan["last_tick_at"] = now
                if plan["expires_at"] is not None and now >= plan["expires_at"]:
                    plan["status"] = "failed"
                    plan["last_error"] = "计划超过执行期限"
                    self._skip_pending(plan, "计划已过期")
                    counts["failed"] += 1
                    self._emit_transitions(plan, previous_states)
                    continue
                issued = 0
                by_id = {item["order_id"]: item for item in plan["orders"]}
                for order in plan["orders"]:
                    if issued >= self._max_orders_per_tick:
                        break
                    if order["status"] in self.TERMINAL:
                        continue
                    dependency_states = [by_id[item]["status"] for item in order["depends_on"]]
                    if any(value in {"failed", "skipped", "cancelled"} for value in dependency_states):
                        order["status"] = "skipped"
                        order["last_error"] = "前置命令未成功"
                        continue
                    if not all(value == "succeeded" for value in dependency_states):
                        order["status"] = "waiting"
                        continue
                    if now < float(order["next_attempt_at"]):
                        order["status"] = "waiting"
                        continue
                    order["status"] = "running"
                    order["attempts"] += 1
                    order["last_attempt_at"] = now
                    issued += 1
                    counts["orders"] += 1
                    plan["commands_issued"] += 1
                    try:
                        result = self._executor(plan, order, state)
                    except Exception as exc:
                        result = {"accepted": False, "error": str(exc), "retryable": True}
                    order["result"] = self._safe_result(result)
                    if self._validator is not None:
                        try:
                            validation = self._validator(plan, order, state, result)
                        except Exception as exc:
                            validation = {"accepted": False, "complete": False, "retryable": True, "reason": f"validation error: {exc}"}
                        result = dict(result)
                        result["validation"] = validation
                        order["result"] = self._safe_result(result)
                        if validation.get("accepted") is not True:
                            result["accepted"] = False
                            result["error"] = validation.get("reason") or "action validation failed"
                        elif validation.get("complete") is False:
                            result["complete"] = False
                    if result.get("accepted") is True and result.get("complete", True) is not False:
                        order["status"] = "succeeded"
                        order["completed_at"] = now
                        order["last_error"] = None
                        counts["succeeded"] += 1
                        continue
                    if result.get("accepted") is True:
                        order["status"] = "retrying"
                        order["next_attempt_at"] = now + order["retry_seconds"]
                        continue
                    order["last_error"] = str(result.get("error") or "命令未被接受")
                    retryable = result.get("retryable", True) is not False
                    if retryable and (
                        order["max_attempts"] is None
                        or order["attempts"] < order["max_attempts"]
                    ):
                        order["status"] = "retrying"
                        order["next_attempt_at"] = now + order["retry_seconds"]
                    else:
                        order["status"] = "failed"
                        order["completed_at"] = now
                        counts["failed"] += 1
                        if order["on_failure"] == "abort":
                            plan["last_error"] = f"命令 {order['order_id']} 失败：{order['last_error']}"
                            self._skip_pending(plan, "计划因关键命令失败而终止")
                            break
                self._refresh_plan_status(plan)
                self._emit_transitions(plan, previous_states)
        return counts

    def _emit_transitions(
        self, plan: dict[str, Any], previous_states: dict[str, str]
    ) -> None:
        if self._on_transition is None:
            return
        for order in plan["orders"]:
            previous = previous_states.get(order["order_id"], "queued")
            current = str(order["status"])
            if previous == current:
                continue
            try:
                self._on_transition(plan, order, previous, current)
            except Exception:
                # State reporting must never stop command execution.
                continue

    def cancel(self, plan_id: str | None = None) -> int:
        now = self._clock()
        count = 0
        with self._lock:
            for plan in self._plans.values():
                if plan_id and plan["plan_id"] != plan_id:
                    continue
                if plan["status"] in self.TERMINAL:
                    continue
                plan["status"] = "cancelled"
                plan["completed_at"] = now
                previous_states = {
                    order["order_id"]: str(order["status"])
                    for order in plan["orders"]
                }
                for order in plan["orders"]:
                    if order["status"] not in self.TERMINAL:
                        order["status"] = "cancelled"
                        order["completed_at"] = now
                count += 1
                self._emit_transitions(plan, previous_states)
        return count

    def plans(self, plan_id: str | None = None, *, include_finished: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            values = [
                self.public_plan(plan)
                for plan in self._plans.values()
                if (not plan_id or plan["plan_id"] == plan_id)
                and (include_finished or plan["status"] not in self.TERMINAL)
            ]
        return sorted(values, key=lambda item: item["created_at"], reverse=True)

    @property
    def active_count(self) -> int:
        with self._lock:
            return sum(plan["status"] not in self.TERMINAL for plan in self._plans.values())

    @classmethod
    def public_plan(cls, plan: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(plan)
        now = time.monotonic()
        result["remaining_seconds"] = (
            max(0, round(float(plan["expires_at"]) - now))
            if plan["expires_at"] is not None else None
        )
        return result

    def _validate_orders(self, orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(orders, list) or not orders:
            raise OrderPlanError("orders 必须至少包含一条命令")
        allowed = {
            "move", "attack", "stop", "guard", "deploy", "explore",
            "produce", "load", "capture", "repair", "unload", "wait",
        }
        normalized = []
        ids: set[str] = set()
        for index, raw in enumerate(orders, 1):
            if not isinstance(raw, dict):
                raise OrderPlanError("每条命令必须是对象")
            order_id = str(raw.get("order_id") or f"order_{index}").strip()[:40]
            if not order_id or order_id in ids:
                raise OrderPlanError(f"命令 ID 重复或为空：{order_id}")
            ids.add(order_id)
            action = str(raw.get("action", "")).strip().lower()
            if action not in allowed:
                raise OrderPlanError(f"不支持的组合命令：{action}")
            depends = raw.get("depends_on", [])
            if isinstance(depends, str):
                depends = [depends]
            if not isinstance(depends, list) or not all(isinstance(item, str) for item in depends):
                raise OrderPlanError(f"{order_id}.depends_on 必须是命令 ID 数组")
            payload = dict(raw)
            payload.update({
                "order_id": order_id,
                "action": action,
                "depends_on": [item.strip() for item in depends],
                "status": "queued",
                "attempts": 0,
                "max_attempts": (
                    max(1, int(raw["max_attempts"]))
                    if raw.get("max_attempts") is not None else None
                ),
                "retry_seconds": max(0.1, float(raw.get("retry_seconds", 3.0))),
                "next_attempt_at": self._clock() + max(0.0, float(raw.get("delay_seconds", 0))),
                "on_failure": str(raw.get("on_failure", "abort")).lower(),
                "last_error": None,
                "result": None,
            })
            if payload["on_failure"] not in {"abort", "skip"}:
                raise OrderPlanError(f"{order_id}.on_failure 必须是 abort 或 skip")
            normalized.append(payload)
        by_id = {item["order_id"]: item for item in normalized}
        for item in normalized:
            unknown = [dependency for dependency in item["depends_on"] if dependency not in by_id]
            if unknown:
                raise OrderPlanError(f"{item['order_id']} 引用了不存在的前置命令：{unknown}")
        self._reject_cycles(by_id)
        return normalized

    @staticmethod
    def _reject_cycles(by_id: dict[str, dict[str, Any]]) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(order_id: str) -> None:
            if order_id in visiting:
                raise OrderPlanError("组合命令存在循环依赖")
            if order_id in visited:
                return
            visiting.add(order_id)
            for dependency in by_id[order_id]["depends_on"]:
                visit(dependency)
            visiting.remove(order_id)
            visited.add(order_id)

        for order_id in by_id:
            visit(order_id)

    @classmethod
    def _skip_pending(cls, plan: dict[str, Any], reason: str) -> None:
        for order in plan["orders"]:
            if order["status"] not in cls.TERMINAL:
                order["status"] = "skipped"
                order["last_error"] = reason

    @classmethod
    def _refresh_plan_status(cls, plan: dict[str, Any]) -> None:
        statuses = [item["status"] for item in plan["orders"]]
        if all(value == "succeeded" for value in statuses):
            plan["status"] = "succeeded"
            plan["completed_at"] = plan["last_tick_at"]
        elif all(value in cls.TERMINAL for value in statuses):
            plan["status"] = "failed" if "failed" in statuses else "succeeded"
            plan["completed_at"] = plan["last_tick_at"]
        elif any(value == "running" for value in statuses):
            plan["status"] = "running"
        else:
            plan["status"] = "waiting"

    @staticmethod
    def _safe_result(result: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value for key, value in result.items()
            if isinstance(value, (str, int, float, bool, type(None), list, dict))
        }
