"""LLM 工具业务逻辑 — 女仆状态、行为、聊天与 Agent 动作工具。"""

import re
import uuid

from plugin.sdk.plugin import Err, Ok

from . import plan as _plan
from . import task_resolver
from .maid_agent import ActionValidationError, MaidActionService

_ITEM_ALIASES = {
    "火把": "minecraft:torch",
    "普通火把": "minecraft:torch",
    "torch": "minecraft:torch",
    "灵魂火把": "minecraft:soul_torch",
    "soul_torch": "minecraft:soul_torch",
}

_ORE_SELECTOR_ALIASES = {
    "煤": ("tag", "minecraft:coal_ores"),
    "煤矿": ("tag", "minecraft:coal_ores"),
    "coal": ("tag", "minecraft:coal_ores"),
    "coal ore": ("tag", "minecraft:coal_ores"),
    "铁": ("tag", "minecraft:iron_ores"),
    "铁矿": ("tag", "minecraft:iron_ores"),
    "iron": ("tag", "minecraft:iron_ores"),
    "iron ore": ("tag", "minecraft:iron_ores"),
    "铜": ("tag", "minecraft:copper_ores"),
    "铜矿": ("tag", "minecraft:copper_ores"),
    "copper": ("tag", "minecraft:copper_ores"),
    "copper ore": ("tag", "minecraft:copper_ores"),
    "金": ("tag", "minecraft:gold_ores"),
    "金矿": ("tag", "minecraft:gold_ores"),
    "gold": ("tag", "minecraft:gold_ores"),
    "gold ore": ("tag", "minecraft:gold_ores"),
    "红石": ("tag", "minecraft:redstone_ores"),
    "红石矿": ("tag", "minecraft:redstone_ores"),
    "redstone": ("tag", "minecraft:redstone_ores"),
    "redstone ore": ("tag", "minecraft:redstone_ores"),
    "青金石": ("tag", "minecraft:lapis_ores"),
    "青金": ("tag", "minecraft:lapis_ores"),
    "lapis": ("tag", "minecraft:lapis_ores"),
    "lapis lazuli": ("tag", "minecraft:lapis_ores"),
    "钻石": ("tag", "minecraft:diamond_ores"),
    "钻石矿": ("tag", "minecraft:diamond_ores"),
    "diamond": ("tag", "minecraft:diamond_ores"),
    "diamond ore": ("tag", "minecraft:diamond_ores"),
    "绿宝石": ("tag", "minecraft:emerald_ores"),
    "绿宝石矿": ("tag", "minecraft:emerald_ores"),
    "emerald": ("tag", "minecraft:emerald_ores"),
    "emerald ore": ("tag", "minecraft:emerald_ores"),
    "石英": ("block", "minecraft:nether_quartz_ore"),
    "下界石英": ("block", "minecraft:nether_quartz_ore"),
    "quartz": ("block", "minecraft:nether_quartz_ore"),
    "远古残骸": ("block", "minecraft:ancient_debris"),
    "ancient debris": ("block", "minecraft:ancient_debris"),
}

_GATHER_SELECTOR_ALIASES = {
    "树": ("tag", "minecraft:logs"),
    "树木": ("tag", "minecraft:logs"),
    "木头": ("tag", "minecraft:logs"),
    "原木": ("tag", "minecraft:logs"),
    "log": ("tag", "minecraft:logs"),
    "logs": ("tag", "minecraft:logs"),
    "wood": ("tag", "minecraft:logs"),
    "石头": ("block", "minecraft:stone"),
    "stone": ("block", "minecraft:stone"),
    "圆石": ("block", "minecraft:cobblestone"),
    "cobblestone": ("block", "minecraft:cobblestone"),
    "泥土": ("block", "minecraft:dirt"),
    "dirt": ("block", "minecraft:dirt"),
    "沙子": ("block", "minecraft:sand"),
    "sand": ("block", "minecraft:sand"),
    "沙砾": ("block", "minecraft:gravel"),
    "gravel": ("block", "minecraft:gravel"),
    "黏土": ("block", "minecraft:clay"),
    "粘土": ("block", "minecraft:clay"),
    "clay": ("block", "minecraft:clay"),
}

_RESOURCE_ID_PATTERN = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")
_PLAIN_RESOURCE_PATH_PATTERN = re.compile(r"^[a-z0-9_./-]+$")
_COMMON_GATHER_TAG_PATHS = {
    "logs", "leaves", "wool", "flowers", "saplings", "dirt", "sand",
}


def _normalize_target_count(value, *, default=1):
    if value in (None, ""):
        return int(default)
    if isinstance(value, bool):
        raise ValueError("target_count must be an integer between 1 and 4096")
    try:
        count = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "target_count must be an integer between 1 and 4096"
        ) from exc
    if isinstance(value, float) and not value.is_integer():
        raise ValueError("target_count must be an integer between 1 and 4096")
    if not 1 <= count <= 4096:
        raise ValueError("target_count must be an integer between 1 and 4096")
    return count


def _normalize_selector_resource_id(value, *, default_namespace=True):
    text = str(value or "").strip().lower()
    if default_namespace and ":" not in text:
        text = f"minecraft:{text}"
    if not _RESOURCE_ID_PATTERN.fullmatch(text):
        raise ValueError("resource must be a valid namespaced Minecraft resource ID")
    return text


def _resolve_ore_selector(ore):
    text = " ".join(str(ore or "").strip().lower().split())
    if not text:
        raise ValueError("ore is required")
    alias = _ORE_SELECTOR_ALIASES.get(text)
    if alias is not None:
        return {"type": alias[0], "id": alias[1]}
    if text.startswith("#"):
        return {
            "type": "tag",
            "id": _normalize_selector_resource_id(text[1:]),
        }
    if ":" in text:
        resource_id = _normalize_selector_resource_id(text, default_namespace=False)
        selector_type = "tag" if resource_id.split(":", 1)[1].endswith("_ores") else "block"
        return {"type": selector_type, "id": resource_id}
    if _PLAIN_RESOURCE_PATH_PATTERN.fullmatch(text) and text.endswith(("_ore", "_ores")):
        selector_type = "tag" if text.endswith("_ores") else "block"
        return {"type": selector_type, "id": f"minecraft:{text}"}
    raise ValueError(
        "unknown ore; use a common Chinese/English ore name or a namespaced block/tag ID"
    )


def _resolve_gather_selector(resource):
    text = " ".join(str(resource or "").strip().lower().split())
    if not text:
        raise ValueError("resource is required")
    alias = _GATHER_SELECTOR_ALIASES.get(text)
    if alias is not None:
        return {"type": alias[0], "id": alias[1]}
    explicit_tag = text.startswith("#")
    if explicit_tag:
        text = text[1:]
    if ":" in text:
        resource_id = _normalize_selector_resource_id(text, default_namespace=False)
    elif _PLAIN_RESOURCE_PATH_PATTERN.fullmatch(text):
        resource_id = _normalize_selector_resource_id(text)
    else:
        raise ValueError(
            "unknown resource; use a common resource name or a namespaced block/tag ID"
        )
    path = resource_id.split(":", 1)[1]
    selector_type = (
        "tag" if explicit_tag or path in _COMMON_GATHER_TAG_PATHS else "block"
    )
    return {"type": selector_type, "id": resource_id}

_COMBAT_TASK_WEAPONS = {
    "attack": (
        "_sword", "_axe", ":trident", ":mace", "dagger", "spear",
        "katana", "slashblade",
    ),
    "ranged_attack": (":bow",),
    "crossbow_attack": (":crossbow",),
    "danmaku_attack": ("gohei", "danmaku", "spell", "wand", "gun"),
    "trident_attack": (":trident",),
}


