"""行程总结：到货时汇总全趟生成猫娘总结。"""

from __future__ import annotations

from typing import Any, Dict


class TripSummary:
    """把行程统计拼成猫娘总结文本。"""

    def __init__(self, templates: dict) -> None:
        self._t = templates

    def build_fact(self, trip: Dict[str, Any]) -> str:
        """到货结算的**数据行**（respond 通道用）。

        旧版到货时推的是 build() 那串自带"喵！🚚💨"的成句，宿主 LLM 又把成句
        复述一遍 → 一句话两种人格、还夹着一个没闭合的「。这里只给客观数据，
        措辞交宿主按人设说。
        """
        bits = []
        dst = trip.get("dst") or "目的地"
        bits.append(f"目的地 {dst}")
        if trip.get("distance_km"):
            bits.append(f"全程 {float(trip['distance_km']):.0f} km")
        if trip.get("revenue"):
            bits.append(f"收入 {trip['revenue']} €")
        if trip.get("cargo"):
            bits.append(f"货物 {trip['cargo']}")
        if trip.get("duration_min"):
            h, m = divmod(int(trip["duration_min"]), 60)
            bits.append(f"用时 {h} 小时 {m} 分")
        notes = []
        for key, label in (("speedings", "超速"), ("hard_brakes", "急刹"),
                           ("crashes", "碰撞"), ("refuels", "加油")):
            if trip.get(key):
                notes.append(f"{label} {trip[key]} 次")
        if notes:
            bits.append("途中 " + "、".join(notes))
        if trip.get("fuel_avg"):
            bits.append(f"平均油耗 {float(trip['fuel_avg']):.1f} L/100km")
        return "[到货结算] " + "；".join(bits)

    def build(self, trip: Dict[str, Any]) -> str:
        """生成总结文本。trip 含 src/dst/cargo/distance/revenue/
        speedings/hard_brakes/crashes/fuel_avg/refuels/duration_min。"""
        parts = []
        head = self._t.get("summary_head", "「{dst}」这趟：{distance:.0f} km")
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
