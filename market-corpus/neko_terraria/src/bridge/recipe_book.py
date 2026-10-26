"""配方书：把 mod 的真实配方收进来，供合成推演使用。

为什么需要它：
    mod 物品（灾厄/瑟银/各种整合包）的配方只有游戏里才知道，
    写死在 Python 里的常识表根本覆盖不到。没有真实配方，
    猫娘对着一件 mod 装备只会说"我搞不到"，推演就废了。

它负责三件事：
    1. 向 mod 要全量配方（含材料、合成站、来源 mod），落盘缓存
    2. 名字对得上：mod 回的是英文名，主人说的是中文，要能互相认
    3. 反查：某个材料能做出什么、某件东西缺哪一步

缓存放在 data/recipes/ 下，与 mod_items 同级，换整合包会自动重建。
"""

import asyncio
import copy
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 常见原版物品中英对照，帮主人的中文对上 mod 回的英文名。
# 模组物品使用游戏注册表中的本地化名称和完整内部名称。
CN_EN: Dict[str, str] = {
    "铁矿": "Iron Ore", "铜矿": "Copper Ore", "银矿": "Silver Ore",
    "金矿": "Gold Ore", "锡矿": "Tin Ore", "铅矿": "Lead Ore",
    "钨矿": "Tungsten Ore", "铂金矿": "Platinum Ore",
    "铁锭": "Iron Bar", "铜锭": "Copper Bar", "银锭": "Silver Bar",
    "金锭": "Gold Bar", "锡锭": "Tin Bar", "铅锭": "Lead Bar",
    "钨锭": "Tungsten Bar", "铂金锭": "Platinum Bar",
    "铁镐": "Iron Pickaxe", "铜镐": "Copper Pickaxe",
    "银镐": "Silver Pickaxe", "金镐": "Gold Pickaxe",
    "木材": "Wood", "石块": "Stone Block", "土块": "Dirt Block",
    "火把": "Torch", "工作台": "Work Bench", "熔炉": "Furnace",
    "铁砧": "Iron Anvil", "抓钩": "Grappling Hook", "绳": "Rope",
    "恶魔石": "Demonite Ore", "魔矿": "Demonite Ore", "陨石": "Meteorite",
    "陨铁矿": "Meteorite", "陨石矿": "Meteorite", "猩红矿": "Crimtane Ore",
    "钴矿": "Cobalt Ore", "钯金矿": "Palladium Ore", "秘银矿": "Mythril Ore",
    "山铜矿": "Orichalcum Ore", "精金矿": "Adamantite Ore", "钛金矿": "Titanium Ore",
    "叶绿矿": "Chlorophyte Ore", "狱石": "Hellstone", "黑曜石": "Obsidian",
    "凝胶": "Gel", "木头": "Wood", "石头": "Stone Block", "泥土": "Dirt Block",
    "铜斧": "Copper Axe", "铁斧": "Iron Axe", "银斧": "Silver Axe", "金斧": "Gold Axe",
    "木钓竿": "Wood Fishing Pole", "铁钓竿": "Reinforced Fishing Pole",
}
EN_CN: Dict[str, str] = {v: k for k, v in CN_EN.items()}

# 合成站中文名
STATION_CN: Dict[str, str] = {
    "Work Bench": "工作台", "Furnace": "熔炉", "Iron Anvil": "铁砧",
    "Mythril Anvil": "秘银砧", "Adamantite Forge": "精金熔炉",
    "Hellforge": "地狱熔炉", "Bottle": "瓶子", "Table": "桌子",
    "Cooking Pot": "锅", "Loom": "织布机", "Sawmill": "锯木机",
    "Tinkerer's Workshop": "工匠作坊", "Alchemy Table": "炼药桌",
}

CACHE_TTL = 3600.0


