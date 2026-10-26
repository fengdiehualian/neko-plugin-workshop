"""猫娘陪玩模型：单人修仙的唯一"活"伴侣。

三合一角色：
- 道侣(daolv)：亲密度达标后缔结，主陪玩身份
- 徒弟(shitu)：玩家收猫娘为徒(或拜猫娘为师)
- 宗门成员(sect)：猫娘随玩家入宗/与玩家共建宗门

猫娘自身的修为/战力从玩家存档推导(随玩家成长)，保证"陪玩"数值始终贴合。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .player import NEKO_ID, PlayerSave
from .realm import REALMS


class NekoCompanion:
    """猫娘虚拟伴侣。"""

    def __init__(self, game: Any, config: Optional[Dict[str, Any]] = None) -> None:
        self.game = game
        cfg = config or {}
        self.enabled = cfg.get("enabled", True)
        self.name = cfg.get("name", "喵喵")
        self.realm_bonus = cfg.get("realm_bonus", 0.1)      # 猫娘境界 = 玩家境界×(1+bonus)
        self.daolv_enabled = cfg.get("daolv_enabled", True)
        self.shitu_enabled = cfg.get("shitu_enabled", True)
        self.sect_enabled = cfg.get("sect_enabled", True)
        self.qinmidu_per_gift = cfg.get("qinmidu_per_gift", 60)
        self.gift_item = cfg.get("gift_item", "百合花篮")

    # ── 猫娘自身的虚拟存档(从玩家推导) ─────

    def compute_save(self, player: PlayerSave) -> PlayerSave:
        """猫娘 = 玩家境界+1(名称好看), 属性 = 玩家 × (1+realm_bonus)。

        以玩家自身属性为基准缩放(而非境界表的绝对数值)，保证各境界期
        猫娘都是"并肩作战不掉队"的 1.1 倍伙伴，避免低境界期数值爆炸。
        """
        neko = PlayerSave(user_id=NEKO_ID, name=self.name)
        neko.realm_idx = min(
            player.realm_idx + 1,
            len(REALMS) - 1,
        )
        neko.body_idx = player.body_idx
        neko.exp = player.exp
        scale = 1.0 + self.realm_bonus
        neko.max_hp = int(player.max_hp * scale)
        neko.attack = int(player.attack * scale)
        neko.defense = int(player.defense * scale)
        neko.max_mana = int(player.max_mana * scale)
        neko.hp = neko.max_hp
        neko.mana = neko.max_mana
        neko.crit_rate = 0.10
        return neko

    def battle_stats(self, player: PlayerSave) -> Dict[str, int]:
        """猫娘的战斗属性(用于双人战/团本/宗门战助阵)。"""
        s = self.compute_save(player)
        return {
            "attack": s.attack,
            "defense": s.defense,
            "max_hp": s.max_hp,
            "hp": s.hp,
            "crit_rate": 0.10,
            "realm": s.realm_name(),
        }

    def assist_bonus(self) -> float:
        """猫娘护法加成(突破成功率)。"""
        return self.realm_bonus if self.enabled else 0.0

    # ── 三合一关系判断(基于玩家存档) ───────

    @staticmethod
    def is_daolv(player: PlayerSave) -> bool:
        return player.daolv == NEKO_ID

    @staticmethod
    def is_disciple(player: PlayerSave) -> bool:
        """玩家收猫娘为徒。"""
        return NEKO_ID in player.disciples

    @staticmethod
    def is_master(player: PlayerSave) -> bool:
        """玩家拜猫娘为师。"""
        return player.master == NEKO_ID

    @staticmethod
    def is_sect_member(player: PlayerSave) -> bool:
        """猫娘随玩家在宗门。"""
        return bool(player.sect) and player.sect != ""

    def relationship_summary(self, player: PlayerSave) -> Dict[str, bool]:
        return {
            "daolv": self.is_daolv(player),
            "disciple": self.is_disciple(player),
            "master": self.is_master(player),
            "sect_member": self.is_sect_member(player),
            "qinmidu": player.qinmidu,
        }

    # ── 亲密度操作 ─────────────────────────

    def add_qinmidu(self, player: PlayerSave, delta: int) -> int:
        player.qinmidu = max(0, player.qinmidu + delta)
        return player.qinmidu
