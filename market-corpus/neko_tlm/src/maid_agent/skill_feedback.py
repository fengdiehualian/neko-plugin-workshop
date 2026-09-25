"""Conversation feedback for checkpointed high-level maid skills."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Mapping


class SkillFeedbackHandler:
    """Emit only skill-level blocked and terminal decisions to the main LLM.

    Child maid actions are owned by ``SkillRunner`` and deliberately do not
    reach the ordinary action feedback path.  The runner persists notification
    revisions; the in-memory sets below only close the small duplicate window
    before that checkpoint has been written.
    """

    def __init__(self, plugin, *, progress_interval: float = 1.5, clock=None):
        self._plugin = plugin
        self._clock = clock or time.monotonic
        self._progress_interval = max(1.0, min(2.0, float(progress_interval)))
        self._last_progress: dict[str, tuple[str, float]] = {}
        self._blocked_sent: set[tuple[str, int]] = set()
        self._finished_sent: set[tuple[str, int, str]] = set()

    async def progress(self, run_snapshot: Mapping[str, Any]) -> bool:
        """Optionally expose aggregated skill progress without waking the LLM."""
        run = _snapshot(run_snapshot)
        skill_id = str(run.get("skill_id") or "")
        if not skill_id:
            return False
        child = run.get("child_action") \
            if isinstance(run.get("child_action"), Mapping) else {}
        result = run.get("result") if isinstance(run.get("result"), Mapping) else {}
        stage = str(child.get("stage") or result.get("stage") or run.get("status") or "RUNNING")
        now = self._clock()
        previous = self._last_progress.get(skill_id)
        if previous and previous[0] == stage \
                and now - previous[1] < self._progress_interval:
            return False
        try:
            await self._plugin._push_minecraft_context(
                _progress_text(run),
                ai_behavior="read",
                priority=1,
                metadata={
                    "description": "Minecraft 女仆 Skill 进度",
                    "skill_id": skill_id,
                    "skill_name": str(run.get("skill_name") or ""),
                    "revision": _integer(run.get("revision")),
                    "child_kind": str(child.get("kind") or ""),
                    "stage": stage,
                },
                aggregate=True,
                coalesce_key=f"mc_maid_skill_progress:{run.get('maid_id') or skill_id}",
            )
        except Exception as exc:
            logger = getattr(self._plugin, "logger", None)
            if logger is not None:
                logger.warning("[MaidSkill] progress feedback failed: %s", exc)
            return False
        self._last_progress[skill_id] = (stage, now)
        return True

    async def blocked(self, run_snapshot: Mapping[str, Any]) -> bool:
        run = _snapshot(run_snapshot)
        skill_id = str(run.get("skill_id") or "")
        revision = _integer(run.get("revision"))
        notified_revision = _integer(run.get("blocked_notification_revision"))
        if not skill_id or notified_revision >= revision > 0:
            return False
        key = (skill_id, revision)
        if key in self._blocked_sent:
            return False
        await self._push_with_retry(
            _blocked_text(run),
            ai_behavior="respond",
            priority=5,
            metadata={
                "description": "Minecraft 女仆 Skill 阻塞",
                "skill_id": skill_id,
                "skill_name": str(run.get("skill_name") or ""),
                "revision": revision,
                "status": "BLOCKED",
                "reason": str(run.get("last_failure_reason") or ""),
                "decision_required": bool(run.get("decision_required", True)),
            },
            aggregate=False,
            coalesce_key=None,
        )
        logger = getattr(self._plugin, "logger", None)
        if logger is not None:
            logger.info(
                "[MaidSkill] blocked feedback queued feedback_id=%s reason=%s",
                f"skill:{skill_id}:blocked:{revision}",
                str(run.get("last_failure_reason") or "BLOCKED"),
            )
        self._blocked_sent.add(key)
        self._last_progress.pop(skill_id, None)
        return True

    async def finished(self, run_snapshot: Mapping[str, Any]) -> bool:
        run = _snapshot(run_snapshot)
        status = str(run.get("status") or "").upper()
        if status == "BLOCKED":
            return await self.blocked(run)
        skill_id = str(run.get("skill_id") or "")
        revision = _integer(run.get("revision"))
        if not skill_id:
            return False
        key = (skill_id, revision, status)
        if key in self._finished_sent:
            return False
        await self._push_with_retry(
            _finished_text(run),
            ai_behavior="respond",
            priority=5,
            metadata={
                "description": "Minecraft 女仆 Skill 结束",
                "skill_id": skill_id,
                "skill_name": str(run.get("skill_name") or ""),
                "revision": revision,
                "status": status,
                "reason": str(run.get("last_failure_reason") or ""),
            },
            aggregate=False,
            coalesce_key=None,
        )
        logger = getattr(self._plugin, "logger", None)
        if logger is not None:
            logger.info(
                "[MaidSkill] terminal feedback queued feedback_id=%s status=%s",
                f"skill:{skill_id}:terminal:{revision}", status,
            )
        self._finished_sent.add(key)
        self._last_progress.pop(skill_id, None)
        return True

    async def _push_with_retry(self, text: str, **kwargs) -> None:
        for attempt, delay in enumerate((0.0, 0.5, 1.5)):
            if delay:
                await asyncio.sleep(delay)
            try:
                queued = await self._plugin._push_minecraft_context(text, **kwargs)
                if queued is False:
                    raise RuntimeError("Minecraft context push was not queued")
                return
            except Exception as exc:
                logger = getattr(self._plugin, "logger", None)
                if logger is not None:
                    logger.warning(
                        "[MaidSkill] feedback enqueue failed attempt=%s/3: %s",
                        attempt + 1, exc,
                    )
                if attempt == 2:
                    raise


def _snapshot(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        data = as_dict()
        return dict(data) if isinstance(data, Mapping) else {}
    return {}


def _integer(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _progress_text(run: Mapping[str, Any]) -> str:
    result = run.get("result") if isinstance(run.get("result"), Mapping) else {}
    child = run.get("child_action") \
        if isinstance(run.get("child_action"), Mapping) else {}
    collected = _integer(run.get("collected_count"))
    target = _integer((run.get("args") or {}).get("target_count")) \
        if isinstance(run.get("args"), Mapping) else 0
    stage = str(child.get("stage") or result.get("stage") or run.get("status") or "RUNNING")
    kind = str(child.get("kind") or "内部动作")
    text = (
        f"女仆 Skill {run.get('skill_name') or '任务'} 正在执行，内部动作：{kind}，阶段：{stage}，"
        f"已采集 {collected}/{target if target > 0 else '?'} 个目标方块。"
        "这是内部进度，不要仅因本消息打断玩家或另行启动动作。"
    )
    detail = child.get("detail") if isinstance(child.get("detail"), Mapping) else {}
    planner = detail.get("planner_decision") \
        if isinstance(detail.get("planner_decision"), Mapping) else None
    if planner is None:
        java_progress = result.get("java_progress") \
            if isinstance(result.get("java_progress"), Mapping) else {}
        planner = java_progress.get("planner_decision") \
            if isinstance(java_progress.get("planner_decision"), Mapping) else None
    return text + _planner_progress_summary(planner)


def _planner_progress_summary(planner: Any) -> str:
    if not isinstance(planner, Mapping):
        return ""
    choice = str(planner.get("choice") or "unknown")
    direction = str(planner.get("direction") or "unknown")
    shape = str(planner.get("shape") or "unknown")
    cost = planner.get("total_cost")
    cost_text = f"，预计成本 {cost}" if isinstance(cost, (int, float)) else ""
    return (
        f" 本轮 Java MiningPlanner 选择 {choice}，方向 {direction}，形状 {shape}{cost_text}；"
        "这是服务端成本规划结果，不要逐格改写路线。"
    )


def _blocked_text(run: Mapping[str, Any]) -> str:
    result = run.get("result") if isinstance(run.get("result"), Mapping) else {}
    reason = str(run.get("last_failure_reason") or result.get("reason") or "BLOCKED")
    suggestions = result.get("suggestions")
    decision = run.get("decision_context") \
        if isinstance(run.get("decision_context"), Mapping) else result.get("decision")
    safe_result = dict(result)
    diagnostic = json.dumps(
        safe_result, ensure_ascii=False, separators=(",", ":"), default=str
    )[:4000]
    text = (
        f"女仆 Skill {run.get('skill_name') or '任务'}（skill_id={run.get('skill_id')}）"
        f"已阻塞，原因：{reason}。已采集 {max(0, _integer(run.get('collected_count')))} 个目标方块。"
        f"结构化诊断：{diagnostic}。"
    )
    if isinstance(suggestions, list) and suggestions:
        text += "服务端给出的候选方案：" + json.dumps(
            suggestions, ensure_ascii=False, separators=(",", ":"), default=str
        )[:2000] + "。"
    if isinstance(decision, Mapping) and decision:
        text += "结构化决策闸门：" + json.dumps(
            dict(decision), ensure_ascii=False, separators=(",", ":"), default=str
        )[:2000] + "。"
    construction_instruction = _construction_recovery_instruction(reason)
    if construction_instruction:
        text += construction_instruction
    text += (
        "这是 Skill 终态，不会自动继续。你必须基于 decision 或 suggestions 给出一个具体方案；"
        "若 Java 给出 decision，则按 blocked_reason 和 adjustable_fields 选择不同参数。"
        "当前没有暂停、原地恢复或 submit-decision 协议；方案依据充分后只能调用 "
        "mc_start_skill 创建新的 Skill；"
        "若涉及换层、危险地形、清除额外方块或玩家选择，先说明具体方案并请求确认。"
        "禁止原样重启、编造坐标或声称仍在执行。"
    )
    return text


def _construction_recovery_instruction(reason: str) -> str:
    code = str(reason or "").strip().upper()
    if code == "NO_BUILDING_MATERIAL":
        return (
            "这是建筑材料不足：具体方案只能是让玩家给女仆背包补充普通实心方块，或经确认后"
            "改用 placement_policy=disabled 并选择不需要放置的不同路线；不要原样重试。"
        )
    if code == "PLACEMENT_BUDGET_EXHAUSTED":
        return (
            "这是人工放置上限耗尽：经玩家确认后可将 max_placements 改为0，或选择不同路线；"
            "不要把它误判成缺矿。"
        )
    if code == "WATER_SEAL_FAILED":
        return (
            "封水未能安全完成：应提出换方向/换矿道形状或终止的具体方案，不要在同一水体原样循环。"
        )
    if code == "WATER_SEAL_REQUIRES_DRY_START":
        return (
            "女仆当前身体占用了需要封堵的水格：应先移动到附近有支撑的干燥位置，再用不同方向或"
            "矿道形状新建任务；禁止尝试把方块放进女仆身体。"
        )
    if code == "PLACEMENT_PROTECTED":
        return (
            "目标位置禁止放置：绝不能绕过保护；只能让玩家把女仆移出保护区、改走不需放置的路线或终止。"
        )
    if code == "BACKPACK_FULL":
        return (
            "这是女仆背包已满或无法完整容纳下一目标掉落：当前没有正在收尾的锁定矿脉；"
            "可能尚未开始采矿，也可能刚挖完一条矿脉。"
            "具体方案只能是：让女仆返回基地/玩家身边，并由玩家或已有卸货流程把物品存入箱子；"
            "或明确丢弃、移走物品来腾出容量后重新启动 mine_ore；或终止挖矿。"
            "只换 selector 不能创造背包容量，禁止只换 selector、原样重启或继续在同一位置挖矿。"
        )
    return ""


def _finished_text(run: Mapping[str, Any]) -> str:
    status = str(run.get("status") or "UNKNOWN").upper()
    result = run.get("result") if isinstance(run.get("result"), Mapping) else {}
    reason = str(run.get("last_failure_reason") or result.get("reason") or status)
    diagnostic = json.dumps(
        dict(result), ensure_ascii=False, separators=(",", ":"), default=str
    )[:4000]
    if status == "SUCCEEDED":
        outcome = "已经成功完成"
    elif status == "CANCELLED":
        outcome = "已取消"
    else:
        outcome = f"已结束，状态为 {status}，原因是 {reason}"
    return (
        f"女仆 Skill {run.get('skill_name') or '任务'}（skill_id={run.get('skill_id')}）{outcome}。"
        f"实际采集数量：{max(0, _integer(run.get('collected_count')))}。"
        f"结构化结果：{diagnostic}。请按真实 Skill 终态和实际数量回应玩家；"
        "只有 SUCCEEDED 才能声称该 Skill 目标完成。若玩家还要求了后续任务，"
        "现在必须调用对应真实工具启动它，不能只口头声称已经开始；失败时不得声称成功。"
    )
