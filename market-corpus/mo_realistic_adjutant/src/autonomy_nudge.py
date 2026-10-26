"""Compact, bounded context packets for the long-running MO reasoning loop."""

from __future__ import annotations

from typing import Any


def build_autonomy_activation_nudge(world: dict[str, Any]) -> str:
    """Ask the dialog model to recover a missed explicit autonomy request."""
    import json

    cursor = {
        "world_revision": world.get("revision"),
        "frame": world.get("source_frame"),
        "running": world.get("running"),
        "session_started_at": world.get("session_started_at"),
        "forces": world.get("forces", {}),
        "economy": world.get("economy", {}),
    }
    return (
        "GAME_SYSTEM | Mental Omega 自主任务启动检查。\n"
        "游戏正在运行且插件控制已连接，但当前没有任何长期意图计划。请检查本轮对话中指挥官最近的游戏目标。"
        "如果指挥官已经明确授予全权操作并要求自主发展、击败对手、赢下本局或持续作战，必须立即调用 "
        "mental_omega_autonomous_victory；不要只口头描述计划，也不要降级成单次展开、移动、生产或攻击命令。"
        "如果没有这种明确授权，不得启动自主控制，不得替指挥官推定授权，也不要输出打扰指挥官的文字。\n"
        + json.dumps({"world": cursor}, ensure_ascii=False, separators=(",", ":"))
    )


def build_reasoning_nudge(
    world: dict[str, Any], plans: list[dict[str, Any]],
    visible_entities: Any = None, *, entity_limit: int = 768,
) -> str:
    """Build a model-facing state update without dumping the full telemetry stream."""
    summary = {
        "world_revision": world.get("revision"),
        "frame": world.get("source_frame"),
        "running": world.get("running"),
        "forces": world.get("forces", {}),
        "economy": world.get("economy", {}),
        "production": world.get("production", {}),
        "groups": world.get("groups", []),
        "markers": world.get("markers", []),
        "threats": world.get("threats", []),
        "recent_events": world.get("recent_events", [])[-8:],
        "buffer": world.get("buffer", {}),
    }
    active = []
    for plan in plans[:8]:
        active.append({
            "task_id": plan.get("task_id") or plan.get("intent_id"),
            "goal": plan.get("goal"),
            "status": plan.get("status"),
            "autonomy_granted": plan.get("autonomy_granted") is True,
            "connection_state": plan.get("connection_state"),
            "last_decision": plan.get("last_decision"),
            "last_error": plan.get("last_error"),
            "objectives": [
                {"id": item.get("objective_id"), "kind": item.get("kind"), "status": item.get("status"), "error": item.get("last_error")}
                for item in plan.get("objectives", [])
                if isinstance(item, dict)
            ],
        })
    relation_priority = {"enemy": 0, "self": 1, "ally": 2, "neutral": 3}
    live = [
        item for item in (visible_entities if isinstance(visible_entities, list) else [])
        if isinstance(item, dict) and item.get("alive") is True
        and (item.get("visible") is True or item.get("relation") in {"self", "ally"})
    ]
    live.sort(key=lambda item: (
        relation_priority.get(str(item.get("relation")), 9), int(item.get("object_id", 0))
    ))
    compact_entities = [
        {
            "id": item.get("object_id"), "side": item.get("relation"),
            "kind": item.get("kind"), "type": item.get("type_id"),
            "name": item.get("display_name"), "group": item.get("group_number"),
            "cell": item.get("cell"), "mission": item.get("mission_id"),
            "hp": item.get("health_ratio"), "ammo": item.get("ammo"),
        }
        for item in live[:max(1, min(int(entity_limit), 2048))]
    ]
    import json
    return (
        "GAME_SYSTEM | Mental Omega 长期自主决策回合。\n"
        "指挥官已对 autonomy_granted=true 的任务授予持续作战权限。请根据最新世界状态重新评估总目标，"
        "直接调用 mental_omega_plan_intent、mental_omega_submit_order_plan 或其他 MO 工具追加/修正动作。"
        "不要询问指挥官下一步选什么，不要只做口头建议，也不要重复提交仍在执行的 task_id。"
        "有安全且必要的动作时，本回合必须调用工具；只有动作仍在执行或没有安全动作时才继续观察。"
        "不要把记忆中的敌人当作当前可攻击目标。\n"
        + json.dumps(
            {"world": summary, "visible_entities": compact_entities, "active_tasks": active},
            ensure_ascii=False, separators=(",", ":"),
        )
    )