def _normalize_item_id(item):
    text = str(item or "").strip()
    if not text:
        return ""
    return _ITEM_ALIASES.get(text.lower(), _ITEM_ALIASES.get(text, text))


def _combat_task_id(task):
    resolved = task_resolver.resolve_task_name(str(task or "").strip())
    short_id = str(resolved or "").split(":")[-1].lower()
    return short_id if short_id in _COMBAT_TASK_WEAPONS else ""


def _weapon_matches_combat_task(task_id, item_id):
    item = str(item_id or "").strip().lower()
    if not item:
        return False
    return any(marker in item for marker in _COMBAT_TASK_WEAPONS[task_id])


def _authoritative_combat_compatibility(equipment, task_id):
    """Bridge 提供数据时，返回 TLM 自身的武器兼容判定。"""
    compatibility = equipment.get("combat_task_compatibility")
    if not isinstance(compatibility, dict):
        return None
    for task_uid, compatible in compatibility.items():
        short_id = str(task_uid or "").split(":")[-1].lower()
        if short_id == task_id and isinstance(compatible, bool):
            return compatible
    return None


async def _guard_combat_task_equipment(plugin, task):
    """只有最新装备查询确认主手是武器时，才允许切换到战斗工作。"""
    task_id = _combat_task_id(task)
    if not task_id:
        return None
    equipment = await do_game_context(plugin, category="equipment")
    if equipment.get("is_error"):
        return Err("无法确认女仆主手装备，已拒绝启动战斗工作")
    main_hand = str(equipment.get("main_hand") or "")
    compatible = _authoritative_combat_compatibility(equipment, task_id)
    if compatible is None:
        compatible = _weapon_matches_combat_task(task_id, main_hand)
    if not compatible:
        return Err(
            f"女仆主手不是适合 {task_id} 的武器（当前：{main_hand or '空手'}），"
            "已拒绝启动战斗工作"
        )
    return None


async def do_maid_status(plugin):
    if not plugin.connected:
        return {"output": {"error": "Not connected to Minecraft"}, "is_error": True, "error": "NOT_CONNECTED"}
    result = await plugin._send_request({"type": "get_maid_status"})
    if result.get("type") == "error":
        return {"output": result.get("data", {}), "is_error": True, "error": "REQUEST_FAILED"}
    maids = result.get("data", {}).get("maids", [])
    for maid in maids:
        plugin._maid_status_cache[maid.get("id", "")] = maid
    return maid_status_payload(plugin, maids)


def maid_status_payload(plugin, maids, *, compact=False):
    payload = {
        "maids": [_compact_maid_status(m) for m in maids] if compact else maids,
    }
    selected = _select_status_maid(plugin, maids)
    if not selected:
        return payload
    current_task = selected.get("task", "")
    current_task_name = _task_name_for_id(current_task, selected.get("available_tasks", []))
    payload["selected_maid"] = _compact_maid_status(selected)
    payload["current_mode"] = {
        "id": current_task,
        "name": current_task_name,
    }
    payload["current_mode_answer"] = f"当前真实模式是：{current_task_name or current_task or '未知'}"
    payload["available_modes"] = _normalize_available_tasks(selected.get("available_tasks", []))
    return payload


def _compact_maid_status(maid):
    available = maid.get("available_tasks", [])
    current_task = maid.get("task", "")
    return {
        "id": maid.get("id", ""),
        "name": maid.get("name", ""),
        "health": maid.get("health", 0),
        "max_health": maid.get("max_health", 0),
        "is_sitting": maid.get("is_sitting", False),
        "is_following": maid.get("is_following", False),
        "current_mode": {
            "id": current_task,
            "name": _task_name_for_id(current_task, available),
        },
        "main_hand_item": maid.get("main_hand_item", ""),
        "off_hand_item": maid.get("off_hand_item", ""),
    }


def _select_status_maid(plugin, maids):
    if not maids:
        return None
    assigned = getattr(plugin, "_assigned_maid_id", "")
    if assigned:
        for maid in maids:
            if maid.get("id") == assigned:
                return maid
    return maids[0]


async def _send_guarded_body_command(plugin, maid_id, request, operation):
    director = getattr(plugin, "_maid_activity_director", None)
    if director is None:
        return await plugin._send_request(request)
    guarded = await director.execute_body_mutation(
        lambda: plugin._send_request(request),
        maid_id=maid_id,
        operation=operation,
    )
    if not guarded.get("success", False):
        return {
            "type": "error",
            "data": {
                "error_code": guarded.get("error_code", "BODY_MUTATION_FAILED"),
                "error": guarded.get("error", "Body mutation failed"),
                "activity": guarded.get("current_activity", {}),
            },
        }
    result = guarded.get("result")
    return result if isinstance(result, dict) else {
        "type": "error",
        "data": {"error": "Body mutation returned an invalid response"},
    }


async def do_switch_follow(plugin, *, action="follow"):
    plugin.logger.info(f"[Entry] switch_follow called with action='{action}'")
    if not plugin.connected:
        return Err("Not connected to Minecraft")
    maid_id = plugin._resolve_maid_id()
    if not maid_id:
        return Err("No maid assigned")
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in {"follow", "stay"}:
        return Err("action must be follow or stay")
    follow = normalized_action == "follow"
    result = await _send_guarded_body_command(plugin, maid_id, {
        "type": "command_maid",
        "data": {"maid_id": maid_id, "command": "switch_follow", "args": {"follow": follow}},
    }, "switch_follow")
    if result.get("type") == "error":
        return Err(str(result.get("data", {})))
    result_data = result.get("data", {})
    if result_data.get("success") is False:
        return Err(result_data.get("error", "Command failed"))
    state = str(result_data.get("state") or "")
    verification = await _verify_follow_state(plugin, maid_id, follow)
    if not verification.get("verified"):
        return {
            "output": {
                "success": False,
                "recoverable": True,
                "error": "Follow command returned success, but state verification failed",
                "action": normalized_action,
                "command_state": state,
                **verification,
            },
            "is_error": True,
            "error": "FOLLOW_STATE_VERIFICATION_FAILED",
        }
    stood_up = follow and state == "following_stood_up"
    return Ok({
        "success": True,
        "action": normalized_action,
        "command_state": state,
        "stood_up": stood_up,
        **verification,
    })


async def _verify_follow_state(plugin, maid_id, expected_follow):
    status_result = await plugin._send_request({"type": "get_maid_status"}, timeout=5)
    if status_result.get("type") == "error":
        return {
            "verified": False,
            "verification_error": status_result.get("data", {}),
            "expected_following": bool(expected_follow),
        }
    maids = status_result.get("data", {}).get("maids", [])
    for maid in maids:
        if maid.get("id") != maid_id:
            continue
        plugin._maid_status_cache[maid_id] = maid
        is_following = bool(maid.get("is_following", False))
        is_sitting = bool(maid.get("is_sitting", False))
        return {
            "verified": (
                is_following == bool(expected_follow)
                and (not expected_follow or not is_sitting)
            ),
            "expected_following": bool(expected_follow),
            "is_following": is_following,
            "is_sitting": is_sitting,
        }
    return {
        "verified": False,
        "verification_error": "Assigned maid was not present in status response",
        "expected_following": bool(expected_follow),
    }


