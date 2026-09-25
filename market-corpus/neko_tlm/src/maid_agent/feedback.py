"""Routes useful maid action feedback into the N.E.K.O conversation."""

import asyncio
import json
import time
from typing import Dict

from .models import ActionRecord

_KIND_NAMES = {
    "navigate": "寻路",
    "return_to_position": "安全返程",
    "harvest_blocks": "采集",
    "autonomous_mining": "自主挖矿",
    "attack": "攻击",
}


class ActionFeedbackHandler:
    def __init__(self, plugin, progress_interval: float = 1.5, clock=None):
        self._plugin = plugin
        self._progress_interval = max(1.0, min(2.0, float(progress_interval)))
        self._clock = clock or time.monotonic
        self._last_progress: Dict[str, tuple] = {}
        self._finished = set()
        self._decisions = set()
        self._failure_counts: Dict[str, int] = {}

    async def progress(self, record: ActionRecord) -> bool:
        key = self._key(record)
        now = self._clock()
        previous = self._last_progress.get(key)
        if previous and previous[0] == record.stage and now - previous[1] < self._progress_interval:
            return False
        self._last_progress[key] = (record.stage, now)
        await self._plugin._push_minecraft_context(
            self._progress_text(record),
            ai_behavior="read",
            priority=1,
            metadata={
                "description": "Minecraft 女仆 Agent 动作进度",
                "action_id": record.action_id,
                "generation": record.generation,
            },
            aggregate=True,
            coalesce_key=f"mc_maid_action_progress:{record.maid_id or record.action_id}",
        )
        return True

    async def decision_required(self, record: ActionRecord) -> bool:
        key = f"{self._key(record)}:{record.sequence}"
        if key in self._decisions:
            return False
        await self._push_with_retry(
            self._progress_text(record)
                + " 服务端标记此阶段需要新的决策。你必须基于服务端事实给出一个具体、可执行的"
                  "解决方案，不能只道歉、复述错误或泛泛询问。若方案仍在玩家原始授权范围内且不"
                  "增加危险或破坏范围，立即调用相应工具执行；否则明确说明方案并只询问缺少的那一项确认。",
            ai_behavior="respond", priority=4,
            metadata={"description": "Minecraft 女仆 Agent 动作需要决策",
                      "action_id": record.action_id, "generation": record.generation},
            aggregate=False, coalesce_key=None,
        )
        self._decisions.add(key)
        return True

    async def finished(self, record: ActionRecord) -> bool:
        key = self._key(record)
        if key in self._finished:
            return False
        same_failure_count = self._same_failure_count(record)
        await self._push_with_retry(
            self._finished_text(record, same_failure_count),
            ai_behavior="respond",
            priority=5,
            metadata={
                "description": "Minecraft 女仆 Agent 动作结束",
                "action_id": record.action_id,
                "generation": record.generation,
                "status": record.status,
                "end_reason": record.end_reason,
            },
            aggregate=False,
            coalesce_key=None,
        )
        self._finished.add(key)
        self._last_progress.pop(key, None)
        return True

    def _same_failure_count(self, record: ActionRecord) -> int:
        prefix = f"{record.maid_id}:{record.kind}:"
        if record.status == "SUCCEEDED":
            for key in list(self._failure_counts):
                if key.startswith(prefix):
                    self._failure_counts.pop(key, None)
            return 0
        if record.status in {"CANCELLED", "SUPERSEDED"}:
            return 0
        message = record.result.get("message") if isinstance(record.result, dict) else ""
        signature = f"{prefix}{record.end_reason}:{message}"
        count = self._failure_counts.get(signature, 0) + 1
        self._failure_counts[signature] = count
        while len(self._failure_counts) > 256:
            self._failure_counts.pop(next(iter(self._failure_counts)))
        return count

    async def _push_with_retry(self, text: str, **kwargs) -> None:
        """Retry important decision/terminal feedback before marking it delivered."""
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
                        "[MaidAgent] feedback enqueue failed attempt=%s/3: %s",
                        attempt + 1, exc,
                    )
                if attempt == 2:
                    raise

    @staticmethod
    def _key(record: ActionRecord) -> str:
        return f"{record.action_id}:{record.generation}"

    @staticmethod
    def _progress_text(record: ActionRecord) -> str:
        kind = _KIND_NAMES.get(record.kind, record.kind or "动作")
        text = f"女仆 Agent 的{kind}动作正在执行，阶段：{record.stage or record.status}。"
        planner = record.detail.get("planner_decision") \
            if isinstance(record.detail, dict) else None
        if isinstance(planner, dict):
            choice = str(planner.get("choice") or "unknown")
            direction = str(planner.get("direction") or "unknown")
            shape = str(planner.get("shape") or "unknown")
            cost = planner.get("total_cost")
            text += f" MiningPlanner 选择 {choice}，方向 {direction}，形状 {shape}"
            if isinstance(cost, (int, float)):
                text += f"，预计成本 {cost}"
            text += "。"
        text += "这是执行进度，不要仅因这条进度消息打断玩家。"
        return text

    @staticmethod
    def _finished_text(record: ActionRecord, same_failure_count: int = 0) -> str:
        kind = _KIND_NAMES.get(record.kind, record.kind or "动作")
        result = record.result if isinstance(record.result, dict) else {}
        partial = result.get("partial") is True
        request_satisfied = result.get("request_satisfied") is True
        request_unsatisfied = result.get("request_satisfied") is False
        harvest_verified = record.kind != "harvest_blocks" or request_satisfied
        if (record.status == "SUCCEEDED" and not partial
                and not request_unsatisfied and harvest_verified):
            outcome = "已经成功完成"
        elif record.status == "SUCCEEDED":
            outcome = "只完成了本次部分采集，尚未满足请求"
        else:
            reason = record.end_reason or record.status
            outcome = f"已结束，状态为 {record.status}，原因是 {reason}"
        text = f"女仆 Agent 的{kind}动作（action_id={record.action_id}）{outcome}。"
        if record.kind == "harvest_blocks":
            harvested = max(0, _integer(result.get("harvested")))
            requested = max(0, _integer(result.get("requested")))
            text += (
                f" 服务端确认本动作实际采集 {harvested} 块"
                + (f"，本动作请求 {requested} 块" if requested else "")
                + ";request_satisfied="
                + ("true" if request_satisfied else "false" if request_unsatisfied else "unknown")
                + "。"
            )
            if partial or not request_satisfied:
                text += (
                    " 这不能证明玩家的总数量目标完成，禁止把本动作说成已经采够；"
                    "需要由高级 gather_blocks Skill 的累计终态确认。"
                )
        message = result.get("message")
        if message:
            text += f" 服务端信息：{message}。"
        retry_hint = record.result.get("retry_hint") if isinstance(record.result, dict) else None
        selector_missing = message == "no_matching_block_found"
        prospect_safety_limit = message in {
            "prospecting_budget_exhausted_without_match",
            "prospecting_distance_or_depth_budget_exhausted",
            "prospecting_excavation_budget_exhausted",
            "prospecting_excavation_budget_would_be_exceeded",
            "target_route_excavation_budget_would_be_exceeded",
            "prospecting_segment_limit_exhausted",
        }
        if message and message.startswith(("prospecting_", "target_route_")):
            prospect_safety_limit = prospect_safety_limit or any(
                marker in message for marker in (
                    "budget_exhausted", "budget_would_be_exceeded",
                    "distance_or_depth", "segment_limit",
                )
            )
        path_origin_drift = message in {
            "maid_is_no_longer_at_terrain_step_origin",
            "terrain_origin_drift_replan_exhausted",
        }
        local_navigation_edge = message in {
            "native_navigation_cannot_reach_terrain_step",
            "native_navigation_rejected_terrain_step",
            "native_navigation_finished_before_terrain_step",
            "controlled_descend_made_no_progress",
        }
        prospect_dead_end = message in {
            "no_safe_prospecting_step_found",
            "all_auto_prospect_directions_exhausted",
        }
        if retry_hint and not selector_missing and not prospect_safety_limit \
                and not path_origin_drift and not local_navigation_edge \
                and not prospect_dead_end:
            text += f" 重试提示：{retry_hint}。"
        if message == "target_chunk_not_loaded":
            text += (
                " 如果玩家请求的是某类附近资源而不是明确坐标，请立刻改用对应的 block/tag "
                "selector 在已加载区块和 search_radius 内重试一次；不要强制加载区块，不要让玩家靠近"
                "这个未经确认的坐标，也不要原样重试 target_pos。"
            )
        if selector_missing:
            text += (
                " 不要自动重复同一动作。附近搜索没有匹配到该 selector；如果请求的是矿石，"
                "请确认使用正确的 minecraft:*_ores 标签。"
            )
        if prospect_safety_limit:
            diagnostics = []
            for key, label in (
                ("prospect_segment", "当前段"),
                ("prospect_max_segments", "段数上限"),
                ("prospect_segment_steps", "当前段步数"),
                ("prospect_steps", "已前进步数"),
                ("prospect_total_step_limit", "总步数上限"),
                ("prospect_descent_steps", "已下降步数"),
                ("prospect_total_descent_limit", "总下降上限"),
                ("prospect_blocks_cleared", "已开凿方块"),
                ("prospect_excavation_budget", "开凿预算"),
                ("prospect_remaining_excavation_budget", "剩余开凿预算"),
            ):
                value = record.result.get(key) if isinstance(record.result, dict) else None
                if value is not None:
                    diagnostics.append(f"{label}={value}")
            if diagnostics:
                text += " 服务端安全限制：" + "，".join(diagnostics) + "。"
            text += (
                " 这是旧版服务端的距离、深度、段数或开凿预算终止，并不表示资源 selector 错误，"
                "也与附近扫描半径无关。当前实现已取消这些总量上限；先确认游戏实际加载的是最新"
                "JAR和插件，再决定是否重新启动动作。"
            )
        if path_origin_drift:
            text += (
                " 这是路径执行位置偏移，不是矿石 selector 或目标方块选择错误；服务端已经耗尽"
                "本次有界重规划。不要改变 selector 或自动原样重试，可让玩家确认女仆没有被推挤后再决定。"
            )
        if local_navigation_edge:
            mining_plan = record.result.get("mining_plan") \
                if isinstance(record.result, dict) else None
            selector = record.result.get("selector") \
                if isinstance(record.result, dict) else None
            text += " 自定义地形路线已经找到，但当前局部移动边无法执行；这不是扫描范围问题。"
            if mining_plan and mining_plan != "nearby":
                text += (
                    "不要增大 search_radius 或原样重试；持续探矿应根据实时位置改选安全开掘方向。"
                )
            elif isinstance(selector, str) and selector.startswith("position:"):
                text += (
                    "保留玩家指定方块，不要换目标；应先安全重定位女仆，或说明局部清障方案并在需要时请求确认。"
                )
            else:
                text += (
                    "服务端已耗尽本轮可执行候选路线；不要增大 search_radius 或原样重试，"
                    "应选择其他附近目标或提出不同的安全清障方案。"
                )
        if prospect_dead_end:
            result = record.result if isinstance(record.result, dict) else {}
            exhausted = result.get("prospect_directions_exhausted") is True
            attempted = result.get("prospect_attempted_directions")
            origin = result.get("prospect_origin")
            attempt_count = result.get("prospect_direction_attempts")
            cardinal_directions = {"north", "east", "south", "west"}
            if exhausted and isinstance(attempted, list) \
                    and all(isinstance(value, str) for value in attempted) \
                    and set(attempted) == cardinal_directions \
                    and attempt_count == 4 \
                    and isinstance(origin, dict) \
                    and all(axis in origin for axis in ("x", "y", "z")):
                text += (
                    " 服务端已从同一实时起点尝试或排除四个水平方向，在当前安全与执行约束下"
                    "都未产生可执行的下一探矿步；具体原因可能是净空、支撑、危险地形或局部"
                    "执行受阻。这不是 selector 或 search_radius 问题。禁止扩大"
                    "search_radius、轮换同一组方向或原样重启。必须采用几何上不同的方案："
                    "若世界感知能确认安全新起点，先导航过去再保留原 selector 继续；否则说明"
                    "具体阻挡和拟清除位置，并在扩大破坏范围前请求玩家确认。"
                )
            else:
                text += (
                    " 当前探矿方向没有安全下一步；这不是 selector 或 search_radius 问题。"
                    "不要增大 search_radius 或原样重试，应安全重定位，或提出具体清障方案。"
                )
        construction_reason = str(
            (record.result.get("blocked_reason") if isinstance(record.result, dict) else "")
            or message
            or record.end_reason
            or ""
        ).strip().upper()
        text += _construction_recovery_text(construction_reason)
        if record.status not in {"SUCCEEDED", "CANCELLED", "SUPERSEDED"}:
            if isinstance(record.result, dict) and record.result:
                safe_result = {
                    key: value for key, value in record.result.items()
                    if key != "retry_hint"
                }
                diagnostic = json.dumps(
                    safe_result, ensure_ascii=False, separators=(",", ":"),
                    default=str,
                )
                text += f" 结构化诊断：{diagnostic[:4000]}。"
            text += (
                " 这是需要解决的动作故障：你必须给出一个具体方案，不能只说失败、道歉、复述日志或"
                "让玩家自己想办法。方案没有扩大玩家授权、没有新增危险且所需资源已具备时，立即调用"
                "工具执行一次不同的恢复方案；涉及缺工具、保护区、危险地形、扩大破坏范围或玩家选择时，"
                "先清楚说明拟采取的方案并请求必要确认。禁止用完全相同的参数反复重启形成循环。"
            )
            if same_failure_count >= 2:
                text += (
                    f" 这是同类故障连续第{same_failure_count}次出现；禁止再次自动提交相同或等价参数。"
                    "必须改用不同方案，若没有安全的不同方案则把具体方案和必要选择交给玩家确认。"
                )
        text += "请根据真实终态回应玩家；失败时不要声称动作成功。"
        return text


