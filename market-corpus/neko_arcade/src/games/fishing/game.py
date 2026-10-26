"""钓鱼小游戏 —— 核心玩法。

玩法：每日钓鱼 → 鱼缸收藏 → 鱼市售鱼 → 鱼竿鱼饵 → 鱼蛋商店。
指令（handle_command 的 cmd）：
  钓鱼 / 钓鱼 x       —— 抛竿（x 为次数，默认 1）
  鱼缸 / 查看鱼缸     —— 查看收藏
  升级鱼缸            —— 扩容（+5 容量 +2 每日钓次）
  鱼市 / 售鱼 [全部|序号|鱼名] —— 出售鱼获换鱼蛋
  鱼竿 / 换竿 [名称]  —— 查看/切换鱼竿
  鱼饵 / 换饵 [名称]  —— 查看/切换鱼饵
  商店 / 购买 [名称]  —— 购买鱼竿/鱼饵/钓鱼券

鱼竿的 failProtection 是"空竿保护": 上鱼失败时按该概率变成干净的"空竿"
(不掉垃圾、不触发倒霉事件)。fishdata.json 里带 seasonal 的鱼只在活动窗口内
可钓, requiredBaitIds 指定了饵的鱼必须挂对应鱼饵(否则永远钓不到)。
  帮助                —— 玩法说明
"""

from __future__ import annotations

import os
import random
from datetime import date
from typing import Any, Dict, List, Optional

from ...core.contracts import GameAdapter
from ...core.datafile import read_json
from .data import (
    BAITS,
    RARITY_LABELS,
    RARITY_SELL_LIMITS,
    RODS,
    UNSELLABLE,
)

_DATA = None


def _load_fishdata() -> Dict[str, Any]:
    """惰性加载鱼池数据（优先 fishdata.json.gz，包里只发布压缩版）。"""
    global _DATA
    if _DATA is None:
        path = os.path.join(os.path.dirname(__file__), "fishdata.json")
        _DATA = read_json(path, {})
    return _DATA


def _roll_rarity(rarity_bias: Optional[Dict[str, float]] = None) -> str:
    """按稀有度权重抽取稀有度（应用鱼竿/鱼饵的倾向修正后重新归一化）。"""
    data = _load_fishdata()
    weights: Dict[str, float] = dict(data["rarityWeights"])
    if rarity_bias:
        for r, delta in rarity_bias.items():
            if r in weights:
                weights[r] = max(0.0, weights[r] + delta)
    total = sum(weights.values())
    roll = random.random() * total
    acc = 0.0
    for r, w in weights.items():
        acc += w
        if roll <= acc:
            return r
    return "common"


def _season_ok(meta: Dict[str, Any], today: Any) -> bool:
    """活动窗口判定: startDate <= today < endDateExclusive。

    窗口字段写坏(缺字段/日期不合法) → 当成普通鱼放行: 一条脏数据不该让整池消失。
    """
    try:
        start = date.fromisoformat(str(meta.get("startDate")))
        end = date.fromisoformat(str(meta.get("endDateExclusive")))
    except (TypeError, ValueError):
        return True
    return start <= today < end


def _eligible_pool(rarity: str, bait_id: str = "plain",
                   today: Optional[Any] = None) -> List[Dict[str, Any]]:
    """按活动窗口 + 指定鱼饵过滤鱼池。

    fishdata.json 里的 seasonal / requiredBaitIds 以前**被引擎完全忽略** —— 节日鱼
    全年都能钓上来、指定饵也不起作用(线上表现为"非节日钓到节日限定鱼")。
    过滤后池子为空 → 退回原始池子，保证永远有鱼可出(不会冒"未知生物")。
    """
    data = _load_fishdata()
    pool = data["fishTypes"].get(rarity) or []
    if not pool:
        return []
    day = today or date.today()
    ok: List[Dict[str, Any]] = []
    for tpl in pool:
        season = tpl.get("seasonal")
        if isinstance(season, dict) and not _season_ok(season, day):
            continue
        need = tpl.get("requiredBaitIds")
        if isinstance(need, list) and need and bait_id not in need:
            continue
        ok.append(tpl)
    return ok or pool


