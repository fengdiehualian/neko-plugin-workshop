"""职业系统：采集(采药/采矿) + 炼丹/炼器(配方合成)。

单人化：采集对象为野外资源(NPC 生态)，炼丹/炼器消耗背包材料合成。
配方简化版(完整配方表 data/items/合成列表.json 后续按需接入)。
"""

from __future__ import annotations

import time
from typing import Any, Dict

from .items import ItemCatalog
from .player import PlayerSave

GATHER_CD_SECONDS = 120          # 采集冷却(秒)
REFINE_HERBS = 2                 # 炼丹消耗草药数
FORGE_MATERIALS = 3              # 炼器消耗材料数


class Occupation:
    """职业中间件。"""

    def __init__(self, game: Any, catalog: ItemCatalog) -> None:
        self.game = game
        self.catalog = catalog

    @staticmethod
    def _cd_left(save: PlayerSave, key: str) -> int:
        last = save.extra.get(key, 0)
        remain = int(GATHER_CD_SECONDS - (time.time() - last))
        return max(0, remain)

    async def gather(self, save: PlayerSave, kind: str) -> Dict[str, Any]:
        """采药(kind=药) / 采矿(kind=矿) → 随机草药/材料。"""
        key = f"gather_{kind}"
        remain = self._cd_left(save, key)
        if remain > 0:
            return {"ok": False, "msg": f"采集冷却中,剩 {remain}s"}
        cls = "草药" if kind == "药" else "材料"
        item = self.catalog.random_of_class(cls)
        if item is None:
            return {"ok": False, "msg": "暂无可采集资源"}
        save.extra[key] = time.time()
        save.bag[item["name"]] = save.bag.get(item["name"], 0) + 1
        action = "采药" if kind == "药" else "采矿"
        return {"ok": True, "msg": f"{action}获得 {item['name']} ×1",
                "item": item["name"], "kind": cls}

    async def refine_pill(self, save: PlayerSave) -> Dict[str, Any]:
        """炼丹: 消耗 2 种草药 → 1 颗丹药。"""
        herbs = self._bag_of_class(save, "草药")
        if len(herbs) < REFINE_HERBS:
            return {"ok": False, "msg": f"草药不足(需要 {REFINE_HERBS} 种,当前 {len(herbs)}),先「采药」吧"}
        consumed = herbs[:REFINE_HERBS]
        for h in consumed:
            save.bag[h] -= 1
            if save.bag[h] <= 0:
                del save.bag[h]
        pill = self.catalog.random_of_class("丹药")
        save.bag[pill["name"]] = save.bag.get(pill["name"], 0) + 1
        return {"ok": True, "msg": f"炼丹成功!消耗{'、'.join(consumed)},获得 {pill['name']} ×1",
                "item": pill["name"], "consumed": consumed}

    async def forge_equip(self, save: PlayerSave) -> Dict[str, Any]:
        """炼器: 消耗 3 种材料 → 1 件装备。"""
        mats = self._bag_of_class(save, "材料")
        if len(mats) < FORGE_MATERIALS:
            return {"ok": False, "msg": f"材料不足(需要 {FORGE_MATERIALS} 种,当前 {len(mats)}),先「采矿」吧"}
        consumed = mats[:FORGE_MATERIALS]
        for m in consumed:
            save.bag[m] -= 1
            if save.bag[m] <= 0:
                del save.bag[m]
        equip = self.catalog.random_of_class("装备")
        save.bag[equip["name"]] = save.bag.get(equip["name"], 0) + 1
        return {"ok": True, "msg": f"炼器成功!消耗{'、'.join(consumed)},获得装备 {equip['name']} ×1",
                "item": equip["name"], "consumed": consumed}

    def _bag_of_class(self, save: PlayerSave, cls: str) -> list:
        out = []
        for name, count in save.bag.items():
            it = self.catalog.search(name)
            if it and it.get("class") == cls:
                out.append(name)
        return out
