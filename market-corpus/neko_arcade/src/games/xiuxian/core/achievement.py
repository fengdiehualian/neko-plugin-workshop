"""成就系统: 条件式解锁, 里程碑事件自动点亮。"""

from __future__ import annotations

from typing import Any, Dict, List

from .player import PlayerSave


def _stats(save: PlayerSave) -> Dict[str, Any]:
    return save.extra.setdefault("stats", {})


def _bump(save: PlayerSave, key: str, n: int = 1) -> None:
    s = _stats(save)
    s[key] = s.get(key, 0) + n


# 成就定义: {id, name, desc, check(save) -> bool}
ACHIEVEMENTS: List[Dict[str, Any]] = [
    {"id": "first_step", "name": "初入仙途", "desc": "踏入仙途",
     "check": lambda s: bool(s.created_at)},
    {"id": "break1", "name": "境界初成", "desc": "突破到虚妄境",
     "check": lambda s: s.realm_idx >= 1},
    {"id": "break10", "name": "凡尘蜕变", "desc": "突破到浮生境",
     "check": lambda s: s.realm_idx >= 6},
    {"id": "cult100", "name": "修炼达人", "desc": "修炼100次",
     "check": lambda s: _stats(s).get("cultivate", 0) >= 100},
    {"id": "battle50", "name": "战斗狂人", "desc": "战斗50次",
     "check": lambda s: _stats(s).get("battle", 0) >= 50},
    {"id": "boss1", "name": "屠龙初试", "desc": "讨伐BOSS胜利1次",
     "check": lambda s: _stats(s).get("boss_win", 0) >= 1},
    {"id": "boss10", "name": "镇世妖皇", "desc": "讨伐BOSS胜利10次",
     "check": lambda s: _stats(s).get("boss_win", 0) >= 10},
    {"id": "rich1w", "name": "腰缠万贯", "desc": "拥有1万灵石",
     "check": lambda s: s.lingshi_total() >= 10000},
    {"id": "daolv", "name": "道侣情深", "desc": "与猫娘结为道侣",
     "check": lambda s: s.daolv == "neko"},
    {"id": "sect1", "name": "开宗立派", "desc": "建立宗门",
     "check": lambda s: bool(s.sect)},
    {"id": "pet1", "name": "御兽大师", "desc": "拥有一只仙宠",
     "check": lambda s: bool(s.extra.get("pets", {}))},
    {"id": "secret10", "name": "秘境行者", "desc": "探索秘境10次",
     "check": lambda s: _stats(s).get("secret", 0) >= 10},
    {"id": "sign30", "name": "持之以恒", "desc": "签到30天",
     "check": lambda s: s.sign_days >= 30},
]


class Achievement:
    """成就中间件。"""

    def __init__(self, game: Any) -> None:
        self.game = game

    def record(self, save: PlayerSave, key: str, n: int = 1) -> None:
        _bump(save, key, n)

    def check_all(self, save: PlayerSave) -> List[Dict[str, Any]]:
        """检查并解锁新成就, 返回新解锁列表。"""
        unlocked = set(save.achievements)
        news = []
        for a in ACHIEVEMENTS:
            if a["id"] in unlocked:
                continue
            try:
                if a["check"](save):
                    unlocked.add(a["id"])
                    save.achievements.append(a["id"])
                    news.append(a)
            except Exception:
                continue
        return news

    def view(self, save: PlayerSave) -> Dict[str, Any]:
        unlocked = set(save.achievements)
        rows = []
        for a in ACHIEVEMENTS:
            rows.append({"id": a["id"], "name": a["name"], "desc": a["desc"],
                         "unlocked": a["id"] in unlocked})
        return {"total": len(ACHIEVEMENTS), "unlocked": len(unlocked), "rows": rows}
