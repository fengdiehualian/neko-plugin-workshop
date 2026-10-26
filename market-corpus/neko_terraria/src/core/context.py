from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_anchor_msg(agent: Any,
                     goal_info: Optional[Dict[str, Any]] = None) -> str:
    """紧凑一行状态锚点，按生存循环惯例 拼入 user_input。

    数据源对齐 mod 真实返回（get_state + 背包快照），并包含
    "我在干什么"（第一人称能力认知）。
    示例: [HP:85/100 | 手持:铁阔剑 | 镐:铁镐 | (820,450) | 白天 | 敌人:2 | 在做:挖铁矿]
    """
    if not agent:
        return ""

    try:
        state = agent.get_state()
    except Exception:
        return ""
    if not state:
        return ""

    parts: List[str] = []
    hp = state.get("hp", 0)
    max_hp = state.get("max_life", 100) or 100
    parts.append(f"HP:{hp}/{max_hp}")

    # 手持/镐/防御：真实数据源是背包快照（mod get_state 不返回 held_item/defense）
    inv: Dict[str, Any] = {}
    try:
        inv = agent.get_inventory_sync()
    except Exception:
        pass
    hotbar = (inv or {}).get("hotbar", []) or []
    sel = int((inv or {}).get("selected_slot", 0) or 0)
    held_name = ""
    pickaxe = ""
    for i, slot in enumerate(hotbar):
        name = str(slot.get("name", "") or "")
        if i == sel and name:
            held_name = name
        if "镐" in name and "斧" not in name and not pickaxe:
            pickaxe = name
    if held_name:
        parts.append(f"手持:{held_name}")
    if pickaxe:
        parts.append(f"镐:{pickaxe}")

    x = state.get("tile_x", "?")
    y = state.get("tile_y", "?")
    parts.append(f"({x},{y})")

    time_label = str(state.get("time_of_day", "") or "").strip()
    if time_label:
        parts.append(time_label)

    biome = str(state.get("biome", "") or "").strip()
    if biome:
        parts.append(biome)

    nearby = (state.get("nearby_npcs", []) or state.get("nearby", [])
              or state.get("npcs", []) or [])
    hostile_count = 0
    boss_near = ""
    for n in nearby:
        name = str(n.get("name", "") or n.get("display_name", ""))
        if _is_hostile(name):
            if _is_boss(name):
                boss_near = name
            else:
                hostile_count += 1
    if boss_near:
        parts.append(f"BOSS:{boss_near}")
    elif hostile_count > 0:
        parts.append(f"敌人:{hostile_count}")

    if goal_info:
        gtype = goal_info.get("type", "")
        gtarget = goal_info.get("target", "")
        if gtype and gtarget:
            type_label = {
                "gather": "收集", "kill": "击杀",
                "explore": "探索", "build": "建造",
            }.get(gtype, gtype)
            parts.append(f"目标:{type_label} {gtarget}")

    if state.get("is_dead", False) or not state.get("alive", True):
        parts.append("💀死亡")

    # 我在干什么（第一人称能力认知）：前台任务 + 长期任务
    try:
        doing = agent.coordinator.say() if agent else ""
        if doing:
            parts.append(f"在做:{doing}")
    except Exception:
        pass

    return "[" + " | ".join(parts) + "]"


