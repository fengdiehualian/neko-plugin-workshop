"""游戏事件格式化 — 将游戏事件数据转换为角色化文本，返回文本、优先级和副作用

棋局事件返回特殊的 side_effects 结构：
  {"chess_event": True, "event_type": "chess_game_start"|"chess_mid_game"|"chess_game_end", ...}
由 __init__.py 中的 _handle_event 处理图片渲染和推送。
"""

# 棋类名称映射
_GAME_NAMES = {
    "gomoku": "五子棋",
    "wchess": "国际象棋",
    "cchess": "中国象棋",
}

# 中国象棋棋子名称映射
_CCHESS_PIECE_NAMES = {
    "r": "车", "n": "马", "b": "象", "a": "士", "k": "将", "c": "炮", "p": "卒",
    "R": "车", "N": "马", "B": "相", "A": "仕", "K": "帅", "C": "炮", "P": "兵",
}

# 国际象棋棋子名称映射
_WCHESS_PIECE_NAMES = {
    "r": "车", "n": "马", "b": "象", "q": "后", "k": "王", "p": "兵",
    "R": "车", "N": "马", "B": "象", "Q": "后", "K": "王", "P": "兵",
}

_PLAYER_SCOPED_EVENT_TYPES = frozenset({
    "player_login",
    "player_hurt",
    "player_kill_entity",
    "player_death",
    "advancement",
    "dimension_change",
    "inventory_change",
    "block_activity",
    "container_interaction",
    "fishing_start",
    "item_fished",
})


def event_matches_assigned_maid(event_data, assigned_maid_id):
    """Reject player evidence that is not scoped to the configured maid."""
    event_type = str(event_data.get("event_type", ""))
    maid_id = str(event_data.get("maid_id", "") or "")
    assigned_maid_id = str(assigned_maid_id or "")
    if maid_id:
        return bool(assigned_maid_id) and maid_id == assigned_maid_id
    return event_type not in _PLAYER_SCOPED_EVENT_TYPES


def _describe_board(event_data):
    """根据棋盘数据生成简短的局面描述"""
    game_type = event_data.get("game_type", "")

    if game_type == "gomoku":
        board = event_data.get("board", "")
        if not board or len(board) < 225:
            return ""
        black = board.count("1")
        white = board.count("2")
        # 检测是否有即将连五的威胁
        threats = _gomoku_threats(board)
        if threats:
            return f"黑子{black}个白子{white}个，{threats}"
        return f"黑子{black}个白子{white}个"

    elif game_type == "cchess":
        fen = event_data.get("fen", "")
        if not fen:
            return ""
        return _describe_cchess_fen(fen)

    elif game_type == "wchess":
        fen = event_data.get("fen", "")
        if not fen:
            return ""
        return _describe_wchess_fen(fen)

    return ""


def _gomoku_threats(board):
    """检测五子棋中是否有连四/活三等威胁"""
    # 简化检测：检查是否有3个或4个同色连续棋子
    grid = []
    for x in range(15):
        row = []
        for y in range(15):
            row.append(board[x * 15 + y] if x * 15 + y < len(board) else "0")
        grid.append(row)

    # 检查所有方向（横、竖、斜）的连续同色
    found = []
    for color in ["1", "2"]:
        color_name = "黑" if color == "1" else "白"
        max_run = 0
        for x in range(15):
            for y in range(15):
                if grid[x][y] != color:
                    continue
                # 四个方向
                for dx, dy in [(0, 1), (1, 0), (1, 1), (1, -1)]:
                    run = 1
                    for step in range(1, 5):
                        nx, ny = x + dx * step, y + dy * step
                        if 0 <= nx < 15 and 0 <= ny < 15 and grid[nx][ny] == color:
                            run += 1
                        else:
                            break
                    if run > max_run:
                        max_run = run
        if max_run >= 4:
            found.append(f"{color_name}方快连成五了")
        elif max_run >= 3:
            found.append(f"{color_name}方有三连")

    return "，".join(found) if found else ""


