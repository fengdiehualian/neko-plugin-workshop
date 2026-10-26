"""补救推理：缺东西的时候自己想办法，而不是甩一句"我做不了"。

原来的大脑一遇到缺口就拒绝：
    "挖铁" + 没镐子 → "我没有镐子，挖不了矿" → 结束。
真正会想的猫娘应该继续往下推一层：
    没镐子 → 箱子里有吗？→ 有，那就先去取
                        → 没有，那能合成吗？→ 能，材料够吗？→ 够，先合成
                                                            → 不够，先挖材料

所以补救是一棵有深度的树，按代价从小到大试：
    身上已有 < 箱子里取 < 合成 < 现挖 < 求助主人
每找到一条路就生成"前置步骤"，插到原步骤前面。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# 补救代价，越小越优先
COST_HAVE = 0
COST_FETCH = 1
COST_CRAFT = 2
COST_MINE = 3
COST_ASK = 9

# 镐子这类"能力物品"的候选，从最容易得到的开始
PICKAXE_CANDIDATES = ("铜镐", "铁镐", "银镐", "金镐")
# mod 候选上限：装了大型整合包时物品成千上万，不能逐个查箱子
MAX_MOD_CANDIDATES = 12
MAX_CRAFT_TRY = 4
# 矿 -> 锭
ORE_TO_BAR = {"铁矿": "铁锭", "铜矿": "铜锭", "银矿": "银锭",
              "金矿": "金锭", "锡矿": "锡锭", "铅矿": "铅锭"}


@dataclass
class Fix:
    """一条补救方案。"""

    how: str                                   # fetch / craft / mine / ask
    desc: str                                  # 人话
    cost: int = COST_ASK
    steps: List[Dict[str, Any]] = field(default_factory=list)  # 要插入的前置步骤

    def say(self) -> str:
        return self.desc


class Reasoner:
    """针对推演出来的缺口，递归找补救办法。"""

    def __init__(self, agent, world) -> None:
        self.agent = agent
        self.world = world

    async def fix_for(self, item: str, amount: int, vi,
                      depth: int = 0, seen: Optional[set[str]] = None) -> Optional[Fix]:
        """给"缺 amount 个 item"找一条最省事的补救路。"""
        seen = set(seen or ())
        item_key = str(item or "").casefold().strip()
        if depth > 3 or not item or item_key in seen:
            return None

        # 能力物品（镐/斧/钓竿/钩）要先落成具体物品名
        if item in ("镐", "斧", "钓竿", "钩爪"):
            return await self._fix_capability(item, vi, depth, seen | {item_key})

        # amount is the additional shortage, not the target inventory total.
        if amount <= 0:
            return Fix(how="have", desc=f"无需再补{item}", cost=COST_HAVE)

        # 2) 箱子里取
        try:
            chest = await self.agent.nearest_chest_with(item)
        except Exception:
            chest = None
        if chest is not None:
            return Fix(how="fetch",
                       desc=f"箱子({chest.get('x')},{chest.get('y')})里有{item}，先去取",
                       cost=COST_FETCH,
                       steps=[{"action": "fetch", "item": item,
                               "amount": amount}])

        # 3) 合成（材料不够就继续往下推；mod 配方也走这条）
        recipe = await self.world.recipe_for(item, vi, amount)
        if recipe is not None:
            ok_station, lack = await self.world.station_ready(item, recipe)
            if not ok_station:
                return Fix(how="ask", desc=f"做{item}需要满足：{lack}", cost=COST_ASK)
            trial = vi.copy()
            takes, missing = recipe.requirements(amount, trial)
            for name, count in takes:
                trial.take(name, count)
            pre: List[Dict[str, Any]] = []
            feasible = True
            reasons = []
            for name, count in missing:
                sub = await self.fix_for(name, count, trial, depth + 1,
                                         seen | {item_key})
                if sub is None or sub.cost >= COST_ASK or not sub.steps:
                    feasible = False
                    break
                simulated = await self.world.simulate(sub.steps, trial)
                if not simulated.ok or simulated.final.count(name) < count:
                    feasible = False
                    break
                trial = simulated.final
                trial.take(name, count)
                pre.extend(sub.steps)
                reasons.append(sub.desc)
            if feasible:
                pre.append({"action": "craft", "item": item, "amount": amount,
                            "recipe_index": recipe.recipe_index})
                desc = f"合成{item}" + ("（" + "、".join(reasons) + "）" if reasons else "")
                return Fix(how="craft", desc=desc, cost=COST_CRAFT, steps=pre)

        # 4) 自己挖（矿物类，mod 的英文名矿石也要认得）
        if self._is_mineable(item):
            if (vi.has_axe if self._is_wood(item) else vi.has_pickaxe):
                return Fix(how="mine", desc=f"自己去挖{amount}个{item}",
                           cost=COST_MINE,
                           steps=[{"action": "chop" if self._is_wood(item) else "mine", "item": item,
                                   "amount": amount}])

        # 5) 实在没辙，求助主人
        return Fix(how="ask", desc=f"我搞不到{item}，主人能给我吗", cost=COST_ASK)

    def _is_wood(self, item: str) -> bool:
        from .item_npc_dict import item_id
        return item_id(item, getattr(self.agent, "registry", None)) == 9

    def _is_mineable(self, item: str) -> bool:
        """这东西能不能自己挖出来。

        mod 矿石叫 "Aerialite Ore" 这种英文名，只判断中文"矿"结尾会漏掉，
        导致猫娘明明能挖却说搞不到。
        """
        from .item_npc_dict import ORE_ITEM_TO_TILE, item_id
        registry = getattr(self.agent, "registry", None)
        iid = item_id(item, registry)
        if iid <= 0:
            return False
        if iid in ORE_ITEM_TO_TILE or iid in (2, 3, 9):
            return True
        return registry is not None and "ore" in registry.describe(str(iid)).get("tags", [])

    def _candidates(self, kind: str) -> List[str]:
        """能力物品的候选名单：原版常见 + mod 里同类物品。

        只认死那几个原版镐子的话，装了 mod 的存档会漏掉一堆能用的工具。
        """
        base = {
            "镐": list(PICKAXE_CANDIDATES),
            "斧": ("铜斧", "铁斧", "银斧", "金斧", "Wooden Axe"),
            "钓竿": ("木钓竿", "钓竿", "铁钓竿", "Fishing Rod"),
            "钩爪": ["抓钩", "Grappling Hook"],
        }.get(kind, [])
        if isinstance(base, tuple):
            base = list(base)
        reg = getattr(self.agent, "registry", None)
        if reg is None:
            return base
        want = "tool" if kind in ("镐", "斧", "钓竿") else "accessory"
        try:
            names: List[str] = []
            if not reg.live:
                return base
            for mod, items in getattr(reg, "mods", {}).items():
                uses = reg.uses.get(mod, {})
                tags = reg.tags.get(mod, {})
                for name in items:
                    if uses.get(name) != want:
                        continue
                    tg = tags.get(name, [])
                    if kind == "镐" and ("pickaxe" in tg or "pick" in tg
                                         or "镐" in name or "pickaxe" in name):
                        names.append(name)
                    elif kind == "斧" and ("axe" in tg or "斧" in name
                                           or "axe" in name):
                        names.append(name)
                    elif kind == "钓竿" and ("fishing" in tg or "钓竿" in name
                                             or "鱼竿" in name
                                             or "rod" in name):
                        names.append(name)
                    elif kind == "钩爪" and ("hook" in tg or "钩" in name
                                             or "hook" in name):
                        names.append(name)
            # mod 物品可能成百上千，逐个查箱子会把 mod 通信打爆，取前若干个
            return list(dict.fromkeys(base + names[:MAX_MOD_CANDIDATES]))
        except Exception:
            return base

    def _tool_score(self, name: str, kind: str) -> tuple:
        """Rank real tool candidates by their item capability, not name order."""
        registry = getattr(self.agent, "registry", None)
        iid = self.agent.resolve_item(name) if hasattr(self.agent, "resolve_item") else -1
        info = registry.describe(str(iid)) if registry is not None and iid > 0 else {}
        attr = {"镐": "pick", "斧": "axe", "钓竿": "fishing_pole"}.get(kind, "")
        power = int(info.get(attr, 0) or 0)
        power = max(power, int(info.get("pickaxe_power", 0) or 0),
                    int(info.get("axe_power", 0) or 0), int(info.get("damage", 0) or 0))
        # Known vanilla tiers remain useful when the registry does not expose
        # power fields, while unknown mod tools stay eligible below them.
        fallback_tier = {"铜": 1, "锡": 1, "木": 0, "仙人掌": 1,
                         "铁": 2, "铅": 2, "银": 3, "钨": 3,
                         "金": 4, "铂金": 5}.get(next(
                             (k for k in ("铜", "锡", "木", "仙人掌", "铁", "铅", "银", "钨", "金", "铂金") if k in name), ""), 0)
        return power, fallback_tier

    async def _fix_capability(self, kind: str, vi, depth: int,
                              seen: Optional[set[str]] = None) -> Optional[Fix]:
        """缺镐子/钩爪这类能力物品：挑一个最容易到手的具体物品。"""
        cands = sorted(self._candidates(kind),
                       key=lambda n: self._tool_score(n, kind), reverse=True)
        best: Optional[Fix] = None
        for name in cands:
            # 身上有就直接用
            if vi.count(name) > 0:
                return Fix(how="have", desc=f"身上有{name}", cost=COST_HAVE)
            try:
                chest = await self.agent.nearest_chest_with(name)
            except Exception:
                chest = None
            if chest is not None:
                return Fix(how="fetch",
                           desc=f"箱子里有{name}，先去拿上",
                           cost=COST_FETCH,
                           steps=[{"action": "fetch", "item": name,
                                   "amount": 1}])
        # 箱子里都没有，试试合成最便宜的那个（只在少量候选里找，别递归爆炸）
        for name in cands[:MAX_CRAFT_TRY]:
            # A recipe for an axe requiring an axe (or equivalent tool) is a
            # circular plan; do not keep asking the reasoner to make itself.
            name_key = str(name).casefold().strip()
            if name_key in (seen or set()):
                continue
            f = await self.fix_for(name, 1, vi, depth + 1,
                                   (seen or set()) | {str(kind).casefold()})
            if f and f.how in ("craft", "mine") and (best is None or f.cost < best.cost):
                best = f
        if best:
            return best
        # 无执行条件情感交互：说清楚缺什么、想要主人怎么帮
        ask_msg = {
            "镐": "主人我没有镐子挖不了矿喵，主人有也可以给我喵",
            "斧": "主人我没有斧头无法砍树喵，主人有也可以给我喵",
            "钓竿": "主人我没有钓竿钓不了鱼喵，主人有也可以给我喵",
            "钩爪": "主人我没有钩爪爬不上去喵，主人有也可以给我喵",
        }.get(kind, f"我没有{kind}，主人给我一个吧")
        # Prefer an actionable blocker from candidate assessment over a vague
        # request. This distinguishes missing materials from missing station.
        if self.world.book is not None:
            try:
                await self.world.book.refresh()
                await self.world.book.refresh_availability()
                for name in cands[:MAX_CRAFT_TRY]:
                    recipe = self.world.book.find(name, inventory=vi, amount=1)
                    if recipe is None:
                        continue
                    if not recipe.environment_ready:
                        ready, lack = await self.world.station_ready(name, recipe)
                        if not ready:
                            ask_msg = f"做{name}需要合成环境：{lack}"
                            break
                    _, missing = recipe.requirements(1, vi)
                    if missing:
                        materials = "、".join(f"{n}×{count}" for n, count in missing[:3])
                        ask_msg = f"做{name}还缺材料：{materials}；附近没有可取得来源"
            except Exception:
                pass
        return Fix(how="ask", desc=ask_msg, cost=COST_ASK)
