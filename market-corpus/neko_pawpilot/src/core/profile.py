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
        # 油耗画像：用于"这趟比平时费不费油"的真实对比（遥测给的是 L/100km）
        fuel_avg = float(stats.get("fuel_avg", 0) or 0)
        if 0 < fuel_avg < 200:            # 明显异常值不计入
            p["fuel_sum"] = float(p.get("fuel_sum", 0)) + fuel_avg
            p["fuel_n"] = int(p.get("fuel_n", 0)) + 1
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
        """新纪录/连续无事故 → **事实行**（措辞交宿主 LLM，不写死台词）。"""
        if result.get("new_record"):
            return (f"[纪录] 单程新纪录：{result.get('record_km', 0):.0f} km，"
                    f"是他跑过最长的一趟")
        streak = result.get("safe_streak", 0)
        if streak >= 10:
            return f"[纪录] 连续 {streak} 单零事故，可以夸一句"
        if streak == 5:
            return f"[纪录] 连续 {streak} 单零事故"
        return None

    def avg_fuel(self) -> float:
        """历史平均油耗（L/100km）；没有数据返回 0。"""
        n = int(self._profile.get("fuel_n", 0) or 0)
        if n <= 0:
            return 0.0
        return float(self._profile.get("fuel_sum", 0) or 0) / n

    def fuel_verdict(self, fuel_avg: float) -> str:
        """这趟油耗与历史均值的对比（真实数据，单位为遥测里的 L/100km）。"""
        base = self.avg_fuel()
        cur = float(fuel_avg or 0)
        if base <= 0 or cur <= 0:
            return ""
        diff = cur - base
        if diff <= -1.0:
            return (f"这趟平均油耗 {cur:.1f} L/100km，比平时（{base:.1f}）省了 "
                    f"{abs(diff):.1f}，开得挺顺")
        if diff >= 1.0:
            return (f"这趟平均油耗 {cur:.1f} L/100km，比平时（{base:.1f}）高了 "
                    f"{diff:.1f}，急加速和急刹会明显吃油")
        return f"这趟平均油耗 {cur:.1f} L/100km，和平时（{base:.1f}）差不多"

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