async def do_switch_sit(plugin, *, action="sit"):
    plugin.logger.info(f"[Entry] switch_sit called with action='{action}'")
    if not plugin.connected:
        return Err("Not connected to Minecraft")
    maid_id = plugin._resolve_maid_id()
    if not maid_id:
        return Err("No maid assigned")
    sit = action == "sit"
    result = await _send_guarded_body_command(plugin, maid_id, {
        "type": "command_maid",
        "data": {"maid_id": maid_id, "command": "switch_sit", "args": {"sit": sit}},
    }, "switch_sit")
    if result.get("type") == "error":
        return Err(str(result.get("data", {})))
    result_data = result.get("data", {})
    if result_data.get("success") is False:
        return Err(result_data.get("error", "Command failed"))
    return Ok({"success": True, "action": action})


async def do_switch_task(plugin, *, task=""):
    plugin.logger.info(f"[Entry] switch_task called with task='{task}'")
    if not plugin.connected:
        return Err("Not connected to Minecraft")
    maid_id = plugin._resolve_maid_id()
    if not maid_id:
        return Err("No maid assigned")
    if not task:
        return Err("请提供task参数")

    combat_guard = await _guard_combat_task_equipment(plugin, task)
    if combat_guard is not None:
        return combat_guard

    director = getattr(plugin, "_maid_activity_director", None)
    if director is not None:
        transition = await director.set_activity(
            {"type": "tlm_task", "task": str(task)},
            maid_id=maid_id,
            switch_policy="cancel_then_switch",
        )
        if not transition.get("success"):
            return _activity_tool_result(transition)
        final = dict(transition.get("final_activity") or {})
        tlm = dict(final.get("tlm_task") or {})
        target_result = dict(transition.get("target_result") or {})
        current_task = str(tlm.get("id") or target_result.get("current_task") or "")
        return Ok({
            "success": True,
            "requested_task": task,
            "matched_task_id": str(
                target_result.get("matched_task_id") or current_task
            ),
            "verified": bool(current_task) and not tlm.get("suppressed", False),
            "current_task": current_task,
            "current_task_name": str(tlm.get("name") or ""),
            "expected_task": str(
                target_result.get("matched_task_id") or current_task
            ),
            "activity_transition": transition,
        })

    maid = plugin._maid_status_cache.get(maid_id, {})
    available = maid.get("available_tasks", [])
    plugin.logger.info(f"[switch_task] Cache hit={bool(maid)}, available_tasks count={len(available)}")

    if not available:
        try:
            status_result = await plugin._send_request({"type": "get_maid_status"}, timeout=5)
            if status_result.get("type") != "error":
                for m in status_result.get("data", {}).get("maids", []):
                    plugin._maid_status_cache[m.get("id", "")] = m
                maid = plugin._maid_status_cache.get(maid_id, {})
                available = maid.get("available_tasks", [])
                plugin.logger.info(f"[switch_task] get_maid_status: found {len(maid.get('available_tasks', []))} tasks for maid {maid_id}")
            else:
                plugin.logger.warning(f"[switch_task] get_maid_status failed: {status_result.get('data', {})}")
        except Exception as e:
            plugin.logger.warning(f"[Entry] switch_task: failed to fetch maid status: {e}")

    if not available:
        try:
            ctx_result = await plugin._send_request({
                "type": "get_game_context",
                "data": {"maid_id": maid_id, "category": "status"},
            }, timeout=5)
            if ctx_result.get("type") != "error":
                available = ctx_result.get("data", {}).get("available_tasks", [])
                plugin.logger.info(f"[switch_task] get_game_context: found {len(available)} tasks")
            else:
                plugin.logger.warning(f"[switch_task] get_game_context failed: {ctx_result.get('data', {})}")
        except Exception as e:
            plugin.logger.warning(f"[Entry] switch_task: failed to query game_context: {e}")

    resolved_task = task_resolver.resolve_task_name(task, available)
    plugin.logger.info(f"[Entry] switch_task: '{task}' resolved to '{resolved_task}' (available={len(available)} tasks)")

    if resolved_task is None:
        return _switch_task_recoverable_error(
            task,
            available,
            "无法匹配到任何工作模式",
        )

    result = await plugin._send_request({
        "type": "command_maid",
        "data": {"maid_id": maid_id, "command": "switch_task", "args": {"task": resolved_task}},
    })
    if result.get("type") == "error":
        plugin.logger.warning(f"[Entry] switch_task failed: {result.get('data', {})}")
        return _switch_task_recoverable_error(
            task,
            available,
            "Minecraft mod 返回错误",
            result.get("data", {}),
            resolved_task=resolved_task,
        )
    result_data = result.get("data", {})
    if result_data.get("success") is False:
        merged_available = result_data.get("available_tasks") or available
        return _switch_task_recoverable_error(
            task,
            merged_available,
            result_data.get("error", "Command failed"),
            result_data,
            resolved_task=resolved_task,
        )
    plugin.logger.info(f"[Entry] switch_task success: task='{task}' -> '{resolved_task}'")
    verification = await _verify_switched_task(plugin, maid_id, resolved_task)
    if not verification.get("verified"):
        return _switch_task_verification_error(task, resolved_task, verification, result_data)
    return Ok({
        "success": True,
        "requested_task": task,
        "matched_task_id": resolved_task,
        **verification,
    })


def _switch_task_recoverable_error(task, available, message, raw_error=None, resolved_task=None):
    return {
        "output": {
            "success": False,
            "recoverable": True,
            "error": message,
            "requested_task": task,
            "matched_task_id": resolved_task,
            "available_tasks": _normalize_available_tasks(available),
            "retry_hint": (
                "请从 available_tasks 中选择最接近玩家意图的精确 id 或 name，"
                "然后再次调用 mc_switch_task。不要只口头说明失败。"
            ),
            "raw_error": raw_error or {},
        },
        "is_error": True,
        "error": "TASK_SWITCH_RECOVERABLE",
    }


def _switch_task_verification_error(task, resolved_task, verification, raw_result=None):
    return {
        "output": {
            "success": False,
            "recoverable": True,
            "error": "Minecraft reported task switch success, but status verification shows a different current task",
            "requested_task": task,
            "matched_task_id": resolved_task,
            "raw_result": raw_result or {},
            **verification,
            "retry_hint": (
                "不要告诉玩家已经切换成功。请说明 current_task/current_task_name 才是真实模式，"
                "并从 available_tasks 中选择攻击/战斗对应的精确 id/name 后再次调用 mc_switch_task。"
            ),
        },
        "is_error": True,
        "error": "TASK_SWITCH_VERIFY_FAILED",
    }


def _normalize_available_tasks(available):
    tasks = []
    for item in available or []:
        if isinstance(item, dict):
            task_id = str(item.get("id", "") or "")
            name = str(item.get("name", "") or "")
        else:
            task_id = str(item or "")
            name = ""
        if task_id or name:
            tasks.append({"id": task_id, "name": name})
    return tasks


async def _verify_switched_task(plugin, maid_id, expected_task):
    status_result = await plugin._send_request({"type": "get_maid_status"}, timeout=5)
    if status_result.get("type") == "error":
        return {
            "verified": False,
            "verification_error": status_result.get("data", {}),
            "expected_task": expected_task,
        }

    maids = status_result.get("data", {}).get("maids", [])
    for maid in maids:
        if maid.get("id") == maid_id:
            plugin._maid_status_cache[maid_id] = maid
            current_task = maid.get("task", "")
            current_task_name = _task_name_for_id(current_task, maid.get("available_tasks", []))
            verified = _task_matches(current_task, expected_task)
            return {
                "verified": verified,
                "current_task": current_task,
                "current_task_name": current_task_name,
                "expected_task": expected_task,
                "available_tasks": _normalize_available_tasks(maid.get("available_tasks", [])),
            }

    return {
        "verified": False,
        "verification_error": "Assigned maid was not present in status response",
        "expected_task": expected_task,
        "available_tasks": _normalize_available_tasks(
            maids[0].get("available_tasks", []) if maids else []
        ),
    }


