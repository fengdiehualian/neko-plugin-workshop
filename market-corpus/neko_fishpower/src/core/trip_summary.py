"""渔获总结：出钓结束（game_end）时的汇总叙事。"""

from __future__ import annotations

from typing import Any, Dict

from .memory import MemoryStore


class TripSummary:
    """出钓总结：本次渔获 / 新图鉴 / 赌场战绩。"""

    def __init__(self, memory: MemoryStore) -> None:
        self.memory = memory

    def build(self, session: Dict[str, Any]) -> str:
        """构建收竿总结。session: {caught, new_journal, sold_total, gamble_net}"""
        parts = []
        caught = int(session.get("caught", 0))
        if caught:
            parts.append(f"钓了 {caught} 条")
        if session.get("new_journal"):
            parts.append(f"新图鉴 {session['new_journal']} 种")
        if session.get("last_fish"):
            parts.append(f"最后那条是{session['last_fish']}")
        sold = int(session.get("sold_total", 0))
        if sold:
            parts.append(f"卖鱼赚了 {sold} 金币")
        net = int(session.get("gamble_net", 0))
        if net:
            parts.append(f"赌场{'赢' if net > 0 else '输'}了 {abs(net)} 金币")
        if not parts:
            return "今天没怎么钓喵，下次加油！"
        return "收竿总结喵：" + "，".join(parts) + "~"
