"""旅程账本：每单收支计算 + 月度汇总。"""

from __future__ import annotations

import datetime
from typing import Any, Dict


class Ledger:
    """记录每单收入-油费-过路费-罚款-修车=净赚。"""

    def __init__(self, memory) -> None:
        self.memory = memory

    def record(self, trip: Dict[str, Any]) -> Dict[str, float]:
        """记一笔账，返回收支明细。"""
        revenue = float(trip.get("revenue", 0))
        tolls = float(trip.get("tolls", 0))
        fines = float(trip.get("fines", 0))
        fuel_cost = float(trip.get("fuel_cost", 0))
        repair = float(trip.get("repair", 0))
        net = revenue - tolls - fines - fuel_cost - repair
        month = _month_key()
        entry = {
            "ts": trip.get("ts", 0),
            "src": trip.get("src", ""),
            "dst": trip.get("dst", ""),
            "revenue": revenue,
            "tolls": tolls,
            "fines": fines,
            "fuel_cost": fuel_cost,
            "repair": repair,
            "net": net,
        }
        self.memory.remember("ledger", f"trip_{trip.get('ts', 0)}", entry,
                             importance=0.6)
        # 月度汇总
        month_entry = self.memory.query("ledger", f"month_{month}") or {
            "revenue": 0.0, "tolls": 0.0, "fines": 0.0,
            "fuel_cost": 0.0, "repair": 0.0, "net": 0.0, "trips": 0,
        }
        for k in ("revenue", "tolls", "fines", "fuel_cost", "repair", "net"):
            month_entry[k] = float(month_entry.get(k, 0)) + locals()[k]
        month_entry["trips"] = int(month_entry.get("trips", 0)) + 1
        self.memory.remember("ledger", f"month_{month}", month_entry, importance=0.7)
        return entry

    def month_summary(self) -> Dict[str, Any]:
        """本月汇总（供面板/问答）。"""
        month = _month_key()
        return self.memory.query("ledger", f"month_{month}") or {}

    def render_summary(self) -> str:
        """猫娘话术：本月账本。"""
        m = self.month_summary()
        if not m:
            return "这个月还没跑单喵，先接一单吧~"
        return (f"这个月跑了 {m.get('trips', 0)} 单喵：赚 {m.get('revenue', 0):.0f} €，"
                f"油费 {m.get('fuel_cost', 0):.0f}，过路费 {m.get('tolls', 0):.0f}，"
                f"罚款 {m.get('fines', 0):.0f}，修车 {m.get('repair', 0):.0f}，"
                f"净赚 {m.get('net', 0):.0f} €~")


def _month_key() -> str:
    return datetime.date.today().strftime("%Y-%m")