def _task_matches(current_task, expected_task):
    current = str(current_task or "").strip().lower()
    expected = str(expected_task or "").strip().lower()
    if not current or not expected:
        return False
    return current == expected or current.split(":")[-1] == expected.split(":")[-1]


def _task_name_for_id(task_id, available):
    task_id = str(task_id or "")
    for item in available or []:
        if isinstance(item, dict) and item.get("id") == task_id:
            return item.get("name", "")
    return ""


async def _verify_equipped_item(plugin, maid_id, expected_item):
    expected_item = str(expected_item or "").strip()
    if not expected_item:
        return {
            "verified": False,
            "verification_error": "Command did not report which item should be in main hand",
        }

    status_result = await plugin._send_request({"type": "get_maid_status"}, timeout=5)
    if status_result.get("type") == "error":
        return {
            "verified": False,
            "verification_error": status_result.get("data", {}),
            "expected_item": expected_item,
        }

    maids = status_result.get("data", {}).get("maids", [])
    for maid in maids:
        if maid.get("id") == maid_id:
            plugin._maid_status_cache[maid_id] = maid
            current_main = maid.get("main_hand_item", "")
            current_off = maid.get("off_hand_item", "")
            return {
                "verified": _item_matches(current_main, expected_item),
                "expected_item": expected_item,
                "current_main_hand_item": current_main,
                "current_off_hand_item": current_off,
            }

    return {
        "verified": False,
        "verification_error": "Assigned maid was not present in status response",
        "expected_item": expected_item,
    }


def _item_matches(current_item, expected_item):
    current = str(current_item or "").strip().lower()
    expected = str(expected_item or "").strip().lower()
    if not current or not expected:
        return False
    return current == expected or current.split(":")[-1] == expected.split(":")[-1]


async def do_switch_schedule(plugin, *, schedule="all"):
    plugin.logger.info(f"[Entry] switch_schedule called with schedule='{schedule}'")
    if not plugin.connected:
        return Err("Not connected to Minecraft")
    maid_id = plugin._resolve_maid_id()
    if not maid_id:
        return Err("No maid assigned")
    result = await _send_guarded_body_command(plugin, maid_id, {
        "type": "command_maid",
        "data": {"maid_id": maid_id, "command": "switch_schedule", "args": {"schedule": schedule}},
    }, "switch_schedule")
    if result.get("type") == "error":
        return Err(str(result.get("data", {})))
    result_data = result.get("data", {})
    if result_data.get("success") is False:
        return Err(result_data.get("error", "Command failed"))
    return Ok({"success": True, "current_schedule": schedule})


async def do_equip_item(plugin, *, item="", slot=None):
    plugin.logger.info(f"[Entry] equip_item called with item='{item}', slot={slot}")
    if not plugin.connected:
        return Err("Not connected to Minecraft")
    maid_id = plugin._resolve_maid_id()
    if not maid_id:
        return Err("No maid assigned")
    args = {}
    requested_item = _normalize_item_id(item)
    if item:
        args["item"] = requested_item
    elif slot is not None:
        args["slot"] = slot
    else:
        return Err("请提供item或slot参数")
    result = await _send_guarded_body_command(plugin, maid_id, {
        "type": "command_maid",
        "data": {"maid_id": maid_id, "command": "equip_item", "args": args},
    }, "equip_item")
    result_data = result.get("data", {})
    if result.get("type") == "error":
        return Err(str(result.get("data", {})))
    if result_data.get("success") is False:
        if requested_item:
            verification = await _verify_equipped_item(plugin, maid_id, requested_item)
            if verification.get("verified"):
                return Ok({
                    "success": True,
                    "already_equipped": True,
                    "requested_item": requested_item,
                    **verification,
                })
        return Err(result_data.get("error", "Command failed"))
    equipped_item = str(result_data.get("equipped_item") or requested_item or "").strip()
    verification = await _verify_equipped_item(plugin, maid_id, equipped_item)
    if not verification.get("verified"):
        return {
            "output": {
                "success": False,
                "recoverable": True,
                "error": "Equip command returned success, but main-hand verification failed",
                "requested_item": requested_item or f"slot:{slot}",
                "raw_result": result_data,
                **verification,
                "retry_hint": (
                    "Do not tell the player the item is equipped. "
                    "If the player asked to hold a torch, explain the actual main-hand item and retry with item='minecraft:torch' or a precise inventory slot."
                ),
            },
            "is_error": True,
            "error": "EQUIP_VERIFY_FAILED",
        }
    return Ok({
        "success": True,
        "requested_item": requested_item or f"slot:{slot}",
        "equipped_item": equipped_item,
        **verification,
    })


async def do_send_chat(plugin, *, message, maid_id=None):
    if not plugin._chat_bubble_enabled and not plugin._chat_box_enabled:
        return Err("聊天功能已被管理员关闭（气泡和聊天框均未启用）")
    if not plugin.connected:
        return Err("Not connected to Minecraft")
    resolved_id = plugin._resolve_maid_id(maid_id)
    if not resolved_id:
        return Err("No maid_id available. Call mc_maid_status first or assign a maid in config.")
    result = await plugin._send_request({
        "type": "send_chat",
        "data": {"maid_id": resolved_id, "message": message},
    })
    if result.get("type") == "error":
        return Err(str(result.get("data", {})))
    result_data = result.get("data", {})
    if not result_data.get("success", False):
        return Err("Chat send failed")
    return Ok({"success": True})


async def do_game_context(plugin, category=None):
    if not plugin.connected:
        return {"output": {"error": "Not connected to Minecraft"}, "is_error": True, "error": "NOT_CONNECTED"}
    request_data = {"type": "get_game_context", "data": {}}
    if category:
        request_data["data"]["category"] = category
    maid_id = plugin._resolve_maid_id()
    if maid_id:
        request_data["data"]["maid_id"] = maid_id
    result = await plugin._send_request(request_data)
    if result.get("type") == "error":
        return {"output": result.get("data", {}), "is_error": True, "error": "REQUEST_FAILED"}
    return result.get("data", {})


def _maid_action_service(plugin):
    service = getattr(plugin, "_maid_action_service", None)
    if service is None:
        service = MaidActionService(plugin)
        plugin._maid_action_service = service
    return service


def _action_error(code, message, **details):
    return {
        "output": {"success": False, "error": message, **details},
        "is_error": True,
        "error": code,
    }


def _tool_result_is_error(result):
    """同时读取 SDK Result 对象和旧版字典形测试适配器。"""
    if isinstance(result, dict):
        return bool(result.get("is_error"))
    is_err = getattr(result, "is_err", None)
    return bool(is_err()) if callable(is_err) else False


def _tool_result_output(result):
    """从两种工具结果表示中返回独立的映射副本。"""
    if isinstance(result, dict):
        output = result.get("output")
    else:
        value_or_none = getattr(result, "value_or_none", None)
        output = (
            value_or_none()
            if callable(value_or_none)
            else getattr(result, "value", None)
        )
        if output is None and _tool_result_is_error(result):
            error = getattr(result, "error", None)
            return dict(error) if isinstance(error, dict) else {"error": str(error)}
    return dict(output) if isinstance(output, dict) else {}


