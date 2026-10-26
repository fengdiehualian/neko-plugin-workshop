"""玩家存档模型：境界/属性/资源/背包/关系，以及存档读写。

数据体系(练气+炼体双轨、灵石、纳戒、关系)，单人化：
关系字段只面向猫娘(Neko)与 NPC。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .realm import RealmSystem

DEFAULT_BASE_HP = 100
DEFAULT_BASE_ATTACK = 10
DEFAULT_BASE_DEFENSE = 5
DEFAULT_BASE_MANA = 10

# 猫娘虚拟玩家 id
NEKO_ID = "neko"


@dataclass
class PlayerSave:
    """一个修仙者存档(玩家或猫娘共用)。"""

    user_id: str
    name: str = "无名散修"
    gender: str = "未知"
    realm_idx: int = 0          # 练气境界索引
    body_idx: int = 0           # 炼体境界索引
    exp: int = 0                # 修为(经验)
    hp: int = DEFAULT_BASE_HP   # 当前气血
    max_hp: int = DEFAULT_BASE_HP
    mana: int = DEFAULT_BASE_MANA
    max_mana: int = DEFAULT_BASE_MANA
    attack: int = DEFAULT_BASE_ATTACK
    defense: int = DEFAULT_BASE_DEFENSE
    crit_rate: float = 0.05
    lingshi_low: int = 100      # 下品灵石
    lingshi_mid: int = 0        # 中品灵石
    lingshi_high: int = 0       # 上品灵石
    bag: Dict[str, int] = field(default_factory=dict)   # {item_id: count}
    sect: str = ""              # 宗门 id(""=无)
    daolv: str = ""             # 道侣 id("neko"=猫娘)
    master: str = ""            # 师父 id
    disciples: List[str] = field(default_factory=list)
    qinmidu: int = 0            # 与猫娘亲密度
    sign_days: int = 0
    last_sign: str = ""         # YYYY-MM-DD
    achievements: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    extra: Dict[str, Any] = field(default_factory=dict)  # 内景地/元神/法身等扩展

    # ── 序列化 ─────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]],
                  user_id: str = "") -> "PlayerSave":
        if not data:
            return cls(user_id=user_id)
        known = {f.name for f in cls.__dataclass_fields__.values()}
        kwargs = {k: v for k, v in data.items() if k in known}
        kwargs["user_id"] = kwargs.get("user_id") or user_id
        # 显式给 extra 兜底(避免 asdict 里缺键)
        if "extra" not in kwargs:
            kwargs["extra"] = {}
        return cls(**kwargs)

    # ── 属性计算(境界加成) ─────────────────

    def refresh_stats(self) -> None:
        """按境界重算属性(绝对值)：练气(主轨) + 炼体(体修叠加)。"""
        rs = RealmSystem.realm_stats(self.realm_idx)
        bs = RealmSystem.body_stats(self.body_idx)
        self.max_hp = rs["max_hp"] + bs["max_hp"]
        self.attack = rs["attack"] + bs["attack"]
        self.defense = rs["defense"] + bs["defense"]
        self.max_mana = rs.get("max_mana", 0) + self.realm_idx * 200
        self.crit_rate = max(rs.get("crit_rate", 0.01),
                             bs.get("crit_rate", 0.01))
        self.hp = min(self.hp or self.max_hp, self.max_hp)
        self.mana = min(self.mana or self.max_mana, self.max_mana)

    def realm_name(self) -> str:
        return RealmSystem.realm_name(self.realm_idx)

    def body_name(self) -> str:
        return RealmSystem.body_name(self.body_idx)

    def lingshi_total(self) -> int:
        """灵石统一折算(1中品=100下品, 1上品=10000下品)。"""
        return (self.lingshi_low
                + self.lingshi_mid * 100
                + self.lingshi_high * 10000)

    def add_lingshi(self, amount: int) -> None:
        """加减灵石(自动向上品折算),允许负数。"""
        total = self.lingshi_total() + amount
        if total < 0:
            total = 0
        self.lingshi_high, rem = divmod(total, 10000)
        self.lingshi_mid, self.lingshi_low = divmod(rem, 100)

    # ── 关系 ───────────────────────────────

    @property
    def is_daolv(self) -> bool:
        return self.daolv == NEKO_ID

    @property
    def has_sect(self) -> bool:
        return bool(self.sect)

    def snapshot(self) -> Dict[str, Any]:
        """给面板/卡片用的状态快照。"""
        return {
            "name": self.name,
            "gender": self.gender,
            "realm": self.realm_name(),
            "body": self.body_name(),
            "realm_idx": self.realm_idx,
            "body_idx": self.body_idx,
            "exp": self.exp,
            "hp": f"{self.hp}/{self.max_hp}",
            "attack": self.attack,
            "defense": self.defense,
            "lingshi": self.lingshi_total(),
            "sect": self.sect or "无",
            "daolv": self.daolv or "无",
            "qinmidu": self.qinmidu,
            "sign_days": self.sign_days,
        }


class PlayerStore:
    """玩家存档读写：经插件 store 持久化，带内存缓存。"""

    def __init__(self, game: Any) -> None:
        self.game = game
        self._cache: Dict[str, PlayerSave] = {}

    async def load(self, user_id: str) -> PlayerSave:
        if user_id in self._cache:
            return self._cache[user_id]
        data = await self.game.get_user_data(user_id, None)
        save = PlayerSave.from_dict(data, user_id=user_id)
        if data is None:
            # 无存档 → created_at 置 0 标记「未踏入仙途」，
            # 否则 dataclass 默认 time.time() 会让 _h_start 误判 already。
            save.created_at = 0
        save.refresh_stats()
        self._cache[user_id] = save
        return save

    async def save(self, save: PlayerSave) -> None:
        save.updated_at = time.time()
        self._cache[save.user_id] = save
        await self.game.save_user_data(save.user_id, save.to_dict())

    def drop(self, user_id: str) -> None:
        self._cache.pop(user_id, None)

    def clear_cache(self) -> None:
        self._cache.clear()
