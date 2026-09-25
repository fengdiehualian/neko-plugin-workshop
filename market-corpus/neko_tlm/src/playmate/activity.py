import time
from dataclasses import dataclass


@dataclass
class ActivityUpdate:
    state: str
    label: str
    text: str


class PlayerActivityInference:
    def __init__(self, debounce_checks=2, cooldown_seconds=120):
        self._debounce_checks = max(1, int(debounce_checks or 1))
        self._cooldown_seconds = max(0, int(cooldown_seconds or 0))
        self._stable_state = "unknown"
        self._candidate_state = "unknown"
        self._candidate_count = 0
        self._last_change_time = 0

    @property
    def stable_state(self):
        return self._stable_state

    @property
    def stable_label(self):
        return self._label(self._stable_state)

    def observe(self, awareness_data, memory=None, now=None):
        now = now or time.time()
        state = self._classify(awareness_data or {}, memory)
        if state == self._stable_state:
            self._candidate_state = state
            self._candidate_count = 0
            return None
        if state == self._candidate_state:
            self._candidate_count += 1
        else:
            self._candidate_state = state
            self._candidate_count = 1
        if self._candidate_count < self._debounce_checks:
            return None
        if now - self._last_change_time < self._cooldown_seconds:
            return None
        old_state = self._stable_state
        self._stable_state = state
        self._last_change_time = now
        label = self._label(state)
        old_label = self._label(old_state)
        text = f"玩家活动状态从{old_label}变为{label}，可以按这个阶段自然陪玩，不需要刻意打断。"
        return ActivityUpdate(state=state, label=label, text=text)

    def _classify(self, data, memory):
        evidence_state = self._recent_evidence_activity(memory)
        if evidence_state:
            return evidence_state
        if data.get("player_on_fire") or data.get("player_is_drowning"):
            return "combat"
        player_health = data.get("player_health", 20) or 20
        player_max_health = data.get("player_max_health", 20) or 20
        if player_health < player_max_health * 0.35:
            return "combat"
        if self._has_close_hostile(data):
            return "combat"
        distance = data.get("maid_player_distance")
        if distance is not None and distance > 50:
            return "away"
        held = str(data.get("player_held_item", "")).lower()
        if "fishing_rod" in held:
            return "fishing"
        dimension = str(data.get("player_dimension", ""))
        if "the_nether" in dimension:
            return "nether_exploring"
        if "the_end" in dimension:
            return "end_exploring"
        if data.get("is_underground"):
            if any(k in held for k in ("pickaxe", "torch", "ore")):
                return "mining"
            return "underground_exploring"
        if self._is_building_item(held):
            return "base_building"
        if any(k in held for k in ("pickaxe", "shovel", "axe")):
            return "gathering"
        if self._is_redstone_item(held):
            return "redstone_engineering"
        if any(k in held for k in ("sword", "bow", "crossbow", "trident", "shield")):
            return "danger_exploring"
        if self._has_inventory_context(data):
            return "organizing"
        structures = data.get("nearby_structures") or []
        if structures:
            return "exploring"
        if self._has_travel_hint(data, memory):
            return "traveling"
        if "emerald" in held and self._recent_trading_evidence(memory):
            return "trading"
        if not held:
            return "idle"
        return "unknown"

    def _has_close_hostile(self, data):
        for hostile in data.get("nearby_hostiles") or []:
            if hostile.get("distance", 999) < 12:
                return True
        return False

    def _recent_evidence_activity(self, memory):
        if not memory:
            return None
        now = time.time()
        for item in reversed(memory.recent(8)):
            if item.kind in ("player_hurt", "maid_hurt"):
                if now - item.timestamp > 45:
                    continue
                return "combat"
            if item.kind == "player_kill_entity":
                if now - item.timestamp > 90:
                    continue
                return self._kill_activity_state(item.summary)
            if item.kind == "block_activity":
                if now - item.timestamp > 120:
                    continue
                state = self._block_activity_state(item.summary)
                if state:
                    return state
            if item.kind == "container_interaction":
                if now - item.timestamp > 180:
                    continue
                return "organizing"
            if item.kind in ("fishing_start", "item_fished"):
                if now - item.timestamp > 180:
                    continue
                return "fishing"
        return None

    def _block_activity_state(self, text):
        if "倾向于挖矿" in text:
            return "mining"
        if "倾向于建造/布置" in text:
            return "base_building"
        if "倾向于采集整理" in text or "倾向于挖掘整理" in text:
            return "gathering"
        if "连续放置" in text:
            return "base_building"
        if "连续破坏" in text:
            return "gathering"
        return None

    def _kill_activity_state(self, text):
        lower = str(text or "").lower()
        mob_keywords = ("zombie", "skeleton", "creeper", "spider", "slime", "phantom", "witch", "blaze")
        if any(keyword in lower for keyword in mob_keywords):
            return "mob_farming"
        return "combat"

    def _has_inventory_context(self, data):
        held = str(data.get("player_held_item", "")).lower()
        if any(k in held for k in ("chest", "barrel", "shulker_box", "bundle")):
            return True
        inventory = data.get("maid_inventory") or []
        return len(inventory) >= 18 and not data.get("maid_is_underground")

    def _has_travel_hint(self, data, memory):
        distance = data.get("maid_player_distance")
        if distance is not None and 18 <= distance <= 50:
            held = str(data.get("player_held_item", "")).lower()
            if any(k in held for k in ("map", "compass", "elytra", "boat", "minecart")):
                return True
            if data.get("nearby_structures"):
                return True
        if not memory:
            return False
        recent_kinds = [item.kind for item in memory.recent(5)]
        return "block_activity" not in recent_kinds and "player_kill_entity" not in recent_kinds and distance is not None and distance > 24

    def _is_building_item(self, held):
        if not held:
            return False
        building_keywords = (
            "planks", "glass", "brick", "concrete", "wool", "stairs", "slab",
            "fence", "door", "trapdoor", "scaffolding",
        )
        if any(k in held for k in building_keywords):
            return True
        exact_blocks = (
            "minecraft:stone", "minecraft:cobblestone",
        )
        return held in exact_blocks

    def _is_redstone_item(self, held):
        if not held:
            return False
        redstone_keywords = (
            "redstone", "repeater", "comparator", "piston", "observer",
            "hopper", "dispenser", "dropper", "lever", "button",
            "pressure_plate", "tripwire", "daylight_detector",
        )
        return any(k in held for k in redstone_keywords)

    def _recent_trading_evidence(self, memory):
        if not memory:
            return False
        now = time.time()
        container_count = 0
        for item in memory.recent(8):
            if item.kind == "container_interaction" and now - item.timestamp < 120:
                container_count += 1
        return container_count >= 2

    def _label(self, state):
        return {
            "unknown": "未知",
            "combat": "战斗/危险探索",
            "away": "远离女仆",
            "mining": "挖矿",
            "underground_exploring": "地下探索",
            "gathering": "采集整理",
            "base_building": "建家/布置",
            "danger_exploring": "危险探索",
            "organizing": "整理物品",
            "fishing": "钓鱼",
            "traveling": "赶路/跑图",
            "mob_farming": "刷怪/战斗收尾",
            "exploring": "探索",
            "idle": "闲置",
            "redstone_engineering": "红石工程",
            "nether_exploring": "下界探索",
            "end_exploring": "末地探索",
            "trading": "村民交易",
        }.get(state, state)
