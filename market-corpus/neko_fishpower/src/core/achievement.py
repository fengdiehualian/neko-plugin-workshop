"""成就/里程碑：插件自定义庆祝（总渔获/图鉴进度/稀有鱼/连续不跑鱼）。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .memory import MemoryStore

MILESTONES = [
    # (key, 条件值, 庆祝语模板)
    ("total_caught", 100, "钓到第 100 条鱼了喵！钓鱼小达人！🎉"),
    ("total_caught", 500, "500 条！这片海的鱼都认识你了喵！👑"),
    ("journal", None, None),  # 全图鉴（动态判断）
    ("rare_caught", 10, "钓到 10 条稀有货了喵！眼光毒辣！✨"),
    ("rare_caught", 50, "50 条稀有！你是稀有鱼猎人喵！🌟"),
]


class Achievement:
    """里程碑统计 + 庆祝（持久化到 memory）。"""

    def __init__(self, memory: MemoryStore) -> None:
        self.memory = memory
        self._streak = 0

    def _get(self, key: str) -> int:
        return int((self.memory.query("achievement", key) or {}).get("count", 0))

    def _bump(self, key: str) -> int:
        return self.memory.bump("achievement", key, "count")

    def on_caught(self, msg: Dict[str, Any]) -> Optional[str]:
        """上钩统计 + 里程碑判断。"""
        total = self._bump("total_caught")
        rarity = str(msg.get("rarity", ""))
        if rarity in ("rare", "legendary") or msg.get("shiny"):
            rare = self._bump("rare_caught")
        else:
            rare = self._get("rare_caught")
        self._streak += 1
        # 里程碑庆祝（一次性的：按 key+阈值记录已庆祝）
        for key, threshold, line in MILESTONES:
            if line is None:
                continue
            if key == "total_caught" and total == threshold:
                return line
            if key == "rare_caught" and rare == threshold:
                return line
        return None

    def on_miss(self) -> None:
        """跑鱼：连续上钩中断。"""
        self._streak = 0

    def on_journal(self, count: int, total: int) -> Optional[str]:
        """图鉴里程碑：50% / 全图鉴。"""
        if not total:
            return None
        half = total // 2
        if count == half and self._get("journal_half") == 0:
            self._bump("journal_half")
            return f"图鉴收集过半了喵！{count}/{total}，离全图鉴不远了！"
        if count >= total and self._get("journal_full") == 0:
            self._bump("journal_full")
            return "全图鉴达成喵！！你是渔力全开的传说！👑🎉"
        return None

    def streak_tip(self) -> Optional[str]:
        """连续上钩里程碑（20 连）。"""
        if self._streak == 20:
            return "连续 20 竿不跑鱼喵！稳如老狗，值得表扬！⭐"
        return None
