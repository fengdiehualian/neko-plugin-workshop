"""事件检测：轮询遥测快照，边缘检测输出驾驶事件。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from ..adapters.telemetry_client import TelemetryReader, TruckSnapshot

EV_SPEEDING = "speeding"
EV_HARD_BRAKE = "hard_brake"
EV_CRASH = "crash"
EV_JOB_START = "job_start"
EV_JOB_DELIVERED = "job_delivered"
EV_JOB_CANCELLED = "job_cancelled"
EV_FINE = "fine"
EV_TOLLGATE = "tollgate"
EV_REFUEL = "refuel"
EV_TRAILER_ATTACH = "trailer_attach"
EV_TRAILER_DETACH = "trailer_detach"
EV_TIME_WARNING = "time_warning"
EV_TIME_OVER = "time_over"
EV_LOW_FUEL = "low_fuel"
EV_GAME_START = "game_start"
EV_GAME_END = "game_end"
EV_TRIP_PROGRESS = "trip_progress"
EV_DISTANCE_MARK = "distance_mark"     # 距离分级预告


def _gen_distance_anchors(total_km: float) -> tuple:
    """按任务总里程自动生成距离锚点（不写死）。

    规则：总里程的比例档（50%/25%/10%/5%/2.5%/1%）取整去重，
    不足 1km 的档丢弃；短途单至少保留 1km 一档。
    """
    anchors = set()
    for ratio in (0.5, 0.25, 0.1, 0.05, 0.025, 0.01):
        a = round(total_km * ratio)
        if a >= 1:
            anchors.add(a)
    # 短途单（比例档可能只剩 1km）：补 0.5km 档
    if total_km < 60 and not anchors:
        anchors.add(1)
    return tuple(sorted(anchors, reverse=True))
EV_TIME_RELAXED = "time_relaxed"    # 时间充裕
EV_TIME_TIGHT = "time_tight"        # 时间紧张
EV_EARLY_ARRIVAL = "early_arrival"  # 到货提前
EV_CARGO_DAMAGE = "cargo_damage"    # 货物完好率跌破阈值


@dataclass
class TruckEvent:
    name: str
    ts: float = field(default_factory=time.time)
    snapshot: Optional[TruckSnapshot] = None
    data: dict = field(default_factory=dict)


class EventEngine:
    """基于快照边缘检测产生事件，带防刷屏仲裁。"""

    def __init__(self, config: object) -> None:
        self.cooldown = config.event_cooldown_s
        self.time_warn_min = config.time_warn_min
        self.speeding_reset_s = config.speeding_reset_s
        self.speeding_escalate_kmh = config.speeding_escalate_kmh
        self.crash_delta = config.crash_damage_delta
        self.brake_force = config.hard_brake_force
        self.brake_speed = config.hard_brake_speed_kmh
        self.low_fuel_pct = config.low_fuel_percent
        self._last: Optional[TruckSnapshot] = None
        self._fuel_last: Optional[float] = None
        self._damage_last: Optional[float] = None
        self._last_fire: dict = {}
        self._warned_low_time = False
        self._warned_over_time = False
        self._warned_low_fuel = False
        self._warned_relaxed = False
        self._warned_tight = False
        self._warned_cargo = False
        self._warned_early = False
        self._progress_fired: dict = {}
        self._distance_fired: dict = {}
        self._distance_init: Optional[float] = None
        self._distance_anchors: tuple = ()
        self._sinks: List[Callable[[TruckEvent], None]] = []
        self._last_speeding_fire = 0.0
        self._last_speeding_speed = 0.0

    def apply_config(self, config: object) -> None:
        """热更新阈值。"""
        self.cooldown = config.event_cooldown_s
        self.time_warn_min = config.time_warn_min
        self.speeding_reset_s = config.speeding_reset_s
        self.speeding_escalate_kmh = config.speeding_escalate_kmh
        self.crash_delta = config.crash_damage_delta
        self.brake_force = config.hard_brake_force
        self.brake_speed = config.hard_brake_speed_kmh
        self.low_fuel_pct = config.low_fuel_percent

    def on_event(self, sink: Callable[[TruckEvent], None]) -> None:
        self._sinks.append(sink)

    def _emit(self, name: str, snap: TruckSnapshot, data: dict = None,
              now: float = None, force: bool = False) -> Optional[TruckEvent]:
        now = now or time.time()
        if not force:
            last = self._last_fire.get(name, 0)
            if now - last < self.cooldown:
                return None
        self._last_fire[name] = now
        ev = TruckEvent(name=name, ts=now, snapshot=snap, data=data or {})
        for s in self._sinks:
            s(ev)
        return ev

    def feed(self, s: TruckSnapshot) -> List[TruckEvent]:
        out: List[TruckEvent] = []
        now = time.time()
        prev = self._last

        if not s.sdk_active:
            # 游戏未激活：只检测退出事件，重置边缘状态防陈旧帧误报
            if prev is not None and prev.sdk_active:
                ev = self._emit(EV_GAME_END, s, force=True)
                if ev:
                    out.append(ev)
            self._last = s
            self._last_speeding_speed = 0.0
            self._fuel_last = None
            self._damage_last = None
            return out

        if prev is None or not prev.sdk_active:
            ev = self._emit(EV_GAME_START, s, force=True)
            if ev:
                out.append(ev)

        if s.is_speeding:
            quiet = (now - self._last_speeding_fire) >= self.speeding_reset_s
            escalated = s.speed_kmh > self._last_speeding_speed + self.speeding_escalate_kmh
            if quiet or escalated:
                ev = self._emit(EV_SPEEDING, s, force=escalated)
                if ev:
                    out.append(ev)
                    self._last_speeding_fire = now
                    self._last_speeding_speed = s.speed_kmh
        else:
            # 脱离超速：重置速度和冷却窗口，防边界振荡绕过冷却
            self._last_speeding_speed = 0.0
            self._last_speeding_fire = 0.0

        if s.user_brake > self.brake_force and s.speed_kmh > self.brake_speed:
            ev = self._emit(EV_HARD_BRAKE, s)
            if ev:
                out.append(ev)

        if self._damage_last is not None:
            delta = s.max_damage - self._damage_last
            if delta > self.crash_delta:
                ev = self._emit(EV_CRASH, s, {"delta": delta})
                if ev:
                    out.append(ev)
        self._damage_last = s.max_damage

        if s.ev_refuel_payed and (prev is None or not prev.ev_refuel_payed):
            ev = self._emit(EV_REFUEL, s, force=True)
            if ev:
                out.append(ev)
        elif self._fuel_last is not None and s.fuel > self._fuel_last + 20:
            ev = self._emit(EV_REFUEL, s)
            if ev:
                out.append(ev)
        self._fuel_last = s.fuel

        if s.fuel_capacity > 0 and s.fuel_percent < self.low_fuel_pct:
            if not self._warned_low_fuel:
                ev = self._emit(EV_LOW_FUEL, s, {"percent": s.fuel_percent})
                if ev:
                    out.append(ev)
                self._warned_low_fuel = True
        elif s.fuel_percent > 25:
            self._warned_low_fuel = False

        if s.on_job and s.planned_distance_km > 0:
            pct = s.trip_progress_percent
            if pct is not None:
                # 行程节点：1/3、1/2、2/3、完成（含剩余里程交互播报）
                for cp in (33, 50, 67, 100):
                    if pct >= cp and not self._progress_fired.get(cp, False):
                        self._progress_fired[cp] = True
                        ev = self._emit(EV_TRIP_PROGRESS, s, {
                            "percent": pct,
                            "km": s.route_remaining_km,
                            "min": s.route_remaining_time_min,
                            "mark": cp,
                        }, force=True)
                        if ev:
                            out.append(ev)
                        break
        elif not s.on_job and self._progress_fired:
            self._progress_fired = {}

        # 距离分级锚点：按任务总里程自动生成（不写死），剩余距离交叉时预告
        if s.on_job and s.route_remaining_km > 0:
            rem = s.route_remaining_km
            if self._distance_init is None:
                self._distance_init = rem
                self._distance_anchors = _gen_distance_anchors(rem)
            for dkm in self._distance_anchors:
                if rem <= dkm and not self._distance_fired.get(dkm, False):
                    self._distance_fired[dkm] = True
                    ev = self._emit(EV_DISTANCE_MARK, s, {
                        "remaining_km": rem,
                        "mark": dkm,
                    }, force=True)
                    if ev:
                        out.append(ev)
                    break
        elif not s.on_job:
            self._distance_fired = {}
            self._distance_init = None
            self._distance_anchors = ()

        if prev is not None:
            if s.on_job and not prev.on_job:
                ev = self._emit(EV_JOB_START, s)
                if ev:
                    out.append(ev)
                self._warned_low_time = False
                self._warned_over_time = False
                self._warned_relaxed = False
                self._warned_tight = False
                self._warned_cargo = False
                self._warned_early = False
                self._distance_fired = {}
                self._distance_init = None
                self._distance_anchors = ()
            if not s.on_job and prev.on_job:
                if s.ev_job_delivered:
                    ev = self._emit(EV_JOB_DELIVERED, s)
                elif s.ev_job_cancelled:
                    ev = self._emit(EV_JOB_CANCELLED, s,
                                    {"penalty": s.job_cancelled_penalty})
                else:
                    ev = self._emit("job_end", s, force=True)
                if ev:
                    out.append(ev)

        if s.on_job:
            rem = s.delivery_remaining_min
            if rem is not None:
                if rem <= 0 and not self._warned_over_time:
                    ev = self._emit(EV_TIME_OVER, s, force=True)
                    if ev:
                        out.append(ev)
                    self._warned_over_time = True
                elif rem <= self.time_warn_min and not self._warned_low_time \
                        and not self._warned_over_time:
                    ev = self._emit(EV_TIME_WARNING, s, {"min": rem}, force=True)
                    if ev:
                        out.append(ev)
                    self._warned_low_time = True
                elif rem > self.time_warn_min * 1.5 and not self._warned_relaxed:
                    # 时间充裕（一次）
                    ev = self._emit(EV_TIME_RELAXED, s, {"min": rem}, force=True)
                    if ev:
                        out.append(ev)
                    self._warned_relaxed = True
                elif self.time_warn_min < rem <= self.time_warn_min * 1.1 \
                        and not self._warned_tight:
                    # 时间紧张（一次）
                    ev = self._emit(EV_TIME_TIGHT, s, {"min": rem}, force=True)
                    if ev:
                        out.append(ev)
                    self._warned_tight = True

        # 货物完好率跌破阈值（接货时 100% → 途中跌破 95% 提醒一次）
        if s.on_job and prev is not None and not self._warned_cargo:
            if 0 < s.job_cargo_damage < 0.05:
                ev = self._emit(EV_CARGO_DAMAGE, s, {"pct": (1 - s.job_cargo_damage) * 100},
                                force=True)
                if ev:
                    out.append(ev)
                self._warned_cargo = True

        # 到货提前（交付时间余量仍大 + 即将到货）
        if s.on_job and s.ev_job_delivered and not self._warned_early:
            rem = s.delivery_remaining_min
            if rem is not None and rem > 30:
                ev = self._emit(EV_EARLY_ARRIVAL, s, {"min": rem}, force=True)
                if ev:
                    out.append(ev)
                self._warned_early = True

        if prev is not None and s.trailer_attached != prev.trailer_attached:
            name = EV_TRAILER_ATTACH if s.trailer_attached else EV_TRAILER_DETACH
            ev = self._emit(name, s)
            if ev:
                out.append(ev)

        if s.ev_fined and (prev is None or not prev.ev_fined):
            ev = self._emit(EV_FINE, s, force=True)
            if ev:
                out.append(ev)
        if s.ev_tollgate and (prev is None or not prev.ev_tollgate):
            ev = self._emit(EV_TOLLGATE, s, force=True)
            if ev:
                out.append(ev)

        self._last = s
        return out

    def run_forever(self, interval: float = 1.0) -> None:
        """轮询循环（带重连）：游戏未运行时等待重试。"""
        while True:
            try:
                with TelemetryReader() as t:
                    while True:
                        try:
                            s = t.snapshot()
                        except Exception:
                            time.sleep(interval)
                            continue
                        self.feed(s)
                        time.sleep(interval)
            except Exception:
                time.sleep(interval)
