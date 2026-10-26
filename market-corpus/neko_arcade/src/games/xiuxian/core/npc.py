"""NPC 系统：单人修仙世界里的"群众演员"，顶替原版所有需要真人的位置。

NPC 分类(对应 docs/SINGLE_PLAYER_DESIGN.md 第一节)：
- 散修 NPC: 可打劫/切磋/比武对象(略弱于玩家)
- 魔修 NPC: 恶人阵营, 会打劫玩家
- BOSS NPC: 妖王/天骄/若陀龙王 等副本目标(战力 1.5~3× 玩家)
- 队友 NPC: 团本/讨伐临时队友(自动补齐战力)

所有 NPC 按玩家境界实时缩放生成，无需持久化(每次生成即用)。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .realm import RealmSystem


@dataclass
class NPC:
    nid: str
    name: str
    role: str                 # 散修 / 魔修 / BOSS / 队友
    realm_idx: int
    hp: int
    attack: int
    defense: int
    crit_rate: float = 0.05
    loot: List[str] = field(default_factory=list)     # 掉落物(进背包)
    reward_lingshi: int = 0   # 战利灵石
    quote: str = ""           # 开战台词(猫娘/系统播报用)

    def summary(self) -> str:
        return (f"{self.name}({RealmSystem.realm_name(self.realm_idx)}, "
                f"HP{self.hp} 攻{self.attack} 防{self.defense})")


class NPCPool:
    """NPC 池：按玩家境界生成/缩放。"""

    SANXIU_NAMES = ["云游散修", "无名剑修", "丹尘子", "青衫客",
                    "断水真人", "木灵子", "白鹤道人", "冷月仙子"]
    MOXIU_NAMES = ["血魔老祖", "噬魂魔君", "阴煞道人", "黑风老魔",
                   "尸傀真人", "九幽邪修"]
    BOSS_TEMPLATES = [
        {"name": "黑风妖王", "ratio": 1.5, "loot": ["妖丹", "妖王精血"], "quote": "吼——!妖王在此,尔等受死!"},
        {"name": "天骄·荒", "ratio": 2.0, "loot": ["天骄精魄", "荒古秘术"], "quote": "同境天骄,唯我独尊!"},
        {"name": "若陀龙王", "ratio": 2.5, "loot": ["龙鳞", "龙髓"], "quote": "吾乃若陀龙王,凡人退避!"},
        {"name": "厄土魔尊", "ratio": 3.0, "loot": ["魔尊本源", "厄土晶核"], "quote": "厄土之下,万物凋零……"},
    ]

    @staticmethod
    def _scale(base: int, ratio: float) -> int:
        return max(1, int(base * ratio))

    @classmethod
    def spawn_sanxiu(cls, player: Any, role: str = "散修") -> NPC:
        """散修/魔修: 玩家属性 × 0.7~0.85, 可打劫/切磋。"""
        ratio = random.uniform(0.7, 0.85)
        pool = cls.MOXIU_NAMES if role == "魔修" else cls.SANXIU_NAMES
        name = random.choice(pool)
        return NPC(
            nid=f"{role}_{random.randint(1000, 9999)}",
            name=name, role=role,
            realm_idx=max(0, player.realm_idx - random.randint(0, 1)),
            hp=cls._scale(player.max_hp, ratio),
            attack=cls._scale(player.attack, ratio),
            defense=cls._scale(player.defense, ratio),
            crit_rate=random.uniform(0.03, 0.10),
            reward_lingshi=cls._scale(200, ratio) * (player.realm_idx + 1),
            loot=[] if role == "散修" else ["魔修残魂"],
            quote=f"{name}:哼,区区散修也敢造次?",
        )

    @classmethod
    def spawn_boss(cls, player: Any, idx: int = 0) -> NPC:
        """BOSS: 玩家属性 × 1.5~3.0, 需要猫娘助阵。"""
        tpl = cls.BOSS_TEMPLATES[idx % len(cls.BOSS_TEMPLATES)]
        ratio = tpl["ratio"]
        return NPC(
            nid=f"boss_{tpl['name']}",
            name=tpl["name"], role="BOSS", realm_idx=player.realm_idx,
            hp=cls._scale(player.max_hp, ratio * 1.5),
            attack=cls._scale(player.attack, ratio * 1.2),
            defense=cls._scale(player.defense, ratio * 1.0),
            crit_rate=0.12,
            loot=list(tpl["loot"]),
            reward_lingshi=cls._scale(2000, ratio) * (player.realm_idx + 1),
            quote=tpl["quote"],
        )

    @classmethod
    def spawn_tuanben_boss(cls, player: Any) -> NPC:
        """团本 BOSS(帝尊级): 玩家属性 × 2.2, 需猫娘+队友合攻。"""
        ratio = 2.2
        return NPC(
            nid="boss_dizun",
            name="荒古帝尊", role="BOSS", realm_idx=player.realm_idx,
            hp=cls._scale(player.max_hp, ratio * 1.6),
            attack=cls._scale(player.attack, ratio * 1.3),
            defense=cls._scale(player.defense, ratio * 1.1),
            crit_rate=0.15,
            loot=["帝尊遗宝", "荒古帝经残页", "帝血"],
            reward_lingshi=cls._scale(5000, ratio) * (player.realm_idx + 1),
            quote="吾掌一界生灭,蝼蚁也敢犯上?",
        )

    @classmethod
    def spawn_npc_sect(cls, player: Any) -> NPC:
        """NPC 宗门(护山大阵): 玩家属性 × 1.6, 宗门大战目标。"""
        names = ["血煞宗", "太玄门", "万剑阁", "幽冥殿", "青云宗"]
        ratio = 1.6
        return NPC(
            nid="npc_sect",
            name=random.choice(names), role="宗门", realm_idx=player.realm_idx,
            hp=cls._scale(player.max_hp, ratio * 1.5),
            attack=cls._scale(player.attack, ratio * 1.2),
            defense=cls._scale(player.defense, ratio * 1.2),
            crit_rate=0.10,
            loot=["宗门秘藏", "护山大阵残图"],
            reward_lingshi=cls._scale(3000, ratio) * (player.realm_idx + 1),
            quote="犯我山门者,虽远必诛!",
        )

    @classmethod
    def spawn_teammate(cls, player: Any) -> NPC:
        """队伍 NPC(团本/讨伐): 玩家属性 × 1.0~1.2, 自动补齐战力。"""
        ratio = random.uniform(1.0, 1.2)
        return NPC(
            nid=f"mate_{random.randint(1000, 9999)}",
            name=random.choice(["铁剑师兄", "凝霜师姐", "酒仙前辈", "铁拳师弟"]),
            role="队友", realm_idx=player.realm_idx,
            hp=cls._scale(player.max_hp, ratio),
            attack=cls._scale(player.attack, ratio),
            defense=cls._scale(player.defense, ratio),
            crit_rate=0.10,
            reward_lingshi=0, loot=[],
            quote="并肩作战,共进退!",
        )


class CombatEngine:
    """战斗引擎(回合制状态机): 攻方(玩家[+猫娘/队友]) vs 守方(NPC)。

    返回结构化战报，叙事交给主项目猫娘大脑。
    """

    MAX_ROUNDS = 30

    @staticmethod
    def _dmg(atk: int, dfn: int) -> int:
        """比例减伤伤害公式：攻击×(1-减伤率)，减伤率有 60% 上限，
        避免境界表里巨大的防御值把伤害压成 1(全数值段鲁棒)。"""
        atk = max(0, atk)
        dfn = max(0, dfn)
        if atk <= 0:
            return 1
        mitigation = min(0.6, dfn / (dfn + atk * 2))
        return max(1, int(atk * (1 - mitigation)))

    @staticmethod
    def fight(attacker: Dict[str, Any], defender: Dict[str, Any]) -> Dict[str, Any]:
        """回合制结算。

        attacker/defender: {hp, attack, defense, crit_rate}
        返回 {win, rounds, hp_left(胜方剩余), detail}
        """
        a_hp = max(1, int(attacker.get("hp", 1)))
        d_hp = max(1, int(defender.get("hp", 1)))
        a_atk = max(1, int(attacker.get("attack", 1)))
        d_atk = max(1, int(defender.get("attack", 1)))
        a_dfn = max(0, int(attacker.get("defense", 0)))
        d_dfn = max(0, int(defender.get("defense", 0)))
        a_crit = float(attacker.get("crit_rate", 0.05))
        d_crit = float(defender.get("crit_rate", 0.05))

        rng = random.Random()
        for rnd in range(1, CombatEngine.MAX_ROUNDS + 1):
            # 攻方出手
            dmg = CombatEngine._dmg(a_atk, d_dfn)
            if rng.random() < a_crit:
                dmg = int(dmg * 2)
            d_hp -= dmg
            if d_hp <= 0:
                return {"win": True, "rounds": rnd, "hp_left": a_hp,
                        "detail": f"第{rnd}回合击破对手(伤害{dmg})"}
            # 守方反击
            dmg2 = CombatEngine._dmg(d_atk, a_dfn)
            if rng.random() < d_crit:
                dmg2 = int(dmg2 * 2)
            a_hp -= dmg2
            if a_hp <= 0:
                return {"win": False, "rounds": rnd, "hp_left": d_hp,
                        "detail": f"第{rnd}回合不敌对手(受创{dmg2})"}
        # 30 回合未分胜负 → 按剩余血量判
        return {"win": a_hp >= d_hp, "rounds": CombatEngine.MAX_ROUNDS,
                "hp_left": max(a_hp, d_hp), "detail": "鏖战三十回合"}

    @staticmethod
    def player_side(player: Any, *helpers: Optional[NPC]) -> Dict[str, int]:
        """玩家 + 猫娘/队友 合并战力(团队战力 = 主战 + 助阵加成)。"""
        hp = max(player.max_hp, 1)
        atk = max(player.attack, 1)
        dfn = max(player.defense, 0)
        crit = max(player.crit_rate, 0.05)
        for h in helpers:
            if h is None:
                continue
            hp += h.hp
            atk += h.attack
            dfn += h.defense
            crit = max(crit, h.crit_rate)
        return {"hp": hp, "attack": atk, "defense": dfn, "crit_rate": crit}