def _integer(value, fallback=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _construction_recovery_text(reason: str) -> str:
    if reason == "NO_BUILDING_MATERIAL":
        return (
            " 具体恢复方案：让玩家给女仆背包补充普通实心方块，或经确认后改用"
            " placement_policy=disabled 并选择不需要放置的不同路线；不要原样重试。"
        )
    if reason == "PLACEMENT_BUDGET_EXHAUSTED":
        return (
            " 具体恢复方案：经玩家确认后把 max_placements 改为0，或选择不需继续放置的不同路线；"
            "这不是缺矿。"
        )
    if reason == "WATER_SEAL_FAILED":
        return (
            " 具体恢复方案：更换方向或矿道形状以绕开水体，无法确认安全路线时停止；"
            "禁止在同一水体原样循环。"
        )
    if reason == "WATER_SEAL_REQUIRES_DRY_START":
        return (
            " 女仆占用了需要封堵的水格；先将她移动到附近有支撑的干燥位置，再选择不同方向或"
            "矿道形状重启，禁止把方块放进女仆身体。"
        )
    if reason == "PLACEMENT_PROTECTED":
        return (
            " 该位置受保护，绝不能绕过保护；只能让玩家将女仆移出保护区、改走不需放置的路线或终止。"
        )
    if reason == "PLACEMENT_SPACE_OBSTRUCTED":
        return (
            " 放置空间被实体占用；先让玩家或其他实体离开施工格，"
            "或改走不需修建该支撑点的路线，禁止原样重试。"
        )
    if reason in {"PLACEMENT_CONTEXT_CANNOT_PLACE", "PLACEMENT_STATE_INVALID"}:
        return (
            " 当前支撑位不具备合法放置条件；改选支撑位、高度或不同路线，"
            "不要对同一坐标重复提交。"
        )
    return ""
