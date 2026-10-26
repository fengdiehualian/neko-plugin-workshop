"""渔获账本：卖鱼/赌场收支，月度汇总。"""

from __future__ import annotations

import time
from typing import Any, Dict

from .memory import MemoryStore


class Ledger:
    """记账：卖鱼收入 / 赌场输赢 / 装备支出。"""

    def __init__(self, memory: MemoryStore) -> None:
        self.memory = memory

    def record_sale(self, amount: int) -> None:
        """卖鱼入账。"""
        if amount <= 0:
            return
        cur = self.memory.bump("ledger", "sale", "count")
        d = dict(self.memory.query("ledger", "sale") or {})
        d["total"] = int(d.get("total", 0)) + amount
        d["count"] = cur
        d["last_ts"] = time.time()
        self.memory.remember("ledger", "sale", d)

    def record_gamble(self, won: bool, amount: int) -> None:
        """赌场输赢记录。"""
        kind = "wins" if won else "losses"
        cur = self.memory.bump("gambling", kind, "count")
        d = dict(self.memory.query("gambling", kind) or {})
        d["total"] = int(d.get("total", 0)) + abs(amount)
        d["count"] = cur
        d["last_ts"] = time.time()
        self.memory.remember("gambling", kind, d)

    def month_summary(self) -> Dict[str, Any]:
        """月度汇总（当前实现为累计汇总，M2 起按月分桶）。"""
        sale = self.memory.query("ledger", "sale") or {}
        wins = self.memory.query("gambling", "wins") or {}
        losses = self.memory.query("gambling", "losses") or {}
        income = int(sale.get("total", 0)) + int(wins.get("total", 0))
        expense = int(losses.get("total", 0))
        return {
            "sale_count": int(sale.get("count", 0)),
            "sale_total": int(sale.get("total", 0)),
            "gamble_wins": int(wins.get("count", 0)),
            "gamble_losses": int(losses.get("count", 0)),
            "income": income,
            "expense": expense,
            "net": income - expense,
        }

    def render_summary(self) -> str:
        s = self.month_summary()
        lines = [
            f"渔获账本喵：卖了 {s['sale_count']} 次共 {s['sale_total']} 金币，",
            f"赌场赢 {s['gamble_wins']} 次、输 {s['gamble_losses']} 次，",
            f"净赚 {s['net']} 金币~",
        ]
        return "".join(lines)