class Recipe:
    """Keep the wire identity and all requirements; quantities are per batch."""

    def __init__(self, data: Dict[str, Any]) -> None:
        self.data = copy.deepcopy(data)
        self.name = str(data.get("name", ""))
        self.full_name = str(data.get("full_name", ""))
        self.item_id = int(data.get("item_id", -1))
        self.recipe_index = int(data.get("recipe_index", -1))
        self.amount = int(data.get("amount", 1))
        if self.item_id <= 0 or self.amount <= 0 or self.recipe_index < 0:
            raise ValueError("Invalid recipe output")
        self.mod = data.get("mod", "Terraria")
        self.ingredients = copy.deepcopy(data.get("materials", []) or [])
        if any(int(m.get("stack", 0)) <= 0 or int(m.get("id", -1)) <= 0
               for m in self.ingredients):
            raise ValueError("Invalid ingredient")
        self.materials = [(m.get("full_name") or m.get("name", ""), int(m["stack"]))
                          for m in self.ingredients]
        self.stations = [st.get("name", "") if isinstance(st, dict) else str(st)
                         for st in data.get("stations", []) or []]
        self.conditions = copy.deepcopy(data.get("conditions", []) or [])
        self.available = data.get("available") is True
        self.environment_ready = data.get("environment_ready") is True

    def batches(self, amount: int) -> int:
        return (max(0, amount) + self.amount - 1) // self.amount

    def requirements(self, amount: int, inventory) -> Tuple[List[Tuple[str, int]], List[Tuple[str, int]]]:
        """Reserve shared ingredients once; accept mixed stacks from recipe groups."""
        trial = inventory.copy()
        takes, missing = [], []
        for ingredient in self.ingredients:
            need = int(ingredient["stack"]) * self.batches(amount)
            options = [ingredient, *(ingredient.get("alternatives", []) or [])]
            seen = set()
            for option in options:
                try:
                    iid = int(option.get("id", -1))
                except (TypeError, ValueError, AttributeError):
                    continue
                if iid <= 0:
                    continue
                if iid in seen:
                    continue
                seen.add(iid)
                # Inventory snapshots are keyed by the wire item ID.  Names
                # can be localized or collide across mods, so never use them
                # to decide whether a grouped ingredient is available.
                take = min(need, trial.count_id(iid))
                if take:
                    trial.take(f"id:{iid}", take)
                    takes.append((f"id:{iid}", take))
                    need -= take
                if not need:
                    break
            if need:
                name = ingredient.get("full_name") or ingredient.get("name", "")
                missing.append((name, need))
        return takes, missing

    def is_modded(self) -> bool:
        return self.mod not in ("", "Terraria")

    def say(self) -> str:
        materials = []
        for ingredient in self.ingredients:
            names = [ingredient.get("name", ""),
                     *(a.get("name", "") for a in ingredient.get("alternatives", []) or [])]
            label = "/".join(dict.fromkeys(cn_name(n) for n in names if n))
            materials.append(f"{label}x{ingredient['stack']}")
        text = f"{cn_name(self.name)}x{self.amount} = " + "、".join(materials)
        if self.stations:
            text += "（要" + "、".join(station_cn(s) for s in self.stations) + "）"
        if self.conditions:
            text += "；条件：" + "、".join(c.get("name", "") for c in self.conditions)
        return text

    def snapshot(self) -> Dict[str, Any]:
        return dict(copy.deepcopy(self.data), cn=cn_name(self.name),
                    available=self.available, environment_ready=self.environment_ready)


def cn_name(en: str) -> str:
    return EN_CN.get(en, en)


def station_cn(en: str) -> str:
    return STATION_CN.get(en, en)


def _norm(s: str) -> str:
    return "".join(str(s or "").casefold().replace("_", "").split())


