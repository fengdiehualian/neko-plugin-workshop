"""旅途日记：把"跑过什么"写成能回味的记录（陪玩记忆的一部分）。

为什么要单独一层：`profile` 记的是**统计**（跑了多少公里、超速几次），
`diary` 记的是**故事**——统计能告诉你数字，日记能让她第二天说
"昨天你从米兰拉废金属去都灵，路上还下着雨"。

两者都落在宿主的同一份记忆里，但读取方式和用途完全不同，所以分开放：
- profile → 面板画像 / 里程碑成就
- diary   → 收车小结 / 下次见面时的问候 / 陪聊时的"回忆"素材

数据来源：到货归档时的 trip 字典（src/dst/cargo/km/revenue/crashes/…），
不额外采集任何东西。
"""

from __future__ import annotations

import datetime
import time
from typing import Any, Dict, List, Optional

KIND = "diary"


class TripDiary:
    """按天累积的行程日记（持久化在宿主记忆里）。"""

    def __init__(self, memory: Any) -> None:
        self.memory = memory

    # ── 写入 ──
    def write(self, trip: Dict[str, Any], note: str = "") -> Dict[str, Any]:
        """到货后写一条日记。note 可放当天的天气/心情等补充。"""
        ts = int(trip.get("ts") or time.time())
        day = datetime.date.today().isoformat()
        entry = {
            "ts": ts,
            "day": day,
            "src": str(trip.get("src", "") or ""),
            "dst": str(trip.get("dst", "") or ""),
            "cargo": str(trip.get("cargo", "") or ""),
            "km": round(float(trip.get("distance_km", 0) or 0), 1),
            "revenue": float(trip.get("revenue", 0) or 0),
            "crashes": int(trip.get("crashes", 0) or 0),
            "speedings": int(trip.get("speedings", 0) or 0),
            "hard_brakes": int(trip.get("hard_brakes", 0) or 0),
            "duration_min": int(trip.get("duration_min", 0) or 0),
            "note": str(note or "")[:120],
        }
        self.memory.remember(KIND, f"{day}_{ts}", entry, importance=0.55)
        return entry

    # ── 读取 ──
    def entries(self, day: Optional[str] = None) -> List[Dict[str, Any]]:
        store = self.memory.query(KIND) or {}
        out = [e for e in store.values()
               if isinstance(e, dict) and (day is None or e.get("day") == day)]
        out.sort(key=lambda e: float(e.get("ts", 0) or 0))
        return out

    def today(self) -> List[Dict[str, Any]]:
        return self.entries(datetime.date.today().isoformat())

    def last(self) -> Optional[Dict[str, Any]]:
        """最近一条日记（跨天也算）。"""
        items = self.entries()
        return items[-1] if items else None

    def today_summary(self) -> Dict[str, Any]:
        """今天的小结数据（不足一单也返回 km=0，方便面板/播报判断）。"""
        items = self.today()
        cities = []
        for e in items:
            for name in (e.get("src"), e.get("dst")):
                if name and name not in cities:
                    cities.append(str(name))
        return {
            "trips": len(items),
            "km": round(sum(float(e.get("km", 0) or 0) for e in items), 1),
            "revenue": sum(float(e.get("revenue", 0) or 0) for e in items),
            "crashes": sum(int(e.get("crashes", 0) or 0) for e in items),
            "speedings": sum(int(e.get("speedings", 0) or 0) for e in items),
            "hard_brakes": sum(int(e.get("hard_brakes", 0) or 0) for e in items),
            "cities": cities,
        }

    def render_today(self) -> str:
        """今日小结的**数据行**（措辞交宿主 LLM，不写死台词）。"""
        s = self.today_summary()
        if not s["trips"]:
            return ""
        bits = [f"今天跑了 {s['km']:.0f} km、完成 {s['trips']} 单",
                f"收入 {s['revenue']:.0f} €"]
        if s["cities"]:
            bits.append("去过 " + "、".join(s["cities"][:4]))
        notes = []
        if s["speedings"]:
            notes.append(f"超速 {s['speedings']} 次")
        if s["hard_brakes"]:
            notes.append(f"急刹 {s['hard_brakes']} 次")
        if s["crashes"]:
            notes.append(f"碰撞 {s['crashes']} 次")
        if notes:
            bits.append("途中 " + "、".join(notes))
        return "[今日收车] " + "；".join(bits)

    def render_welcome_back(self) -> str:
        """再次上线的问候素材：上次跑到哪、上次那趟是什么。"""
        last = self.last()
        if not last:
            return ""
        try:
            when = datetime.datetime.fromtimestamp(float(last.get("ts", 0)))
            stamp = f"{when.month}月{when.day}日 {when.hour:02d}:{when.minute:02d}"
        except (ValueError, OSError):
            stamp = "上次"
        bits = [f"上次收车时间 {stamp}"]
        if last.get("dst"):
            bits.append(f"上一趟跑到 {last['dst']}")
        if last.get("cargo"):
            bits.append(f"拉的是 {last['cargo']}")
        if last.get("km"):
            bits.append(f"那一趟 {float(last['km']):.0f} km")
        if last.get("crashes"):
            bits.append("途中出过事故")
        return "[回归] " + "；".join(bits)

    def snapshot(self, top: int = 5) -> Dict[str, Any]:
        items = self.entries()
        return {"today": self.today_summary(),
                "count": len(items),
                "recent": items[-top:][::-1]}
