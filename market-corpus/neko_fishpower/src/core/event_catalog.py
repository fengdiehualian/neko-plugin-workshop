"""事件目录：每种事件的类别/冷却/优先级/抢占标记。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

BROADCAST_FREQUENCY_MULTIPLIERS = {
    "quiet": 2.5,
    "standard": 1.0,
    "active": 0.5,
}


@dataclass(frozen=True)
class EventSpec:
    category: str      # caught/casino/grill/boss/journal/lifecycle/chatter
    cooldown_seconds: float
    priority: int      # 越大越优先（单槽窗口抢位）
    preempt: bool      # 抢占通道（立即输出，走 critical 冷却）


SPECS: Dict[str, EventSpec] = {
    # ── 钓鱼核心：高频事件默认长冷却（不刷屏，猫娘不是播报机器） ──
    # cast/bite/miss 属于"每竿都发生"的常态，300s 冷却≈基本不主动报，
    # 只有值得说的时刻（稀有/新鱼/Boss/赌局）才由 caught/discovered 等触发。
    "cast": EventSpec("caught", 300.0, 20, False),
    "bite": EventSpec("caught", 300.0, 40, False),
    "caught": EventSpec("caught", 90.0, 60, True),        # 上钩高光，抢占
    "miss": EventSpec("caught", 300.0, 30, False),
    "seagull": EventSpec("caught", 120.0, 25, False),
    # ── 赌场：玩家反复玩是常态，长冷却只在大输赢时冒泡 ──
    "bet": EventSpec("casino", 600.0, 35, False),
    "roulette_result": EventSpec("casino", 300.0, 45, False),
    "slot_spin": EventSpec("casino", 600.0, 20, False),
    "slot_result": EventSpec("casino", 300.0, 40, False),
    # ── 烤鱼 ──
    "grill_start": EventSpec("grill", 180.0, 20, False),
    "grill_done": EventSpec("grill", 90.0, 45, False),
    # ── Boss ──
    "boss_spawn": EventSpec("boss", 10.0, 70, True),
    "boss_hp": EventSpec("boss", 30.0, 40, False),
    "boss_death": EventSpec("boss", 8.0, 80, True),
    # ── 图鉴/经济 ──
    "discovered": EventSpec("journal", 30.0, 55, False),
    "sold": EventSpec("journal", 300.0, 35, False),
    "kill": EventSpec("journal", 60.0, 30, False),
    # ── 大事件 ──
    "player_death": EventSpec("boss", 5.0, 90, True),  # 玩家死亡高优先抢占
    # ── 成就 ──
    "achievement": EventSpec("journal", 15.0, 60, False),
    # ── 生命周期 ──
    "game_start": EventSpec("lifecycle", 0.0, 50, True),
    "game_end": EventSpec("lifecycle", 0.0, 50, True),
    # ── 闲聊（低优先） ──
    "chatter": EventSpec("chatter", 300.0, 10, False),
}

# 抢占事件集合（预留给 safety 检查）
PREEMPT_IDS = frozenset(
    name for name, es in SPECS.items() if es.preempt
)


def spec(event_name: str) -> EventSpec:
    return SPECS.get(event_name, EventSpec("chatter", 300.0, 10, False))


def preempt_ids() -> frozenset:
    return PREEMPT_IDS
