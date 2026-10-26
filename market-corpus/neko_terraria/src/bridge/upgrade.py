"""英雄成长引擎（v0.10）：小猫娘自己也会变强 —— 周期扫配方、合成升级装备并换装。

按生存循环惯例 TaskRunner.check_upgrade / UPGRADE_CHECK_INTERVAL：
- 每 upgrade_interval_secs 检查一次 vs 当前手持/防具
- 有可合成且防御/伤害更高的 → 合成 → auto_equip 穿戴上
- 合成缺材料：能自己挖/砍优先补，否则保持静默（下次再来）

底层：recipe_book 已存全量配方（data/recipes/recipes.json），
weapon/pickaxe/armor 三类联动 mod.get_recipes 的 available 位（可用性 C# 已算）。
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 升级检查间隔（秒）
UPGRADE_INTERVAL = 180
# 升级类别 → (标签, 统计键, minimun 差值才升级)
UPGRADE_CATEGORIES = [
    ("weapon", "damage", 2),
    ("pick", "pick", 1),
    ("armor", "defense", 1),
]
# 材料缺了可以自己凑的两个基础物（对应 gather_wood/mine）
GATHERABLE = {"木材", "木头", "铁矿", "铜矿", "锡矿", "银矿", "钨矿", "金矿"}


class UpgradeEngine:
    def __init__(self, agent) -> None:
        self.agent = agent
        self._last_run = 0.0

    async def consider(self) -> bool:
        """周期入口：距上次 >= interval 才真跑，否则直接返回 False。"""
        if time.time() - self._last_run < UPGRADE_INTERVAL:
            return False
        self._last_run = time.time()
        made = await self._run()
        if made > 0:
            try:
                await self.agent.send_chat("我自己做了个更好的装备喵～")
            except Exception:
                pass
        return made > 0

    async def _run(self) -> int:
        """扫三类升级，能做的合成并穿上。返回合成了几件。"""
        made = 0
        try:
            made = await self._check_and_craft()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[upgrade] 升级检查异常: {e}")
        return made

    # ---------------- 合成 ----------------
    async def _check_and_craft(self) -> int:
        made = 0
        # 1. 先穿上背包里已有更好的（auto_equip 两次跑不出新装备就 skip）
        await self.agent.equip.auto_equip()
        # 2. 武器/镐/防具
        recipes = await self._recipes()
        if recipes:
            made += await self._try_craft_upgrades(recipes)
            # 合成后如果出了更好的防具再穿一次（原 auto_equip 拿不到刚合成的）
            try:
                await self.agent.equip.auto_equip()
            except Exception:
                pass
        return made

    async def _try_craft_upgrades(self, recipes: List[Dict[str, Any]]) -> int:
        """扫三类升级，能做的合成。返回合成件数。"""
        made = 0
        try:
            inv = await self.agent.mod.get_inventory()
        except Exception:
            inv = {}
        cur_weapon = _best_stat(inv, "damage")
        cur_pick = _best_stat(inv, "pick")
        cur_def = _best_stat(inv, "defense")

        for cat, key, min_gain in UPGRADE_CATEGORIES:
            cur = _cur(cat, cur_weapon, cur_pick, cur_def)
            best = _pick_candidate(recipes, cat, key, cur, min_gain)
            if best is None:
                continue
            name = best["name"]
            iid = int(best.get("item_id", -1) or -1)
            if iid < 0:
                continue
            if not best.get("available"):
                await self._gather_needed(best)
                recipes = await self._recipes()  # 重查可用性
                best = _pick_candidate(recipes, cat, key, cur, min_gain)
                if best is None or not best.get("available"):
                    continue
                name = best["name"]
                iid = int(best.get("item_id", -1) or -1)
                if iid < 0:
                    continue
            # 传递已选中的 recipe_index：同一物品可能有多个配方（不同
            # 材料/合成站），只按 item_id 让 Mod 自己挑会重新选到不可用
            # 或材料更贵的配方，导致升级规划与实际合成不一致。
            recipe_index = best.get("recipe_index")
            try:
                recipe_index = int(recipe_index) if recipe_index is not None else None
            except (TypeError, ValueError):
                recipe_index = None
            ok = await self.agent.mod.craft(item_id=iid, amount=1,
                                            recipe_index=recipe_index)
            if ok > 0:
                made += 1
                if cat == "armor":
                    try:
                        await self.agent.equip.auto_equip()
                    except Exception:
                        pass
                self.agent.log(f"升级：合成了 {name}", "item")
        return made

    async def _recipes(self) -> List[Dict[str, Any]]:
        try:
            return await self.agent.mod.get_recipes("all") or []
        except Exception:
            return []

    async def _gather_needed(self, recipe: Dict[str, Any]) -> None:
        """缺材料：能自己挖/砍的先补一点（只补基础矿物与木头，防钻牛角尖）。"""
        for m in recipe.get("materials", []) or []:
            name = str(m.get("name", ""))
            if name not in GATHERABLE:
                continue
            try:
                iid = self.agent.resolve_item(name)
                if iid < 0:
                    continue
                await self.agent.mining.mine_target(name, 1)
            except Exception:
                pass


def _cur(cat: str, weapon: int, pick: int, defense: int) -> int:
    return {"weapon": weapon, "pick": pick, "armor": defense}.get(cat, 0)


def _best_stat(inv: Dict[str, Any], key: str) -> int:
    best = 0
    for slot in ("hotbar", "inventory", "equipped"):
        for it in inv.get(slot, []) or []:
            try:
                best = max(best, int(it.get(key, 0) or 0))
            except Exception:
                pass
    return best


def _pick_candidate(
    recipes: List[Dict[str, Any]], cat: str, key: str, cur: int, min_gain: int
) -> Optional[Dict[str, Any]]:
    """从 all 配方里挑出最好的可升级目标。

    配方结构（C# SendRecipes v0.5）是平铺的
    {item_id,name,amount,mod,available,materials,stations,damage,pick,axe,defense}，
    物品属性在顶层（result 为空）。取统计键直接读顶层。
    """
    best: Optional[Dict[str, Any]] = None
    best_val = cur
    for r in recipes:
        iid = int(r.get("item_id", -1) or -1)
        name = str(r.get("name", "") or "")
        if iid < 0 or not name:
            continue
        val = int(r.get(key, 0) or 0)
        if val < cur + min_gain:
            continue
        if best is None or val > best_val:
            best = r
            best_val = val
    return best
