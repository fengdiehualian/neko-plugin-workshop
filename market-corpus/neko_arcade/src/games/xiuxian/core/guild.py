"""宗门系统 = 单机洞府(多人宗门降维为单人经营养成子系统)。

核心策略(采纳单人适配设计):
1. 懒结算：资源产出按 last_settle_time 时间差计算，查询时才结算(无后台定时任务)
2. 宗门仓库 = 第二背包：单人无权限冲突，随时存取
3. 宗门贡献 = 灌注经验：消耗个人 exp 按比例转化为宗门灵石(无竞争，纯单机养成)
4. members 永远只有 1 个真实 user_id(猫娘/NPC 为挂名成员)
5. 药田生长用 harvest_time 懒结算，到期自动可收

存储：guild_{guild_id} 独立于 player_{user_id}，经插件 store 持久化。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# 懒结算速率：每级宗门每 10 分钟产出灵石数
SPIRIT_STONE_PER_10MIN = 50
# 药田生长周期(秒)
HERB_GROW_SECONDS = 300
# 经验灌注比率：1 exp → 多少灵石(平衡: 过高会让"修炼→贡献"成为最佳灵石来源,
# 碾压打劫/秘境/签到/任务; 调低后只作"溢出修为换灵石"的温和通道)
EXP_TO_STONE_RATIO = 2
# 宗门仓库容量上限(第二背包)
STORE_CAPACITY = 50

STORE_PREFIX = "xiuxian_guild"


@dataclass
class GuildSave:
    """宗门存档(单机洞府)。"""

    guild_id: str
    name: str
    level: int = 1
    members: List[str] = field(default_factory=list)       # [user_id, ...] 单人=1个
    spirit_stones: int = 0                                  # 灵石池(懒结算)
    store: Dict[str, int] = field(default_factory=dict)     # 宗门仓库(第二背包) {item: count}
    facilities: Dict[str, int] = field(
        default_factory=lambda: {"main_hall": 1, "alchemy_room": 0, "spirit_array": 0})
    herb_field: Optional[Dict[str, Any]] = None             # {plant, harvest_time, yield_total}
    last_settle_time: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]],
                  guild_id: str = "") -> "GuildSave":
        if not data:
            return cls(guild_id=guild_id)
        known = {f.name for f in cls.__dataclass_fields__.values()}
        kwargs = {k: v for k, v in data.items() if k in known}
        kwargs["guild_id"] = kwargs.get("guild_id") or guild_id
        return cls(**kwargs)

    def store_count(self) -> int:
        return sum(self.store.values())

    def can_store(self, count: int = 1) -> bool:
        return self.store_count() + count <= STORE_CAPACITY


class GuildStore:
    """guild_{guild_id} 持久化(独立于 player 存档)。"""

    def __init__(self, game: Any) -> None:
        self.game = game
        self._cache: Dict[str, GuildSave] = {}

    async def load(self, guild_id: str) -> Optional[GuildSave]:
        if guild_id in self._cache:
            return self._cache[guild_id]
        data = await self.game.get_user_data(f"{STORE_PREFIX}:{guild_id}", None)
        if not data:
            return None
        guild = GuildSave.from_dict(data, guild_id=guild_id)
        self._cache[guild_id] = guild
        return guild

    async def save(self, guild: GuildSave) -> None:
        self._cache[guild.guild_id] = guild
        await self.game.save_user_data(f"{STORE_PREFIX}:{guild.guild_id}",
                                       guild.to_dict())

    async def load_or_create(self, guild_id: str, name: str,
                             owner: str) -> GuildSave:
        guild = await self.load(guild_id)
        if guild is None:
            guild = GuildSave(guild_id=guild_id, name=name, members=[owner])
            await self.save(guild)
        return guild

    def drop(self, guild_id: str) -> None:
        self._cache.pop(guild_id, None)


class GuildManager:
    """宗门中间件：由 user_id 解析 guild_id，处理宗门指令(懒结算/第二背包/灌注)。"""

    def __init__(self, game: Any) -> None:
        self.game = game
        self.store = GuildStore(game)

    # ── 懒结算(核心) ──────────────────────

    def settle(self, guild: GuildSave) -> int:
        """按时间差结算宗门灵石产出(每级每10分钟 SPIRIT_STONE_PER_10MIN)。
        返回本次新增灵石数。"""
        now = time.time()
        elapsed_min = (now - guild.last_settle_time) / 600.0
        if elapsed_min < 1:
            return 0
        gain = int(elapsed_min * SPIRIT_STONE_PER_10MIN * guild.level)
        if gain > 0:
            guild.spirit_stones += gain
            guild.last_settle_time = now
        return gain

    # ── 宗门贡献 = 灌注经验 ───────────────

    async def contribute_exp(self, guild: GuildSave, exp: int) -> int:
        """消耗 exp 灌注宗门，产出灵石(1 exp → EXP_TO_STONE_RATIO 灵石)。"""
        gain = int(exp * EXP_TO_STONE_RATIO)
        guild.spirit_stones += gain
        await self.store.save(guild)
        return gain

    # ── 宗门仓库 = 第二背包 ───────────────

    async def store_put(self, guild: GuildSave, item: str, count: int = 1) -> bool:
        if count <= 0 or not guild.can_store(count):
            return False
        guild.store[item] = guild.store.get(item, 0) + count
        await self.store.save(guild)
        return True

    async def store_take(self, guild: GuildSave, item: str,
                         count: int = 1) -> int:
        cur = guild.store.get(item, 0)
        take = min(count, cur)
        if take <= 0:
            return 0
        guild.store[item] = cur - take
        if guild.store[item] <= 0:
            del guild.store[item]
        await self.store.save(guild)
        return take

    # ── 药田(懒结算生长) ─────────────────

    async def plant_herb(self, guild: GuildSave, plant: str) -> bool:
        if guild.herb_field and guild.herb_field.get("harvest_time", 0) > time.time():
            return False  # 药田还在生长
        guild.herb_field = {
            "plant": plant,
            "harvest_time": time.time() + HERB_GROW_SECONDS,
            "yield_total": 10 + guild.level * 5,
        }
        await self.store.save(guild)
        return True

    async def harvest_herb(self, guild: GuildSave) -> Optional[Dict[str, Any]]:
        hf = guild.herb_field
        if not hf:
            return None
        if hf.get("harvest_time", 0) > time.time():
            return {"ready": False, "plant": hf["plant"],
                    "remain": int(hf["harvest_time"] - time.time())}
        guild.herb_field = None
        await self.store.save(guild)
        return {"ready": True, "plant": hf["plant"], "yield": hf["yield_total"]}