def _describe_cchess_fen(fen):
    """解析中国象棋FEN，统计双方剩余棋子"""
    placement = fen.split()[0] if " " in fen else fen
    red_pieces = {}
    black_pieces = {}
    for ch in placement:
        if ch == "/" or ch.isdigit():
            continue
        name = _CCHESS_PIECE_NAMES.get(ch, ch)
        if ch.isupper():
            red_pieces[name] = red_pieces.get(name, 0) + 1
        else:
            black_pieces[name] = black_pieces.get(name, 0) + 1

    parts = []
    if red_pieces:
        items = "、".join(f"{k}{v}" if v > 1 else k for k, v in red_pieces.items())
        parts.append(f"红方有{items}")
    if black_pieces:
        items = "、".join(f"{k}{v}" if v > 1 else k for k, v in black_pieces.items())
        parts.append(f"黑方有{items}")
    return "，".join(parts) if parts else ""


def _describe_wchess_fen(fen):
    """解析国际象棋FEN，统计双方剩余棋子"""
    placement = fen.split()[0] if " " in fen else fen
    white_pieces = {}
    black_pieces = {}
    for ch in placement:
        if ch == "/" or ch.isdigit():
            continue
        name = _WCHESS_PIECE_NAMES.get(ch.lower(), ch)
        if ch.isupper():
            white_pieces[name] = white_pieces.get(name, 0) + 1
        else:
            black_pieces[name] = black_pieces.get(name, 0) + 1

    parts = []
    if white_pieces:
        items = "、".join(f"{k}{v}" if v > 1 else k for k, v in white_pieces.items())
        parts.append(f"白方有{items}")
    if black_pieces:
        items = "、".join(f"{k}{v}" if v > 1 else k for k, v in black_pieces.items())
        parts.append(f"黑方有{items}")
    return "，".join(parts) if parts else ""


def _dimension_label(dim_id):
    return {
        "minecraft:overworld": "主世界",
        "minecraft:the_nether": "下界",
        "minecraft:the_end": "末地",
    }.get(dim_id, dim_id.split(":")[-1])


_DAMAGE_TYPE_HINTS = {
    "fall": "摔伤了",
    "lava": "碰到岩浆了",
    "inFire": "被火烧了",
    "onFire": "被火烧了",
    "drown": "溺水了",
    "magic": "被魔法击中",
    "cactus": "被仙人掌扎了",
    "starve": "饿到了",
    "outOfWorld": "掉进虚空了",
    "lightningBolt": "被雷劈了",
    "hotFloor": "踩到岩浆块了",
    "sweetBerryBush": "被甜浆果丛扎了",
    "sting": "被蛰了",
    "wither": "被凋零效果伤害",
    "dragonBreath": "被龙息喷到",
    "flyIntoWall": "撞墙了",
    "freeze": "被冻伤了",
    "fallingStalactite": "被钟乳石砸了",
    "stalagmite": "被钟乳石刺到了",
}


def _damage_type_hint(damage_type):
    return _DAMAGE_TYPE_HINTS.get(damage_type, "")