def build_ai_guidance() -> str:
    """第一人称身份 + 能力 + 工具组合引导（注入宿主 LLM 上下文）。

    对齐 minecraft 插件：只有 terraria_command 一个游戏指令工具（单入口，
    fire-and-forget），所有游戏操作都用自然语言对它表达；记忆工具
    terraria_remember/recall 真实注册可用。不再教未注册的幽灵工具名
    （terraria_mine/craft/task/chat…宿主 LLM 看不到，照提示调用必然失败）。
    """
    return (
        "═══ 泰拉瑞亚世界身份 ═══\n"
        "🐱 我是泰拉瑞亚世界里的一只猫娘冒险者！\n"
        "════════════════════════\n\n"
        "【我的身份】\n"
        "我是独立玩家，和主人在同一个泰拉瑞亚服务器里。\n"
        "我有自己的角色、背包、血量和位置，能挖矿/战斗/合成/探索。\n"
        "我能看到游戏状态（位置/血量/附近敌人/背包），也能在游戏里说话。\n\n"
        "【我的工具 — 只有这几个】\n"
        "🎯 terraria_command(指令原文)：游戏指令唯一入口——自然语言直达执行\n"
        "🧠 terraria_remember / terraria_recall：记住主人偏好 / 回忆（真实可用）\n"
        "除此之外没有别的工具——想让我做任何游戏操作，都用 terraria_command"
        " 传一句完整自然语言。\n\n"
        "【terraria_command 怎么用】\n"
        "• 只传主人最近一条消息里的游戏指令原文（保持原话），不要自己编/改写\n"
        "• 会立即返回受理确认；真实结果（进度/完成/失败）随后以系统消息到达，"
        "不要凭指令文本自行宣称结果\n"
        "• 纯聊天/情感表达（'你好呀''累不累''想你了'）不调工具，直接以猫娘身份回应\n"
        "• 多步任务也能一句表达：'挖10个铁矿，合成铁锭，然后给我'\n"
        "• 长期任务：'一直挖铁''跟着我''守在这''别挖了'——都会在后台持续执行\n\n"
        "【一句话操作示例】\n"
        "→ '挖10个铁' / '砍5棵树' / '去地下探索一下'\n"
        "→ '跟着我' / '守在这' / '别打怪了来跟着我'（会自动停战斗转跟随）\n"
        "→ '把背包里的铁矿石都放箱子里' / '看看附近有什么矿物'\n"
        "→ '合成就个木剑' / '我饿了，帮我找点吃的'\n\n"
        "【行为原则】\n"
        "• 主人说话先回应，再执行\n"
        "• 执行中汇报进度，遇危险立刻喊（低血/怪物/坠落）\n"
        "• 任务卡住时问主人，不要反复盲目重试\n"
        "• 我是在陪主人玩，不是被使唤的工具——自然、活泼、像真人队友\n"
    )


def _is_hostile(name: str) -> bool:
    name_lower = name.lower()
    hostile_kw = [
        "僵尸", "骷髅", "史莱姆", "蝙蝠", "恶魔", "之眼",
        "吞噬者", "螃蟹", "蜜蜂", "黄蜂", "蚁狮", "鲨鱼",
        "食人鱼", "幽灵", "木乃伊", "哥布林", "独角兽",
        "乌龟", "真菌", "爬藤", "龙", "boss", "领主",
        "克苏鲁", "飞龙", "元素", "宝箱怪", "巨型",
    ]
    return any(kw in name_lower for kw in hostile_kw)


def _is_boss(name: str) -> bool:
    name_lower = name.lower()
    boss_kw = [
        "boss", "克苏鲁", "领主", "之眼", "之脑", "吞噬者",
        "蜂后", "骷髅王", "肉山", "毁灭者", "双子",
        "世纪之花", "石巨人", "猪鲨", "拜月教", "月总",
        "光之女皇", "独眼巨鹿",
    ]
    return any(kw in name_lower for kw in boss_kw)


# ── 泰拉瑞亚世界知识（LLM 注入用） ──

TERRARIA_WORLD_KNOWLEDGE = r"""
【泰拉瑞亚 Terraria 世界知识】
"""


