"""记忆唤起：场景 → 相关记忆 → 猫娘话术线索。"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from .memory import MemoryStore


class Recall:
    """按场景唤起记忆，产出话术线索。"""

    FUZZY_THRESHOLD = 0.35  # weight 低于此值 → 模糊化引用

    def __init__(self, memory: MemoryStore) -> None:
        self.memory = memory

    def _fuzzy(self, text: str, entry: Optional[Dict[str, Any]]) -> str:
        """低 weight 记忆用模糊措辞（像人一样记不清）。"""
        if entry and entry.get("weight", 0) < self.FUZZY_THRESHOLD:
            return f"好像…{text}"
        return text

    def on_crash(self) -> Optional[str]:
        """事故：本月事故计数入库（首条也记），第 N 次报数。"""
        month = _month_key()
        key = f"crash_{month}"
        prev = self.memory.query("events", key) or {}
        self.memory.remember("events", key,
                             {"count": prev.get("count", 0) + 1},
                             importance=0.8)
        entry = self.memory.bump("events", key)
        count = entry.get("count", 0) if entry else 1
        if count > 1:
            return f"这是你本月第 {count} 次撞了喵…"
        return None

    def on_job_start(self, src: str, dst: str, cargo: str) -> List[str]:
        """接单：唤起城市/货物/同路线记忆（只唤起不计数，到货才正式入库）。"""
        hints = []
        for hint in (self.on_city_seen(dst), self.on_cargo_seen(cargo)):
            if hint:
                hints.append(hint)
        route = self.memory.query("cities", f"route_{src}_{dst}") or {}
        if route and route.get("count", 0) > 1:
            hints.append(f"这条线你跑过 {route.get('count')} 次了喵")
        return hints

    def on_city_seen(self, city: str) -> Optional[str]:
        """接单时唤起该城足迹（不计数，仅 bump 权重）。"""
        entry = self.memory.bump("cities", city)
        if not entry:
            return None
        count = entry.get("count", 0)
        if count <= 1:
            return f"第一次去{city}喵！"
        return self._fuzzy(f"这是你第 {count} 次去{city}了喵！", entry)

    def on_cargo_seen(self, cargo: str) -> Optional[str]:
        """接单时唤起货物记忆（不计数，仅 bump 权重）。"""
        entry = self.memory.bump("cargos", cargo)
        if not entry:
            return None
        count = entry.get("count", 0)
        return f"{cargo}你运过 {count} 次了喵，老手！"

    def on_trip_end(self, trip: Dict[str, Any]) -> None:
        """行程结束：更新城市/货物/路线统计 + 事件。"""
        dst = trip.get("dst", "")
        cargo = trip.get("cargo", "")
        src = trip.get("src", "")
        if dst:
            c = self.memory.query("cities", dst) or {}
            self.memory.remember("cities", dst, {"count": c.get("count", 0) + 1,
                                                 "last": trip.get("ts", 0)})
        if cargo:
            c = self.memory.query("cargos", cargo) or {}
            self.memory.remember("cargos", cargo, {"count": c.get("count", 0) + 1,
                                                   "best_income": max(c.get("best_income", 0),
                                                                      trip.get("revenue", 0))})
        if src and dst:
            route = self.memory.query("cities", f"route_{src}_{dst}") or {}
            self.memory.remember("cities", f"route_{src}_{dst}",
                                 {"count": route.get("count", 0) + 1})
        if trip.get("crashes", 0) > 0:
            month = _month_key()
            ev = self.memory.query("events", f"crash_{month}") or {}
            self.memory.remember("events", f"crash_{month}",
                                 {"count": ev.get("count", 0) + trip.get("crashes", 0)},
                                 importance=0.8)

    def on_relationship(self, key: str, data: Dict[str, Any],
                        importance: float = 0.6) -> None:
        """写入关系记忆（玩家偏好/昵称/赌约）。"""
        self.memory.remember("relationship", key, data, importance=importance)

    def context_hint(self, top: int = 5) -> str:
        """top-N 记忆注入 LLM 上下文（跨类别取 weight 最高）。"""
        entries = []
        for kind in ("cities", "cargos", "events", "relationship"):
            store = self.memory.query(kind) or {}
            for key, e in store.items():
                if e.get("archived"):
                    continue
                entries.append((e.get("weight", 0), kind, key, e))
        entries.sort(key=lambda x: x[0], reverse=True)
        if not entries:
            return ""
        lines = []
        for _w, kind, key, e in entries[:top]:
            if kind == "cities" and e.get("count"):
                lines.append(f"去过{e.get('count')}次的{key}")
            elif kind == "cargos" and e.get("count"):
                income = f"，最高收入{e.get('best_income')}" if e.get("best_income") else ""
                lines.append(f"运过{e.get('count')}次{key}{income}")
            elif kind == "relationship":
                lines.append(f"{key}={e.get('value')}")
        return "；".join(lines) if lines else ""


def _month_key() -> str:
    return datetime.date.today().strftime("%Y-%m")
