"""泰拉瑞亚原版物品/NPC id 映射，并提供 mod 扩展物品加载。"""

from typing import Dict

ITEM_IDS: Dict[str, int] = {
    # Vanilla ItemID constants verified against the tModLoader stable API.
    "dirt": 2,
    "stone": 3,
    "wood": 9,
    "gel": 23,
    "torch": 8,
    "copper_ore": 12,
    "iron_ore": 11,
    "silver_ore": 14,
    "gold_ore": 13,
    "tin_ore": 699,
    "lead_ore": 700,
    "tungsten_ore": 701,
    "platinum_ore": 702,
    "demonite_ore": 56,
    "crimtane_ore": 880,
    "meteorite": 116,
    "hellstone": 174,
    "obsidian": 173,
    "cobalt_ore": 364,
    "palladium_ore": 1104,
    "mythril_ore": 365,
    "orichalcum_ore": 1105,
    "adamantite_ore": 366,
    "titanium_ore": 1106,
    "chlorophyte_ore": 947,
    "wood_hammer": 196,
    "iron_pickaxe": 1,
    "copper_pickaxe": 3509,
    "iron_broadsword": 4,
    "iron_helmet": 90,
    "iron_chainmail": 81,
    "iron_greaves": 77,
    "workbench": 36,
    "furnace": 33,
    "iron_anvil": 35,
    "magic_mirror": 50,
    "hook": 118,
    "grappling_hook": 84,
    "copper_axe": 3506,
    "iron_axe": 10,
    "silver_axe": 3512,
    "gold_axe": 3518,
    "wood_fishing_pole": 2289,
    "reinforced_fishing_pole": 2291,
    "iron_bar": 22,
    "copper_bar": 20,
    "silver_bar": 21,
    "gold_bar": 19,
    "tin_bar": 703,
    "lead_bar": 704,
    "tungsten_bar": 705,
    "platinum_bar": 706,
    "copper_coin": 71,
    "silver_coin": 72,
    "gold_coin": 73,
    "platinum_coin": 74,
}

NPC_IDS: Dict[str, int] = {
    "slime": 1, "blue_slime": 1, "green_slime": 2, "red_slime": 3,
    "skeleton": 21, "zombie": 22, "demon_eye": 23, "eye_of_cthulhu": 4,
    "king_slime": 50, "eater_of_worlds": 13, "brain_of_cthulhu": 266,
    "queen_bee": 222, "skeletron": 35, "wall_of_flesh": 113,
    "goblin_army": 100, "boss": 4,
}

# ── 矿石：物品名 → (ItemID, TileID) ──
# C# find_ore 用 tile_type 过滤（TileID），而 item_id() 返回物品 ID，
# 两者编号不同（铁矿 ItemID=11 / TileID=7）——不能混用。此为 find_ore 专用映射。
ORE_ITEM_TO_TILE: Dict[int, int] = {
    12: 6,     # 铜矿
    699: 166,  # 锡矿
    11: 7,     # 铁矿
    700: 167,  # 铅矿
    14: 8,     # 银矿
    701: 168,  # 钨矿
    13: 9,     # 金矿
    702: 169,  # 铂金矿
    116: 37,   # 陨铁矿
    56: 22,    # 魔矿
    880: 204,  # 猩红矿
    173: 56,   # 黑曜石
    174: 58,   # 狱石
    364: 107,  # 钴矿
    1104: 221, # 钯金矿
    365: 108,  # 秘银矿
    1105: 222, # 山铜矿
    366: 111,  # 精金矿
    1106: 223, # 钛金矿
    947: 211,  # 叶绿矿
}

# 中文矿石名 → TileID（供 find_ore 按目标矿定位）
ORE_NAME_TILE: Dict[str, int] = {
    "铜矿": 6, "锡矿": 166, "铁矿": 7, "铅矿": 167, "银矿": 8, "钨矿": 168,
    "金矿": 9, "铂金矿": 169, "铂金": 169, "陨铁矿": 37, "陨铁": 37,
    "魔矿": 22, "猩红矿": 204, "黑曜石": 56, "狱石": 58, "钴矿": 107,
    "钯金矿": 221, "秘银矿": 108, "山铜矿": 222, "精金矿": 111,
    "钛金矿": 223, "叶绿矿": 211,
}

NAME_TO_ITEM: Dict[int, str] = {v: k for k, v in ITEM_IDS.items()}


def item_id(name: str, registry=None) -> int:
    """Resolve one exact item; ambiguous names never select a random mod item."""
    name = str(name or "").strip()
    if not name:
        return -1
    if registry is not None:
        rid = registry.resolve(name)
        if rid > 0 or rid == -2:
            return rid
    from .recipe_book import CN_EN
    english = CN_EN.get(name, name)
    if registry is not None:
        rid = registry.resolve(english)
        if rid > 0 or rid == -2:
            return rid
        if registry.live:
            # A complete live registry is authoritative, including absent items.
            return -1
    if english.casefold().startswith("terraria/"):
        english = english.split("/", 1)[1]
    key = "".join(english.casefold().replace("_", "").split())
    aliases = {"dirtblock": "dirt", "stoneblock": "stone", "woodenhammer": "wood_hammer"}
    key = aliases.get(key, key)
    return next((iid for n, iid in ITEM_IDS.items() if n.replace("_", "") == key.replace("_", "")), -1)


def tile_type_of(name: str, iid: int = -1, registry=None) -> int:
    """Return a TileID, or -1 for unknown. Never turn unknown into an unfiltered scan."""
    if registry is not None and registry.live:
        resolved = item_id(name, registry)
        if resolved <= 0 or (iid > 0 and resolved != iid):
            return -1
        return int(registry.describe(str(resolved)).get("create_tile", -1))
    if iid > 0:
        return ORE_ITEM_TO_TILE.get(iid, -1)
    iid = item_id(name)
    return ORE_ITEM_TO_TILE.get(iid, ORE_NAME_TILE.get((name or "").strip(), -1))


def npc_id(name: str) -> int:
    return NPC_IDS.get(name.lower(), -1)


def item_name(iid: int) -> str:
    return NAME_TO_ITEM.get(iid, f"item_{iid}")


def load_mod_items(path: str) -> Dict[str, Dict[str, int]]:
    import json
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
