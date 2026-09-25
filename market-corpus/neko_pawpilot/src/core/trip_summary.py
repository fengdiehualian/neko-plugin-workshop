"""行程总结：到货时汇总全趟生成猫娘总结。"""

from __future__ import annotations

from typing import Any, Dict


class TripSummary:
    """把行程统计拼成猫娘总结文本。"""

    def __init__(self, templates: dict) -> None:
        self._t = templates

    def build(self, trip: Dict[str, Any]) -> str:
        """生成总结文本。trip 含 src/dst/cargo/distance/revenue/
        speedings/hard_brakes/crashes/fuel_avg/refuels/duration_min。"""
        parts = []
        head = self._t.get("summary_head", "「{dst}这趟：{distance:.0f} km")
        try:
            parts.append(head.format(dst=trip.get("dst", ""), distance=trip.get("distance_km", 0)))
        except (KeyError, ValueError):
            parts.append(f"「{trip.get('dst', '')}这趟")
        if trip.get("revenue"):
            parts.append(self._fmt("summary_revenue", "赚了 {revenue} €",
                                   revenue=trip.get("revenue", 0)))
        if trip.get("cargo"):
            parts.append(self._fmt("summary_cargo", "拉的是 {cargo}", cargo=trip.get("cargo", "")))
        if trip.get("duration_min"):
            h, m = divmod(int(trip.get("duration_min", 0)), 60)
            parts.append(self._fmt("summary_duration", "用时 {h} 小时 {m} 分", h=h, m=m))
        notes = []
        if trip.get("speedings"):
            notes.append(f"超速 {trip['speedings']} 次")
        if trip.get("hard_brakes"):
            notes.append(f"急刹 {trip['hard_brakes']} 次")
        if trip.get("crashes"):
            notes.append(f"车祸 {trip['crashes']} 次")
        if trip.get("refuels"):
            notes.append(f"加油 {trip['refuels']} 次")
        if notes:
            parts.append(self._fmt("summary_notes", "途中：{notes}", notes="，".join(notes)))
        if trip.get("fuel_avg"):
            parts.append(self._fmt("summary_fuel", "油耗 {fuel_avg:.1f}L/100km",
                                   fuel_avg=trip.get("fuel_avg", 0)))
        tail = self._t.get("summary_tail", "")
        if tail:
            parts.append(tail)
        return " ".join(parts) + " 🚚💨"

    @staticmethod
    def _fmt(key: str, default: str, **kw: Any) -> str:
        try:
            return default.format(**kw)
        except (KeyError, ValueError):
            return default
