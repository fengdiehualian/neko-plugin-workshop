"""游戏事件发射器 —— 监听游戏状态变化，检测并发出自然语言事件。 的 19 种事件源。"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from .event_bus import EventType

if TYPE_CHECKING:
    from .interaction_engine import InteractionEngine

MINING_ORE_NAMES: Set[str] = {
    "铜矿", "铁矿", "银矿", "金矿", "铂金矿",
    "陨石", "狱石", "钴矿", "钯金矿", "秘银矿",
    "精金矿", "钛金矿", "叶绿矿",
}

BOSS_KEYWORDS: List[str] = [
    "克苏鲁", "领主", "之眼", "之脑", "吞噬者",
    "蜂后", "骷髅王", "肉山", "毁灭者", "双子",
    "世纪之花", "石巨人", "猪鲨", "拜月教", "月总",
    "光之女皇", "独眼巨鹿", "史莱姆王", "boss",
]


class GameEventEmitter:

    def __init__(self, agent: Any = None) -> None:
        self.interaction: Optional["InteractionEngine"] = None
        self.agent = agent
        self._prev_snap: Dict[str, Any] = {}
        self._snap_count: int = 0

        # 战斗追踪
        self._last_combat_time: float = 0.0
        self._combat_damage_sum: int = 0
        self._combat_kill_count: int = 0
        self._combat_enemy_name: str = ""
        self._combat_cooldown: float = 5.0

        # 挖矿追踪
        self._last_mine_time: float = 0.0
        self._mining_ore_counts: Dict[str, int] = {}
        self._mining_baselined: bool = False
        self._mining_cooldown: float = 10.0

        # 探索追踪
        self._last_explore_time: float = 0.0
        self._last_biome: str = ""
        self._chests_seen: Set[str] = set()
        self._explore_cooldown: float = 15.0

        # 目标追踪
        self._current_goal: Optional[Dict[str, Any]] = None
        self._goal_emitted: bool = False

        # 危险追踪（溺水/岩浆/坠落 + HP 紧急 + Boss）
        self._last_danger_time: float = 0.0
        self._danger_cooldown: float = 20.0
        self._drowning_warned: bool = False

        # HP 紧急追踪 —— 带 HP 恢复自动重置，避免 flag 卡死
        self._hp_crash_emitted: bool = False
        self._low_hp_emitted: bool = False
        self._boss_nearby_emitted: bool = False
        self._last_hp_emergency_time: float = 0.0
        self._hp_emergency_cooldown: float = 30.0

        # 社交追踪
        self._nearby_player_names: Set[str] = set()
        self._last_social_time: float = 0.0
        self._social_cooldown: float = 30.0

        # 环境追踪
        self._last_time_of_day: str = ""
        self._last_weather: str = ""

    def bind_interaction(self, engine: "InteractionEngine") -> None:
        self.interaction = engine

    def _inject(self, event_type: str, intensity: float = 0.5,
                description: str = "", data: Optional[Dict] = None,
                urgent: bool = False) -> None:
        """Fire-and-forget 注入事件到异步 InteractionEngine。
        
        inject_event 是 async 方法，但 emitter 的 tick / callback
        在同步上下文调用。通过 ensure_future 调度到运行中的事件循环。
        如果无 event loop 正在运行则安全忽略。
        """
        if not self.interaction:
            return
        event_data = dict(data or {})
        if urgent:
            event_data["urgent"] = True
        try:
            asyncio.ensure_future(
                self.interaction.inject_event(
                    event_type, intensity=intensity,
                    description=description, data=event_data))
        except RuntimeError:
            pass  # 没有运行的 event loop（测试/非异步上下文）

    def tick(self, state: Dict[str, Any]) -> None:
        """每帧采样调一次，diff 检测各类事件。"""
        self._snap_count += 1
        prev = self._prev_snap
        now = time.monotonic()

        if self._snap_count <= 2:
            self._prev_snap = dict(state)
            return

        self._check_combat_events(state, prev, now)
        self._check_mining_events(state, prev, now)
        self._check_exploration_events(state, prev, now)
        self._check_danger_events(state, prev, now)
        self._check_social_events(state, prev, now)
        self._check_environment_events(state, prev, now)

        self._prev_snap = dict(state)

    def on_goal_set(self, goal_type: str, target: str, reason: str = "") -> None:
        self._current_goal = {"type": goal_type, "target": target, "reason": reason}
        self._goal_emitted = False
        if self.interaction:
            text = f"决定去 {'收集' if goal_type == 'gather' else '击杀' if goal_type == 'kill' else goal_type} {target}"
            if reason:
                text += f"（{reason}）"
            self._inject(
                EventType.GOAL_SET, intensity=0.3, description=text,
                data={"goal": self._current_goal})

    def on_goal_completed(self, goal_type: str, target: str) -> None:
        self._current_goal = None
        self._goal_emitted = False
        if self.interaction:
            text = f"搞定了！{'收集' if goal_type == 'gather' else '击杀' if goal_type == 'kill' else goal_type} {target} 完成～"
            self._inject(EventType.GOAL_COMPLETED, intensity=0.3, description=text)

    def on_goal_failed(self, goal_type: str, target: str, reason: str = "") -> None:
        self._current_goal = None
        self._goal_emitted = False
        if self.interaction:
            text = f"失败了... {'收集' if goal_type == 'gather' else '击杀' if goal_type == 'kill' else goal_type} {target}"
            if reason:
                text += f"（{reason}）"
            self._inject(EventType.GOAL_FAILED, intensity=0.25, description=text)

    def on_killed_enemy(self, enemy_name: str) -> None:
        self._combat_kill_count += 1
        is_boss = any(kw in enemy_name.lower() for kw in BOSS_KEYWORDS)
        if self.interaction:
            if is_boss:
                self._inject(
                    EventType.BOSS_KILLED, intensity=0.9,
                    description=f"击败了 {enemy_name}！太厉害了喵～",
                    data={"boss_name": enemy_name})
            elif self._combat_enemy_name and self._combat_enemy_name != enemy_name:
                self._inject(
                    EventType.ENEMY_KILLED, intensity=0.4,
                    description=f"干掉了 {enemy_name}！")

    def on_equipment_upgraded(self, slot: str, old_item: str, new_item: str) -> None:
        if self.interaction:
            self._inject(
                EventType.EQUIPMENT_UPGRADED, intensity=0.3,
                description=f"装备升级：{slot} {old_item} → {new_item}！变强了喵～")

    def on_inventory_full(self) -> None:
        if self.interaction:
            self._inject(
                EventType.INVENTORY_FULL, intensity=0.25,
                description="背包满了！得丢点东西或者回去放一下...")

    # ── 内部事件检测 ──

    def _check_combat_events(self, cur: Dict, prev: Dict, now: float) -> None:
        cur_hp = cur.get("hp", 100)
        prev_hp = prev.get("hp", 100)
        max_hp = cur.get("max_life", 100) or 100

        hp_lost = prev_hp - cur_hp
        if hp_lost > 0:
            self._combat_damage_sum += hp_lost
            nearby = (cur.get("nearby_npcs", []) or cur.get("nearby", [])
                      or cur.get("npcs", []) or [])
            enemy_name = self._get_nearest_enemy_name(nearby)
            if enemy_name:
                self._combat_enemy_name = enemy_name
            self._last_combat_time = now

        time_since = now - self._last_combat_time
        if (time_since > self._combat_cooldown and
                self._combat_damage_sum > 0 and self.interaction):
            dmg = self._combat_damage_sum
            kills = self._combat_kill_count
            enemy = self._combat_enemy_name

            if kills > 0 and dmg > max_hp * 0.3:
                text = f"刚才掉了{dmg}点血，不过记录到击败了{kills}只敌人。"
                self._inject(EventType.COMBAT_SUMMARY, intensity=0.45, description=text)
            elif dmg > max_hp * 0.5:
                text = f"刚才掉了{dmg}点血（剩余{cur_hp}）。"
                if enemy:
                    text += f"附近有{enemy}，伤害来源尚未确认。"
                self._inject(EventType.COMBAT_SUMMARY, intensity=0.55, description=text)

            self._combat_damage_sum = 0
            self._combat_kill_count = 0
            self._combat_enemy_name = ""

    def _check_mining_events(self, cur: Dict, prev: Dict, now: float) -> None:
        if now - self._last_mine_time < self._mining_cooldown:
            return
        # 使用整份背包数量；在快捷栏和主背包之间移物不代表获得新矿石。
        hotbar: List[Dict] = []
        try:
            inv = self.agent.get_inventory_sync() if self.agent else {}
            if not inv:
                return
            hotbar = (inv.get("hotbar", []) or []) + (inv.get("inventory", []) or [])
        except Exception:
            return
        current: Dict[str, int] = {}
        for slot in hotbar:
            if not isinstance(slot, dict):
                continue
            name = str(slot.get("name", "") or "")
            if not name:
                continue
            try:
                stack = max(0, int(slot.get("stack", slot.get("count", 1)) or 0))
            except (TypeError, ValueError):
                stack = 0
            for ore in MINING_ORE_NAMES:
                if ore in name:
                    current[ore] = current.get(ore, 0) + stack
        if not self._mining_baselined:
            self._mining_ore_counts = current
            self._mining_baselined = True
            return
        for ore, count in current.items():
            if count > self._mining_ore_counts.get(ore, 0):
                self._mining_ore_counts = current
                self._last_mine_time = now
                if self.interaction:
                    self._inject(
                        EventType.ORE_FOUND, intensity=0.25,
                        description=f"背包中的{ore}数量增加，当前共{count}个；来源未确认。")
                return
        self._mining_ore_counts = current

    def _check_exploration_events(self, cur: Dict, prev: Dict, now: float) -> None:
        if now - self._last_explore_time < self._explore_cooldown:
            return

        chests = cur.get("nearby_chests", []) or []
        for chest in chests:
            chest_id = str(chest.get("pos", chest.get("name", "")))
            if chest_id and chest_id not in self._chests_seen:
                self._chests_seen.add(chest_id)
                self._last_explore_time = now
                if self.interaction:
                    self._inject(
                        EventType.CHEST_FOUND, intensity=0.3,
                        description=f"发现 {chest.get('name', '宝箱')}！附近有宝箱～")
                return

        cur_biome = str(cur.get("biome", "") or "")
        prev_biome = str(prev.get("biome", "") or "")
        if cur_biome and cur_biome != prev_biome and cur_biome != self._last_biome:
            self._last_biome = cur_biome
            self._last_explore_time = now
            if self.interaction and prev_biome:
                self._inject(
                    EventType.BIOME_CHANGED, intensity=0.15,
                    description=f"离开了 {prev_biome}，进入了 {cur_biome}")

    def _check_danger_events(self, cur: Dict, prev: Dict, now: float) -> None:
        cur_hp = cur.get("hp", 100)
        max_hp = cur.get("max_life", 100) or 100
        # Mod 直接推送液体与呼吸状态；兼容旧 Mod 时再退化到 movement/buff 推导。
        movement = str(cur.get("movement_state", "") or "").lower()
        is_in_water = bool(cur.get("in_water", False)) or "swim" in movement
        is_in_lava = bool(cur.get("in_lava", False)) or any(
            "lava" in str(b).lower() or "岩浆" in str(b)
            for b in (cur.get("buffs", []) or []))
        try:
            breath = int(cur.get("breath", 200) or 0)
        except (TypeError, ValueError):
            breath = 200
        on_ground = bool(cur.get("grounded", True))
        # 坠落：非地面且向下速度大（velocity_y>0 向下，像素/秒，>10*16≈160 算坠落）
        vel_y = float(cur.get("velocity_y", 0) or 0)
        fall_speed = max(0, vel_y)
        cooldown_ok = now - self._last_danger_time > self._danger_cooldown
        hp_ratio = cur_hp / max(max_hp, 1)

        # ── HP 恢复自动重置 flag（Bug fix：防止低血 flag 卡死） ──
        if hp_ratio > 0.4:
            if self._hp_crash_emitted or self._low_hp_emitted:
                self._hp_crash_emitted = False
                self._low_hp_emitted = False
                self._last_hp_emergency_time = 0.0  # 冷却一并重置，恢复后再低血能立刻报
        # Boss 检测：从 nearby_npcs 识别（mod 不返回 boss_nearby 键）
        boss = ""
        for e in cur.get("nearby_npcs", []) or []:
            if (e.get("friendly", False)
                    or e.get("townNPC", e.get("town_npc", False))
                    or int(e.get("life", 0) or 0) <= 0):
                continue
            nm = str(e.get("name", "") or "").lower()
            if any(kw in nm for kw in BOSS_KEYWORDS):
                boss = nm
                break
        if not boss:
            self._boss_nearby_emitted = False

        # ── Boss 附近（紧急） ──
        if boss and not self._boss_nearby_emitted and self.interaction:
            self._boss_nearby_emitted = True
            self._inject(
                EventType.BOSS_NEARBY, intensity=0.9,
                description=f"BOSS {boss} 在附近！危险！")

        # ── HP 极低（紧急）── 独立 cooldown 防止频繁触发 ──
        hp_cooldown_ok = now - self._last_hp_emergency_time > self._hp_emergency_cooldown

        if hp_ratio > 0 and hp_ratio <= 0.2 and not self._hp_crash_emitted and hp_cooldown_ok:
            self._hp_crash_emitted = True
            self._last_hp_emergency_time = now
            if self.interaction:
                self._inject(
                    EventType.HP_CRASH, intensity=0.95,
                    description=f"血量极低({cur_hp}/{max_hp})！！快死了喵！！")

        if hp_ratio > 0 and hp_ratio <= 0.35 and not self._low_hp_emitted and hp_cooldown_ok:
            self._low_hp_emitted = True
            self._last_hp_emergency_time = now
            if self.interaction:
                self._inject(
                    EventType.LOW_HP, intensity=0.7,
                    description=f"血量危险({cur_hp}/{max_hp})，需要回血")

        # ── 溺水 ──
        if is_in_water and breath < 50 and not self._drowning_warned:
            self._drowning_warned = True
            if self.interaction and cooldown_ok:
                self._last_danger_time = now
                self._inject(
                    EventType.DROWNING, intensity=0.75,
                    description=f"水好深，快上去了！（呼吸{breath}）咕噜咕噜...")
        elif not is_in_water:
            self._drowning_warned = False

        # ── 岩浆 ──
        if is_in_lava and cur_hp > 0 and self.interaction and cooldown_ok:
            self._last_danger_time = now
            self._inject(
                EventType.IN_LAVA, intensity=0.85,
                description="啊啊啊掉进岩浆了！好烫好烫！！")

        # ── 坠落（#11: fall_speed 阈值按像素速度，>160px/s 视为坠落） ──
        if not on_ground and fall_speed > 160 and cur_hp > 0 and self.interaction and cooldown_ok:
            self._last_danger_time = now
            self._inject(
                EventType.FALLING, intensity=0.55,
                description=f"哇啊啊正在坠落！（速度{fall_speed:.0f}px/s）要摔了！")

    def _check_social_events(self, cur: Dict, prev: Dict, now: float) -> None:
        if now - self._last_social_time < self._social_cooldown:
            return
        nearby_players = cur.get("nearby_players", []) or []
        current_names: Set[str] = set(
            str(p.get("name", "") or "") for p in nearby_players)
        new_players = current_names - self._nearby_player_names
        if new_players and self._nearby_player_names:
            for name in new_players:
                if self.interaction:
                    self._last_social_time = now
                    self._inject(
                        EventType.PLAYER_NEARBY, intensity=0.15,
                        description=f"「{name}」出现在附近～")
        self._nearby_player_names = current_names

    def _check_environment_events(self, cur: Dict, prev: Dict, now: float) -> None:
        # mod 返回 time_of_day（"白天"/"夜晚"）
        cur_time = str(cur.get("time_of_day", "") or cur.get("time", "") or "").strip()
        if cur_time and cur_time != self._last_time_of_day:
            self._last_time_of_day = cur_time
            if self.interaction:
                self._inject(
                    EventType.TIME_CHANGED,
                    intensity=0.25 if "夜" in cur_time else 0.15,
                    description=f"{cur_time}了～")

        cur_weather = str(cur.get("weather", "") or "").strip()
        if cur_weather and cur_weather != self._last_weather:
            self._last_weather = cur_weather
            if self.interaction:
                self._inject(
                    EventType.WEATHER_CHANGED, intensity=0.15,
                    description=f"天气变了：{cur_weather}")

    # ── 辅助 ──

    @staticmethod
    def _get_nearest_enemy_name(nearby: List[Dict]) -> str:
        best = None
        best_dist = float("inf")
        for n in (nearby or []):
            if (n.get("friendly", False)
                    or n.get("townNPC", n.get("town_npc", False))
                    or int(n.get("damage", 0) or 0) <= 0
                    or int(n.get("life", 0) or 0) <= 0):
                continue
            name = str(n.get("name", "") or n.get("display_name", ""))
            dist = n.get("distance", float("inf"))
            if name and (best is None or dist < best_dist):
                best_dist = dist
                best = name
        return best or ""

    def reset(self) -> None:
        self._prev_snap = {}
        self._snap_count = 0
        self._combat_damage_sum = 0
        self._combat_kill_count = 0
        self._combat_enemy_name = ""
        self._mining_ore_counts.clear()
        self._mining_baselined = False
        self._chests_seen.clear()
        self._nearby_player_names.clear()
        self._current_goal = None
        self._goal_emitted = False
        self._drowning_warned = False
        self._last_biome = ""
        self._last_time_of_day = ""
        self._last_weather = ""
        # HP 紧急 flag 重置（死亡/复活时清空）
        self._hp_crash_emitted = False
        self._low_hp_emitted = False
        self._boss_nearby_emitted = False
