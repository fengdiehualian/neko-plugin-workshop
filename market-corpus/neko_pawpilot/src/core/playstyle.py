"""玩法画像：按**真实遥测**判断"这趟是什么玩法"，据此调整陪玩的密度与话题。

为什么需要：陪玩的核心是"看懂你这趟在干什么"。拉 40 吨超限货跑阿尔卑斯下坡，
和空车在城里兜风，需要的陪伴方式完全不同——前者要技术提醒、少废话；
后者要闲聊、要风景、要拍照。

判定只用遥测里真有的字段：
  on_job / cargo(+cargo_id) / cargo_mass / speed / 限速 / 地图比例尺(is_city/is_open_road)
  / 游戏内时间 / route_remaining_km / route_remaining_time_min / delivery_remaining_min
  / 车损 / 雨刷
不引入任何遥测里没有的概念（⚠️ 不编造"国家/海关/称重"之类游戏里没有或读不到的东西）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .cargo import CargoBook


@dataclass(frozen=True)
class Playstyle:
    key: str
    label: str
    chat_bias: str        # 陪玩维度倾向
    gap_mult: float       # 闲聊密度倍率（<1 更安静，>1 更热闹）
    advice: str = ""      # 一句真实玩法建议（可为空）


# 各玩法的陪玩策略（gap_mult 越小 = 越安静，把车道让给安全/技术提醒）
STYLES = {
    "free_roam":     Playstyle("free_roam", "自由兜风", "scenery", 1.2),
    "scenic":        Playstyle("scenic", "观光路", "scenery", 1.3),
    "city_delivery": Playstyle("city_delivery", "市区配送", "city", 0.7),
    "heavy_haul":    Playstyle("heavy_haul", "重载/超限", "truck", 0.8),
    "multi_trailer": Playstyle("multi_trailer", "多节挂车", "truck", 0.8,
                               "编组很长：转弯内轮差大、变道要留足空间、倒车尽量别试"),
    "hurry":         Playstyle("hurry", "赶时间", "progress", 0.8),
    "night_shift":   Playstyle("night_shift", "夜班", "night", 1.0),
    "long_haul":     Playstyle("long_haul", "长途巡航", "cruise", 1.1),
    "delivery":      Playstyle("delivery", "到货收尾", "progress", 1.0),
    "normal":        Playstyle("normal", "常规运输", "road", 1.0),
}


class PlaystyleBook:
    """把实时快照映射成一个玩法画像。"""

    def __init__(self, cargo_book: Optional[CargoBook] = None) -> None:
        self.cargo = cargo_book or CargoBook()

    def classify(self, snap: Any, driving_min: float = 0.0) -> Playstyle:
        if snap is None:
            return STYLES["normal"]
        speed = float(getattr(snap, "speed_kmh", 0) or 0)
        hour = 0
        if getattr(snap, "time_abs_min", 0):
            hour = int(snap.time_abs_min // 60) % 24
        night = hour >= 22 or hour < 5
        mass_t = float(getattr(snap, "cargo_mass", 0) or 0) / 1000.0

        # ① 没接单：自由驾驶（这是玩家很常见的一种玩法，不该被当成"没事干"）
        if not getattr(snap, "on_job", False):
            if getattr(snap, "is_open_road", False) and speed > 40 and 7 <= hour <= 19:
                return STYLES["scenic"]
            return STYLES["free_roam"]

        # ② 停车/到货收尾（ETS2 到货前后都会有一段时间速度接近 0）
        if speed < 5:
            return STYLES["delivery"]

        # ③ 多节挂车（北欧双挂/三挂）：编组本身就改变驾驶方式，优先级最高
        if int(getattr(snap, "trailer_count", 0) or 0) >= 2:
            return STYLES["multi_trailer"]
        # ④ 载重/货物类型：重载优先于其它（它改变整趟的驾驶方式）
        fam = self.cargo.classify(getattr(snap, "cargo_id", ""),
                                  getattr(snap, "cargo", ""))
        heavy = mass_t >= 25.0 or (fam and fam["key"] == "heavy")
        if heavy:
            base = STYLES["heavy_haul"]
            return base
        # ④ 时间压力：剩余交付时间 vs 导航预估时间（都是遥测真值）
        rem_min = getattr(snap, "delivery_remaining_min", None)
        route_min = float(getattr(snap, "route_remaining_time_min", 0) or 0)
        if rem_min is not None and route_min > 0 and rem_min < route_min * 1.3:
            return STYLES["hurry"]
        # ⑤ 市区配送：红绿灯多、行人多，最该少说话
        if getattr(snap, "is_city", False):
            return STYLES["city_delivery"]
        # ⑥ 夜班
        if night:
            return STYLES["night_shift"]
        # ⑦ 长途巡航：高速 + 余程还长
        remain = float(getattr(snap, "route_remaining_km", 0) or 0)
        if getattr(snap, "is_open_road", False) and speed > 55 and remain > 150:
            return STYLES["long_haul"]
        return STYLES["normal"]

    def describe(self, snap: Any, driving_min: float = 0.0) -> dict:
        """给面板/播报用的画像数据。"""
        st = self.classify(snap, driving_min)
        fam = self.cargo.classify(getattr(snap, "cargo_id", ""),
                                  getattr(snap, "cargo", "")) if snap is not None else None
        return {"key": st.key, "label": st.label, "bias": st.chat_bias,
                "gap_mult": st.gap_mult, "advice": st.advice,
                "cargo_family": (fam or {}).get("label", "")}
