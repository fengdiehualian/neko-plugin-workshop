"""事件规格表：每个事件带类别/优先级/抢占资格/冷却。"""

from __future__ import annotations

from dataclasses import dataclass

# 事件类别
CAT_SAFETY = "safety"        # 驾驶安全（超速/急刹/车祸/低油量）
CAT_TASK = "task"            # 任务相关（接单/到货/罚款/超时）
CAT_TRIP = "trip"            # 旅程小事（加油/收费站/挂车/进度）
CAT_LIFECYCLE = "lifecycle"  # 进游戏/退游戏
CAT_CHATTER = "chatter"      # 陪伴闲聊

# 优先级 1-10，越高越优先
# 抢占资格：紧急事件可打断当前输出

BROADCAST_FREQUENCIES = frozenset({"quiet", "standard", "active"})
BROADCAST_FREQUENCY_MULTIPLIERS = {"quiet": 1.6, "standard": 1.0, "active": 0.65}

BROADCAST_CATEGORY_DEFAULTS = {
    "safety": True,
    "task": True,
    "trip": True,
    "lifecycle": True,
    "chatter": False,
}


@dataclass(frozen=True)
class EventSpec:
    """单个事件的静态策略。"""

    event_id: str
    category: str
    priority: int
    preempt: bool          # 可抢占当前输出
    cooldown_seconds: float  # <0 表示一次触发后长期冷却（如接单/到货）


EVENT_CATALOG: dict[str, EventSpec] = {
    "crash":          EventSpec("crash", CAT_SAFETY, 9, True, 15),
    "hard_brake":     EventSpec("hard_brake", CAT_SAFETY, 6, False, 20),
    "vehicle_damage": EventSpec("vehicle_damage", CAT_SAFETY, 6, False, 60),
    "speeding":       EventSpec("speeding", CAT_SAFETY, 5, False, 30),
    "low_fuel":       EventSpec("low_fuel", CAT_SAFETY, 5, False, -1),
    "time_warning":   EventSpec("time_warning", CAT_TASK, 6, False, 120),
    "time_over":      EventSpec("time_over", CAT_TASK, 7, False, -1),
    "job_delivered":  EventSpec("job_delivered", CAT_TASK, 5, False, -1),
    "job_cancelled":  EventSpec("job_cancelled", CAT_TASK, 3, False, -1),
    "fine":           EventSpec("fine", CAT_TASK, 5, False, -1),
    "job_start":      EventSpec("job_start", CAT_TASK, 4, False, -1),
    "tollgate":       EventSpec("tollgate", CAT_TRIP, 3, False, -1),
    "ferry":          EventSpec("ferry", CAT_TRIP, 4, False, -1),
    "train":          EventSpec("train", CAT_TRIP, 4, False, -1),
    "mountain_pass":  EventSpec("mountain_pass", CAT_TRIP, 4, False, 300),
    "refuel":         EventSpec("refuel", CAT_TRIP, 3, False, -1),
    "trailer_attach": EventSpec("trailer_attach", CAT_TRIP, 2, False, -1),
    "trailer_detach": EventSpec("trailer_detach", CAT_TRIP, 2, False, -1),
    "trip_progress":  EventSpec("trip_progress", CAT_TRIP, 2, False, 300),
    "distance_mark":  EventSpec("distance_mark", CAT_TRIP, 3, False, 120),
    "game_start":     EventSpec("game_start", CAT_LIFECYCLE, 2, False, -1),
    "game_end":       EventSpec("game_end", CAT_LIFECYCLE, 2, False, -1),
    "time_relaxed":   EventSpec("time_relaxed", CAT_TASK, 3, False, -1),
    "time_tight":     EventSpec("time_tight", CAT_TASK, 5, False, -1),
    "early_arrival":  EventSpec("early_arrival", CAT_TASK, 4, False, -1),
    "cargo_damage":   EventSpec("cargo_damage", CAT_TASK, 6, False, 30),
    # ── 机务/操作类（2026-10：主人指出"灯光气压这些采集了就没用过"）──
    # 这些信号一直躺在遥测里、面板上也显示，但从来没进过播报层
    "air_pressure_low":  EventSpec("air_pressure_low", CAT_SAFETY, 8, False, 180),
    "engine_warning":    EventSpec("engine_warning", CAT_SAFETY, 8, False, 240),
    "park_brake_moving": EventSpec("park_brake_moving", CAT_SAFETY, 8, False, 120),
    "engine_stalled":    EventSpec("engine_stalled", CAT_SAFETY, 8, False, 120),
    "overheat":          EventSpec("overheat", CAT_SAFETY, 7, False, 300),
    "lights_off_night":  EventSpec("lights_off_night", CAT_SAFETY, 7, False, 300),
    "wheels_airborne":   EventSpec("wheels_airborne", CAT_SAFETY, 7, False, 120),
    "brake_temp_high":   EventSpec("brake_temp_high", CAT_SAFETY, 6, False, 300),
    "adblue_low":        EventSpec("adblue_low", CAT_SAFETY, 5, False, 900),
    "battery_low":       EventSpec("battery_low", CAT_SAFETY, 5, False, 600),
    "high_beam_on":      EventSpec("high_beam_on", CAT_TRIP, 3, False, 600),
    "blinker_forgotten": EventSpec("blinker_forgotten", CAT_TRIP, 3, False, 300),
    # 休息/睡觉（ETS2 真实机制：睡一觉会把强制休息倒计时顶回去）
    "rested":            EventSpec("rested", CAT_TRIP, 4, False, 600),
}


def spec(event_id: str) -> EventSpec:
    """取事件规格；未知事件给保守默认。"""
    return EVENT_CATALOG.get(event_id, EventSpec(event_id, CAT_TRIP, 1, False, 30))
