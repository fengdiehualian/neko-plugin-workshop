"""诸天投影: 穿越诸天万界, 获取特产资源。

单人化: 每个世界都是 NPC 生态(原版全服共享的诸天体系降维成单人穿越),
随机世界 → 探索一次 → 得特产(修为/灵石/宝物)或遇险(损失)。
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

from .player import PlayerSave

# (世界名, 描述, 特产类型, 特产值/宝物名, 遇险概率)
WORLDS: List[Dict[str, Any]] = [
    {"name": "修真界", "desc": "灵气充沛,灵药遍地", "type": "exp", "value": 600, "risk": 0.10},
    {"name": "斗气大陆", "desc": "异火横行,斗者如云", "type": "exp", "value": 900, "risk": 0.15},
    {"name": "魔法世界", "desc": "魔力涌动,元素活跃", "type": "lingshi", "value": 900, "risk": 0.10},
    {"name": "遮天界", "desc": "大帝争锋,禁区林立", "type": "exp", "value": 1300, "risk": 0.20},
    {"name": "完美世界", "desc": "神魔乱战,宝骨遍地", "type": "lingshi", "value": 1600, "risk": 0.20},
    {"name": "荒古禁地", "desc": "危机四伏,秘藏无数", "type": "treasure", "value": "荒古秘藏", "risk": 0.30},
    {"name": "魔法少女世界", "desc": "星光闪耀,魔女传说", "type": "exp", "value": 800, "risk": 0.10},
    {"name": "小樱库洛牌世界", "desc": "库洛牌散落各处", "type": "treasure", "value": "库洛牌", "risk": 0.15},
]


class Zhutian:
    """诸天投影中间件。"""

    def __init__(self, game: Any) -> None:
        self.game = game

    async def project(self, save: PlayerSave) -> Dict[str, Any]:
        """投影到一个随机世界并探索一次。"""
        world = random.choice(WORLDS)
        roll = random.random()
        if roll < world["risk"]:
            # 遇险
            lost = int(save.lingshi_total() * 0.05)
            save.add_lingshi(-lost)
            return {"ok": True, "world": world, "danger": True,
                    "lost": lost,
                    "msg": (f"【投影·{world['name']}】{world['desc']}\n"
                            f"遭遇劫难,损失 {lost} 灵石(快跑喵!)")}
        # 得特产
        typ, val = world["type"], world["value"]
        detail = ""
        if typ == "exp":
            save.exp += val
            detail = f"修为 +{val}"
        elif typ == "lingshi":
            save.add_lingshi(val)
            detail = f"灵石 +{val}"
        else:  # treasure
            save.bag[val] = save.bag.get(val, 0) + 1
            detail = f"获得宝物「{val}」×1"
        return {"ok": True, "world": world, "danger": False,
                "msg": f"【投影·{world['name']}】{world['desc']}\n探索收获: {detail}",
                "type": typ, "value": val, "detail": detail}

    def status(self, save: PlayerSave) -> Optional[Dict[str, Any]]:
        return save.extra.get("zhutian_last")
