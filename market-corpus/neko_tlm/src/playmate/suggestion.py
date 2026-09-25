import time


class ProactiveSuggestionTrigger:
    def __init__(self, cooldown_seconds=600):
        self._cooldown_seconds = max(0, int(cooldown_seconds or 0))
        self._last_trigger = {}

    def observe(self, awareness_data, stable_state, recent_push_count=0, recent_chat_seconds=None, now=None):
        now = now or time.time()
        data = awareness_data or {}
        if recent_push_count > 2:
            return None
        if recent_chat_seconds is not None and recent_chat_seconds < 90:
            return None
        if self._is_dangerous(data, stable_state):
            return None

        suggestion = self._build_suggestion(data, stable_state)
        if not suggestion:
            return None
        key, text, should_respond = suggestion
        if now - self._last_trigger.get(key, 0) < self._cooldown_seconds:
            return None
        self._last_trigger[key] = now
        return {"text": text, "respond": should_respond}

    def _is_dangerous(self, data, stable_state):
        if stable_state in ("combat", "danger_exploring"):
            return True
        if data.get("player_on_fire") or data.get("player_is_drowning"):
            return True
        player_health = data.get("player_health", 20) or 20
        player_max_health = data.get("player_max_health", 20) or 20
        if player_health < player_max_health * 0.5:
            return True
        for hostile in data.get("nearby_hostiles") or []:
            if hostile.get("distance", 999) < 14:
                return True
        return False

    def _build_suggestion(self, data, stable_state):
        inventory = data.get("maid_inventory") or []
        held = str(data.get("player_held_item", "")).lower()
        has_torches = self._has_any(inventory, ("torch", "soul_torch"))
        has_food = self._has_any(inventory, (
            "bread", "cooked_beef", "cooked_porkchop", "cooked_mutton", "cooked_chicken",
            "cooked_cod", "cooked_salmon", "baked_potato", "golden_carrot", "apple",
            "melon_slice", "cookie", "cake", "pumpkin_pie",
        ))

        if stable_state in ("mining", "underground_exploring"):
            if data.get("maid_is_underground") and data.get("maid_light_level", 15) < 7:
                if has_torches:
                    return "dark_torch_available", "这里偏暗，女仆背包里有火把。可以在不打断玩家的前提下，用一句很短的陪玩语气轻轻提醒要不要照亮一下。", True
                return "dark_no_torch", "这里偏暗，而且女仆背包里没有看到火把。可以用一句很短、低打扰的陪玩语气表达担心，不要催促。", True
            if "pickaxe" in held and not has_torches:
                return "mining_no_torch", "玩家像是在挖矿，但女仆背包里没看到火把。可以低频、轻轻提醒补光资源可能不多，不要像库存管理。", True

        if stable_state in ("base_building", "gathering") and inventory:
            useful_blocks = self._top_items(inventory, ("planks", "stone", "cobblestone", "dirt", "glass", "brick", "torch", "lantern"))
            if useful_blocks:
                return "build_inventory", f"玩家在{self._state_label(stable_state)}，女仆背包里有{useful_blocks}。可以自然提一句也许能帮上忙，保持一句话、低打扰。", False

        if stable_state == "fishing":
            return "fishing_wait", "玩家像是在钓鱼，适合低频来一句很轻松的陪等吐槽或小声加油，不要催促也不要打断节奏。", True

        if stable_state == "traveling":
            structures = data.get("nearby_structures") or []
            if structures:
                names = "、".join(str(s.get("name", "")).split(":")[-1] for s in structures[:2] if s.get("name"))
                if names:
                    return "travel_structure", f"玩家像是在赶路或跑图，附近有{names}。可以作为只读素材，之后自然接一句要不要去看看。", False
            return "travel_companion", "玩家像是在赶路，适合作为只读陪玩素材：女仆可以表现自己跟得上、会陪着走，不需要立刻打断。", False

        if stable_state == "organizing":
            return "organizing_quiet", "玩家像是在整理物品或背包。这里只作为只读素材，保持安静陪着，不要主动打扰。", False

        if stable_state == "mob_farming":
            return "mob_farming", "玩家刚结束一波刷怪或战斗收尾。可以作为只读素材，之后自然夸一句打得不错，不要在战斗中分散注意。", False

        player_health = data.get("player_health", 20) or 20
        player_max_health = data.get("player_max_health", 20) or 20
        if has_food and player_health < player_max_health * 0.75 and stable_state not in ("idle", "unknown"):
            return "food_available", "玩家血量不是满的，女仆背包里有食物。可以用一句很短的陪玩语气关心要不要休整一下，不要反复提醒。", False

        return None

    def _has_any(self, inventory, keywords):
        for item in inventory:
            name = str(item.get("item", "")).lower()
            if any(keyword in name for keyword in keywords):
                return True
        return False

    def _top_items(self, inventory, keywords):
        matches = []
        for item in inventory:
            name = str(item.get("item", "")).lower()
            if any(keyword in name for keyword in keywords):
                count = item.get("count", 1)
                matches.append(f"{item.get('item', '')}x{count}")
        return "、".join(matches[:3])

    def _state_label(self, stable_state):
        return {
            "base_building": "建家/布置",
            "gathering": "采集整理",
        }.get(stable_state, stable_state)