def _generate_fish(rarity: str, bait_id: str = "plain",
                   today: Optional[Any] = None) -> Dict[str, Any]:
    """从对应稀有度的鱼池生成一条鱼（随机尺寸/重量）。"""
    pool = _eligible_pool(rarity, bait_id, today)
    if not pool:
        return {"name": "未知生物", "rarity": rarity, "length": 10, "weight": 0.1}
    template = random.choice(pool)
    length = round(random.uniform(template["size"]["min"], template["size"]["max"]), 1)
    weight = round(random.uniform(template["weight"]["min"], template["weight"]["max"]), 2)
    return {
        "name": template["name"], "rarity": rarity,
        "length": length, "weight": weight,
        "seasonal": bool(template.get("seasonal")),
        "event": str((template.get("seasonal") or {}).get("eventId") or ""),
    }


def _fish_sell_value(fish: Dict[str, Any]) -> int:
    """鱼的价值：按稀有度区间 + 体型质量（0.3*长度 + 0.7*重量 归一化）。"""
    if fish["rarity"] in UNSELLABLE:
        return 0
    data = _load_fishdata()
    template = None
    for pool in data["fishTypes"].values():
        for t in pool:
            if t["name"] == fish["name"]:
                template = t
                break
        if template:
            break
    if not template:
        quality = 0.5
    else:
        s, w = template["size"], template["weight"]
        lq = (fish["length"] - s["min"]) / max(0.01, s["max"] - s["min"])
        wq = (fish["weight"] - w["min"]) / max(0.01, w["max"] - w["min"])
        quality = min(1.0, max(0.0, lq * 0.3 + wq * 0.7))
    lo, hi = RARITY_SELL_LIMITS[fish["rarity"]]
    return round(lo + (hi - lo) * quality)


def _today_key() -> str:
    return date.today().isoformat()


