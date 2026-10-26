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
        self._last_station_id: Optional[str] = None

    def update(self, snap) -> None:
        """跟踪连续驾驶时长（只要在开车就算，等红灯不清零、不要求接单）。"""
        if snap.speed_kmh > 5 and not snap.paused:
            if self._drive_start_ts is None:
                self._drive_start_ts = time.time()
        else:
            # 等红灯/堵车单帧减速不清零；真停下或暂停才重置
            if snap.paused and self._drive_start_ts is not None:
                self._drive_start_ts = None
        self._last_drive_snap = snap

    def propose(self, now: float | None = None) -> Optional[str]:
        """返回一条建议；无建议返回 None。

        2026-10 二次修正：旧版开头 `if not snap.on_job: return None`，
        自由驾驶时整段主动提议（加油/休息/夜间/长驾驶）全部失效。
        现在只要在开车就参与，只有"赶时间"这种任务专属项才要求接单。
        """
        now = now or time.time()
        snap = self._last_drive_snap
        if snap is None or snap.paused or snap.speed_kmh < 3:
            return None
        # ① 先做"能不能跑到目的地"的续航判断（比单纯看油量百分比有意义）
        advice = self.fuel_advice(snap, now)
        if advice:
            return advice
        # ② 加油站/服务区接近（按油量/剩余里程需要）
        station = self.station_propose(snap, now)
        if station:
            return station
        # ③ 没有任务或续航未知时，退回"油量百分比偏低"的兜底提醒
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

    def fuel_advice(self, snap, now: float | None = None) -> Optional[str]:
        """按「当前续航 vs 到目的地还剩多少」给出要不要加油的建议。

        这才是玩家真正关心的判断：油量 40% 但目的地就在 30 km 外不用停；
        油量 55% 而还剩 900 km 就必须马上补。三档：
          ratio < 1.0          跑不到 → 必须加，报差多少 + 最近加油站在哪
          1.0 ≤ ratio < 1.25   刚好够 → 建议顺路补一点
          ratio ≥ 1.25         够跑   → 不打扰（除非油量真的见底）
        返回的是**事实行**（措辞交给宿主 LLM），不是硬编码台词。
        """
        remaining = float(getattr(snap, "route_remaining_km", 0) or 0)
        rng = float(getattr(snap, "fuel_range_km", 0) or 0)
        if remaining <= 0 or rng <= 0:
            return None
        now = now or time.time()
        ratio = rng / remaining
        # 已经在附近找到加油站也要能报出来（跑不到时把搜索半径放宽）
        short_km = max(0.0, remaining - rng)
        search_km = 30.0 if ratio < 1.0 else 8.0
        fac = None
        if self.map_kb is not None:
            try:
                fac = self.map_kb.nearest_facility(snap.world_x, snap.world_z,
                                                   "fuel", max_km=search_km)
            except Exception:               # noqa: BLE001
                fac = None
        where = ""
        if fac:
            where = f"；最近的加油站在 {fac.get('distance_km', 0):.1f} km 外"

        if ratio < 1.0:
            key, cd = "fuel_must", 300.0
        elif ratio < 1.25:
            key, cd = "fuel_tight", 900.0
        elif snap.fuel_percent < float(getattr(self.cfg, "low_fuel_percent", 15)):
            key, cd = "fuel_low", 900.0
        else:
            return None
        if now - self._last_propose.get(key, 0) < cd:
            return None
        self._last_propose[key] = now

        base = (f"剩余续航 {rng:.0f} km、到目的地还有 {remaining:.0f} km"
                f"（油量 {snap.fuel_percent:.0f}%）")
        if key == "fuel_must":
            return (f"[加油判断] {base}，差 {short_km:.0f} km 跑不到{where}。"
                    "必须顺路加油，别硬撑。")
        if key == "fuel_tight":
            return f"[加油判断] {base}，刚好够到{where}；顺路补一点更稳。"
        return (f"[加油判断] {base}，够跑但油量已经很低{where}，"
                "建议找机会补满。")

    def station_propose(self, snap, now: float | None = None) -> Optional[str]:
        """接近加油站/服务区：按需提醒（油量低→加油，长途→休息）。

        半径与冷却按 2026-10 实测标定：全图 1157 加油站 / 97 服务区，
        实测当前位置 3km 内 9 个加油站、服务区最近 0.11km——原半径
        1.5/2.0km 太窄，多数时候擦肩而过不触发。改为 3km / 4km，
        冷却 8 分钟 → 4 分钟。离开搜索半径后重置可重新提醒。

        2026-10 二次修正：旧版要求 `snap.on_job`，主人自由驾驶时加油站/服务区
        提醒整段失效（实测反馈"加油站服务区距离也不播报"）→ 只要在开车就提醒。
        """
        if self.map_kb is None or snap.speed_kmh < 3:
            return None
        now = now or time.time()
        # 油量低 → 找加油站；否则长途 → 找服务区
        low_fuel = snap.fuel_percent < 45
        kind = "fuel" if low_fuel else "service"
        max_km = 3.0 if low_fuel else 4.0
        fac = self.map_kb.nearest_facility(snap.world_x, snap.world_z,
                                           kind=kind, max_km=max_km)
        if not fac:
            self._last_station_id = None
            return None
        sid = fac.get("id")
        if sid == self._last_station_id:
            return None
        if now - self._last_propose.get(f"station_{sid}", 0) < 240:
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
        """OCR 检测到雨/雪 → 天气事实行（每 10 分钟一次，措辞交宿主）。"""
        now = now or time.time()
        if not ocr_text:
            return None
        text = ocr_text.lower()
        if ("rain" in text or "雨" in text) and now - self._last_propose.get("rain", 0) > 600:
            self._last_propose["rain"] = now
            return "[天气] 画面/屏幕上确认在下雨，路面是湿的；提醒路滑、刹车距离变长"
        if ("snow" in text or "雪" in text) and now - self._last_propose.get("snow", 0) > 600:
            self._last_propose["snow"] = now
            return "[天气] 画面/屏幕上确认在下雪、路面可能结冰；提醒千万别急刹"
        return None

    @staticmethod
    def _is_night(time_abs_min: int) -> bool:
        hour = (time_abs_min // 60) % 24
        return hour >= 23 or hour < 5
