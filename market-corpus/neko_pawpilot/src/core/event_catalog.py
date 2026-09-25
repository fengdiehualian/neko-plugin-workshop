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
    "speeding":       EventSpec("speeding", CAT_SAFETY, 5, False, 30),
    "low_fuel":       EventSpec("low_fuel", CAT_SAFETY, 5, False, -1),
    "time_warning":   EventSpec("time_warning", CAT_TASK, 6, False, 120),
    "time_over":      EventSpec("time_over", CAT_TASK, 7, False, -1),
    "job_delivered":  EventSpec("job_delivered", CAT_TASK, 5, False, -1),
    "job_cancelled":  EventSpec("job_cancelled", CAT_TASK, 3, False, -1),
    "fine":           EventSpec("fine", CAT_TASK, 5, False, -1),
    "job_start":      EventSpec("job_start", CAT_TASK, 4, False, -1),
    "tollgate":       EventSpec("tollgate", CAT_TRIP, 3, False, -1),
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
    "cargo_damage":   EventSpec("cargo_damage", CAT_TASK, 5, False, -1),
}


def spec(event_id: str) -> EventSpec:
    """取事件规格；未知事件给保守默认。"""
    return EVENT_CATALOG.get(event_id, EventSpec(event_id, CAT_TRIP, 1, False, 30))


def preempt_ids() -> frozenset[str]:
    return frozenset(eid for eid, s in EVENT_CATALOG.items() if s.preempt)
