"""路线规划：行程预览/服务区建议/路线选择（导航脑）。"""

from __future__ import annotations

from typing import Any, Dict, Optional

# 路线选择：高速收费快 vs 国道免费慢（估算）
ROUTE_OPTIONS = [
    {"id": "highway", "name": "收费高速", "speed": 80, "cost_per_100km": 3.0,
     "desc": "快但有过路费"},
    {"id": "scenic", "name": "国道/风景线", "speed": 55, "cost_per_100km": 0.0,
     "desc": "免费但慢，风景好"},
]


class RoutePlanner:
    """接单时生成路线叙事 + 选择建议。"""

    def __init__(self, knowledge) -> None:
        self.knowledge = knowledge

    def preview(self, snap) -> Optional[str]:
        """接货时行程预览：路线/里程/时间 + 出行提示。"""
        if not snap.on_job:
            return None
        dist = snap.planned_distance_km
        eta_h = dist / 65.0  # 平均速度估算
        parts = [f"{snap.city_src}到{snap.city_dst}约 {dist} km，预计 {eta_h:.1f} 小时喵"]
        known = self.knowledge.city_distance(snap.city_src, snap.city_dst)
        if known:
            parts[0] = f"{snap.city_src}到{snap.city_dst}约 {known} km，预计 {known / 65:.1f} 小时喵"
        # 长途分段建议
        if dist > 600:
            parts.append("900 公里级别长途喵，我建议中途歇一觉")
        elif dist > 300:
            parts.append("中长途喵，路上我会提醒你休息的~")
        # 夜间出行提示（游戏内时间）
        if snap.time_abs_min is not None:
            hour = (snap.time_abs_min // 60) % 24
            if hour >= 23 or hour < 5:
                parts.append("这单要在夜里跑喵，我陪你，注意灯光和车速")
            elif hour < 6 or hour >= 20:
                parts.append("傍晚光线差，我帮你盯着限速喵")
        return " ".join(parts)

    def service_area_advice(self, snap) -> Optional[str]:
        """服务区建议：结合油量续航。"""
        if not snap.on_job:
            return None
        fuel_range = snap.fuel_range_km
        if fuel_range <= 0:
            return None
        remaining = snap.route_remaining_km
        if fuel_range < remaining * 0.6:
            return (f"油量续航只剩 {fuel_range:.0f} km，任务还剩 {remaining:.0f} km，"
                    "路上记得找服务区加油喵！")
        if fuel_range < remaining:
            return (f"油量续航 {fuel_range:.0f} km，任务还剩 {remaining:.0f} km，"
                    "刚好够但别错过服务区喵~")
        return None

    def route_choice(self, snap) -> Optional[str]:
        """路线选择：让玩家二选一。"""
        if not snap.on_job:
            return None
        dist = snap.planned_distance_km
        fast_h = dist / 80.0
        slow_h = dist / 55.0
        toll = dist / 100.0 * 3.0
        return (f"这条路我看了下喵：高速 {fast_h:.1f} 小时但过路费约 {toll:.0f} €；"
                f"国道 {slow_h:.1f} 小时免费但慢。你选哪个？")

    def snapshot(self) -> Dict[str, Any]:
        return {"options": ROUTE_OPTIONS}
