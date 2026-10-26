"""驾驶场景状态机：场景门控事件输出。"""

from __future__ import annotations

from typing import Optional

IDLE = "IDLE"                # 无任务
JOB_PICKUP = "JOB_PICKUP"    # 接单
DRIVING = "DRIVING"          # 行驶中
URBAN = "URBAN"              # 市区
HIGHWAY = "HIGHWAY"          # 高速
DELIVERY = "DELIVERY"        # 卸货
SETTLE = "SETTLE"            # 结算

ALL_SCENARIOS = (IDLE, JOB_PICKUP, DRIVING, URBAN, HIGHWAY, DELIVERY, SETTLE)

# 场景 → 允许的事件类别
SCENARIO_CATEGORIES = {
    IDLE: {"safety", "lifecycle", "chatter"},
    JOB_PICKUP: {"task", "lifecycle"},
    DRIVING: {"safety", "task", "trip", "lifecycle", "chatter"},
    URBAN: {"safety", "task", "trip", "lifecycle", "chatter"},
    HIGHWAY: {"safety", "task", "trip", "lifecycle", "chatter"},
    DELIVERY: {"task", "lifecycle"},
    SETTLE: {"task", "lifecycle", "chatter"},
}

# 高速判定限速阈值（km/h）
HIGHWAY_SPEED_LIMIT = 80


class ScenarioMachine:
    """根据遥测快照推导当前驾驶场景。"""

    def __init__(self) -> None:
        self.current = IDLE
        self.prev: Optional[str] = None

    def update(self, snap) -> str:
        """由快照推导场景并返回。"""
        moving = snap.speed_kmh > 5
        # 主菜单/未接管：SDK 不活跃且车没动 → IDLE（只放行 lifecycle）
        if not snap.sdk_active and not moving:
            self._set(IDLE)
            return self.current
        # 自由驾驶（没接单但在跑）同样算驾驶。
        # 旧版这里直接判 IDLE，而 IDLE 只允许 safety/lifecycle/chatter，
        # 于是"不开任务随便跑"时 task/trip 全被门控挡掉——玩家只能听见超速提醒
        # （2026-10 实测反馈："除了问我超速其他什么都不知道聊"）。
        if not snap.on_job:
            self._set(DRIVING if moving else IDLE)
            return self.current
        if snap.paused:
            self._set(JOB_PICKUP if not self.current else self.current)
            return self.current
        speed = snap.speed_kmh
        if speed < 5:
            self._set(DELIVERY)
        elif snap.speed_limit_kmh >= HIGHWAY_SPEED_LIMIT:
            self._set(HIGHWAY)
        elif speed > 0:
            self._set(URBAN if snap.speed_limit_kmh < 60 else DRIVING)
        return self.current

    def allow(self, category: str) -> bool:
        """当前场景是否允许该事件类别。"""
        return category in SCENARIO_CATEGORIES.get(self.current, set())

    def _set(self, new: str) -> None:
        if new != self.current:
            self.prev = self.current
            self.current = new