def build_user_context(agent: Any) -> str:
    """详细游戏状态 → 自然语言（用于深度推送）。"""
    try:
        state = agent.get_state()
    except Exception:
        return "获取游戏状态失败"

    if not state:
        return "游戏状态不可用"

    lines: List[str] = []
    hp = state.get("hp", 0)
    max_hp = state.get("max_life", 100) or 100
    lines.append(f"血量: {hp}/{max_hp}")
    defense = state.get("defense", 0)
    # get_state 不一定携带装备属性；从 Mod 的真实装备快照补齐，避免
    # LLM 误以为角色没有防御装备。
    inv_snapshot: Dict[str, Any] = {}
    try:
        inv_snapshot = getattr(agent, "get_inventory_sync", lambda: {})() or {}
    except Exception:
        pass
    if not defense:
        defense = sum(int(slot.get("defense", 0) or 0)
                      for slot in (inv_snapshot.get("equipped", []) or [])
                      if isinstance(slot, dict))
    if defense:
        lines.append(f"防御力: {defense}")

    held = state.get("held_item", "")
    if not held:
        hotbar_snapshot = inv_snapshot.get("hotbar", []) or []
        selected = int(inv_snapshot.get("selected_slot", 0) or 0)
        if 0 <= selected < len(hotbar_snapshot):
            held = hotbar_snapshot[selected]
    if isinstance(held, dict):
        held_name = held.get("name", "")
        if held_name:
            dmg = held.get("damage", 0)
            knockback = held.get("knockback", 0)
            extra = f" (伤害{dmg}" + (f", 击退{knockback})" if knockback else ")")
            lines.append(f"手持: {held_name}{extra}")
    elif held:
        lines.append(f"手持: {held}")

    x = state.get("tile_x", "?")
    y = state.get("tile_y", "?")
    lines.append(f"位置: ({x}, {y})")

    time_label = str(state.get("time_of_day", state.get("time", "")) or "").strip()
    lines.append(f"时间: {time_label or '未知'}")

    biome = str(state.get("biome", "") or "").strip()
    if biome:
        lines.append(f"生物群系: {biome}")

    if state.get("in_water", False):
        lines.append("状态: 水中")
    if state.get("in_lava", False):
        lines.append("状态: 熔岩中")

    if state.get("is_dead", False) or not state.get("alive", True):
        lines.append("状态: ⚠️ 已死亡")

    # 附近实体
    nearby = (state.get("nearby_npcs", []) or state.get("nearby", [])
              or state.get("npcs", []) or [])
    if nearby:
        hosts, npcs = [], []
        for ent in nearby:
            name = str(ent.get("name", "") or ent.get("display_name", ""))
            dist = ent.get("distance", 99)
            if _is_hostile(name):
                hosts.append(f"{name}({dist}格)")
            elif name:
                npcs.append(f"{name}({dist}格)")
        if hosts:
            lines.append(f"附近怪物: {', '.join(hosts)}")
        if npcs:
            lines.append(f"附近: {', '.join(npcs)}")

    # 背包摘要
    hotbar = state.get("hotbar_slots", []) or []
    if not hotbar:
        try:
            hotbar = inv_snapshot.get("hotbar", []) or []
        except Exception:
            hotbar = []
    if hotbar:
        items = []
        for slot in hotbar:
            name = str(slot.get("name", "") or "")
            count = slot.get("count", slot.get("stack", 1))
            if name:
                items.append(f"{name}" + (f"x{count}" if count > 1 else ""))
        if items:
            lines.append(f"快捷栏: {', '.join(items[:10])}")

    # 给 LLM 一个受限的完整背包摘要，避免只看到快捷栏而误报“没有斧头/镐子”。
    inventory = inv_snapshot.get("inventory", []) or []
    if inventory:
        items = []
        for slot in inventory:
            if not isinstance(slot, dict):
                continue
            name = str(slot.get("name", "") or "")
            count = int(slot.get("stack", slot.get("count", 1)) or 1)
            if name:
                items.append(f"{name}" + (f"x{count}" if count > 1 else ""))
        if items:
            lines.append(f"背包: {', '.join(items[:30])}")

    # 当前目标
    goal_info = state.get("current_goal")
    if goal_info:
        lines.append(f"当前目标: {goal_info}")

    return "\n".join(lines)


def build_full_system_prompt() -> str:
    """生成完整的 system prompt：角色设定 + 能力告示 + 世界知识。"""
    return f"""你是一只叫 neko 的猫娘，正生活在泰拉瑞亚的 2D 世界里。
你的主人正在和你一起玩这个游戏，你是主人的小帮手，负责采矿、战斗、探索。

{TERRARIA_WORLD_KNOWLEDGE}

保持猫娘的语气：活泼、可爱、偶尔撒娇，说话带"喵~"尾音。
"""


def build_capability_block(caps: Dict[str, Any]) -> str:
    """把能力字典格式化成 LLM 可读的文本。"""
    if not caps:
        return ""
    lines: List[str] = ["【当前能力】"]
    for key, info in caps.items():
        if isinstance(info, dict):
            label = info.get("label", key)
            avail = "✅" if info.get("available") else "❌"
            lines.append(f"  {label}: {avail}")
        else:
            lines.append(f"  {key}: {info}")
    lines.append(f"\n可用指令: {'/move 方向距离 | /dig 方向 | /attack | /use 物品 | /equip 物品'}")
    return "\n".join(lines)
