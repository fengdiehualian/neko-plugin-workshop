"""事件总线：模块间解耦通信，指令打断走这里。"""

import asyncio
import inspect
import logging
from typing import Any, Callable, Dict, List

log = logging.getLogger(__name__)

_event_bus: "EventBus | None" = None


class EventType:
    """游戏世界事件类型常量。"""
    PLAYER_DIED = "player_died"
    PLAYER_RESPAWNED = "player_respawned"
    COMBAT_HIT = "combat_hit"
    COMBAT_KILLED = "combat_killed"
    ENEMY_KILLED = "enemy_killed"
    BOSS_SPAWNED = "boss_spawned"
    BOSS_KILLED = "boss_killed"
    BOSS_NEARBY = "boss_nearby"
    COMBAT_SUMMARY = "combat_summary"
    ORE_FOUND = "ore_found"
    CHEST_FOUND = "chest_found"
    FOUND_CHEST = "found_chest"          # vision 层事件
    FOUND_RARE = "found_rare"            # vision 层事件
    ENEMY_SPOTTED = "enemy_spotted"      # vision 层事件
    TERRAIN_CHANGED = "terrain_changed"  # vision 层 + 通用
    RARE_ITEM = "rare_item"
    EQUIPMENT_UPGRADED = "equipment_upgraded"
    INVENTORY_FULL = "inventory_full"
    GOAL_SET = "goal_set"
    GOAL_COMPLETED = "goal_completed"
    GOAL_FAILED = "goal_failed"
    LOW_HP = "low_hp"
    HP_CRASH = "hp_crash"
    DROWNING = "drowning"
    IN_LAVA = "in_lava"
    FALLING = "falling"
    BIOME_CHANGED = "biome_changed"
    PLAYER_NEARBY = "player_nearby"
    TIME_CHANGED = "time_changed"
    WEATHER_CHANGED = "weather_changed"
    INVASION = "invasion"
    INVASION_START = "invasion_start"
    INVASION_END = "invasion_end"
    MULTIPLAYER_MODE_CHANGED = "multiplayer_mode_changed"
    COMMAND_INTERRUPT = "command_interrupt"


def get_event_bus() -> "EventBus":
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus


class EventBus:
    def __init__(self) -> None:
        self._subs: Dict[str, List[Callable[[Any], Any]]] = {}
        self._tasks: set[asyncio.Task] = set()

    def subscribe(self, event: str, cb: Callable[[Any], Any]) -> None:
        # 去重：面板断开→重连会多次 start()，各模块重复 bind/subscribe，
        # 同回调被 append 两次 → 事件双触发（player_died 推两次等）
        subs = self._subs.setdefault(event, [])
        if cb not in subs:
            subs.append(cb)

    def unsubscribe(self, event: str, cb: Callable[[Any], Any]) -> None:
        subs = self._subs.get(event, [])
        if cb in subs:
            subs.remove(cb)

    async def publish(self, event: str, data: Any) -> None:
        for cb in list(self._subs.get(event, [])):
            try:
                result = cb(data)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                log.warning("事件回调失败 event=%s callback=%r: %s", event, cb, exc)

    def fire(self, event: str, data: Any) -> None:
        for cb in list(self._subs.get(event, [])):
            try:
                result = cb(data)
                if not inspect.isawaitable(result):
                    continue
                task = asyncio.create_task(result)
                self._tasks.add(task)
                task.add_done_callback(self._task_done)
            except Exception as exc:
                log.warning("事件派发失败 event=%s callback=%r: %s", event, cb, exc)

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        try:
            error = task.exception()
        except Exception as exc:
            log.warning("读取事件任务异常失败: %s", exc)
            return
        if error is not None:
            log.warning("异步事件回调失败: %s", error, exc_info=error)

    def fire_player_event(self, event: str, data: Dict[str, Any]) -> None:
        self.fire(event, data)

    def fire_combat_event(self, event: str, enemy: str,
                          damage: int = 0, **kwargs) -> None:
        self.fire(event, {"enemy_name": enemy, "damage": damage, **kwargs})

    def fire_goal_event(self, event: str, goal_type: str,
                        target: str, reason: str = "") -> None:
        self.fire(event, {"type": goal_type, "target": target, "reason": reason})

    def fire_explore_event(self, event: str, **kwargs) -> None:
        self.fire(event, kwargs)