class RecipeBook:
    def __init__(self, agent, base_dir: str = "") -> None:
        self.agent = agent
        self._by_name = {}
        self._by_id = {}
        self._by_material = {}
        self._recipes = []
        self._loaded_at = 0.0
        self._lock = asyncio.Lock()
        self.live = False
        base = Path(base_dir) if base_dir else Path(__file__).resolve().parent.parent
        self.cache_file = base / "data" / "recipes" / "recipes.json"

    def invalidate(self) -> None:
        self.live = False
        self._loaded_at = 0.0
        for recipe in self._recipes:
            recipe.available = recipe.environment_ready = False

    def _index(self, recipes: List[Recipe]) -> None:
        self._recipes = recipes
        self._by_name, self._by_id, self._by_material = {}, {}, {}
        for recipe in recipes:
            self._by_id.setdefault(recipe.item_id, []).append(recipe)
            for name in {recipe.name, recipe.full_name, cn_name(recipe.name)} - {""}:
                self._by_name.setdefault(_norm(name), []).append(recipe)
            for ing in recipe.ingredients:
                for option in [ing, *(ing.get("alternatives", []) or [])]:
                    for name in {option.get("name", ""), option.get("full_name", ""),
                                 str(option.get("id", ""))} - {""}:
                        hits = self._by_material.setdefault(_norm(name), [])
                        if recipe not in hits:
                            hits.append(recipe)

    def count(self) -> int:
        return len(self._recipes)

    def loaded(self) -> bool:
        return self.live

    async def refresh(self, force: bool = False) -> int:
        async with self._lock:
            if not force and self.live and time.monotonic() - self._loaded_at < CACHE_TTL:
                return self.count()
            try:
                registry = getattr(self.agent, "registry", None)
                if registry is not None and not registry.live:
                    registry.sync_from_enum(await self.agent.mod.enum_items())
                    if not registry.live:
                        raise ValueError("Item registry synchronization failed")
                raw = await self.agent.mod.get_recipes("all")
                if not isinstance(raw, list):
                    raise ValueError("Recipe synchronization failed")
                # A mod pack can expose an obsolete/partial recipe while the
                # rest of the registry is valid.  One malformed entry must
                # not discard every recipe and make all crafting impossible.
                recipes = []
                for data in raw:
                    if not isinstance(data, dict):
                        continue
                    try:
                        recipes.append(Recipe(data))
                    except (TypeError, ValueError, KeyError):
                        continue
                if not recipes and raw:
                    raise ValueError("No valid recipes in synchronization response")
            except Exception:
                self.invalidate()
                # Disk data is descriptive only: mod IDs and recipe indexes change between sessions.
                return 0
            self._index(recipes)
            self.live = True
            self._loaded_at = time.monotonic()
            self._save(raw)
            return self.count()

    def _save(self, raw: List[Dict[str, Any]]) -> None:
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    async def refresh_availability(self) -> bool:
        """Refresh dynamic flags without transferring the full recipe book on every action."""
        if not self.live:
            await self.refresh()
        if not self.live:
            return False
        try:
            status = await self.agent.mod.get_recipe_status()
            if status is None:
                raise ValueError("Recipe status unavailable")
            available = set(status["available"])
            environment = set(status["environment_ready"])
            for recipe in self._recipes:
                recipe.available = recipe.recipe_index in available
                recipe.environment_ready = recipe.recipe_index in environment
            return True
        except Exception:
            # A transient status request failure must not turn every known
            # recipe into an apparent impossibility.  The full recipe payload
            # carries the last authoritative flags; retain those flags and
            # let the executor perform its own craft/inventory verification.
            # Clearing them here caused a brief TCP hiccup to make the planner
            # report "没有合成环境" for unrelated items.
            return False

    def _lookup(self, item) -> List[Recipe]:
        if not self.live:
            return []
        from .item_npc_dict import item_id
        iid = item_id(item, getattr(self.agent, "registry", None))
        if iid > 0:
            return self._by_id.get(iid, [])
        if iid == -2:
            return []
        hits = self._by_name.get(_norm(item), [])
        if not hits:
            hits = self._by_name.get(_norm(CN_EN.get(str(item), "")), [])
        return hits if len({r.item_id for r in hits}) == 1 else []

    def find(self, item, inventory=None, amount: int = 1, recipe_index=None) -> Optional[Recipe]:
        candidates = self._lookup(item)
        if recipe_index is not None:
            return next((r for r in candidates if r.recipe_index == recipe_index), None)
        if not candidates:
            return None
        def rank(recipe):
            missing = recipe.requirements(amount, inventory)[1] if inventory is not None else []
            return (not recipe.environment_ready, bool(missing), sum(n for _, n in missing),
                    not recipe.available)
        return min(candidates, key=rank)

    def find_all(self, item: str) -> List[Recipe]:
        return list(self._lookup(item))

    def materials_of(self, item: str) -> List[Tuple[str, int]]:
        recipe = self.find(item)
        return list(recipe.materials) if recipe else []

    def stations_of(self, item: str) -> List[str]:
        recipe = self.find(item)
        return list(recipe.stations) if recipe else []

    def used_in(self, material: str) -> List[Recipe]:
        from .item_npc_dict import item_id
        if not self.live:
            return []
        iid = item_id(material, getattr(self.agent, "registry", None))
        if iid == -2:
            return []
        return list(self._by_material.get(str(iid) if iid > 0 else _norm(material), []))

    def is_craftable(self, item: str) -> bool:
        return self.find(item) is not None
