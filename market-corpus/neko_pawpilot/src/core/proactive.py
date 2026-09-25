"""主动提议：按驾驶状态主动给建议（L3 低频通道）。"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional


class Proactive:
    """低油量/疲劳/长驾驶/夜间/红绿灯路况的主动提议。"""

    def __init__(self, config: object, map_kb: Optional[Any] = None) -> None:
        self.cfg = config
        self.map_kb = map_kb
        self._last_propose: Dict[str, float] = {}
        self._drive_start_ts: Optional[float] = None
        self._last_drive_snap: Optional[Any] = None
        self._last_traffic_light_id: Optional[str] = None
        self._last_station_id: Optional[str] = None

    def update(self, snap) -> None:
        """跟踪连续驾驶时长（仅非任务/暂停/长停时重置）。"""
        if snap.on_job and snap.speed_kmh > 5 and not snap.paused:
            if self._drive_start_ts is None:
                self._drive_start_ts = time.time()
        else:
            # 等红灯/堵车单帧减速不清零；仅脱离任务或暂停时重置
            if (not snap.on_job or snap.paused) and self._drive_start_ts is not None:
                self._drive_start_ts = None
        self._last_drive_snap = snap

    def propose(self, now: float | None = None) -> Optional[str]:
        """返回一条建议；无建议返回 None。"""
        now = now or time.time()
        snap = self._last_drive_snap
        if snap is None or not snap.on_job:
            return None
        # 红绿灯路况（接近路口，每 5 分钟同一灯一次）
        traffic = self.traffic_propose(snap, now)
        if traffic:
            return traffic
        # 加油站/服务区接近（按油量/剩余里程需要）
        station = self.station_propose(snap, now)
        if station:
            return station
        # 低油量（每 10 分钟一次，阈值读配置）
        fuel_pct = float(getattr(self.cfg, "low_fuel_percent", 15))
        if snap.fuel_percent < fuel_pct and now - self._last_propose.get("fuel", 0) > 600:
            self._last_propose["fuel"] = now
            return "油量只剩 {:.0f}% 了喵，前面有服务区记得加油~".format(snap.fuel_percent)
        # 疲劳（接近强制休息，每 10 分钟一次）
        rest = snap.rest_stop_min
        if rest is not None and 0 < rest < 120 and now - self._last_propose.get("rest", 0) > 600:
            self._last_propose["rest"] = now
            return "离强制休息还有 {:.0f} 分钟喵，找个休息区睡一觉吧".format(rest)
        # 长驾驶（>90 分钟，每 20 分钟一次）
        if self._drive_start_ts and now - self._drive_start_ts > 5400 \
                and now - self._last_propose.get("long", 0) > 1200:
            self._last_propose["long"] = now
            return "连续开了一个半小时了喵，歇会儿吧，疲劳驾驶危险呀~"
        # 夜间（game.time 深夜，每 20 分钟一次）
        if snap.time_abs_min is not None and self._is_night(snap.time_abs_min) \
                and now - self._last_propose.get("night", 0) > 1200:
            self._last_propose["night"] = now
            return "深夜开车我陪你喵，别睡啦！"
        # 时间紧张但车速快（每 10 分钟一次）
        rem = snap.delivery_remaining_min
        if rem is not None and rem < 90 and snap.speed_kmh > 70 \
                and now - self._last_propose.get("rush", 0) > 600:
            self._last_propose["rush"] = now
            return "赶时间也别超速喵，稳一点更划算~"
        return None

    def traffic_propose(self, snap, now: float | None = None) -> Optional[str]:
        """接近红绿灯/路口：提醒减速观察（每灯 5 分钟冷却）。"""
        if self.map_kb is None or not snap.on_job:
            return None
        now = now or time.time()
        tl = self.map_kb.nearest_facility(snap.world_x, snap.world_z,
                                          kind="traffic_light", max_km=1.2)
        if not tl:
            # 离开搜索半径：重置，下次回来可重新提醒
            self._last_traffic_light_id = None
            return None
        lid = tl.get("id")
        if lid == self._last_traffic_light_id:
            return None
        # 每灯独立冷却
        if now - self._last_propose.get(f"traffic_{lid}", 0) < 300:
            return None
        self._last_propose[f"traffic_{lid}"] = now
        self._last_traffic_light_id = lid
        dist = tl.get("distance_km", 0)
        if dist < 0.3:
            return "前方路口到啦，注意红绿灯减速喵！"
        return f"前方 {dist:.1f} km 有路口信号灯，提前收油喵~"

    def station_propose(self, snap, now: float | None = None) -> Optional[str]:
        """接近加油站/服务区：按需提醒（油量低→加油，长途→休息）。

        每站 8 分钟冷却；离开搜索半径后重置可重新提醒。
        """
        if self.map_kb is None or not snap.on_job:
            return None
        now = now or time.time()
        # 油量低 → 找加油站；否则长途 → 找服务区
        low_fuel = snap.fuel_percent < 45
        kind = "fuel" if low_fuel else "service"
        max_km = 1.5 if low_fuel else 2.0
        fac = self.map_kb.nearest_facility(snap.world_x, snap.world_z,
                                           kind=kind, max_km=max_km)
        if not fac:
            self._last_station_id = None
            return None
        sid = fac.get("id")
        if sid == self._last_station_id:
            return None
        if now - self._last_propose.get(f"station_{sid}", 0) < 480:
            return None
        self._last_propose[f"station_{sid}"] = now
        self._last_station_id = sid
        dist = fac.get("distance_km", 0)
        if low_fuel:
            pct = snap.fuel_percent
            if dist < 0.3:
                return f"油量只剩 {pct:.0f}% 了喵，加油站就在眼前，进去补点油吧！"
            return f"油量剩 {pct:.0f}%，前方 {dist:.1f} km 有加油站，顺路加个油喵~"
        remaining = snap.route_remaining_km
        if dist < 0.3:
            return "前面就是服务区喵，长途开累了正好歇一歇！"
        return f"前方 {dist:.1f} km 有服务区，这单还剩 {remaining:.0f} km，中途休息一下喵~"

    def weather_propose(self, ocr_text: str, now: float | None = None) -> Optional[str]:
        """OCR 检测到雨/雪 → 天气提醒（每 10 分钟一次）。"""
        now = now or time.time()
        if not ocr_text:
            return None
        text = ocr_text.lower()
        if ("rain" in text or "雨" in text) and now - self._last_propose.get("rain", 0) > 600:
            self._last_propose["rain"] = now
            return "下雨路滑喵，慢点开，刹车留足距离~"
        if ("snow" in text or "雪" in text) and now - self._last_propose.get("snow", 0) > 600:
            self._last_propose["snow"] = now
            return "下雪了喵！路面结冰，千万别急刹！"
        return None

    @staticmethod
    def _is_night(time_abs_min: int) -> bool:
        hour = (time_abs_min // 60) % 24
        return hour >= 23 or hour < 5
