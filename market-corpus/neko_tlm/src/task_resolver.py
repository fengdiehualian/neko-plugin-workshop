"""任务名解析与模糊匹配 — 将中文同义词（如"种田""打草"）映射到 TLM 任务 ID"""

_TASK_SYNONYMS = {
    "farm": ["种田", "农耕", "农场", "收田", "收割", "种地", "务农", "耕地", "农田", "收麦", "种麦", "种菜"],
    "sugar_cane": ["甘蔗", "收甘蔗", "种甘蔗", "打甘蔗", "砍甘蔗"],
    "melon": ["瓜", "西瓜", "南瓜", "收瓜", "种瓜", "瓜类"],
    "grass": ["草", "打草", "割草", "除草", "拔草", "杂草", "清草"],
    "feed": ["喂", "喂食", "喂养", "喂动物", "饲养"],
    "shear": ["剪", "剪毛", "剪羊毛", "剃毛"],
    "milk": ["挤奶", "牛奶", "挤牛奶"],
    "torch": ["火把", "插火把", "照明", "点灯", "下矿", "挖矿", "探洞", "矿洞", "找矿", "洞穴"],
    "attack": ["攻击", "打怪", "战斗", "杀怪", "近战"],
    "ranged_attack": ["弓", "弓箭", "射箭", "弓兵", "远程"],
    "crossbow_attack": ["弩", "弩箭", "弩兵"],
    "danmaku_attack": ["弹幕", "射击", "符卡"],
    "trident_attack": ["三叉戟", "投掷"],
    "idle": ["待机", "空闲", "休息", "待命", "什么都不做", "停下"],
    "brew": ["酿造", "药水", "酿酒"],
    "cocoa": ["可可", "可可豆", "种可可"],
    "snow": ["雪", "铲雪", "清雪"],
    "board_games": [
        "游戏", "游戏模式", "小游戏", "玩", "棋牌", "桌游", "玩游戏",
        "玩小游戏", "下棋", "棋", "五子棋", "象棋", "国际象棋",
    ],
}


def resolve_task_name(task, available_tasks=None):
    if ":" in task:
        return task
    if available_tasks:
        return fuzzy_match_task(task, available_tasks)
    # 无 available_tasks — 尝试通过同义词查找获取 short_id
    short_id = _synonym_lookup(task)
    if short_id:
        return f"touhou_little_maid:{short_id}"
    return task


def _synonym_lookup(query):
    """通过同义词表查找短ID，无匹配返回 None"""
    query_str = query.strip()
    query_lower = query_str.lower()
    for short_id, synonyms in _TASK_SYNONYMS.items():
        if query_lower == short_id.lower():
            return short_id
        for syn in synonyms:
            if query_str == syn or query_lower == syn.lower():
                return short_id
    return None


def fuzzy_match_task(query, available_tasks):
    if not available_tasks:
        return None
    query_lower = query.lower().strip()
    short_id_map = {}
    for t in available_tasks:
        if isinstance(t, dict):
            task_id = t.get("id", "")
            task_name = t.get("name", "")
        else:
            task_id = str(t)
            task_name = str(t)
        short_id = task_id.split(":")[-1] if ":" in task_id else task_id
        short_id_map[short_id.lower()] = task_id
        if query_lower == task_name.lower() or query_lower == short_id.lower() or query_lower == task_id.lower():
            return task_id
    for short_id_key, task_id in short_id_map.items():
        synonyms = _TASK_SYNONYMS.get(short_id_key, [])
        for syn in synonyms:
            if query == syn or query_lower == syn.lower():
                return task_id
            if syn in query or query in syn:
                return task_id
    best_match = None
    best_score = 0
    for t in available_tasks:
        if isinstance(t, dict):
            task_id = t.get("id", "")
            task_name = t.get("name", "")
        else:
            task_id = str(t)
            task_name = str(t)
        task_name_lower = task_name.lower()
        short_id = task_id.split(":")[-1] if ":" in task_id else task_id
        short_id_lower = short_id.lower()
        if query_lower in task_name_lower:
            score = len(query_lower) / max(len(task_name_lower), 1)
            if score > best_score:
                best_score = score
                best_match = task_id
        elif task_name_lower in query_lower:
            score = len(task_name_lower) / max(len(query_lower), 1) * 0.9
            if score > best_score:
                best_score = score
                best_match = task_id
        if query_lower in short_id_lower:
            score = 0.5 + len(query_lower) / max(len(short_id_lower), 1) * 0.5
            if score > best_score:
                best_score = score
                best_match = task_id
        elif short_id_lower in query_lower:
            score = 0.5 + len(short_id_lower) / max(len(query_lower), 1) * 0.4
            if score > best_score:
                best_score = score
                best_match = task_id
    return best_match