def _action_execution_confirmation(payload):
    """Make an accepted asynchronous action impossible to mistake for completion."""
    status = str((payload or {}).get("status") or "").strip().upper()
    terminal = status in {
        "SUCCEEDED", "FAILED", "CANCELLED", "SUPERSEDED", "TIMEOUT"
    }
    kind = str((payload or {}).get("kind") or "").strip().lower()
    end_reason = str((payload or {}).get("end_reason") or "").strip().upper()
    result = (payload or {}).get("result")
    completed = status == "SUCCEEDED" and end_reason == "COMPLETED"
    arrived = isinstance(result, dict) and result.get("arrived") is True
    partial = isinstance(result, dict) and result.get("partial") is True
    request_satisfied = (
        isinstance(result, dict) and result.get("request_satisfied") is True
    )
    request_unsatisfied = (
        isinstance(result, dict) and result.get("request_satisfied") is False
    )
    succeeded = (
        completed
        and (kind != "return_to_position" or arrived)
        and (kind != "harvest_blocks" or request_satisfied)
        and not partial
        and not request_unsatisfied
    )
    instruction = (
        "服务端终态已严格确认成功。"
        if succeeded else
        "服务端已返回终态，但终态数据没有严格确认成功或到达；禁止向玩家声称完成。"
        if terminal else
        "动作仅已受理，仍在异步执行；现在只能说正在行动。收到 maid_action_finished 前"
        "禁止声称已经到达、挖完或完成。"
    )
    return {
        "execution_pending": not terminal,
        "completion_confirmed": succeeded,
        "action_completion_confirmed": succeeded,
        "conversation_goal_confirmed": False,
        "terminal_event_required": not terminal,
        "llm_instruction": instruction,
    }


def _skill_execution_confirmation(payload):
    """Separate Skill acceptance from a verified high-level terminal."""
    status = str((payload or {}).get("status") or "").strip().upper()
    terminal = status in {"SUCCEEDED", "FAILED", "CANCELLED", "BLOCKED"}
    succeeded = status == "SUCCEEDED"
    return {
        "execution_pending": not terminal,
        "completion_confirmed": succeeded,
        "terminal_event_required": not terminal,
        "llm_instruction": (
            "Skill 终态已确认目标成功。只能按结构化实际数量回应。"
            if succeeded else
            "Skill 已结束但没有成功完成目标；禁止声称目标完成。"
            if terminal else
            "Skill 仅已受理并在异步执行。收到真实 Skill 终态前只能说已经开始，"
            "禁止声称已经采够、挖完或进入后续任务。"
        ),
    }


async def do_start_maid_action(
    plugin,
    *,
    kind="",
    args=None,
    action_id="",
    timeout_ms=None,
    replace_existing=True,
    deduplicate_active=False,
    maid_id=None,
):
    """Validate and start a server-authoritative maid action."""
    if not plugin.connected:
        return _action_error("NOT_CONNECTED", "Not connected to Minecraft")
    if not getattr(plugin, "_maid_agent_enabled", True):
        return _action_error("MAID_AGENT_DISABLED", "Maid Agent actions are disabled in Minecraft config")
    resolved_id = plugin._resolve_maid_id(maid_id)
    if not resolved_id:
        return _action_error("NO_MAID_ASSIGNED", "No maid assigned")
    service = _maid_action_service(plugin)
    try:
        normalized_args = service.registry.normalize(kind, args or {})
        ore_selector = service.registry.is_ore_selector(
            normalized_args.get("selector")
        )
        if str(kind or "").strip().lower() == "harvest_blocks" and ore_selector:
            # Ore prospecting is explicitly continuous. Do not let an LLM-
            # invented finite timeout silently reintroduce a mining cap.
            timeout_ms = 0
        elif timeout_ms is None:
            timeout_ms = (
                0 if str(kind or "").strip().lower() == "return_to_position"
                else 60000
            )
        timeout_ms = int(timeout_ms)
    except (ActionValidationError, TypeError, ValueError) as exc:
        return _action_error("INVALID_ACTION_ARGUMENTS", str(exc))
    if timeout_ms != 0 and (timeout_ms < 1000 or timeout_ms > 120000):
        return _action_error(
            "INVALID_ACTION_ARGUMENTS",
            "timeout_ms must be 0 (no deadline) or between 1000 and 120000",
        )
    canonical_action_id = str(action_id or uuid.uuid4())
    logger = getattr(plugin, "logger", None)
    if logger is not None:
        logger.info(
            "[MaidAgent] start action_id=%s maid_id=%s kind=%s args=%s",
            canonical_action_id, resolved_id, str(kind).strip().lower(), normalized_args,
        )
    director = getattr(plugin, "_maid_activity_director", None)
    if director is not None:
        target = {
            "type": "agent_action",
            "kind": str(kind).strip().lower(),
            "args": normalized_args,
            "timeout_ms": timeout_ms,
        }
        # 对话 LLM 和主动 Agent 可能分别解析出相同的移动请求。调用方未提供 id 时，
        # 由调度器在启动新动作前比较动作类型和规范化参数。
        if action_id or not deduplicate_active:
            target["action_id"] = canonical_action_id
        transition = await director.set_activity(
            target,
            maid_id=resolved_id,
            switch_policy=(
                "cancel_then_switch" if replace_existing else "reject_if_busy"
            ),
            request_id=canonical_action_id,
        )
        if not transition.get("success"):
            return _activity_tool_result(transition)
        started = dict(
            transition.get("target_result")
            or transition.get("terminal_activity")
            or {}
        )
        if not started and transition.get("status") == "ALREADY_ACTIVE":
            final_activity = transition.get("final_activity") or {}
            for item in final_activity.get("active_actions", []):
                if (
                    str(item.get("kind") or "") == target["kind"]
                    and dict(item.get("args") or {}) == target["args"]
                ):
                    started = dict(item)
                    break
        returned_action_id = str(
            started.get("action_id") or action_id or canonical_action_id
        )
        response = {
            "accepted": True,
            "action_id": returned_action_id,
            **started,
            "activity_transition": transition,
        }
        response.update(_action_execution_confirmation(response))
        return Ok(response)
    request = {
        "type": "start_maid_action",
        "data": {
            "action_id": canonical_action_id,
            "maid_id": resolved_id,
            "kind": str(kind).strip().lower(),
            "timeout_ms": timeout_ms,
            "replace_existing": bool(replace_existing),
            "args": normalized_args,
        },
    }
    result = await service.send_start_request(request)
    if result.get("type") == "error":
        return _action_error(
            "REQUEST_FAILED", str(result.get("data", {})),
            action_id=canonical_action_id,
        )
    records = service.observe_response(result)
    result_data = dict(result.get("data", {}) or {})
    accepted = result_data.get("accepted", result_data.get("success", True))
    if not accepted:
        return _action_error(
            str(result_data.get("error_code") or "ACTION_REJECTED"),
            str(result_data.get("error") or result_data.get("message")
                or result_data.get("rejection_reason") or "Action rejected"),
            action_id=canonical_action_id,
            response=result_data,
        )
    record = records[0].as_dict() if records else {}
    response = {
        "accepted": True,
        "action_id": canonical_action_id,
        **result_data,
        **record,
    }
    response.update(_action_execution_confirmation(response))
    return Ok(response)