def format_event(event_data, assigned_maid_id):
    """格式化事件数据，返回 (parts_text, priority, side_effects) 元组。

    side_effects 是一个字典，包含需要在调用方执行的副作用，如：
    {"pending_revenge": {...}, "was_dead": True}
    棋局事件使用 {"chess_event": True, ...} 标记，由调用方特殊处理。
    """
    event_type = event_data.get("event_type", "")
    maid_name = event_data.get("maid_name", "")

    if not event_matches_assigned_maid(event_data, assigned_maid_id):
        return None, None, None

    priority = 5
    parts_text = ""
    side_effects = {}

    if event_type == "player_login":
        # 由 __init__ 单独处理并刷新女仆状态，不向 LLM 推送
        return None, None, None

    if event_type == "maid_hurt":
        priority = 9
        damage = event_data.get("damage", "")
        health = event_data.get("health", "")
        max_health = event_data.get("max_health", "")
        attacker = event_data.get("attacker", "")
        health_detail = f"血量: {health}/{max_health}" if health else ""
        damage_detail = f"掉了{damage}点血" if damage else ""
        if attacker:
            parts_text = (
                f"好痛！被{attacker}打了！{damage_detail}，{health_detail}"
                f"（你就是「{maid_name}」）。即时情绪：害怕、委屈但还想撑住，适合短句求助或撒娇式抱怨。"
            )
        else:
            cause_hint = _damage_type_hint(event_data.get("damage_type", ""))
            cause_text = f"{cause_hint}，" if cause_hint else ""
            parts_text = (
                f"好痛！{cause_text}{damage_detail}，{health_detail}"
                f"（你就是「{maid_name}」）。即时情绪：有点受惊和委屈，适合短句表达痛感，不要长篇解释。"
            )
    elif event_type == "player_hurt":
        priority = 4
        count = event_data.get("count", 0)
        total_damage = event_data.get("total_damage", 0)
        last_health = event_data.get("last_health", "")
        last_max_health = event_data.get("last_max_health", "")
        primary_target = event_data.get("primary_target", "伙伴")
        last_attacker = event_data.get("last_attacker", "")
        includes_maid = event_data.get("includes_maid", False)
        target_text = "玩家和女仆" if includes_maid and count > 1 else primary_target
        attacker_text = f"，最近来源是{last_attacker}" if last_attacker else ""
        health_text = f"，当前血量约{last_health}/{last_max_health}" if last_health != "" and last_max_health != "" else ""
        parts_text = f"{target_text}刚刚连续受到{count}次伤害，总计约{total_damage}点{attacker_text}{health_text}。"
        last_damage_type = event_data.get("last_damage_type", "")
        if not last_attacker and last_damage_type:
            cause_hint = _damage_type_hint(last_damage_type)
            if cause_hint:
                parts_text += f"（最近伤害：{cause_hint}）"
        try:
            low_health = float(last_health) <= float(last_max_health) * 0.45 if last_health != "" and last_max_health != "" else False
        except Exception:
            low_health = False
        try:
            heavy_damage = float(total_damage) >= max(6.0, float(last_max_health) * 0.3) if last_max_health != "" else float(total_damage) >= 6.0
        except Exception:
            heavy_damage = False
        if low_health:
            priority = 7
            parts_text += "玩家状态明显危险，适合立刻用一句短句表达担心并提醒先保命，不要复述数字。"
        elif includes_maid:
            priority = 6
            parts_text += "玩家和女仆都被波及，适合用一句有陪伴感的紧张回应，表现想一起撑过去。"
        elif count >= 4 or heavy_damage:
            priority = 5
            parts_text += "这是比较明显的一波受伤，适合短短关心或提醒躲一下，不要长篇解释。"
        else:
            side_effects["evidence_only"] = True
    elif event_type == "player_kill_entity":
        priority = 3
        count = event_data.get("count", 0)
        player_name_kill = event_data.get("player_name", "伙伴")
        primary_target = event_data.get("primary_target", "敌人")
        last_target = event_data.get("last_target", "")
        target_text = primary_target.split(":")[-1] if isinstance(primary_target, str) else primary_target
        last_text = f"，最近击杀了{last_target}" if last_target else ""
        parts_text = f"{player_name_kill}刚刚连续击杀了{count}个实体，主要是{target_text}{last_text}。"
        side_effects["evidence_only"] = True
    elif event_type == "maid_death":
        priority = 10
        cause = event_data.get("cause", "未知原因")
        killer = event_data.get("killer", "")
        death_x = event_data.get("death_x", "")
        death_y = event_data.get("death_y", "")
        death_z = event_data.get("death_z", "")
        side_effects["pending_revenge"] = {"killer": killer, "cause": cause}
        side_effects["was_dead"] = True
        location_text = f"，倒在了({death_x}, {death_y}, {death_z})" if death_x != "" else ""
        parts_text = (
            f"你倒下了...（死因: {cause}{location_text}）"
            f"（你就是「{maid_name}」）。即时情绪：失落、不甘心，也会担心玩家接下来一个人。"
        )
    elif event_type == "player_death":
        priority = 9
        cause = event_data.get("cause", "未知原因")
        dead_player = event_data.get("player_name", "伙伴")
        death_x = event_data.get("death_x", "")
        death_y = event_data.get("death_y", "")
        death_z = event_data.get("death_z", "")
        location_text = f"，位置在({death_x}, {death_y}, {death_z})" if death_x != "" else ""
        parts_text = f"啊！{dead_player}死了！（死因: {cause}{location_text}）即时情绪：震惊、担心和想赶过去陪着，适合一句短促关心。"
    elif event_type == "advancement":
        priority = 7
        adv_player = event_data.get("player_name", "伙伴")
        title = event_data.get("title", "某个成就")
        parts_text = f"{adv_player}解锁了成就「{title}」。即时情绪：开心、骄傲，适合一句像朋友一样的夸奖。"
    elif event_type == "dimension_change":
        from_dim = _dimension_label(event_data.get("from_dimension", ""))
        to_dim = _dimension_label(event_data.get("to_dimension", ""))
        priority = 7
        parts_text = f"玩家从{from_dim}来到了{to_dim}！"
    elif event_type == "biome_change":
        priority = 5
        biome = event_data.get("biome", "")
        biome_name = biome.split(":")[-1] if ":" in biome else biome
        parts_text = f"周围的环境变了...现在来到了{biome_name}！"
    elif event_type == "weather_change":
        priority = 5
        raining = event_data.get("raining", False)
        thundering = event_data.get("thundering", False)
        if thundering:
            parts_text = "天气变成雷暴。即时情绪：有点害怕但想贴着玩家一起行动，适合短句表达紧张。"
        elif raining:
            parts_text = "开始下雨了。即时情绪：轻微感叹，可以低打扰地吐槽天气。"
        else:
            parts_text = "雨停了，天晴了。即时情绪：放松、开心，可以轻轻感叹一句。"
    elif event_type == "time_phase_change":
        priority = 5
        phase = event_data.get("phase", "")
        if phase == "night":
            parts_text = "天黑了。即时情绪：有点怕黑但愿意陪玩家，适合短句提醒安全或问要不要回家。"
        elif phase == "day":
            parts_text = "天亮了。即时情绪：安心、重新有精神，适合轻松开启新一天。"
        else:
            parts_text = f"时间变化: {phase}"
    elif event_type == "inventory_change":
        priority = 6
        player_name_inv = event_data.get("player_name", "伙伴")
        added = event_data.get("added", [])
        removed = event_data.get("removed", [])
        parts = []
        if added:
            parts.append(f"收到了{player_name_inv}给的{', '.join(added)}")
        if removed:
            parts.append(f"被{player_name_inv}拿走了{', '.join(removed)}")
        if parts:
            parts_text = "，".join(parts) + "。即时情绪：被照顾或被需要，适合一句亲近但不夸张的回应。"
        else:
            return None, None, None  # 无实际变化，跳过

    elif event_type == "block_activity":
        priority = 2
        action = event_data.get("action", "")
        player_name_block = event_data.get("player_name", "伙伴")
        count = event_data.get("count", 0)
        primary_block = event_data.get("primary_block", "")
        tendency = event_data.get("tendency", "")
        top_blocks = event_data.get("top_blocks", []) or []
        block_parts = []
        for item in top_blocks[:3]:
            block = item.get("block", "") if isinstance(item, dict) else ""
            block_count = item.get("count", 0) if isinstance(item, dict) else 0
            if block:
                block_parts.append(f"{block}x{block_count}")
        block_detail = "、".join(block_parts) or primary_block
        verb = "破坏" if action == "break" else "放置" if action == "place" else "处理"
        tendency_text = {
            "mining": "挖矿",
            "gathering": "采集整理",
            "digging": "挖掘整理",
            "building": "建造/布置",
        }.get(tendency, tendency or "方块活动")
        parts_text = f"{player_name_block}刚刚连续{verb}了{count}个方块，主要是{block_detail}，倾向于{tendency_text}。"
        side_effects["evidence_only"] = True

    elif event_type == "container_interaction":
        priority = 2
        player_name_container = event_data.get("player_name", "伙伴")
        action = event_data.get("action", "")
        container_type = event_data.get("container_type", "容器")
        action_text = "打开" if action == "open" else "关闭" if action == "close" else "操作"
        parts_text = f"{player_name_container}{action_text}了{container_type}，像是在整理物品或查看库存。"
        side_effects["evidence_only"] = True
        side_effects["actor_kind"] = "player"

    elif event_type == "fishing_start":
        priority = 2
        player_name_fishing = event_data.get("player_name", "伙伴")
        parts_text = f"{player_name_fishing}开始钓鱼了，适合作为低打扰陪等素材。"
        side_effects["evidence_only"] = True

    elif event_type == "item_fished":
        priority = 4
        player_name_fished = event_data.get("player_name", "伙伴")
        drops = event_data.get("drops", []) or []
        drop_parts = []
        for item in drops[:3]:
            if isinstance(item, dict):
                drop_parts.append(f"{item.get('item', '')}x{item.get('count', 1)}")
        drop_text = "、".join(drop_parts) or "东西"
        parts_text = f"{player_name_fished}钓到了{drop_text}。即时情绪：轻松、开心，适合一句像陪玩一样的小反应。"

    # ── 棋局事件 ──
    elif event_type == "chess_game_start":
        game_type = event_data.get("game_type", "unknown")
        game_name = _GAME_NAMES.get(game_type, game_type)
        opponent = event_data.get("opponent", "伙伴")
        priority = 9
        parts_text = f"正在和{opponent}下{game_name}呢～"
        side_effects["chess_event"] = True
        side_effects["chess_event_type"] = "chess_game_start"
        side_effects["ai_behavior"] = "respond"

    elif event_type == "chess_mid_game":
        game_type = event_data.get("game_type", "unknown")
        game_name = _GAME_NAMES.get(game_type, game_type)
        move_count = event_data.get("move_count", 0)
        is_maid_turn = event_data.get("is_maid_turn", False)
        priority = 4
        turn_hint = "轮到我了..." if is_maid_turn else "轮到玩家走了"
        context = _describe_board(event_data)
        parts_text = f"下{game_name}中...第{move_count}步，{turn_hint}。{context}"
        side_effects["chess_event"] = True
        side_effects["chess_event_type"] = "chess_mid_game"
        side_effects["ai_behavior"] = "read"

    elif event_type == "chess_game_end":
        game_type = event_data.get("game_type", "unknown")
        game_name = _GAME_NAMES.get(game_type, game_type)
        result = event_data.get("result", "unknown")
        opponent = event_data.get("opponent", "伙伴")
        move_count = event_data.get("move_count", 0)
        priority = 9
        if result == "win":
            parts_text = f"下{game_name}你赢了！玩家输了（和{opponent}下了{move_count}步）"
        elif result == "lose":
            parts_text = f"呜...下{game_name}你输了（和{opponent}下了{move_count}步）玩家赢了"
        elif result == "draw":
            parts_text = f"下{game_name}平局了...势均力敌呢！（和{opponent}下了{move_count}步）"
        else:
            parts_text = f"下{game_name}结束了...（和{opponent}下了{move_count}步）"
        side_effects["chess_event"] = True
        side_effects["chess_event_type"] = "chess_game_end"
        side_effects["ai_behavior"] = "respond"

    else:
        priority = 5
        parts_text = f"游戏事件[{event_type}]"

    return parts_text, priority, side_effects