class FishingGame(GameAdapter):
    id = "fishing"
    name = "钓鱼"
    description = "每日抛竿钓鱼，收藏稀有鱼获，鱼市换鱼蛋"
    version = "0.1.0"
    icon = "🎣"

    def _cfg(self, key: str, default: Any = None) -> Any:
        """读取 config.json 配置（支持 a.b 嵌套键），缺省回退默认值。"""
        cfg = getattr(self, "_config", None) or {}
        cur = cfg
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    # ── 指令路由 ──────────────────────────

    async def handle_action(
        self, user_id: str, cmd: str, args: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        args = args or {}
        c = (cmd or "").strip()

        # 用「包含」而非「开头」匹配，自然说法（“看看我的鱼缸”“我想钓鱼”）
        # 也能命中，避免被判成 unknown 反复触发邀请。
        if "钓鱼" in c:
            n = self._parse_int(c, args.get("amount", 1))
            return await self._do_fishing(user_id, min(n, int(self._cfg("max_cast_per_command", 10))))
        # 买/卖分流：含「买」→ 购买；「售鱼/卖鱼」→ 卖；裸「鱼市」→ 市场商品列表。
        # 之前「鱼市」被一律当卖鱼，导致「鱼市买鱼饵」无法购买、裸「鱼市」也看不到商品。
        if any(k in c for k in ("买", "购买", "购")):
            # 先剥「买」再剥地点词，避免「鱼市买鱼饵」被「鱼市」截成「买鱼饵」
            name = self._strip_kw(c, "购买", "买", "购")
            name = self._strip_kw(name, "鱼市", "鱼店", "商店")
            return await self._buy(user_id, name)
        if "售鱼" in c or "卖鱼" in c:
            return await self._do_sell(user_id, self._strip_kw(c, "售鱼", "卖鱼"))
        if "鱼市" in c:
            # 裸「鱼市」→ 商品列表；「鱼市 卖X」已在上面拦截
            return await self._buy(user_id, self._strip_kw(c, "鱼市", "鱼店", "商店"))
        if "鱼缸" in c and "升级" in c:
            return await self._upgrade_tank(user_id)
        if "鱼缸" in c:
            return await self._show_tank(user_id)
        if "换竿" in c or "鱼竿" in c:
            return await self._switch_rod(user_id, self._strip_kw(c, "换竿", "鱼竿"))
        if "换饵" in c or "鱼饵" in c:
            return await self._switch_bait(user_id, self._strip_kw(c, "换饵", "鱼饵"))
        if "商店" in c or "鱼店" in c:
            return await self._buy(user_id, self._strip_kw(c, "商店", "鱼店"))
        # 不认识的指令 → 让插件发提示
        return {"message": "", "outcome": "unknown", "facts": []}

    @staticmethod
    def _strip_kw(text: str, *keywords: str) -> str:
        """去掉 text 中第一个命中的关键词，返回其后的文本（用于提取指令参数）。"""
        for kw in sorted(keywords, key=len, reverse=True):
            if kw in text:
                return text.split(kw, 1)[1].strip()
        return text.strip()

    # ── 核心：钓鱼 ────────────────────────

    async def _do_fishing(self, user_id: str, amount: int = 1) -> Dict[str, Any]:
        data = await self._load_player(user_id)
        today = _today_key()

        # 日切
        if data.get("day") != today:
            data["day"] = today
            data["casts_used"] = 0

        daily_casts = int(self._cfg("daily_casts", 5)) + data["tank"]["level"] * int(self._cfg("tank.extra_casts", 2))
        left = daily_casts - data.get("casts_used", 0)
        if left <= 0:
            return {
                "message": f"今天的鱼竿已经抛累了喵（{left}）……明天再来吧，或者去「鱼市」买张钓鱼券？",
                "facts": [], "outcome": "no_casts",
                "today_used": data["casts_used"], "daily_casts": daily_casts,
            }

        amount = max(1, min(amount, left))
        rod = RODS.get(data["rod"], RODS["starter"])
        bait_id = data["bait"]
        bait = BAITS[bait_id]

        results = []
        gained = 0
        for _ in range(amount):
            out = self._single_cast(data, rod, bait)
            results.append(out)
            if out.get("fish"):
                gained += 1
            # 消耗鱼饵（默认饵不消耗）
            if bait_id != "plain" and data["baits"].get(bait_id, 0) > 0:
                data["baits"][bait_id] -= 1
                if data["baits"][bait_id] <= 0:
                    data["bait"] = "plain"
                    bait = BAITS["plain"]

        data["casts_used"] = data.get("casts_used", 0) + amount
        await self._save_player(user_id, data)

        lines = []
        for r in results:
            if r["kind"] == "fish":
                mark = "✨" if r.get("seasonal") else "🐟"
                lines.append(
                    f"{mark} {r['name']}（{RARITY_LABELS[r['rarity']]}）{r['length']}cm {r['weight']}kg"
                    + (f"，价值 {r['value']} 鱼蛋" if r["value"] else "")
                )
            elif r["kind"] == "trash":
                lines.append(f"🗑️ 钓上来一个「{r['name']}」……")
            else:
                lines.append(f"💨 {r.get('message') or '什么都没咬钩……'}")

        left_after = daily_casts - data["casts_used"]
        # 结构化结果（facts + outcome，供大脑情感渲染）
        facts = []
        for r in results:
            if r["kind"] == "fish":
                facts.append({
                    "kind": "catch", "name": r["name"], "rarity": r["rarity"],
                    "size": r["length"], "weight": r["weight"], "value": r.get("value", 0),
                    "seasonal": bool(r.get("seasonal")),
                })
            elif r["kind"] == "trash":
                facts.append({"kind": "trash", "item": r["name"]})
            elif r["kind"] == "event":
                facts.append({"kind": "event", "message": r.get("message", "")})
            else:
                facts.append({"kind": "empty"})
        if not facts:
            facts = [{"kind": "empty"}]
        caught = [f for f in facts if f["kind"] == "catch"]
        rarities = {f["rarity"] for f in caught}
        if rarities & {"legendary", "？"}:
            outcome = "caught_legendary"
        elif any(r in ("epic", "rare") for r in rarities):
            outcome = "caught_rare"
        elif caught:
            outcome = "caught"
        else:
            outcome = "nothing" if any(f["kind"] == "empty" for f in facts) else "trash"
        return {
            "message": f"🎣 抛竿 x{amount}，钓到 {gained} 条鱼！\n" + "\n".join(lines) + f"\n今日剩余 {left_after} 次",
            "facts": facts, "outcome": outcome,
            "today_used": data["casts_used"],
            "daily_casts": daily_casts,
        }

    def _single_cast(self, data: Dict[str, Any], rod: Dict[str, Any], bait: Dict[str, Any]) -> Dict[str, Any]:
        """单次抛竿结算。"""
        data_pool = _load_fishdata()
        rate = float(self._cfg("base_catch_rate", 0.2)) + rod.get("catchRateBonus", 0) + bait.get("catchRateBonus", 0)
        if random.random() > rate:
            # 空竿保护: 鱼竿的 failProtection 以前是**死配置**(「稳钓重竿」写着
            # 0.16 保护却一点用没有，比新手竿还差)。空竿保护命中时，这一竿既不
            # 掉垃圾也不触发倒霉事件，就是干干净净什么都没咬钩。
            protect = float(rod.get("failProtection", 0) or 0)
            if random.random() < protect:
                return {"kind": "empty"}
            # 上鱼失败：垃圾 / 随机事件
            if random.random() < 0.5:
                name = random.choice(data_pool["trashItems"])
                return {"kind": "trash", "name": name}
            return {"kind": "event", "message": random.choice(data_pool["randomEvents"])}

        rarity = _roll_rarity({
            **(rod.get("rarityBias") or {}),
            **(bait.get("rarityBias") or {}),
        })
        fish = _generate_fish(rarity, bait_id=str(bait.get("id") or "plain"))
        fish["value"] = _fish_sell_value(fish)

        # 入缸（传说/彩蛋必入；其他先看容量）
        tank = data["tank"]
        cap = int(self._cfg("tank.default_capacity", 5)) + tank["level"] * int(self._cfg("tank.upgrade_size", 5))
        kept = False
        if fish["rarity"] in UNSELLABLE or len(tank["fishes"]) < cap:
            tank["fishes"].append(fish)
            kept = True
        data["stats"]["total_caught"] = data["stats"].get("total_caught", 0) + 1
        species = data["stats"].setdefault("species", [])
        if fish["name"] not in species:
            species.append(fish["name"])

        msg = f"钓到了 {fish['name']}！"
        if not kept:
            msg += " 但鱼缸满了，只好放生……"
        elif fish["rarity"] in UNSELLABLE:
            msg += " 稀有物种！已放入鱼缸收藏"
        return {"kind": "fish", "fish": fish, "message": msg, **fish}

    # ── 鱼缸 ─────────────────────────────

    async def _show_tank(self, user_id: str) -> Dict[str, Any]:
        data = await self._load_player(user_id)
        tank = data["tank"]
        cap = int(self._cfg("tank.default_capacity", 5)) + tank["level"] * int(self._cfg("tank.upgrade_size", 5))
        fishes = tank["fishes"]
        # 图鉴进度: stats.species 一直在记, 但以前从不展示(玩家看不到自己收集了多少种)
        total_species = sum(len(v) for v in _load_fishdata()["fishTypes"].values())
        species = (data.get("stats") or {}).get("species") or []
        head = (f"🏺 鱼缸（Lv.{tank['level']}，{len(fishes)}/{cap}）"
                f"｜图鉴 {len(species)}/{total_species} 种")
        view_fact = {"kind": "tank_view", "level": tank["level"], "count": len(fishes),
                     "capacity": cap, "species": len(species), "species_total": total_species}
        if not fishes:
            return {"message": f"{head}\n鱼缸还是空的喵……先去「钓鱼」吧！",
                    "facts": [view_fact], "outcome": "tank"}
        lines = []
        for i, f in enumerate(fishes, 1):
            value = f.get("value", _fish_sell_value(f))
            value_txt = f" +{value}蛋" if value else " 收藏"
            mark = "✨" if f.get("seasonal") else ""
            lines.append(f"{i}. {mark}{f['name']}（{RARITY_LABELS[f['rarity']]}）{f['length']}cm {f['weight']}kg{value_txt}")
        return {"message": head + "\n" + "\n".join(lines),
                "facts": [view_fact], "outcome": "tank"}

    async def _upgrade_tank(self, user_id: str) -> Dict[str, Any]:
        data = await self._load_player(user_id)
        tank = data["tank"]
        if tank["level"] >= int(self._cfg("tank.max_level", 10)):
            return {"message": f"鱼缸已经是最高的 {int(self._cfg('tank.max_level', 10))} 级了喵！"}
        cost = self._tank_upgrade_cost(tank["level"] + 1)
        if data["coins"] < cost:
            return {"message": f"升级鱼缸需要 {cost} 鱼蛋，你现在只有 {data['coins']} 喵……"}
        data["coins"] -= cost
        tank["level"] += 1
        await self._save_player(user_id, data)
        cap = int(self._cfg("tank.default_capacity", 5)) + tank["level"] * int(self._cfg("tank.upgrade_size", 5))
        casts = int(self._cfg("daily_casts", 5)) + tank["level"] * int(self._cfg("tank.extra_casts", 2))
        return {"message": f"🏺 鱼缸升到 Lv.{tank['level']}！容量 {cap}，每日钓次 +{int(self._cfg('tank.extra_casts', 2))}（共 {casts} 次）",
                "facts": [{"kind": "tank_upgrade", "new_level": tank["level"], "capacity": cap}],
                "outcome": "upgrade"}

    # ── 鱼市 ─────────────────────────────

    async def _do_sell(self, user_id: str, target: str) -> Dict[str, Any]:
        data = await self._load_player(user_id)
        tank = data["tank"]
        fishes = tank["fishes"]
        target = (target or "").strip()
        if not target or target == "全部":
            indices = list(range(len(fishes)))
        elif target.isdigit():
            idx = int(target) - 1
            if idx < 0 or idx >= len(fishes):
                return {"message": "鱼缸里没有这一条喵"}
            indices = [idx]
        else:
            indices = [i for i, f in enumerate(fishes) if target in f["name"]]
            if not indices:
                return {"message": f"鱼缸里没有「{target}」喵，试试「鱼缸」看看都有什么"}

        total = 0
        sold_names = []
        for i in sorted(indices, reverse=True):
            fish = fishes[i]
            value = fish.get("value", _fish_sell_value(fish))
            if value <= 0:
                continue
            total += value
            sold_names.append(f"{fish['name']} +{value}")
            fishes.pop(i)

        if total == 0:
            return {"message": "没有可以出售的鱼喵（传说/彩蛋鱼只能收藏）"}
        data["coins"] += total
        data["stats"]["market_trades"] = data["stats"].get("market_trades", 0) + 1
        await self._save_player(user_id, data)
        return {
            "message": f"💰 售出 {len(sold_names)} 条鱼，获得 {total} 鱼蛋！\n" + "\n".join(sold_names) + f"\n余额：{data['coins']} 鱼蛋",
            "facts": [{"kind": "sell", "count": len(sold_names), "total_value": total}],
            "outcome": "sold", "coins": data["coins"],
        }

    # ── 装备 ─────────────────────────────

    async def _switch_rod(self, user_id: str, name: str) -> Dict[str, Any]:
        data = await self._load_player(user_id)
        name = (name or "").strip()
        if not name:
            owned = ", ".join(RODS[r]["name"] for r in data["rods"])
            return {"message": f"当前鱼竿：{RODS[data['rod']]['name']}\n拥有：{owned}\n「换竿 疾风短竿」来切换"}
        rod = next((r for r in RODS.values() if name in r["name"] or name in r["id"]), None)
        if rod is None or rod["id"] not in data["rods"]:
            return {"message": f"没有「{name}」这根鱼竿喵，去「商店」买一根吧"}
        data["rod"] = rod["id"]
        await self._save_player(user_id, data)
        return {"message": f"🎣 换上了 {rod['name']}！{rod['description']}",
                "facts": [{"kind": "equip", "item": rod["id"], "type": "rod"}],
                "outcome": "equip"}

    async def _switch_bait(self, user_id: str, name: str) -> Dict[str, Any]:
        data = await self._load_player(user_id)
        name = (name or "").strip()
        if not name:
            owned = ", ".join(f"{BAITS[b]['name']}x{count}" for b, count in data["baits"].items() if count > 0)
            return {"message": f"当前鱼饵：{BAITS[data['bait']]['name']}\n拥有：{owned or '只有默认饵'}\n「换饵 香谷鱼饵」来切换"}
        bait = next((b for b in BAITS.values() if name in b["name"] or name in b["id"]), None)
        if bait is None:
            return {"message": f"没有「{name}」这种鱼饵喵"}
        if bait["id"] != "plain" and data["baits"].get(bait["id"], 0) <= 0:
            return {"message": f"「{bait['name']}」用完了喵，去商店买吧"}
        data["bait"] = bait["id"]
        await self._save_player(user_id, data)
        return {"message": f"🪱 挂上了 {bait['name']}！{bait['description']}",
                "facts": [{"kind": "equip", "item": bait["id"], "type": "bait"}],
                "outcome": "equip"}

    # ── 商店 ─────────────────────────────

    async def _buy(self, user_id: str, name: str) -> Dict[str, Any]:
        data = await self._load_player(user_id)
        name = (name or "").strip()
        if not name:
            lines = ["🛒 鱼市商品：", ""]
            for item in [*RODS.values(), *BAITS.values()]:
                if item["id"] == "plain" or item["price"] <= 0:
                    continue
                lines.append(f"{item['name']}：{item['price']} 鱼蛋（{item['description']}）")
            lines.append("\n「购买 疾风短竿」来买，鱼饵按份购买")
            return {"message": "\n".join(lines)}

        item = next((i for i in [*RODS.values(), *BAITS.values()] if name in i["name"] or name in i["id"]), None)
        if item is None:
            # 清洗量词/语气词后重试(如「买个猎珍长竿」→「个猎珍长竿」→「猎珍长竿」)
            clean = name
            for w in ("一根", "一个", "一支", "一把", "一条", "个", "根", "支", "把"):
                clean = clean.replace(w, "")
            clean = clean.strip()
            if clean and clean != name:
                item = next((i for i in [*RODS.values(), *BAITS.values()]
                             if clean in i["name"] or clean in i["id"]), None)
                if item is not None:
                    name = clean
            if item is None:
                return {"message": f"鱼市没有「{name}」喵"}
        price = item["price"]
        if price <= 0:
            return {"message": f"「{item['name']}」不卖喵"}
        # 幂等: 已拥有的鱼竿/鱼饵不再扣费(宿主可能重复执行同一指令)
        if item["id"] in RODS and item["id"] in data["rods"]:
            return {"message": f"「{item['name']}」你已经有了喵,不用重复买~",
                    "outcome": "buy_idle", "facts": [{"kind": "buy_idle", "item": item["id"]}]}
        if item["id"] not in RODS and item["id"] != "plain" and data["baits"].get(item["id"], 0) > 0:
            return {"message": f"「{item['name']}」你已经有了喵,不用重复买~",
                    "outcome": "buy_idle", "facts": [{"kind": "buy_idle", "item": item["id"]}]}
        if data["coins"] < price:
            return {"message": f"需要 {price} 鱼蛋，你还差 {price - data['coins']} 喵……多钓几条鱼来卖吧"}

        data["coins"] -= price
        if item["id"] in RODS:
            data["rods"].append(item["id"]) if item["id"] not in data["rods"] else None
            data["rods"] = list(dict.fromkeys(data["rods"]))
            msg = f"🎣 买到了 {item['name']}！{item['description']}"
        else:
            data["baits"][item["id"]] = data["baits"].get(item["id"], 0) + item["packSize"]
            msg = f"🪱 买了 {item['packSize']} 份 {item['name']}（共 {data['baits'][item['id']]} 份）"
        await self._save_player(user_id, data)
        return {"message": f"💰 {msg}\n余额：{data['coins']} 鱼蛋",
                "facts": [{"kind": "buy", "item": item["id"], "price": price}],
                "outcome": "buy", "coins": data["coins"]}

    # ── 帮助 / 状态 ──────────────────────

    async def get_status(self, user_id: str = "default") -> Dict[str, Any]:
        """面板展示：每日剩余次数。"""
        try:
            data = await self._load_player(user_id)
            today = _today_key()
            if data.get("day") != today:
                data["casts_used"] = 0
            daily = int(self._cfg("daily_casts", 5)) + data["tank"]["level"] * int(self._cfg("tank.extra_casts", 2))
            return {
                "daily_casts": daily,
                "casts_left": max(0, daily - data.get("casts_used", 0)),
                "coins": data["coins"],
                "tank_level": data["tank"]["level"],
                "tank_count": len(data["tank"]["fishes"]),
                "rod": RODS[data["rod"]]["name"],
            }
        except Exception:
            return {}

    def _tank_upgrade_cost(self, level: int) -> int:
        """从 level 升到 level+1 的花费（读配置，缺省回退默认值）。"""
        base = int(self._cfg("tank.upgrade_cost_base", 300))
        growth = float(self._cfg("tank.upgrade_cost_growth", 1.6))
        return int(base * (growth ** (level - 1)))

    def support_panel(self):
        """面板配置 schema（中文标签，锅巴风格）。"""
        return {
            "schemas": [
                {"label": "钓鱼基础", "component": "Group"},
                {"field": "base_catch_rate", "label": "基础捕获率", "component": "InputNumber",
                 "props": {"min": 0.01, "max": 1, "step": 0.05}, "help": "空竿基础上鱼概率（0~1）"},
                {"field": "daily_casts", "label": "每日免费抛竿", "component": "InputNumber",
                 "props": {"min": 1, "max": 100}, "help": "每天免费抛竿次数"},
                {"field": "max_cast_per_command", "label": "单次最多抛竿", "component": "InputNumber",
                 "props": {"min": 1, "max": 100}, "help": "一条指令最多连续抛竿几次"},
                {"label": "鱼缸", "component": "Group"},
                {"field": "tank.default_capacity", "label": "初始容量", "component": "InputNumber",
                 "props": {"min": 1, "max": 100}},
                {"field": "tank.upgrade_size", "label": "升级容量增量", "component": "InputNumber",
                 "props": {"min": 1, "max": 100}},
                {"field": "tank.extra_casts", "label": "升级额外抛竿", "component": "InputNumber",
                 "props": {"min": 0, "max": 20}},
                {"field": "tank.max_level", "label": "最高等级", "component": "InputNumber",
                 "props": {"min": 1, "max": 100}},
                {"field": "tank.upgrade_cost_base", "label": "升级基础费用", "component": "InputNumber",
                 "props": {"min": 0, "max": 100000}},
                {"field": "tank.upgrade_cost_growth", "label": "费用增长率", "component": "InputNumber",
                 "props": {"min": 1, "max": 10, "step": 0.1}},
            ]
        }

    # ── 存档 ─────────────────────────────

    def _new_player(self) -> Dict[str, Any]:
        return {
            "coins": 0,
            "rod": "starter",
            "rods": ["starter"],
            "bait": "plain",
            "baits": {"plain": 0},
            "tank": {"level": 0, "fishes": []},
            "stats": {"total_caught": 0, "species": [], "market_trades": 0},
            "day": _today_key(),
            "casts_used": 0,
        }

    async def _load_player(self, user_id: str) -> Dict[str, Any]:
        data = await self.get_user_data(user_id)
        if data is None:
            data = self._new_player()
            await self.save_user_data(user_id, data)
        return data

    async def _save_player(self, user_id: str, data: Dict[str, Any]) -> None:
        await self.save_user_data(user_id, data)

    @staticmethod
    def _parse_int(text: str, default: int = 1) -> int:
        digits = "".join(ch for ch in text if ch.isdigit())
        return int(digits) if digits else default