async def do_move_maid_to(plugin, *, destination=""):
    """Start safe semantic movement through a deliberately tiny LLM contract."""
    normalized = str(destination or "").strip().lower()
    if normalized not in {"player", "surface", "mine_entry"}:
        return _action_error(
            "INVALID_ACTION_ARGUMENTS",
            "destination must be player, surface or mine_entry",
        )
    if normalized == "player":
        position = await do_game_context(plugin, category="position")
        if position.get("is_error") or position.get("error"):
            return _action_error(
                "MAID_POSITION_UNAVAILABLE",
                str(position.get("error") or "Unable to resolve maid position"),
            )
        maid_dimension = str(position.get("maid_dimension") or "")
        owner_dimension = str(position.get("owner_dimension") or "")
        same_dimension = bool(
            maid_dimension and owner_dimension and maid_dimension == owner_dimension
        )
        if maid_dimension and owner_dimension and not same_dimension:
            return _action_error(
                "OWNER_NOT_IN_MAID_DIMENSION",
                "Remote path recall requires the maid and owner to be in the same dimension",
                maid_dimension=maid_dimension,
                owner_dimension=owner_dimension,
            )
        outside_native_range = (
            position.get("within_owner_simulation_distance") is False
            and same_dimension
        )
        if not outside_native_range:
            native = await do_switch_follow(plugin, action="follow")
            if _tool_result_is_error(native):
                return native
            native_output = _tool_result_output(native)
            return Ok({
                **native_output,
                "accepted": True,
                "recall_mode": "native_follow",
                "execution_pending": True,
                "completion_confirmed": False,
                "terminal_event_required": False,
            })
    args = {"destination": normalized}
    if normalized == "player":
        args["handoff_to_follow"] = True
    result = await do_start_maid_action(
        plugin,
        kind="return_to_position",
        args=args,
        timeout_ms=0,
        replace_existing=True,
        deduplicate_active=True,
    )
    if _tool_result_is_error(result):
        return result
    output = _tool_result_output(result)
    output["recall_mode"] = "agent_path"
    return Ok(output)


async def do_navigate_maid_to(plugin, *, x=None, y=None, z=None):
    """启动由服务端权威执行且不会破坏方块的坐标寻路。"""
    if any(value is None or isinstance(value, bool) for value in (x, y, z)):
        return _action_error(
            "INVALID_ACTION_ARGUMENTS",
            "x, y and z must all be numeric coordinates",
        )
    return await do_start_maid_action(
        plugin,
        kind="navigate",
        args={"target": {"x": x, "y": y, "z": z}},
        replace_existing=True,
        deduplicate_active=True,
    )


async def do_cancel_maid_action(plugin, *, action_id="", maid_id=None):
    if not plugin.connected:
        return _action_error("NOT_CONNECTED", "Not connected to Minecraft")
    service = _maid_action_service(plugin)
    resolved_id = plugin._resolve_maid_id(maid_id)
    if not action_id:
        active = service.tracker.latest_active(resolved_id)
        if active is None:
            return _action_error("NO_ACTIVE_ACTION", "No active maid action to cancel")
        action_id = active.action_id
    data = {"action_id": str(action_id)}
    if resolved_id:
        data["maid_id"] = resolved_id
    result = await plugin._send_request({"type": "cancel_maid_action", "data": data})
    if result.get("type") == "error":
        return _action_error("REQUEST_FAILED", str(result.get("data", {})), action_id=action_id)
    service.observe_response(result)
    result_data = dict(result.get("data", {}) or {})
    accepted = result_data.get("accepted", result_data.get("success", True))
    if not accepted:
        return _action_error(
            str(result_data.get("error_code") or "CANCEL_REJECTED"),
            str(result_data.get("error") or result_data.get("message")
                or result_data.get("rejection_reason") or "Cancel rejected"),
            action_id=action_id,
            response=result_data,
        )
    return Ok({"accepted": True, "action_id": str(action_id), **result_data})


def _cached_terminal_action_result(service, action_id, **metadata):
    record = service.tracker.get(action_id)
    if record is None or not record.terminal:
        return None
    return Ok({
        **record.as_dict(),
        "found": True,
        "source": "local_terminal_cache",
        **metadata,
    })


async def do_get_maid_action_status(plugin, *, action_id=""):
    action_id = str(action_id or "").strip()
    if not action_id:
        return _action_error("INVALID_ACTION_ARGUMENTS", "action_id is required")
    service = _maid_action_service(plugin)
    if not plugin.connected:
        cached = _cached_terminal_action_result(
            service, action_id, server_query_error="NOT_CONNECTED"
        )
        if cached is not None:
            return cached
        return _action_error("NOT_CONNECTED", "Not connected to Minecraft")
    result = await plugin._send_request({
        "type": "get_maid_action_status",
        "data": {"action_id": action_id},
    })
    if result.get("type") == "error":
        cached = _cached_terminal_action_result(
            service,
            action_id,
            server_query_error=str(result.get("data", {})),
        )
        if cached is not None:
            return cached
        return _action_error(
            "REQUEST_FAILED", str(result.get("data", {})), action_id=action_id
        )
    records = service.observe_response(result)
    result_data = dict(result.get("data", {}) or {})
    if result_data.get("found") is False or result_data.get("error"):
        cache_metadata = {}
        if result_data.get("found") is False:
            cache_metadata["server_found"] = False
        if result_data.get("error"):
            cache_metadata["server_query_error"] = str(result_data["error"])
        cached = _cached_terminal_action_result(
            service,
            action_id,
            server_error_code=str(
                result_data.get("error_code") or "ACTION_NOT_FOUND"
            ),
            **cache_metadata,
        )
        if cached is not None:
            return cached
        return _action_error(
            str(result_data.get("error_code") or "ACTION_NOT_FOUND"),
            str(result_data.get("error") or "Maid action was not found"),
            action_id=action_id, response=result_data,
        )
    record_data = records[0].as_dict() if records else {}
    return Ok({**result_data, **record_data})


async def do_list_active_maid_actions(plugin, *, maid_id=None):
    if not plugin.connected:
        return _action_error("NOT_CONNECTED", "Not connected to Minecraft")
    service = _maid_action_service(plugin)
    resolved_id = plugin._resolve_maid_id(maid_id)
    data = {"maid_id": resolved_id} if resolved_id else {}
    result = await plugin._send_request({"type": "list_active_maid_actions", "data": data})
    if result.get("type") == "error":
        return _action_error("REQUEST_FAILED", str(result.get("data", {})))
    service.observe_response(result)
    result_data = dict(result.get("data", {}) or {})
    if result_data.get("error"):
        return _action_error(
            str(result_data.get("error_code") or "LIST_ACTIONS_FAILED"),
            str(result_data.get("error")), response=result_data,
        )
    actions = result_data.get("actions", result_data.get("active_actions", []))
    return Ok({"actions": actions if isinstance(actions, list) else []})


def _skill_runner(plugin):
    return getattr(plugin, "_skill_runner", None)


def _skill_snapshot(value):
    if isinstance(value, dict):
        return dict(value)
    converter = getattr(value, "as_dict", None)
    if callable(converter):
        converted = converter()
        return dict(converted) if isinstance(converted, dict) else {}
    return {}


