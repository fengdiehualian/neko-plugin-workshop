"""排行榜(天榜): 玩家 vs NPC 虚拟榜。

单人化: 榜单是 NPC 群众演员(战力围绕玩家缩放), 玩家可挑战上位。
"""

from __future__ import annotations

import random
from typing import Any, Dict, List

from .npc import NPC
from .player import PlayerSave

NPC_NAMES = ["青莲剑仙", "玄天老祖", "紫霄真人", "碧落仙子", "幽冥魔尊",
             "赤焰尊者", "太虚道长", "霓裳圣女", "镇岳武神"]


class Ranking:
    """天榜中间件。"""

    def __init__(self, game: Any) -> None:
        self.game = game

    @staticmethod
    def power(save: PlayerSave) -> int:
        return int(save.attack * 2 + save.max_hp * 0.1 + save.defense * 1.5)

    def leaderboard(self, save: PlayerSave) -> List[Dict[str, Any]]:
        """玩家 + 9 个 NPC, 按战力降序。NPC 战力在玩家 0.7~1.6 倍随机。"""
        my_power = self.power(save)
        entries = []
        for name in NPC_NAMES:
            ratio = random.uniform(0.7, 1.6)
            entries.append({
                "name": name, "is_npc": True,
                "power": int(my_power * ratio),
                "ratio": round(ratio, 2),
            })
        entries.append({"name": save.name, "is_npc": False, "power": my_power})
        entries.sort(key=lambda e: e["power"], reverse=True)
        for i, e in enumerate(entries, 1):
            e["rank"] = i
        return entries

    def rank_of(self, save: PlayerSave) -> int:
        for e in self.leaderboard(save):
            if not e["is_npc"]:
                return e["rank"]
        return 0

    def npc_at_rank(self, save: PlayerSave, rank: int) -> NPC:
        """把天榜某名的 NPC 变成可挑战的战斗 NPC。"""
        lb = self.leaderboard(save)
        for e in lb:
            if e["rank"] == rank:
                if e["is_npc"]:
                    ratio = e["ratio"]
                    return NPC(
                        nid=f"rank_{rank}", name=e["name"], role="天榜",
                        realm_idx=save.realm_idx,
                        hp=int(save.max_hp * max(0.5, ratio)),
                        attack=int(save.attack * max(0.5, ratio)),
                        defense=int(save.defense * max(0.5, ratio)),
                        crit_rate=0.10,
                        reward_lingshi=1000 * rank,
                        loot=[],
                        quote=f"{e['name']}:想上天榜,先过我这关!",
                    )
        return None
