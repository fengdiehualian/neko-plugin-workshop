"""钓鱼小游戏 —— 静态数据（鱼竿/鱼饵/商店/价格）。"""

# ── 基础参数 ──────────────────────────────
BASE_CATCH_RATE = 0.20          # 基础上鱼率
DEFAULT_TANK_CAPACITY = 5        # 鱼缸初始容量
TANK_UPGRADE_SIZE = 5            # 每次升级 +5
TANK_UPGRADE_EXTRA_CASTS = 2     # 每级鱼缸 +2 每日钓次
TANK_UPGRADE_COST_BASE = 300     # 升级基础花费
TANK_UPGRADE_COST_GROWTH = 1.6   # 升级花费增长
MAX_TANK_LEVEL = 10
DAILY_CASTS_BASE = 5             # 每日基础钓次

# ── 稀有度 ──────────────────────────────
RARITY_ORDER = ["common", "uncommon", "rare", "epic", "legendary", "？"]

RARITY_LABELS = {
    "common": "常见",
    "uncommon": "稀有",
    "rare": "珍稀",
    "epic": "史诗",
    "legendary": "传说",
    "？": "？？？",
}

# 出售价格区间（legendary/彩蛋不可售，仅收藏）
RARITY_SELL_LIMITS = {
    "common": (35, 80),
    "uncommon": (75, 165),
    "rare": (150, 330),
    "epic": (280, 620),
    "legendary": (0, 0),
    "？": (0, 0),
}

UNSELLABLE = {"legendary", "？"}

# ── 鱼竿 ──────────────────────────────
RODS = {
    "starter": {
        "id": "starter", "name": "新手竹竿", "price": 0,
        "catchRateBonus": 0.0, "failProtection": 0.0, "rarityBias": {},
        "description": "竹节泛着淡青色，竿尾缠着粗麻线，像刚从河岸边削好。",
    },
    "quick": {
        "id": "quick", "name": "疾风短竿", "price": 120,
        "catchRateBonus": 0.0094, "failProtection": 0.08,
        "rarityBias": {"common": 0.028, "uncommon": 0.018, "rare": -0.012, "epic": -0.012, "legendary": -0.004},
        "description": "短竿细窄，竿身有几道像风纹一样的浅刻，握柄轻得像一截羽骨。",
    },
    "steady": {
        "id": "steady", "name": "稳钓重竿", "price": 320,
        "catchRateBonus": -0.0164, "failProtection": 0.16, "rarityBias": {},
        "description": "竿身厚重乌亮，铜色配重环一圈圈压在尾节上。",
    },
    "hunter": {
        "id": "hunter", "name": "猎珍长竿", "price": 500,
        "catchRateBonus": -0.0319, "failProtection": 0.10,
        "rarityBias": {"common": -0.05, "uncommon": -0.025, "rare": 0.042, "epic": 0.028, "legendary": 0.01},
        "description": "长竿线轮深黑，竿梢嵌着一点冷银，整体像一支拉长的猎矛。",
    },
}

# ── 鱼饵 ──────────────────────────────
BAITS = {
    "plain": {
        "id": "plain", "name": "清水团饵", "price": 0, "isDefault": True, "packSize": 0,
        "catchRateBonus": 0.0, "rarityBias": {},
        "description": "清水色饵团圆润透明，表面只挂着一层淡淡水光。",
    },
    "special_bait": {
        "id": "special_bait", "name": "香谷鱼饵", "price": 45, "packSize": 3,
        "catchRateBonus": 0.1558,
        "rarityBias": {"common": 0.055, "uncommon": 0.03, "rare": -0.025, "epic": -0.01, "legendary": -0.003},
        "description": "浅米色饵团夹着碎谷壳，捏开时有一圈松散的细纹。",
    },
    "deep_bait": {
        "id": "deep_bait", "name": "沉流鱼饵", "price": 54, "packSize": 2,
        "catchRateBonus": 0.1148,
        "rarityBias": {"common": -0.075, "uncommon": -0.035, "rare": 0.045, "epic": 0.028, "legendary": 0.012},
        "description": "深青色饵团沉沉发暗，边缘像被水流磨成钝圆。",
    },
}

# ── 鱼缸升级花费（每级） ──────────────
def tank_upgrade_cost(level: int) -> int:
    """从 level 升到 level+1 的花费。"""
    return int(TANK_UPGRADE_COST_BASE * (TANK_UPGRADE_COST_GROWTH ** (level - 1)))