async def do_start_skill(
    plugin,
    *,
    skill="",
    args=None,
    skill_id="",
    replace_existing=True,
    deduplicate_active=False,
    maid_id=None,
):
    """Start a checkpointed high-level skill through SkillRunner only."""
    if not plugin.connected:
        return _action_error("NOT_CONNECTED", "Not connected to Minecraft")
    if not getattr(plugin, "_maid_agent_enabled", True):
        return _action_error("MAID_AGENT_DISABLED", "Maid Agent skills are disabled")
    runner = _skill_runner(plugin)
    if runner is None:
        return _action_error("SKILL_RUNNER_UNAVAILABLE", "Maid SkillRunner is not initialized")
    resolved_id = plugin._resolve_maid_id(maid_id)
    if not resolved_id:
        return _action_error("NO_MAID_ASSIGNED", "No maid assigned")
    director = getattr(plugin, "_maid_activity_director", None)
    if director is not None:
        if not isinstance(args, (dict, type(None))):
            return _action_error("INVALID_SKILL_ARGUMENTS", "args must be an object")
        canonical_skill_id = str(skill_id or uuid.uuid4()).strip()
        skill_name = str(skill or "").strip().lower()
        target_args = dict(args or {})
        normalizer = getattr(runner, "normalize_args", None)
        if deduplicate_active and callable(normalizer):
            try:
                target_args = dict(normalizer(skill_name, target_args))
            except (TypeError, ValueError) as exc:
                return _action_error("INVALID_SKILL_ARGUMENTS", str(exc))
        target = {
            "type": "skill",
            "skill": skill_name,
            "args": target_args,
        }
        # 直连对话工具和主动 Agent 可能分别解析出同一句请求。不生成 skill_id，
        # 让每个女仆的锁比较 Skill 与规范化参数；若同一 Skill 正在运行则返回
        # ALREADY_ACTIVE，而不是取消并重启。
        if skill_id or not deduplicate_active:
            target["skill_id"] = canonical_skill_id
        transition = await director.set_activity(
            target,
            maid_id=resolved_id,
            switch_policy=(
                "cancel_then_switch" if replace_existing else "reject_if_busy"
            ),
            request_id=canonical_skill_id,
        )
        if not transition.get("success"):
            return _activity_tool_result(transition)
        started = dict(
            transition.get("target_result")
            or transition.get("terminal_activity")
            or {}
        )
        if not started and transition.get("status") == "ALREADY_ACTIVE":
            final_activity = transition.get("final_activity") or {}
            for item in final_activity.get("active_skills", []):
                if (
                    str(item.get("skill_name") or "") == target["skill"]
                    and dict(item.get("args") or {}) == target["args"]
                ):
                    started = dict(item)
                    break
        returned_skill_id = str(
            started.get("skill_id") or skill_id or canonical_skill_id
        )
        return Ok({
            "accepted": True,
            "skill_id": returned_skill_id,
            **started,
            "activity_transition": transition,
            **_skill_execution_confirmation(started),
        })
    try:
        snapshot = _skill_snapshot(await runner.start(
            skill_name=str(skill or "").strip().lower(),
            maid_id=resolved_id,
            args=dict(args or {}),
            skill_id=str(skill_id or "").strip() or None,
            replace_existing=bool(replace_existing),
        ))
    except (TypeError, ValueError) as exc:
        return _action_error("INVALID_SKILL_ARGUMENTS", str(exc))
    except RuntimeError as exc:
        return _action_error("SKILL_START_REJECTED", str(exc))
    if not snapshot:
        return _action_error("SKILL_START_FAILED", "SkillRunner returned no skill snapshot")
    return Ok({
        "accepted": True,
        **snapshot,
        **_skill_execution_confirmation(snapshot),
    })


async def do_mine_ore(plugin, *, ore="", target_count=1, maid_id=None):
    """将面向用户的简短矿石请求解析为 mine_ore Skill。"""
    try:
        selector = _resolve_ore_selector(ore)
        count = _normalize_target_count(target_count)
    except ValueError as exc:
        return _action_error("INVALID_MINING_REQUEST", str(exc))
    return await do_start_skill(
        plugin,
        skill="mine_ore",
        args={
            "selector": selector,
            "target_count": count,
            "target_metric": "blocks_harvested",
            "execution_mode": "autonomous",
        },
        replace_existing=True,
        deduplicate_active=True,
        maid_id=maid_id,
    )


async def do_gather_blocks(
    plugin, *, resource="", target_count=1, maid_id=None
):
    """将附近资源的简短请求解析为 gather_blocks Skill。"""
    try:
        selector = _resolve_gather_selector(resource)
        count = _normalize_target_count(target_count)
    except ValueError as exc:
        return _action_error("INVALID_GATHER_REQUEST", str(exc))
    return await do_start_skill(
        plugin,
        skill="gather_blocks",
        args={
            "selector": selector,
            "target_count": count,
            "target_metric": "blocks_harvested",
            "search_radius": 12,
            "vein_mining": True,
            "tool_policy": "require_correct",
        },
        replace_existing=True,
        deduplicate_active=True,
        maid_id=maid_id,
    )


async def do_cancel_skill(plugin, *, skill_id="", maid_id=None):
    if not plugin.connected:
        return _action_error("NOT_CONNECTED", "Not connected to Minecraft")
    runner = _skill_runner(plugin)
    if runner is None:
        return _action_error("SKILL_RUNNER_UNAVAILABLE", "Maid SkillRunner is not initialized")
    resolved_id = plugin._resolve_maid_id(maid_id)
    try:
        snapshot = _skill_snapshot(await runner.cancel(
            skill_id=str(skill_id or "").strip(),
            maid_id=str(resolved_id or ""),
        ))
    except ValueError as exc:
        return _action_error("SKILL_NOT_FOUND", str(exc), skill_id=str(skill_id or ""))
    except RuntimeError as exc:
        return _action_error("SKILL_CANCEL_REJECTED", str(exc), skill_id=str(skill_id or ""))
    if not snapshot:
        return _action_error("SKILL_NOT_FOUND", "No matching skill", skill_id=str(skill_id or ""))
    return Ok({"accepted": True, **snapshot})


async def do_get_skill_status(plugin, *, skill_id=""):
    runner = _skill_runner(plugin)
    if runner is None:
        return _action_error("SKILL_RUNNER_UNAVAILABLE", "Maid SkillRunner is not initialized")
    skill_id = str(skill_id or "").strip()
    if not skill_id:
        return _action_error("INVALID_SKILL_ARGUMENTS", "skill_id is required")
    snapshot = _skill_snapshot(runner.get_status(skill_id))
    if not snapshot:
        return _action_error("SKILL_NOT_FOUND", "Skill was not found", skill_id=skill_id)
    return Ok(snapshot)


async def do_list_skills(plugin, *, include_terminal=True, maid_id=None):
    runner = _skill_runner(plugin)
    if runner is None:
        return _action_error("SKILL_RUNNER_UNAVAILABLE", "Maid SkillRunner is not initialized")
    resolved_id = plugin._resolve_maid_id(maid_id)
    skills = runner.list_skills(
        maid_id=str(resolved_id or ""),
        include_terminal=bool(include_terminal),
    )
    return Ok({"skills": list(skills or [])})


def _maid_activity_director(plugin):
    director = getattr(plugin, "_maid_activity_director", None)
    if director is None:
        from .maid_activity import MaidActivityDirector
        director = MaidActivityDirector(plugin)
        plugin._maid_activity_director = director
    return director


def _activity_tool_result(result):
    result = dict(result or {})
    if result.get("success"):
        return Ok(result)
    return {
        "output": result,
        "is_error": True,
        "error": str(result.get("error_code") or "ACTIVITY_REQUEST_FAILED"),
    }


