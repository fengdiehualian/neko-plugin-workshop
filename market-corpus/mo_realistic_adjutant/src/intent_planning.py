"""Goal-oriented intent plans compiled above the native order executor."""

from __future__ import annotations

import copy
import re
import threading
import time
import uuid
from typing import Any


class IntentPlanError(ValueError):
    pass


class IntentPlanEngine:
    """Store explainable objectives while lower-level engines execute commands."""

    TERMINAL = {"completed", "failed", "cancelled"}

    def __init__(self) -> None:
        self._plans: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def create(
        self,
        goal: str,
        world: dict[str, Any],
        *,
        product: str | None = None,
        quantity: int | None = None,
        target_query: str | None = None,
        group_number: int | None = None,
        force_size: int | None = None,
        credit_reserve: int | None = None,
        continuous: bool = True,
        objectives: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        text = str(goal or "").strip()
        if not text:
            raise IntentPlanError("goal is required")
        parsed = self._parse_goal(text)
        selected_product = str(product or parsed.get("product") or "").strip()
        selected_quantity = self._positive_int(quantity, parsed.get("quantity"))
        selected_force_size = self._positive_int(force_size, selected_quantity, 12)
        selected_target = self._clean_target(target_query or parsed.get("target_query") or "敌人")
        selected_group = group_number if group_number is not None else parsed.get("group_number")
        if selected_group is not None and not 0 <= int(selected_group) <= 9:
            raise IntentPlanError("group_number must be between 0 and 9")

        normalized = (
            self._normalize_objectives(objectives)
            if objectives is not None
            else self._compile_objectives(
                parsed,
                product=selected_product,
                quantity=selected_quantity,
                target_query=selected_target,
                group_number=(int(selected_group) if selected_group is not None else None),
                force_size=selected_force_size,
                credit_reserve=(
                    None if credit_reserve is None else max(0, int(credit_reserve))
                ),
                continuous=continuous is not False,
            )
        )
        if not normalized:
            raise IntentPlanError("the goal did not contain a supported battlefield objective")

        now = time.monotonic()
        plan_id = uuid.uuid4().hex[:12]
        plan = {
            "intent_id": plan_id,
            "task_id": plan_id,
            "goal": text,
            "intent_kind": self._intent_kind(normalized),
            "status": "planned",
            "detached": True,
            "pending": True,
            "connection_state": "connected",
            "resume_policy": "resume_same_session",
            "created_at": now,
            "updated_at": now,
            "heartbeat_at": now,
            "world_revision": world.get("revision") if isinstance(world, dict) else None,
            "world_frame": world.get("source_frame") if isinstance(world, dict) else None,
            "session_started_at": world.get("session_started_at") if isinstance(world, dict) else None,
            "selected_product": selected_product or None,
            "selected_type_id": None,
            "target_query": selected_target,
            "group_number": selected_group,
            "force_size": selected_force_size,
            "continuous": continuous is not False,
            "autonomy_granted": parsed.get("full_autonomy") is True,
            "assumptions": self._assumptions(parsed, selected_product),
            "constraints": [
                "only currently observed enemies may be targeted",
                "native sidebar availability is authoritative",
                "every dependent action waits for observable completion",
            ],
            "objectives": normalized,
            "executor": {},
            "last_decision": "intent compiled from commander goal",
            "last_error": None,
        }
        with self._lock:
            self._plans[plan_id] = plan
        return self.public(plan)

    def get(self, intent_id: str) -> dict[str, Any] | None:
        with self._lock:
            plan = self._plans.get(str(intent_id))
            return self.public(plan) if plan else None

    def raw(self, intent_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._plans.get(str(intent_id))

    def plans(self, *, include_finished: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            values = [
                self.public(plan) for plan in self._plans.values()
                if include_finished or plan.get("status") not in self.TERMINAL
            ]
        return sorted(values, key=lambda item: item["created_at"], reverse=True)

    def active_ids(self) -> list[str]:
        with self._lock:
            return [
                intent_id for intent_id, plan in self._plans.items()
                if plan.get("status") not in self.TERMINAL
            ]

    def cancel(self, intent_id: str | None = None, *, reason: str | None = None) -> int:
        now = time.monotonic()
        count = 0
        with self._lock:
            for plan in self._plans.values():
                if intent_id and plan["intent_id"] != intent_id:
                    continue
                if plan.get("status") in self.TERMINAL:
                    continue
                plan["status"] = "cancelled"
                plan["pending"] = False
                plan["updated_at"] = now
                if reason:
                    plan["last_error"] = str(reason)
                    plan["last_decision"] = str(reason)
                for objective in plan["objectives"]:
                    if objective["status"] not in {"succeeded", "failed", "cancelled"}:
                        objective["status"] = "cancelled"
                count += 1
        return count

    def set_connection(
        self, connected: bool, *, intent_id: str | None = None, reason: str | None = None,
    ) -> int:
        """Pause detached plans on bridge loss without completing or cancelling them."""
        now = time.monotonic()
        count = 0
        with self._lock:
            for plan in self._plans.values():
                if intent_id and plan["intent_id"] != intent_id:
                    continue
                if plan.get("status") in self.TERMINAL:
                    continue
                target_state = "connected" if connected else "paused_disconnected"
                if plan.get("connection_state") == target_state:
                    continue
                plan["connection_state"] = target_state
                plan["updated_at"] = now
                if reason:
                    plan["last_decision"] = str(reason)
                count += 1
        return count

    def set_objective(
        self,
        intent_id: str,
        objective_id: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            plan = self._plans[intent_id]
            objective = next(
                item for item in plan["objectives"] if item["objective_id"] == objective_id
            )
            objective["status"] = status
            objective["result"] = copy.deepcopy(result) if isinstance(result, dict) else result
            objective["last_error"] = error
            objective["updated_at"] = time.monotonic()
            plan["updated_at"] = objective["updated_at"]
            self._refresh(plan)

    def attach_executor(self, intent_id: str, **values: Any) -> None:
        with self._lock:
            plan = self._plans[intent_id]
            plan["executor"].update(copy.deepcopy(values))
            plan["updated_at"] = time.monotonic()

    def decide(
        self,
        intent_id: str,
        message: str,
        *,
        selected_product: str | None = None,
        selected_type_id: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            plan = self._plans[intent_id]
            plan["last_decision"] = str(message)
            if selected_product:
                plan["selected_product"] = selected_product
            if selected_type_id:
                plan["selected_type_id"] = selected_type_id
            plan["last_error"] = error
            plan["updated_at"] = time.monotonic()

    def heartbeat(
        self, intent_id: str, *, world_revision: Any = None, world_frame: Any = None,
    ) -> None:
        with self._lock:
            plan = self._plans.get(str(intent_id))
            if plan is None or plan.get("status") in self.TERMINAL:
                return
            now = time.monotonic()
            plan["heartbeat_at"] = now
            plan["updated_at"] = now
            if world_revision is not None:
                plan["world_revision"] = world_revision
            if world_frame is not None:
                plan["world_frame"] = world_frame

    def clear(self) -> None:
        with self._lock:
            self._plans.clear()

    @staticmethod
    def public(plan: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(plan)
        heartbeat_at = result.get("heartbeat_at")
        result["heartbeat_age_seconds"] = (
            round(max(0.0, time.monotonic() - float(heartbeat_at)), 3)
            if isinstance(heartbeat_at, (int, float)) else None
        )
        result["pending"] = result.get("status") not in IntentPlanEngine.TERMINAL
        return result

    @staticmethod
    def _parse_goal(goal: str) -> dict[str, Any]:
        compact = re.sub(r"\s+", "", goal)
        full_autonomy = any(token in compact for token in (
            "全权自由操作", "全权操作", "完全自主", "自主作战", "自由发挥",
            "自己决定", "不用问我", "击败对面", "战胜对面", "赢下这局",
        ))
        result: dict[str, Any] = {
            "full_autonomy": full_autonomy,
            "develop": full_autonomy or any(token in compact for token in (
                "发展基地", "建设基地", "自动发展", "建立基地", "维持经济", "基础建筑",
            )),
            "deploy": "部署" in compact and any(token in compact for token in ("建设车", "基地车", "MCV", "mcv")),
            "attack": full_autonomy or any(token in compact for token in ("攻击", "攻打", "进攻", "击败", "战胜", "消灭", "摧毁", "清理敌")),
            "explore": full_autonomy or any(token in compact for token in ("侦察", "探索", "探路")),
            "defend": full_autonomy or any(token in compact for token in ("防守", "守住", "驻守")),
        }
        production = re.search(
            r"(?:出|生产|训练|造)\s*(\d+)\s*(?:个|名|辆|架|队)?\s*"
            r"(.+?)(?=然后|并且|并操控|再|去攻击|攻击|攻打|进攻|$)",
            goal,
            flags=re.IGNORECASE,
        )
        if production:
            result["quantity"] = int(production.group(1))
            result["product"] = production.group(2).strip(" ，,。；;")
        else:
            quantity = re.search(r"(\d+)\s*(?:个|名|辆|架|队)", goal)
            if quantity:
                result["quantity"] = int(quantity.group(1))
        target = re.search(r"(?:攻击|攻打|进攻|击败|战胜|消灭|摧毁|清理)\s*(.+)$", goal)
        if target:
            result["target_query"] = target.group(1).strip(" ，,。；;")
        group = re.search(r"(?:编到|编入|使用|控制)?\s*([0-9])\s*号(?:编队|小组)", goal)
        if group:
            result["group_number"] = int(group.group(1))
        return result

    @staticmethod
    def _compile_objectives(
        parsed: dict[str, Any], *, product: str, quantity: int | None,
        target_query: str, group_number: int | None, force_size: int,
        credit_reserve: int | None, continuous: bool,
    ) -> list[dict[str, Any]]:
        objectives: list[dict[str, Any]] = []

        def add(kind: str, params: dict[str, Any], depends_on: list[str] | None = None) -> str:
            objective_id = f"objective_{len(objectives) + 1}"
            objectives.append({
                "objective_id": objective_id,
                "kind": kind,
                "params": params,
                "depends_on": list(depends_on or []),
                "status": "pending",
                "result": None,
                "last_error": None,
            })
            return objective_id

        previous: list[str] = []
        if parsed.get("full_autonomy"):
            scout_count = max(1, min(2, force_size // 4))
            defense_count = max(1, min(4, force_size // 3))
            attack_offset = scout_count + defense_count
            develop_id = add("develop_base", {"credit_reserve": credit_reserve})
            produce_id = add(
                "produce_force",
                {
                    "product": product or None,
                    "quantity": quantity or force_size,
                    "auto_select": not bool(product),
                    "maintain": True,
                },
                [develop_id],
            )
            add(
                "explore",
                {"query": product or None, "group_number": group_number, "unit_offset": 0, "unit_limit": scout_count},
                [produce_id],
            )
            add(
                "defend",
                {"query": product or None, "group_number": group_number, "unit_offset": scout_count, "unit_limit": defense_count},
                [produce_id],
            )
            add(
                "attack",
                {
                    "query": product or None,
                    "target_query": target_query,
                    "group_number": group_number,
                    "continuous": True,
                    "unit_offset": attack_offset,
                },
                [produce_id],
            )
            return objectives
        if parsed.get("develop"):
            previous = [add("develop_base", {"credit_reserve": credit_reserve})]
        elif parsed.get("deploy"):
            previous = [add("deploy_mcv", {}, previous)]

        if product or quantity is not None or (parsed.get("develop") and parsed.get("attack")):
            produce_id = add(
                "produce_force",
                {
                    "product": product or None,
                    "quantity": quantity or force_size,
                    "auto_select": not bool(product),
                },
                previous,
            )
            previous = [produce_id]

        if parsed.get("attack"):
            previous = [add(
                "attack",
                {
                    "query": product or None,
                    "target_query": target_query,
                    "group_number": group_number,
                    "continuous": continuous,
                },
                previous,
            )]
        if parsed.get("explore"):
            add("explore", {"query": product or None, "group_number": group_number}, previous)
        if parsed.get("defend"):
            add("defend", {"query": product or None, "group_number": group_number}, previous)
        return objectives

    @staticmethod
    def _normalize_objectives(objectives: Any) -> list[dict[str, Any]]:
        if not isinstance(objectives, list) or not objectives:
            raise IntentPlanError("objectives must be a non-empty list")
        allowed = {
            "develop_base", "deploy_mcv", "produce_force", "attack", "explore", "defend",
            "move", "load", "capture", "repair", "stop", "guard", "wait",
        }
        result = []
        ids: set[str] = set()
        for index, raw in enumerate(objectives, 1):
            if not isinstance(raw, dict):
                raise IntentPlanError("each objective must be an object")
            kind = str(raw.get("kind") or "").strip().lower()
            if kind not in allowed:
                raise IntentPlanError(f"unsupported objective kind: {kind}")
            objective_id = str(raw.get("objective_id") or f"objective_{index}").strip()
            if not objective_id or objective_id in ids:
                raise IntentPlanError(f"duplicate objective id: {objective_id}")
            ids.add(objective_id)
            depends = raw.get("depends_on", [])
            if isinstance(depends, str):
                depends = [depends]
            if not isinstance(depends, list) or not all(isinstance(item, str) for item in depends):
                raise IntentPlanError("depends_on must contain objective ids")
            result.append({
                "objective_id": objective_id,
                "kind": kind,
                "params": copy.deepcopy(raw.get("params")) if isinstance(raw.get("params"), dict) else {},
                "depends_on": list(depends),
                "status": "pending",
                "result": None,
                "last_error": None,
            })
        known = {item["objective_id"] for item in result}
        for item in result:
            unknown = set(item["depends_on"]) - known
            if unknown:
                raise IntentPlanError(f"unknown objective dependencies: {sorted(unknown)}")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(objective_id: str) -> None:
            if objective_id in visiting:
                raise IntentPlanError("intent objectives contain a dependency cycle")
            if objective_id in visited:
                return
            visiting.add(objective_id)
            current = next(item for item in result if item["objective_id"] == objective_id)
            for dependency in current["depends_on"]:
                visit(dependency)
            visiting.remove(objective_id)
            visited.add(objective_id)

        for item in result:
            visit(item["objective_id"])
        return result

    @staticmethod
    def _intent_kind(objectives: list[dict[str, Any]]) -> str:
        kinds = {item["kind"] for item in objectives}
        if {"develop_base", "produce_force", "explore", "defend", "attack"}.issubset(kinds):
            return "autonomous_victory"
        if "develop_base" in kinds and "attack" in kinds:
            return "develop_and_attack"
        if "produce_force" in kinds and "attack" in kinds:
            return "produce_and_attack"
        if "develop_base" in kinds:
            return "develop_base"
        if "attack" in kinds:
            return "combat"
        return "compound"

    @staticmethod
    def _assumptions(parsed: dict[str, Any], product: str) -> list[str]:
        assumptions = []
        if parsed.get("develop"):
            assumptions.append("base development remains active while later combat objectives run")
        if not product and parsed.get("attack"):
            assumptions.append("select the cheapest currently buildable combat unit when production unlocks")
        if not parsed.get("target_query") and parsed.get("attack"):
            assumptions.append("attack the nearest currently visible enemy")
        return assumptions

    @staticmethod
    def _positive_int(*values: Any) -> int | None:
        for value in values:
            if isinstance(value, bool) or value is None:
                continue
            number = int(value)
            if number > 0:
                return number
        return None

    @staticmethod
    def _clean_target(value: Any) -> str:
        text = str(value or "").strip()
        for prefix in ("最近的", "附近的", "可见的", "所有的"):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
        if text.casefold() in {"对面", "对手", "敌方", "敌军", "敌人", "enemy", "enemies"}:
            return "敌人"
        return text or "敌人"

    @classmethod
    def _refresh(cls, plan: dict[str, Any]) -> None:
        statuses = [item["status"] for item in plan["objectives"]]
        if any(value == "failed" for value in statuses):
            plan["status"] = "failed"
        elif all(value == "succeeded" for value in statuses):
            plan["status"] = "completed"
        elif any(value in {"running", "waiting"} for value in statuses):
            plan["status"] = "running"
        else:
            plan["status"] = "planned"
        plan["pending"] = plan["status"] not in cls.TERMINAL
