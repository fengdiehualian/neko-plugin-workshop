"""陪伴式生活交互（v0.5）：砍树、钓鱼、工具选择——什么任务用什么工具。

核心：猫娘不是"任务工具"，而是会过日子的小玩家：
- 砍树：找树 → 走过去 → 砍倒 → 捡木头（背包计数确认）
- 钓鱼：找水域 → 走到岸边 → 用钓竿钓鱼 → 收杆（间歇等待）
- 工具选择：挖矿用镐、砍树用斧、打怪用剑、钓鱼用钓竿——
  在动作前自动选中合适工具（select_item），不拿错家伙。

与 idle/explore 的关系：
- idle 周期触发"生活小动作"（砍树/钓鱼/挖矿轮流）
- 都是前台任务或轻量动作，可被主人打断
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)

# 工具名关键词（无 use 字段时按名字兜底）
_PICK_KW = ("镐", "pick")
_AXE_KW = ("斧", "axe")
_ROD_KW = ("钓竿", "鱼竿", "fishing")
_SWORD_KW = ("剑", "sword", "刀")
# 战斗武器 use 类型
_MELEE_USE = ("melee",)
_RANGED_USE = ("ranged",)
_MAGIC_USE = ("magic",)
_SUMMON_USE = ("summon",)
_ALL_WEAPON_USE = _MELEE_USE + _RANGED_USE + _MAGIC_USE + _SUMMON_USE

# 各生活动作节律（秒）
CHOP_INTERVAL = 45.0
FISH_INTERVAL = 90.0


def _looks_like_weapon(name: str) -> bool:
    """名字像武器（法杖/弓/枪/弩/鞭等，用于无 use 字段时兜底）。"""
    kws = ("剑", "刀", "匕首", "法杖", "魔杖", "弓", "弩", "枪", "手枪", "步枪",
           "鞭", "斧刃", "镐刃", "锤", "尖", "矛", "枪刃", "棒", "杖")
    return any(k in name for k in kws)


class LifeEngine:
    def __init__(self, agent) -> None:
        self.agent = agent
        self._last_chop = 0.0
        self._last_fish = 0.0
        self.last_failure = ""
        self.last_cast_count = 0
        self._last_tool_warning = ""
        self._tool_prepare_depth = 0
        self._tool_prepare_seen: set[int] = set()

    # ---------------- 工具选择 ----------------

    async def _craft_basic_tool(self, kind: str) -> bool:
        """没有工具时，按真实配方尝试制作一把基础工具。

        这里只使用 Mod 返回的配方、材料和合成环境；不会用 give_item
        凭空生成工具。制作成功后由调用方重新拉取背包并选择工具。
        """
        candidates = {
            "pick": (
                "Copper Pickaxe", "Iron Pickaxe", "Silver Pickaxe",
                "Gold Pickaxe", "Platinum Pickaxe", "铜镐", "铁镐",
                "银镐", "金镐", "铂金镐",
            ),
            "axe": (
                "Copper Axe", "Iron Axe", "Silver Axe", "Gold Axe",
                "Platinum Axe", "War Axe of the Night", "铜斧", "铁斧",
                "银斧", "金斧", "铂金斧",
            ),
            "rod": (
                "Wood Fishing Pole", "Reinforced Fishing Pole",
                "木钓竿", "强化钓竿",
            ),
        }.get(kind, ())
        if not candidates:
            return False
        book = getattr(self.agent, "recipe_book", None)
        if book is None:
            return False
        try:
            await book.refresh()
            await book.refresh_availability()
            from .world_model import WorldModel

            inventory = await WorldModel(self.agent, book).snapshot()
            # Prefer the strongest recipe output that is already craftable.  A
            # fixed Copper->Iron order made a weak starter tool win even when
            # the player had materials for a much better tool.
            recipes = []
            for recipe in getattr(book, "_recipes", ()):
                if not self._recipe_matches_tool(recipe, kind, candidates):
                    continue
                if not recipe.environment_ready:
                    continue
                recipes.append(recipe)
            recipes.sort(key=lambda r: self._recipe_tool_score(r, kind), reverse=True)
            for recipe in recipes[:8]:
                # A recipe may be real but not immediately craftable.  Make a
                # bounded attempt to obtain its ingredients through an actual
                # chest/mining/recipe path before giving up.
                _, missing = recipe.requirements(1, inventory)
                if missing:
                    prepared = await self._prepare_tool_materials(
                        recipe, inventory, seen=set())
                    if not prepared:
                        continue
                    inventory = await WorldModel(self.agent, book).snapshot()
                    _, missing = recipe.requirements(1, inventory)
                    if missing:
                        continue
                before = await self.agent.mod.get_inventory()
                crafted = await self.agent.mod.craft(
                    item_id=recipe.item_id,
                    amount=1,
                    recipe_index=recipe.recipe_index,
                )
                after = await self.agent.mod.get_inventory()
                gained = self._count_item_id(after, recipe.item_id) - self._count_item_id(before, recipe.item_id)
                if crafted > 0 and gained > 0:
                    self.agent._inv_full = after
                    self.agent.log(f"工具准备：按真实配方制作了 {recipe.name}", "item")
                    return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.agent.log(f"自动制作工具失败：{exc}", "warn")
        return False

    async def _prepare_tool_materials(self, recipe, inventory, seen: set[int],
                                      output_amount: int = 1) -> bool:
        """Obtain missing recipe materials without manufacturing the tool itself."""
        from .reasoner import Reasoner
        from .world_model import WorldModel

        if recipe.item_id in seen:
            return False
        seen = seen | {recipe.item_id}
        output_amount = max(1, int(output_amount or 1))
        _takes, missing = recipe.requirements(output_amount, inventory)
        if not missing:
            return True
        if self._tool_prepare_depth >= 2:
            return False
        self._tool_prepare_depth += 1
        try:
            for name, amount in missing:
                iid = self.agent.resolve_item(name)
                if iid <= 0 or iid in seen:
                    return False
                try:
                    chest = await self.agent.nearest_chest_with(name)
                    if chest is not None and await self.agent.take_from_chest(
                            name, chest, amount):
                        inventory = await WorldModel(self.agent, getattr(
                            self.agent, "recipe_book", None)).snapshot()
                        continue
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
                # 先检查真实箱子，再阻断“缺斧头→砍木材→做斧头”的循环。
                if iid == 9:
                    self.last_failure = f"制作工具还缺木材×{amount}（未能从箱子取足，不能用待制作的斧头递归砍树）"
                    return False
                # Prefer an actual mining result for recognized ore materials.
                try:
                    if Reasoner(self.agent, None)._is_mineable(name):
                        _ore_iid, got = await self.agent.mining.mine_target(name, amount)
                        if got >= amount:
                            inventory = await WorldModel(self.agent, getattr(
                                self.agent, "recipe_book", None)).snapshot()
                            continue
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
                book = getattr(self.agent, "recipe_book", None)
                if book is None:
                    return False
                child = book.find(name, inventory=inventory, amount=amount)
                if child is None or not child.environment_ready:
                    return False
                if not await self._prepare_tool_materials(
                        child, inventory, seen, output_amount=amount):
                    return False
                before = await self.agent.mod.get_inventory()
                crafted = await self.agent.mod.craft(
                    item_id=child.item_id, amount=amount,
                    recipe_index=child.recipe_index)
                after = await self.agent.mod.get_inventory()
                self.agent._inv_full = after
                gained = self._count_item_id(after, child.item_id) - self._count_item_id(before, child.item_id)
                if crafted <= 0 or gained < amount:
                    return False
                inventory = await WorldModel(self.agent, book).snapshot()
            # 子配方可能消耗父配方也需要的材料，重新核对整份实际库存。
            inventory = await WorldModel(self.agent, getattr(
                self.agent, "recipe_book", None)).snapshot()
            return not recipe.requirements(output_amount, inventory)[1]
        finally:
            self._tool_prepare_depth -= 1

    def _recipe_matches_tool(self, recipe, kind: str, fallback_names) -> bool:
        """Identify a tool from authoritative registry fields, then names.

        工具类型的权威判据是 registry 的 tags（mod 端把 pick>0/axe>0/fishingPole>0
        分别打成 "pickaxe"/"axe"/"fishing"）；`use` 字段只区分大类
        （tool/weapon/armor/accessory/ore/misc），不区分镐/斧/竿，因此这里不用它。
        """
        registry = getattr(self.agent, "registry", None)
        info = registry.describe(str(recipe.item_id)) if registry is not None else {}
        name = " ".join((str(recipe.name), str(recipe.full_name),
                         str(info.get("name", "")), str(info.get("display_name", "")))).casefold()
        tags = {str(x).casefold() for x in (info.get("tags", []) or [])}
        if kind == "pick":
            return ("pickaxe" in tags or "pick" in tags or "pickaxe" in name
                    or any("镐" in str(n) for n in (recipe.name, recipe.full_name)))
        if kind == "axe":
            return (("axe" in tags or "axe" in name or "斧" in name)
                    and "pickaxe" not in name and "pickaxe" not in tags)
        if kind == "rod":
            return ("fishing" in tags or "rod" in tags or "fishing" in name
                    or "钓竿" in name or "鱼竿" in name)
        return False

    def _recipe_tool_score(self, recipe, kind: str) -> tuple:
        registry = getattr(self.agent, "registry", None)
        info = registry.describe(str(recipe.item_id)) if registry is not None else {}
        attr = {"pick": "pick", "axe": "axe", "rod": "fishing_pole"}.get(kind, "")
        value = max((int(info.get(k, 0) or 0) for k in
                     (attr, f"{kind}_power", "pickaxe_power", "axe_power", "damage")),
                    default=0)
        # Prefer recipe output whose item ID is stable and whose recipe is
        # available; recipe_index is only a deterministic tie breaker.
        return (value, bool(recipe.available), -len(recipe.ingredients), -recipe.recipe_index)

    @staticmethod
    def _count_item_id(inv: Dict[str, Any], iid: int) -> int:
        return sum(int(it.get("stack", 0) or 0)
                   for kind in ("hotbar", "inventory", "equipped")
                   for it in (inv or {}).get(kind, []) or []
                   if int(it.get("id", -1) or -1) == int(iid))

    async def select_tool(self, kind: str) -> bool:
        """选中合适工具/武器。kind:
        - pick/axe/rod：挖矿/砍树/钓鱼
        - weapon：战斗武器（近战/远程/魔法/召唤中伤害最高者）
        - melee/ranged/magic/summon：指定武器类型
        返回是否选到。
        """
        try:
            inv = await self.agent.mod.get_inventory()
            self.agent._inv_full = inv
        except Exception as exc:
            self.last_failure = f"无法读取背包，暂时不能确认工具：{exc}"
            return False
        items = (inv.get("hotbar", []) or []) + (inv.get("inventory", []) or [])

        # 工具类（挖矿/砍树/钓鱼）
        if kind in ("pick", "axe", "rod"):
            kws = {"pick": _PICK_KW, "axe": _AXE_KW,
                   "rod": _ROD_KW}.get(kind, ())
            eligible = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                slot = it.get("inv_slot")
                if slot is None:
                    continue
                name = str(it.get("name", "") or "").lower()
                full_name = str(it.get("full_name", "") or "").lower()
                attr = {"pick": "pick", "axe": "axe", "rod": "fishing_pole"}.get(kind)
                # A present capability field is authoritative.  Falling back
                # to the display name when it is zero makes CopperPickaxe look
                # like an axe because its name contains the substring "axe".
                attr_present = bool(attr and attr in it and it.get(attr) is not None)
                has_attr = bool(attr and int(it.get(attr, 0) or 0) > 0)
                # 某些 Mod 工具不会把原版 axe/pick 字段填回背包快照，
                # 但注册表已经按 Item 属性标记了标签。完整内部名也比
                # 本地化显示名稳定，三者任一命中即可尝试切换。
                tags = set()
                try:
                    registry = getattr(self.agent, "registry", None)
                    if registry is not None:
                        tags.update(registry.describe(str(it.get("id", ""))).get("tags", []) or [])
                except Exception:
                    pass
                tag_match = {"pick": "pickaxe", "axe": "axe", "rod": "fishing"}.get(kind) in tags
                name_match = any(k.lower() in name or k.lower() in full_name for k in kws)
                if kind == "axe" and ("pickaxe" in name or "pickaxe" in full_name):
                    name_match = False
                if has_attr or tag_match or (not attr_present and name_match):
                    power_key = {"pick": "pick", "axe": "axe",
                                 "rod": "fishing_pole"}.get(kind, "")
                    power = int(it.get(power_key, 0) or 0)
                    # A missing capability field should still rank below an
                    # item carrying an authoritative power value.
                    eligible.append((power, int(it.get("damage", 0) or 0), it))
            for _power, _damage, it in sorted(eligible, key=lambda x: (x[0], x[1]), reverse=True):
                if await self.agent.mod.select_item(it["inv_slot"]):
                    self.last_failure = ""
                    self._last_tool_warning = ""
                    return True
            if eligible:
                self.last_failure = "工具已找到，但切换工具未成功"
                return False
            # 指令准备阶段允许制作基础工具；制作后重新读取背包，
            # 避免“明明有材料却一直报告没有斧头/镐子”。
            if await self._craft_basic_tool(kind):
                try:
                    refreshed = await self.agent.mod.get_inventory()
                    self.agent._inv_full = refreshed
                    items = ((refreshed.get("hotbar", []) or [])
                             + (refreshed.get("inventory", []) or []))
                    for it in items:
                        if not isinstance(it, dict) or it.get("inv_slot") is None:
                            continue
                        slot = it["inv_slot"]
                        name = str(it.get("name", "") or "").lower()
                        full_name = str(it.get("full_name", "") or "").lower()
                        attr = {"pick": "pick", "axe": "axe", "rod": "fishing_pole"}[kind]
                        if int(it.get(attr, 0) or 0) > 0 or any(
                                k.lower() in name or k.lower() in full_name for k in kws):
                            if kind == "axe" and ("pickaxe" in name or "pickaxe" in full_name):
                                continue
                            if await self.agent.mod.select_item(slot):
                                self.last_failure = ""
                                self._last_tool_warning = ""
                                return True
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
            self.last_failure = "背包中没有" + {"pick": "镐子", "axe": "斧头", "rod": "钓竿"}[kind]
            return False

        # 战斗武器：按类型挑伤害最高
        weapon_uses = {
            "weapon": _ALL_WEAPON_USE,
            "melee": _MELEE_USE, "ranged": _RANGED_USE,
            "magic": _MAGIC_USE, "summon": _SUMMON_USE,
        }.get(kind, _ALL_WEAPON_USE)
        best_slot, best_dmg = None, 0
        for it in items:
            if not isinstance(it, dict):
                continue
            slot = it.get("inv_slot")
            if slot is None:
                continue
            use = str(it.get("use", "") or "")
            if use:
                # 有 use 字段：按武器类型匹配（法杖/弓/枪/剑都是 weapon 类）
                if use not in weapon_uses:
                    continue
            else:
                # 无 use 字段：按名字猜是不是武器
                name = str(it.get("name", "") or "")
                if not _looks_like_weapon(name):
                    continue
            dmg = int(it.get("damage", 0) or 0)
            if dmg > best_dmg:
                best_dmg = dmg
                best_slot = slot
        if best_slot is not None:
            if await self.agent.mod.select_item(best_slot):
                self.last_failure = ""
                return True
            self.last_failure = "切换武器未成功"
            return False
        self.last_failure = "背包中没有可用武器"
        return False

    # ---------------- 砍树 ----------------

    async def _say_no_tool(self, kind: str, msg: str) -> None:
        """记录可交给任务结果通道的事实；不在每轮循环刷游戏聊天。"""
        if self.last_failure != self._last_tool_warning:
            self.agent.log(self.last_failure or msg, "warn")
            self._last_tool_warning = self.last_failure

    async def _wait_for_body(self) -> None:
        # 长期采集在前台动作/战斗中让路；不能仅在外层每批开始时检查。
        while True:
            lt = self.agent.longterm
            is_longterm = lt.owns_current_action()
            if not getattr(self.agent, "_in_combat", False) and not (is_longterm and lt.yielding()):
                return
            await asyncio.sleep(0.2)

    async def chop_wood(self, target: int = 10, item_id: int = 9) -> int:
        """砍树收集木材。返回本次获得的数量（背包计数确认）。

        真实砍树：走过去 → 选斧头 → 朝树挥斧 → 收掉落 → 计数。
        人物必须真的走到树边（C# InReach 距离校验），挥斧砍下才算数。
        """
        iid = item_id  # 只统计指定木材，不能把普通木材算成其他木材。
        self.last_failure = ""

        # 选斧头；没有就跟主人撒娇（无执行条件情感交互）
        if not await self.select_tool("axe"):
            await self._say_no_tool(
                "斧", "主人我没有斧头无法砍树喵，主人有也可以给我喵")
            return 0

        before = _count_id(self.agent.get_inventory_sync(), iid)

        got = 0
        # 目标棵数约束：曾写死 range(4) 忽略 target 参数——主人说"砍5个木材"
        # 会砍 4 整棵（30+ 木材）。target 是"预期获得量"，按需砍到接近即可。
        # 简化按"至少砍到 target 棵树才算够"不成立（一棵树给 5-20 木材），
        # 故改为：最多砍 target 棵；配合背包计数——到账即停。
        max_trees = max(1, min(int(target or 4), 12))
        unreachable = set()
        for _ in range(max_trees):
            await self._wait_for_body()
            if self.agent.executor and self.agent.executor.should_stop():
                break
            if target and 0 < target <= got:
                break
            trees = await self.agent.mod.find_trees(radius=30)
            trees = [t for t in trees if (t.get("x"), t.get("y")) not in unreachable]
            if not trees:
                self.last_failure = "附近没有可到达的树木"
                break
            tree = trees[0]
            tx, ty = int(tree.get("x", 0)), int(tree.get("y", 0))
            # 走过去（导航途中遇敌会先打再走）
            try:
                if not await self.agent.navigate_to(tx, ty, timeout=15):
                    self.agent.log(f"砍树：无法走到树旁 ({tx},{ty})，换下一棵", "warn")
                    self.last_failure = "无法走到树旁"
                    unreachable.add((tx, ty))
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.agent.log(f"砍树：移动到树旁失败：{exc}", "warn")
                continue
            # 选好斧头，朝树根挥砍（真实挥斧，树真的会被砍倒）
            if not await self.select_tool("axe"):
                break
            for _ in range(12):
                was_busy = getattr(self.agent, "_in_combat", False) or self.agent.longterm.yielding()
                await self._wait_for_body()
                if was_busy and not await self.select_tool("axe"):
                    break
                if self.agent.executor and self.agent.executor.should_stop():
                    break
                try:
                    await self.agent.mod.use_item(tx, ty)
                except Exception:
                    break
                await asyncio.sleep(0.35)
            # 收掉落再数木头（树倒下掉地上的木材得先捡）
            await asyncio.sleep(0.3)
            try:
                await self.agent.mod.collect_items(radius=400)
            except Exception:
                pass
            try:
                inv2 = await self.agent.mod.get_inventory()
                self.agent._inv_full = inv2
                now = _count_id(inv2, iid)
                if now > before + got:
                    got = now - before
                    continue  # 这棵砍到了，下一棵
            except Exception:
                pass
            # 挥了 12 下还没倒：补一发原生砍树（有 InReach 校验，真走到才生效）
            try:
                ok = await self.agent.mod.chop_trees(tx, ty)
                if ok:
                    await asyncio.sleep(0.3)
                    await self.agent.mod.collect_items(radius=400)
            except Exception:
                pass

        # 计数
        try:
            inv = await self.agent.mod.get_inventory()
            self.agent._inv_full = inv
            after = _count_id(inv, iid)
        except Exception:
            after = -1
        if before >= 0 and after >= 0:
            got = max(0, after - before)
        if got == 0 and not self.last_failure:
            self.last_failure = "砍树后没有确认到新增木材"
        self.agent.log(f"砍树完成，获得 {got} 木材", "item")
        return got

    # ---------------- 钓鱼 ----------------

    async def fish(self, attempts: int = 3) -> bool:
        """在水边钓鱼：选钓竿 → 感知生物群系找对应水域 → 岸边甩竿 → 等待 → 收杆。

        泰拉瑞亚钓鱼分三类水域，出的鱼不同：
        - 地表层水域：普通鱼（鲈鱼等）
        - 地下/洞穴层水域：岩层鱼（蝙蝠鱼等）
        - 特殊生物群系水域：丛林/雪地/腐化/神圣/地狱 特有鱼
        鱼饵（蚯蚓/萤火虫/龙虾）是消耗品，use_item 朝水会自动消耗背包鱼饵。
        """
        self.last_cast_count = 0
        self.last_failure = ""
        # 选钓竿；没有就跟主人撒娇
        if not await self.select_tool("rod"):
            await self._say_no_tool(
                "钓竿", "主人我没有钓竿钓不了鱼喵，主人有也可以给我喵")
            return False

        # 感知当前生物群系（决定钓什么水域的鱼）
        try:
            st = self.agent.get_state()
            biome = str(st.get("biome", "") or "")
        except Exception:
            biome = ""

        water = await self.agent.mod.find_water(radius=30)
        if not water:
            # 特殊群系下没水 → 放宽找水范围
            water = await self.agent.mod.find_water(radius=60)
        if not water:
            self.last_failure = "附近没有水域，找不到钓鱼的地方"
            self.agent.log("附近没有水域，找不到钓鱼的地方~", "warn")
            return False

        spot = water[0]
        wx, wy = int(spot.get("x", 0)), int(spot.get("y", 0))
        # 走到水面旁的岸边
        shore_x = wx + 2  # 站水面格旁边
        if not await self.agent.navigate_to(shore_x, wy, timeout=15):
            self.last_failure = "无法走到水边，尚未开始钓鱼"
            return False
        # 导航途中可能打过怪，重新换回钓竿才能抛竿。
        if not await self.select_tool("rod"):
            return False

        where = biome or "普通水域"
        self.agent.log(f"找个{where}水边甩一竿~", "life")
        cast_count = 0
        for _ in range(attempts):
            if self.agent.executor and self.agent.executor.should_stop():
                break
            # 甩竿（use_item 朝水面，自动消耗背包鱼饵）
            try:
                if not await self.agent.mod.use_item(wx, wy):
                    self.last_failure = "抛竿命令未确认成功"
                    break
            except Exception:
                self.last_failure = "抛竿命令失败"
                break
            # 等鱼上钩
            await asyncio.sleep(2.5)
            # 收杆
            try:
                if not await self.agent.mod.use_item(wx, wy):
                    self.last_failure = "收竿命令未确认成功"
                    break
            except Exception:
                self.last_failure = "收竿命令失败"
                break
            cast_count += 1
            self.last_cast_count = cast_count
            await asyncio.sleep(1.0)
            if self.agent.executor and self.agent.executor.should_stop():
                break
        if cast_count:
            # 诚实汇报：mod 不回报是否真钓到鱼，只说自己甩了几竿，
            # 不宣称"钓到鱼了"（A3 诚实化——做不到的判定不编）
            self.agent.log(f"在{where}甩了{cast_count}竿~", "life")
            try:
                await self.agent.send_chat(f"在{where}边甩了{cast_count}竿喵~")
            except Exception:
                pass
        return cast_count > 0

    # ---------------- 生活小动作（idle 驱动） ----------------

    async def do_something(self) -> str:
        """idle 时的陪伴式生活小动作：砍树/钓鱼/挖矿轮流。返回做了什么。"""
        now = time.time()
        # 钓鱼优先级较低（要水），砍树次之
        if now - self._last_fish > FISH_INTERVAL:
            self._last_fish = now
            try:
                if await self.fish():
                    return "fish"
            except Exception as e:
                self.agent.log(f"钓鱼异常: {e}", "warn")
        if now - self._last_chop > CHOP_INTERVAL:
            self._last_chop = now
            try:
                got = await self.chop_wood()
                if got > 0:
                    return "chop"
            except Exception as e:
                self.agent.log(f"砍树异常: {e}", "warn")
        return "idle"


def _count_id(inv: Dict[str, Any], iid: int) -> int:
    total = 0
    for slot in ("hotbar", "equipped", "inventory"):
        for it in (inv or {}).get(slot, []) or []:
            if isinstance(it, dict) and it.get("id") == iid:
                total += int(it.get("stack", 1) or 1)
    return total