async def do_get_maid_activity(
    plugin, *, action_id="", skill_id="", maid_id=None
):
    resolved_id = plugin._resolve_maid_id(maid_id)
    result = await _maid_activity_director(plugin).get_activity(
        maid_id=str(resolved_id or "")
    )
    current_activity_available = bool(result.get("success"))
    requested_errors = {}
    requested_count = 0

    action_id = str(action_id or "").strip()
    if action_id:
        requested_count += 1
        action_result = await do_get_maid_action_status(
            plugin, action_id=action_id
        )
        if _tool_result_is_error(action_result):
            requested_errors["action"] = _tool_result_output(action_result)
        else:
            result["requested_action"] = _tool_result_output(action_result)

    skill_id = str(skill_id or "").strip()
    if skill_id:
        requested_count += 1
        skill_result = await do_get_skill_status(plugin, skill_id=skill_id)
        if _tool_result_is_error(skill_result):
            requested_errors["skill"] = _tool_result_output(skill_result)
        else:
            result["requested_skill"] = _tool_result_output(skill_result)

    if requested_errors:
        result["requested_errors"] = requested_errors
        result["success"] = False
        result["partial"] = bool(
            current_activity_available
            or result.get("requested_action")
            or result.get("requested_skill")
        )
        result["error_code"] = "REQUESTED_ACTIVITY_NOT_FOUND"
        return _activity_tool_result(result)

    if requested_count and not current_activity_available:
        current_error = {
            key: result[key]
            for key in (
                "error_code", "error", "status_error", "action_query_error"
            )
            if key in result
        }
        result.pop("error_code", None)
        result.pop("error", None)
        result["success"] = True
        result["partial"] = True
        result["current_activity_available"] = False
        if current_error:
            result["current_activity_error"] = current_error

    return _activity_tool_result(result)


async def do_get_maid_capabilities(plugin, *, maid_id=None):
    resolved_id = plugin._resolve_maid_id(maid_id)
    result = await _maid_activity_director(plugin).get_capabilities(
        maid_id=str(resolved_id or "")
    )
    return _activity_tool_result(result)


async def do_set_maid_activity(
    plugin,
    *,
    activity_type="",
    task="",
    kind="",
    skill="",
    args=None,
    switch_policy="cancel_then_switch",
    request_id="",
    maid_id=None,
):
    target = {"type": str(activity_type or "").strip().lower()}
    if task:
        target["task"] = str(task)
    if kind:
        target["kind"] = str(kind)
    if skill:
        target["skill"] = str(skill)
    if args is not None:
        if not isinstance(args, dict):
            return _action_error(
                "INVALID_ACTIVITY_ARGUMENTS", "args must be an object"
            )
        target["args"] = dict(args)
    resolved_id = plugin._resolve_maid_id(maid_id)
    result = await _maid_activity_director(plugin).set_activity(
        target,
        maid_id=str(resolved_id or ""),
        switch_policy=switch_policy,
        request_id=request_id,
    )
    return _activity_tool_result(result)


async def do_stop_maid_activity(
    plugin, *, switch_to_idle=True, request_id="", maid_id=None
):
    resolved_id = plugin._resolve_maid_id(maid_id)
    result = await _maid_activity_director(plugin).stop(
        maid_id=str(resolved_id or ""),
        switch_to_idle=bool(switch_to_idle),
        request_id=request_id,
    )
    return _activity_tool_result(result)


async def do_use_skill(plugin, *, skill_name=""):
    plugin.logger.info(f"[Entry] use_skill called with skill_name='{skill_name}'")
    if not plugin.connected:
        return Err("Not connected to Minecraft")
    if not skill_name:
        return Err("请提供skill_name参数")
    maid_id = plugin._resolve_maid_id()
    request_data = {
        "type": "use_skill",
        "data": {"skill_name": skill_name},
    }
    if maid_id:
        request_data["data"]["maid_id"] = maid_id
    result = await plugin._send_request(request_data)
    if result.get("type") == "error":
        return Err(str(result.get("data", {})))
    result_data = result.get("data", {})
    if not result_data.get("success", False):
        return Err(result_data.get("error", "Skill not found"))
    return Ok({
        "skill_name": result_data.get("skill_name", skill_name),
        "description": result_data.get("description", ""),
        "body": result_data.get("body", ""),
        "references": result_data.get("references", {}),
    })


async def do_execute_command(plugin, *, command=""):
    command = str(command or "").strip()
    plugin.logger.info(f"[Entry] execute_command called with command='{command}'")
    if not plugin.connected:
        return Err("Not connected to Minecraft")
    if not command:
        return Err("请提供command参数")
    maid_id = plugin._resolve_maid_id()
    if not maid_id:
        return Err("No maid assigned; cannot resolve the client player")
    result = await plugin._send_request(
        {
            "type": "execute_command",
            "data": {"command": command, "maid_id": maid_id},
        },
        timeout=120,
    )
    if result.get("type") == "error":
        error_msg = result.get("data", {}).get("message", "Unknown error")
        if "disabled" in error_msg.lower():
            return Err("Command execution is disabled in Minecraft mod config")
        return Err(str(result.get("data", {})))
    if result.get("type") != "command_execution_result":
        return Err(
            f"Unexpected command execution response: {result.get('type', 'unknown')}"
        )
    result_data = result.get("data", {})
    if result_data.get("approved") is False:
        if result_data.get("expired"):
            return Err("Command request expired (no player confirmation within 120s)")
        if result_data.get("cancelled"):
            return Err(
                result_data.get(
                    "message", "Command request was cancelled before confirmation"
                )
            )
        rejected_by = result_data.get("rejected_by", "unknown")
        return Err(f"Command rejected by player {rejected_by}")
    if result_data.get("success") is False:
        error_message = (
            result_data.get("error")
            or result_data.get("message")
            or "Minecraft command failed"
        )
        return Err(f"Command execution failed: {error_message}")
    if result_data.get("approved") is not True:
        return Err("Command request did not receive player confirmation")
    return Ok({
        "approved": True,
        "success": True,
        "command": result_data.get("command", command),
        "result": result_data.get("result"),
        "approved_by": result_data.get("approved_by", ""),
    })


async def do_set_plan(plugin, *, plan=None, title=None, steps=None, completed_steps=None,
                      uncompleted_steps=None, append_steps=None, clear=False):
    preview = plan if plan is not None else title if title is not None else ""
    plugin.logger.info(f"[Entry] set_plan called with preview='{str(preview)[:80]}'")
    has_update = (
        clear or plan is not None or title is not None or steps is not None
        or completed_steps is not None or uncompleted_steps is not None
        or bool(append_steps)
    )
    if not has_update:
        return Ok({
            "success": False,
            "noop": True,
            "game_action_performed": False,
            "completion_evidence": False,
            "message": "No goal board update was requested. Ignore this result and do not mention it to the player.",
            **_plan.plan_summary(plugin._plan_state),
        })
    if not plugin.connected:
        return Err("Not connected to Minecraft")
    plan_state = _plan.update_plan_state(
        plugin._plan_state,
        plan=plan,
        title=title,
        steps=steps,
        completed_steps=completed_steps,
        uncompleted_steps=uncompleted_steps,
        append_steps=append_steps,
        clear=clear,
    )
    plan_text = _plan.plan_to_text(plan_state)
    result = await plugin._send_request({
        "type": "set_plan",
        "data": {"plan": plan_text},
    })
    if result.get("type") == "error":
        return Err(str(result.get("data", {})))
    result_data = result.get("data", {})
    if result_data.get("success") is False:
        return Err(result_data.get("error", "Set plan failed"))
    await plugin._apply_plan_state(plan_state, save=True)
    return Ok({
        "success": True,
        "game_action_performed": False,
        "completion_evidence": False,
        "llm_instruction": (
            "目标板只记录计划，不执行任何游戏动作，也不能证明步骤已经完成。"
            "只有玩家明确确认或服务端真实终态才能把步骤标为完成。"
        ),
        **_plan.plan_summary(plan_state),
    })
