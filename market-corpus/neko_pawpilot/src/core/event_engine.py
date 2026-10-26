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
EV_VEHICLE_DAMAGE = "vehicle_damage"  # 车损累计跨档（非碰撞突增）
EV_FERRY = "ferry"                  # 上渡轮（带真实站点名 → 地名来源）
EV_TRAIN = "train"                  # 上火车（同上）
EV_MOUNTAIN_PASS = "mountain_pass"  # 翻越山口/长坡（按海拔变化判定）

# SCS 警告位 → 中文（仪表报警播报用；键名与 TruckSnapshot.warnings 一致）
_WARNING_LABELS = {
    "air_pressure": "气压报警",
    "air_emergency": "气压紧急",
    "fuel": "燃油报警",
    "adblue": "尿素报警",
    "oil_pressure": "机油压力",
    "water_temp": "水温报警",
    "battery": "电压报警",
}

# 货损播报档位（损伤 0-1）：完好率跌破 95% 起报，恶化到 80%/50% 再报
CARGO_DAMAGE_BANDS = (0.05, 0.20, 0.50)
# 车损播报档位（损伤 0-1）：25%/50%/75% 各报一次（修车后重新武装）
VEHICLE_DAMAGE_BANDS = (0.25, 0.50, 0.75)


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
        self.crash_accel_g = float(getattr(config, "crash_accel_g", 0.0) or 0.0)
        self.crash_window_s = float(getattr(config, "crash_damage_window_s", 5.0) or 5.0)
        # 山口判定：180 秒窗口内海拔变化超过该值即报（平原可忽略、山区明显）
        self.pass_delta_m = float(getattr(config, "mountain_pass_delta_m", 120.0) or 120.0)
        self._alt_hist: List[tuple] = []
        self.brake_force = config.hard_brake_force
        self.brake_speed = config.hard_brake_speed_kmh
        self.low_fuel_pct = config.low_fuel_percent
        self._last: Optional[TruckSnapshot] = None
        # 插件重载时游戏本来就在跑：这个时间戳之前不许喊「主人打开了欧卡2」
        self._skip_start_until: float = 0.0
        self._fuel_last: Optional[float] = None
        self._damage_last: Optional[float] = None
        self._damage_hist: List[tuple] = []   # [(ts, max_damage)] 碰撞时间窗
        self._last_fire: dict = {}
        self._warned_low_time = False
        self._warned_over_time = False
        self._warned_low_fuel = False
        self._warned_relaxed = False
        self._warned_tight = False
        self._warned_cargo = False
        self._cargo_band = 0        # 已播报到的货损档位（0=未报，1=报过 5%…）
        self._vehicle_band = 0      # 已播报到的车损档位
        self._warned_early = False
        self._progress_fired: dict = {}
        self._distance_fired: dict = {}
        self._distance_init: Optional[float] = None
        self._distance_anchors: tuple = ()
        self._sinks: List[Callable[[TruckEvent], None]] = []
        self._last_speeding_fire = 0.0
        self._last_speeding_speed = 0.0
        # 机务层的"连续状态"计时（远光/转向灯开着多久了、手刹/气压是否已报过）
        self._high_beam_since: float = 0.0
        self._blinker_since: float = 0.0
        self._air_low_since: float = 0.0
        self._warned_report: dict = {}

    def apply_config(self, config: object) -> None:
        """热更新阈值。"""
        self.cooldown = config.event_cooldown_s
        self.time_warn_min = config.time_warn_min
        self.speeding_reset_s = config.speeding_reset_s
        self.speeding_escalate_kmh = config.speeding_escalate_kmh
        self.crash_delta = config.crash_damage_delta
        self.crash_accel_g = float(getattr(config, "crash_accel_g", 0.0) or 0.0)
        self.crash_window_s = float(getattr(config, "crash_damage_window_s", 5.0) or 5.0)
        self.pass_delta_m = float(getattr(config, "mountain_pass_delta_m", 120.0) or 120.0)
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

    def skip_next_game_start(self, ttl: float = 180.0) -> None:
        """接下来 ttl 秒内不播报 game_start。

        场景：面板点「重载」时游戏正在运行，插件重新加载 → `_last` 是空的，
        旧逻辑会当成"主人刚打开游戏"再喊一次问候。实测重载两次就喊两次，
        主人听到的就是重复的假话。带 TTL 是为了万一这期间他真去重启游戏。
        """
        self._skip_start_until = time.time() + max(0.0, float(ttl))

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
            if now < self._skip_start_until:
                # 插件刚加载时游戏就已经在跑（重载）：只接管状态，不播报开局招呼
                self._skip_start_until = 0.0
            else:
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

        # ── 碰撞判定（2026-10 实测重写）──────────────────────────────
        # 旧版只看"单个采样帧内损伤 +5%"，实测会漏报：刮蹭式撞击的损伤增加
        # 分散在多次采样里（实测一次事故总 +3~7 点，单帧常 <5 点），结果
        # 车损涨到 13% 却一次都没播报。
        # 新版双信号：
        #   a) 时间窗累计：crash_window_s 秒内 max_damage 累计上升 ≥ crash_delta
        #   b) 加速度尖峰：撞击瞬间合成加速度 ≥ crash_accel_g（实测撞击 1.80g，
        #      正常驾驶 <0.4g）
        now_ts = now
        self._damage_hist.append((now_ts, s.max_damage))
        cutoff = now_ts - self.crash_window_s
        while self._damage_hist and self._damage_hist[0][0] < cutoff:
            self._damage_hist.pop(0)
        crash_reason = ""
        window_delta = 0.0
        if len(self._damage_hist) >= 2:
            base_dmg = min(d for _, d in self._damage_hist)
            window_delta = s.max_damage - base_dmg
            if window_delta > self.crash_delta:
                crash_reason = "damage"
        if not crash_reason and self.crash_accel_g > 0 and s.accel_g >= self.crash_accel_g:
            crash_reason = "accel"
        if crash_reason:
            ev = self._emit(EV_CRASH, s, {
                "delta": round(window_delta, 4),
                "window_s": self.crash_window_s,
                "damage": s.max_damage,
                "speed_kmh": s.speed_kmh,
                "accel_g": round(s.accel_g, 2),
                "source": crash_reason,
                "parts": ", ".join(
                    f"{k} {v * 100:.0f}%" for k, v in s.damage_parts.items()
                    if v > 0.01),
            })
            # 触发后重置基线，避免同一段损伤在后续窗口里反复报警
            self._damage_hist = [(now_ts, s.max_damage)]
            if ev:
                out.append(ev)
        self._damage_last = s.max_damage

        if s.ev_refuel_payed and (prev is None or not prev.ev_refuel_payed):
            ev = self._emit(EV_REFUEL, s, force=True)
            if ev:
                out.append(ev)
        elif self._fuel_last is not None and s.fuel > self._fuel_last + 20 \
                and s.in_session:
            ev = self._emit(EV_REFUEL, s)
            if ev:
                out.append(ev)
        self._fuel_last = s.fuel

        # 只在真的在开车时才报低油量：主菜单里共享内存会残留上一辆车的
        # fuelCapacity 而 fuel 已经归零，旧条件会误判成"没油了"（实测面板 0% + 该加油了）
        if (s.fuel_capacity > 0 and s.fuel_percent < self.low_fuel_pct
                and s.in_session):
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
                self._cargo_band = 0          # 新货：货损播报档位重置
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

        # 货物完好率跌破阈值（接货 100% → 途中跌破 95% 起报，继续恶化再报）
        # 修复三处历史 bug：
        #   1) 旧条件 `0 < job_cargo_damage < 0.05` 把语义写反了——只在"损伤
        #      不足 5%"时触发，真正的货损（≥5%，如撞车导致）反而永不播报；
        #   2) 只读 job_cargo_damage，漏掉写在挂车上的实时货损（见
        #      TruckSnapshot.cargo_damage 两路取大值）；
        #   3) 一次性 boolean 播报后永久静默，货损从 5% 恶化到 50% 也不再提醒。
        if s.on_job and prev is not None:
            dmg = s.cargo_damage
            for idx, band in enumerate(CARGO_DAMAGE_BANDS):
                if dmg >= band and self._cargo_band <= idx:
                    self._cargo_band = idx + 1
                    ev = self._emit(EV_CARGO_DAMAGE, s, {
                        "damage": dmg,
                        "pct": max(0.0, (1.0 - dmg) * 100.0),
                        "threshold": band,
                    }, force=True)
                    if ev:
                        out.append(ev)
                    break

        # 车损累计跨档（旧版只有 crash 突增检测：多次小刮蹭/磨损累积到 40%
        # 也不说话，主人根本不知道车该修了）。修车/换车后损伤回落 → 重新武装。
        vehicle_dmg = s.max_damage
        if vehicle_dmg < VEHICLE_DAMAGE_BANDS[0]:
            self._vehicle_band = 0
        else:
            parts = ", ".join(f"{k} {v * 100:.0f}%" for k, v in s.damage_parts.items()
                              if v > 0.01)
            for idx, band in enumerate(VEHICLE_DAMAGE_BANDS):
                if vehicle_dmg >= band and self._vehicle_band <= idx:
                    self._vehicle_band = idx + 1
                    ev = self._emit(EV_VEHICLE_DAMAGE, s, {
                        "damage": vehicle_dmg,
                        "percent": vehicle_dmg * 100.0,
                        "parts": parts,
                        "threshold": band,
                    }, force=True)
                    if ev:
                        out.append(ev)
                    break

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

        # 渡轮 / 火车：过境事件（顺带给出真实站点名，供地点簿学习与介绍）
        if s.ev_ferry and (prev is None or not prev.ev_ferry):
            ev = self._emit(EV_FERRY, s, {
                "source": s.ferry_source, "target": s.ferry_target,
            }, force=True)
            if ev:
                out.append(ev)
        if s.ev_train and (prev is None or not prev.ev_train):
            ev = self._emit(EV_TRAIN, s, {
                "source": s.train_source, "target": s.train_target,
            }, force=True)
            if ev:
                out.append(ev)

        # 山口 / 长坡：按海拔（world_y）变化判定——地图数据里没有桥隧标注，
        # 但海拔是遥测直接给的，翻山是最容易识别、也最值得一提的地标类型。
        if s.paused or not s.on_job:
            self._alt_hist = []
        else:
            self._alt_hist.append((now, s.world_y))
            cutoff_alt = now - 180.0
            while self._alt_hist and self._alt_hist[0][0] < cutoff_alt:
                self._alt_hist.pop(0)
            if len(self._alt_hist) >= 2:
                lo = min(a for _, a in self._alt_hist)
                hi = max(a for _, a in self._alt_hist)
                if (hi - lo) >= self.pass_delta_m:
                    rising = s.world_y >= (lo + hi) / 2.0
                    ev = self._emit(EV_MOUNTAIN_PASS, s, {
                        "climb_m": round(hi - lo, 1),
                        "altitude": round(s.world_y, 1),
                        "direction": "up" if rising else "down",
                    })
                    if ev:
                        out.append(ev)
                        self._alt_hist = [(now, s.world_y)]

        # 机务/操作检查（灯光、气压、温度、尿素、电压、手刹、离地轮…）
        self._check_vehicle(s, prev, now, out)

        self._last = s
        return out

    # ── 机务/操作层 ───────────────────────────────────────────────
    # 2026-10 主人反馈："灯光胎压乱七八糟的碰撞等都要能用上，不是获取了就没用了"。
    # 这些信号本来就一直在遥测里（面板也显示），但从来没有进入播报层。
    def _check_vehicle(self, s: TruckSnapshot, prev: Optional[TruckSnapshot],
                       now: float, out: List[TruckEvent]) -> None:
        speed = float(s.speed_kmh or 0.0)

        def fire(name: str, data: dict = None, force: bool = True) -> None:
            ev = self._emit(name, s, data or {}, now=now, force=force)
            if ev:
                out.append(ev)

        # ① 行驶中熄火（要求"上一帧还着着"的跃变，避免引擎字段缺省 False 时误报）/
        #    手刹没松：都是"车在动但状态不对"
        if speed > 20 and prev is not None and prev.engine_enabled \
                and not s.engine_enabled:
            fire("engine_stalled", {"speed": speed})
        if speed > 5 and s.park_brake:
            fire("park_brake_moving", {"speed": speed})

        # ①b 休息/睡觉：ETS2 的强制休息倒计时在睡觉后会被顶回去很多，
        #     所以"倒计时突然变长"就是真实信号（不看就没有别的办法知道玩家睡了）
        cur_rest = float(getattr(s, "rest_stop_min", 0) or 0)
        prev_rest = float(getattr(prev, "rest_stop_min", 0) or 0) if prev else 0.0
        if prev_rest > 0 and cur_rest > prev_rest + 180:
            fire("rested", {"before_min": prev_rest, "after_min": cur_rest,
                            "slept_h": (cur_rest - prev_rest) / 60.0})

        # ② 轮胎离地/翻滚：truck_wheelOnGround 是 16 槽数组，"0 个着地"只有在
        #    伴随强冲击时才可信（否则可能是该车型没填这个数组）——所以和加速度挂钩，
        #    只在真出事时播，不会因为拿不到数据就乱喊
        if speed > 15 and int(s.wheels_on_ground or 0) == 0 and s.accel_g >= 1.2:
            fire("wheels_airborne", {"speed": speed, "accel_g": round(s.accel_g, 2)})

        # ③ 刹车气压：**不猜单位**。SCS 的 airPressure 各家记录不一致，
        #    权威信号是游戏自己点亮的报警位（airPressureWarning / Emergency）。
        #    数值只作为附注原样带出。
        warn = getattr(s, "warnings", {}) or {}
        if speed > 3 and (warn.get("air_pressure") or warn.get("air_emergency")):
            if not self._air_low_since:
                self._air_low_since = now
            elif now - self._air_low_since >= 5.0:
                fire("air_pressure_low",
                     {"bar": float(s.air_pressure or 0.0),
                      "emergency": bool(warn.get("air_emergency"))})
                self._air_low_since = 0.0
        else:
            self._air_low_since = 0.0

        # ④ 仪表报警（SCS 自己的警告位：油压/水温/气压/电压…）
        #    气压/尿素/燃油/电压都有各自的专属事件，这里排除掉避免同一件事报两遍
        _dedicated = {"air_pressure", "air_emergency", "adblue", "fuel", "battery"}
        warn_on = [k for k, v in (getattr(s, "warnings", {}) or {}).items()
                   if v and k not in _dedicated]
        if warn_on and speed > 3:
            key = ",".join(sorted(warn_on))
            if self._warned_report.get("warnings") != key:
                self._warned_report["warnings"] = key
                fire("engine_warning", {"flags": key,
                                        "text": "、".join(_WARNING_LABELS.get(w, w)
                                                          for w in sorted(warn_on))})
        elif not warn_on:
            self._warned_report.pop("warnings", None)

        # ⑤ 水温/油温过高、刹车过热（各报一次，冷却到点后允许再报）
        if (s.water_temperature or 0) >= 100 or (s.oil_temperature or 0) >= 115:
            fire("overheat", {"water": float(s.water_temperature or 0),
                              "oil": float(s.oil_temperature or 0)})
        if speed > 10 and (s.brake_temperature or 0) >= 250:
            fire("brake_temp_high", {"temp": float(s.brake_temperature or 0),
                                     "retarder": bool(s.retarder_brake)})

        # ⑥ 尿素 / 电压（只在真的在开时提，避免停车场里瞎念叨）
        if speed > 3 and (s.adblue_capacity or 0) > 0:
            pct = float(s.adblue or 0) / float(s.adblue_capacity) * 100.0
            if pct < 15.0:
                fire("adblue_low", {"percent": pct})
        if speed > 3 and 0 < (s.battery_voltage or 0) < 23.5:
            fire("battery_low", {"volt": float(s.battery_voltage or 0)})

        # ⑦ 夜间没开灯（游戏内时间 20:00-05:00，车速起来才管）
        #    time_abs_min=0 表示还没拿到游戏内时间，不能当成"半夜"误报
        hour = int((s.time_abs_min or 0) // 60) % 24
        if s.time_abs_min and speed > 25 and (hour >= 20 or hour < 5) \
                and not s.lights_low_beam and not s.lights_parking \
                and not s.lights_high_beam:
            fire("lights_off_night", {"hour": hour})

        # ⑧ 远光常开（连续 30 秒）/ 转向灯忘关（连续 25 秒）
        if s.lights_high_beam and speed > 10:
            self._high_beam_since = self._high_beam_since or now
            if now - self._high_beam_since >= 30.0:
                fire("high_beam_on", {"speed": speed})
                self._high_beam_since = 0.0
        else:
            self._high_beam_since = 0.0

        blinker = bool(s.blinker_left or s.blinker_right)
        if blinker and speed > 20:
            self._blinker_since = self._blinker_since or now
            if now - self._blinker_since >= 25.0:
                fire("blinker_forgotten",
                     {"side": "左" if s.blinker_left else "右"})
                self._blinker_since = 0.0
        else:
            self._blinker_since = 0.0

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
