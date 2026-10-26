"""宠物(仙宠)系统: 领养/出战/喂食/进阶/寻宝。

数据来自原版 仙宠列表(91)/仙宠口粮(4)/仙宠探索(25)/突破仙宠(8)。
单人化: 仙宠是玩家的战斗伙伴(出战加成) + 寻宝小管家(定时战利)。
"""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ....core.datafile import read_json
from .player import PlayerSave

_ITEMS_DIR = Path(__file__).resolve().parent.parent / "data" / "items"

EXPLORE_SECONDS = 300          # 寻宝周期(秒)
FEED_EXP = 100                 # 每次喂食经验
EVOLVE_COST_PER_LV = 1000      # 进阶灵石成本基数
QUALITY_ORDER = ["仙胎", "灵宠", "仙宠", "神宠", "圣兽"]


def _load_list(name: str) -> List[Dict[str, Any]]:
    # 优先读 <name>.gz（包里只发布压缩版，见 core/datafile.py）
    data = read_json(_ITEMS_DIR / name, [])
    return data if isinstance(data, list) else []


class PetSystem:
    """仙宠中间件。"""

    def __init__(self, game: Any) -> None:
        self.game = game
        self.pets: Dict[str, Dict[str, Any]] = {}
        self.foods: Dict[str, Dict[str, Any]] = {}
        self.explore_loot: List[Dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        for it in _load_list("仙宠列表.json"):
            self.pets[it["name"]] = it
        for it in _load_list("仙宠口粮列表.json"):
            self.foods[it["name"]] = it
        self.explore_loot = _load_list("仙宠探索列表.json")

    # ── 宠物状态(存档于 save.extra["pets"]) ─

    @staticmethod
    def _pets(save: PlayerSave) -> Dict[str, Dict[str, Any]]:
        return save.extra.setdefault("pets", {})

    def active_name(self, save: PlayerSave) -> Optional[str]:
        for n, p in self._pets(save).items():
            if p.get("active"):
                return n
        return None

    def set_active(self, save: PlayerSave, name: str) -> bool:
        pets = self._pets(save)
        if name not in pets:
            return False
        for p in pets.values():
            p["active"] = False
        pets[name]["active"] = True
        return True

    # ── 领养(消耗灵石) ────────────────────

    async def adopt(self, save: PlayerSave, name: str) -> Dict[str, Any]:
        info = self.pets.get(name)
        if info is None:
            return {"ok": False, "msg": f"没有「{name}」这种仙宠"}
        pets = self._pets(save)
        if name in pets:
            return {"ok": False, "msg": f"已有仙宠「{name}」了喵"}
        price = max(1, int(info.get("售价", 1000)))
        if save.lingshi_total() < price:
            return {"ok": False, "msg": f"灵石不足(领养需 {price})"}
        save.add_lingshi(-price)
        pets[name] = {"level": 1, "exp": 0, "active": len(pets) == 0}
        return {"ok": True, "msg": f"领养仙宠「{name}」成功喵(花费{price}灵石)",
                "item": name, "price": price}

    # ── 喂食(消耗口粮, 宠物升级) ──────────

    async def feed(self, save: PlayerSave, pet_name: str,
                   food_name: Optional[str] = None) -> Dict[str, Any]:
        pets = self._pets(save)
        if pet_name not in pets:
            return {"ok": False, "msg": f"还没有仙宠「{pet_name}」,先领养喵"}
        if food_name is None:
            # 自动挑背包里第一种仙宠口粮
            for n, c in save.bag.items():
                if n in self.foods:
                    food_name = n
                    break
        if not food_name or food_name not in self.foods:
            return {"ok": False, "msg": "背包里没有仙宠口粮,去商店买喵"}
        cur = save.bag.get(food_name, 0)
        if cur < 1:
            return {"ok": False, "msg": f"背包里没有「{food_name}」,去商店买喵"}
        save.bag[food_name] = cur - 1
        if save.bag[food_name] <= 0:
            del save.bag[food_name]
        info = self.pets.get(pet_name, {})
        max_lv = int(info.get("等级上限", 100))
        p = pets[pet_name]
        p["exp"] += FEED_EXP
        need = 100 + p["level"] * 50
        leveled = False
        while p["exp"] >= need and p["level"] < max_lv:
            p["exp"] -= need
            p["level"] += 1
            need = 100 + p["level"] * 50
            leveled = True
        lv_txt = f"升到 {p['level']} 级喵!" if leveled else f"当前 {p['level']} 级"
        return {"ok": True, "msg": f"喂食「{pet_name}」成功,{lv_txt}",
                "item": pet_name, "level": p["level"], "leveled": leveled}

    # ── 进阶(消耗灵石, 品质提升) ──────────

    async def evolve(self, save: PlayerSave, pet_name: str) -> Dict[str, Any]:
        pets = self._pets(save)
        if pet_name not in pets:
            return {"ok": False, "msg": f"还没有仙宠「{pet_name}」,先领养喵"}
        info = self.pets.get(pet_name, {})
        q = str(info.get("品质", "仙胎"))
        idx = QUALITY_ORDER.index(q) if q in QUALITY_ORDER else 0
        if idx >= len(QUALITY_ORDER) - 1:
            return {"ok": False, "msg": f"「{pet_name}」已达最高品质 {q}"}
        cost = EVOLVE_COST_PER_LV * pets[pet_name]["level"]
        if save.lingshi_total() < cost:
            return {"ok": False, "msg": f"灵石不足(进阶需 {cost})"}
        save.add_lingshi(-cost)
        pets[pet_name]["evolve"] = pets[pet_name].get("evolve", 0) + 1
        new_q = QUALITY_ORDER[idx + 1]
        return {"ok": True, "msg": f"「{pet_name}」进阶为{new_q}喵!(花费{cost}灵石)",
                "item": pet_name, "quality": new_q, "price": cost}

    # ── 寻宝(定时结算) ────────────────────

    async def explore_start(self, save: PlayerSave, pet_name: str) -> Dict[str, Any]:
        pets = self._pets(save)
        if pet_name not in pets:
            return {"ok": False, "msg": f"还没有仙宠「{pet_name}」,先领养喵"}
        if pets[pet_name].get("explore_end"):
            return {"ok": False, "msg": f"「{pet_name}」正在寻宝中喵"}
        pets[pet_name]["explore_end"] = time.time() + EXPLORE_SECONDS
        return {"ok": True, "msg": f"「{pet_name}」出发寻宝了喵,{EXPLORE_SECONDS//60}分钟后回来",
                "item": pet_name}

    async def explore_settle(self, save: PlayerSave, pet_name: str,
                             force: bool = False) -> Optional[Dict[str, Any]]:
        """结算寻宝。force=True 强制结束(提前召回), 未到期自然结算返回 None。"""
        pets = self._pets(save)
        p = pets.get(pet_name)
        if not p or not p.get("explore_end"):
            return None
        if not force and p["explore_end"] > time.time():
            return None
        p.pop("explore_end")
        if not self.explore_loot:
            return {"ready": True, "item": "灵石", "count": 100}
        loot = random.choice(self.explore_loot)
        return {"ready": True, "item": loot["name"], "count": 1}

    # ── 出战加成(战斗用) ──────────────────

    def combat_bonus(self, save: PlayerSave) -> Dict[str, float]:
        """出战仙宠给玩家的攻击/防御/血量百分比加成。"""
        name = self.active_name(save)
        if not name:
            return {"attack": 0.0, "defense": 0.0, "hp": 0.0}
        info = self.pets.get(name, {})
        p = self._pets(save).get(name, {})
        lv = int(p.get("level", 1))
        evolve = int(p.get("evolve", 0))
        atk_pct = float(info.get("初始加成", 0)) + float(info.get("每级加成", 0)) * (lv - 1)
        atk_pct *= (1 + 0.5 * evolve)
        return {"attack": atk_pct, "defense": atk_pct * 0.5, "hp": atk_pct * 0.8}
