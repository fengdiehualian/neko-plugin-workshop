"""驾驶风格画像 + 里程碑成就。"""

from __future__ import annotations

from typing import Any, Dict, Optional

PROFILE_KEY = "driver_profile"


class DriverProfile:
    """长期统计驾驶风格；里程碑累计。"""

    def __init__(self, memory) -> None:
        self.memory = memory
        self._profile: Dict[str, Any] = {}

    def load(self) -> None:
        self._profile = self.memory.query(PROFILE_KEY, "profile") or {}

    def record(self, stats: Dict[str, Any]) -> None:
        """行程结束更新画像。"""
        p = self._profile
        p["total_km"] = float(p.get("total_km", 0)) + float(stats.get("distance_km", 0))
        p["trips"] = int(p.get("trips", 0)) + 1
        p["speedings"] = int(p.get("speedings", 0)) + int(stats.get("speedings", 0))
        p["hard_brakes"] = int(p.get("hard_brakes", 0)) + int(stats.get("hard_brakes", 0))
        p["crashes"] = int(p.get("crashes", 0)) + int(stats.get("crashes", 0))
        p["revenue"] = float(p.get("revenue", 0)) + float(stats.get("revenue", 0))
        p["night_drives"] = int(p.get("night_drives", 0)) + int(stats.get("night", 0))
        # 更新单程记录
        km = float(stats.get("distance_km", 0))
        new_record = km > float(p.get("longest_trip", 0))
        if new_record:
            p["longest_trip"] = km
        # 连续无事故计数
        if int(stats.get("crashes", 0)) == 0:
            p["safe_streak"] = int(p.get("safe_streak", 0)) + 1
        else:
            p["safe_streak"] = 0
        self.memory.remember(PROFILE_KEY, "profile", p, importance=0.9)
        return {
            "new_record": new_record and km > 100,
            "record_km": km,
            "safe_streak": int(p.get("safe_streak", 0)),
        }

    def record_celebration(self, result: dict) -> Optional[str]:
        """新纪录/连续无事故庆祝话术。"""
        if result.get("new_record"):
            return f"新纪录！单程 {result.get('record_km', 0):.0f} km 喵！你是公路之王！👑"
        streak = result.get("safe_streak", 0)
        if streak >= 10:
            return f"连续 {streak} 单无事故！安全驾驶标兵！🛡️"
        if streak == 5:
            return f"连续 {streak} 单无事故了喵，继续保持！"
        return None

    def label(self) -> str:
        """画像标签。"""
        p = self._profile
        if not p:
            return "新手司机"
        speed_ratio = (int(p.get("speedings", 0)) + 1) / max(int(p.get("trips", 0)), 1)
        if int(p.get("trips", 0)) >= 10 and float(p.get("total_km", 0)) > 3000:
            base = "老司机"
        elif int(p.get("trips", 0)) >= 3:
            base = "成长中的司机"
        else:
            base = "新手司机"
        if speed_ratio > 1.5:
            base += "·爱超速"
        if int(p.get("night_drives", 0)) >= 3:
            base += "·夜猫子"
        if int(p.get("crashes", 0)) == 0 and int(p.get("trips", 0)) >= 5:
            base += "·安全标兵"
        return base

    def milestones(self) -> list:
        """里程碑成就列表。"""
        p = self._profile
        out = []
        km = float(p.get("total_km", 0))
        trips = int(p.get("trips", 0))
        for threshold, name in ((100, "首 100km"), (1000, "首 1000km"), (5000, "5000km 大关")):
            if km >= threshold:
                out.append(name)
        if trips >= 10:
            out.append("10 单老司机")
        if int(p.get("crashes", 0)) == 0 and trips >= 5:
            out.append("连续安全")
        if p.get("longest_trip"):
            out.append(f"单程最长 {float(p['longest_trip']):.0f}km")
        return out

    def snapshot(self) -> Dict[str, Any]:
        return {"label": self.label(), "stats": dict(self._profile),
                "milestones": self.milestones()}
