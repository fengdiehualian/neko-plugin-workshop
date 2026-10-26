"""俄罗斯轮盘游戏：和猫娘赌猫粮。

玩法轮盘签到 / 装弹 / 开枪(可连发) / 认输 / 战绩 / 五类排行 / 庄家抽水。
单人化：玩家 vs 猫娘(NPC 顶替第二个玩家)，轮流开枪，猫娘既是庄家又是对手。
经济：猫粮(轮盘签到领取，装弹下注)。赢家拿走赌注-手续费，输家输掉赌注。
"""

from __future__ import annotations

import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from ...core.contracts import GameAdapter, build_fact

game_class = "RussianGame"

CHAMBERS = 7

# 虚拟榜 NPC(赌徒名号，玩家 vs 虚拟群众)
NPC_NAMES = ["赌神老K", "千面狐狸", "铁公鸡", "梭哈王", "幸运兔",
             "冷面赌徒", "夜猫子", "白手套", "猫娘庄家"]


def random_bullet(num: int) -> List[int]:
    """随机子弹排列：7 膛，随机 num 发子弹。"""
    bullet = [0] * CHAMBERS
    for i in random.sample(range(CHAMBERS), num):
        bullet[i] = 1
    return bullet


class RussianGame(GameAdapter):
    id = "russian"
    name = "俄罗斯轮盘"
    description = "和猫娘玩俄罗斯轮盘赌猫粮：装弹、开枪、中弹者输，庄家抽水。"
    version = "0.1.0"
    icon = "🎰"

    # ── 指令表 (关键词, 说明, handler名)；最长关键词优先匹配 ──
    _RULES: List[Tuple[List[str], str, str]] = [
        (["轮盘签到", "轮盘打卡", "领猫粮"], "每日签到领猫粮", "sign"),
        (["装弹"], "装弹发起对决(装弹 子弹数 金额)", "load"),
        (["开枪", "咔", "嘭", "嘣"], "开枪(可连开N枪)", "fire"),
        (["逃跑", "认输"], "认输输掉赌注", "surrender"),
        (["我的猫粮"], "查看猫粮余额", "food"),
        (["我的战绩"], "查看胜败战绩", "record"),
        (["猫粮排行"], "猫粮排行榜", "rank"),
        (["胜场排行"], "胜场排行榜", "rank"),
        (["败场排行"], "败场排行榜", "rank"),
        (["欧皇排行"], "赢取猫粮排行榜", "rank"),
        (["慈善家排行"], "输掉猫粮排行榜", "rank"),
    ]

    def __init__(self, plugin: Any) -> None:
        super().__init__(plugin)
        self._cache: Dict[str, Dict[str, Any]] = {}

    # ── 配置/存档 ─────────────────────────

    def _cfg(self, key: str, default: Any = None) -> Any:
        """读 config.json(支持 a.b 嵌套), 缺省回退。"""
        cfg = getattr(self, "_config", None) or {}
        cur = cfg
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    def _default_save(self) -> Dict[str, Any]:
        return {
            "food": int(self._cfg("initial_food", 500)), "make_food": 0, "lose_food": 0,
            "win_count": 0, "lose_count": 0,
            "streak": 0, "best_streak": 0,
            "last_sign": "", "sign_days": 0,
            "duel": None,          # 进行中的对决状态机
        }

    async def _load(self, user_id: str) -> Dict[str, Any]:
        if user_id in self._cache:
            return self._cache[user_id]
        data = await self.get_user_data(user_id, None) or {}
        save = self._default_save()
        if isinstance(data, dict):
            for k in save:
                if k in data:
                    save[k] = data[k]
        self._cache[user_id] = save
        return save

    async def _save(self, user_id: str, save: Dict[str, Any]) -> None:
        self._cache[user_id] = save
        await self.save_user_data(user_id, save)

    async def on_unload(self) -> None:
        self._cache.clear()

    # ── 生命周期 ─────────────────────────

    async def on_tick(self, user_id: str) -> None:
        """对局超时(装弹后长期不开枪)自动取消并退回赌注。"""
        save = await self._load(user_id)
        duel = save.get("duel")
        if not duel:
            return
        timeout = int(self._cfg("duel_timeout", 300))
        if time.time() - duel["started_at"] > timeout:
            bet = duel["money"]
            save["food"] += bet
            save["duel"] = None
            await self._save(user_id, save)
            # 局中提醒: 只返回文字, 推送统一由本体 brain 做(契约要求)
            return (
                f"（轮盘对决超时喵，{bet} 猫粮已退回。"
                f"猫娘嘟囔：『主人装完弹就跑，害人家白紧张！』）")

    # ── 核心接口 ─────────────────────────

    async def handle_action(self, user_id: str, cmd: str,
                            args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        cmd = (cmd or "").strip().lstrip("#").strip()
        if not cmd:
            return {"message": "想赌猫粮喵?「轮盘签到」领猫粮，「装弹 1 200」开一局!",
                    "outcome": "idle"}

        # 最长关键词优先
        handler_name = None
        best_len = 0
        for keywords, _, fn in self._RULES:
            for kw in keywords:
                if kw and kw in cmd and len(kw) > best_len:
                    best_len = len(kw)
                    handler_name = fn
                    break

        if not handler_name:
            # 契约: 不认识的指令 → unknown, 大脑触发邀请
            return {"facts": [], "outcome": "unknown", "message": ""}

        handler = getattr(self, f"_h_{handler_name}", None)
        if not handler:
            return {"message": "该功能还没做好喵", "outcome": "error"}
        return await handler(user_id, cmd)

    def classify_event(self, outcome: str, facts: List[Dict[str, Any]]) -> str:
        """赢=highlight(LLM 渲染), 输/认输=lowlight。"""
        if outcome in ("win",):
            return "highlight"
        if outcome in ("lose", "surrender"):
            return "lowlight"
        return super().classify_event(outcome, facts)

    def wants_card(self, outcome: str, facts: List[Dict[str, Any]]) -> bool:
        return outcome in ("win", "lose")

    # ── 指令实现 ─────────────────────────

    async def _h_sign(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        today = time.strftime("%Y-%m-%d")
        if save.get("last_sign") == today:
            return {"outcome": "already_sign",
                    "message": "今天已经签过到啦喵，明天再来~"}
        food = random.randint(int(self._cfg("sign_food_min", 1)),
                              int(self._cfg("sign_food_max", 100)))
        save["food"] += food
        save["last_sign"] = today
        save["sign_days"] += 1
        await self._save(user_id, save)
        return {"outcome": "sign", "facts": [build_fact("sign", food=food)],
                "message": f"轮盘签到成功喵!获得 {food} 猫粮，祝你好运~"
                           f"(已连续签到 {save['sign_days']} 天)"}

    async def _h_load(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.get("duel"):
            return {"outcome": "duel_active",
                    "message": "还有一局在进行呢，先「开枪」或「逃跑」喵!"}
        nums = re.findall(r"\d+", cmd)
        bullets = 1
        bet = int(self._cfg("default_bet", 200))
        if nums:
            v = int(nums[0])
            if 1 <= v <= 6:
                bullets = v
            else:
                bet = v
        if len(nums) >= 2:
            bet = int(nums[1])
        max_bet = int(self._cfg("max_bet", 1000))
        bet = max(1, min(bet, max_bet))
        if save["food"] < bet:
            return {"outcome": "no_food",
                    "message": f"猫粮不够喵，下注 {bet} 但你现在只有 {save['food']} 猫粮!"
                               f"先「轮盘签到」领点猫粮吧~"}
        save["food"] -= bet
        save["duel"] = {
            "bullet": random_bullet(bullets), "index": 0,
            "next": "player", "money": bet,
            "started_at": time.time(), "bullet_num": bullets,
        }
        await self._save(user_id, save)
        bullet_str = "".join("·" if b == 0 else "●" for b in save["duel"]["bullet"])
        return {"outcome": "duel_start",
                "facts": [build_fact("duel_start", bullets=bullets, money=bet)],
                "message": (f"装弹完成喵!{bullets} 发子弹已装入 7 膛左轮，"
                            f"赌注 {bet} 猫粮。\n"
                            f"猫娘深吸一口气：『来吧，主人先开枪!』\n"
                            f"(子弹分布，结算揭晓: {bullet_str})")}

    async def _h_fire(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        duel = save.get("duel")
        if not duel:
            return {"outcome": "no_duel",
                    "message": "还没有对决呢，先「装弹」开一局喵!"}
        if duel["next"] != "player":
            # 防御兜底: 轮到猫娘(正常单人化会自动结算)
            res = await self._neko_shot(user_id, save)
            if res:
                return res
            duel = save["duel"]

        nums = re.findall(r"\d+", cmd)
        n = int(nums[0]) if nums else 1
        facts: List[Dict[str, Any]] = []
        for _ in range(n):
            idx = duel["index"]
            if idx >= CHAMBERS:
                break
            hit = duel["bullet"][idx] == 1
            duel["index"] = idx + 1
            facts.append(build_fact("fire", chamber=idx + 1, hit=hit, shooter="player"))
            if hit:
                return await self._settle(user_id, save, "player", facts)

        # 玩家 N 枪全空 → 猫娘回合(自动开枪)
        res = await self._neko_shot(user_id, save, facts)
        if res:
            return res
        await self._save(user_id, save)
        return {"outcome": "fire", "facts": facts,
                "message": "咔…空枪!轮到猫娘了喵…(猫娘闭眼扣下扳机)"}

    async def _neko_shot(self, user_id: str, save: Dict[str, Any],
                         facts: Optional[List[Dict[str, Any]]] = None
                         ) -> Optional[Dict[str, Any]]:
        """猫娘自动开枪(NPC 回合即时结算)。分出胜负返回结果，否则 None。"""
        duel = save["duel"]
        facts = facts or []
        idx = duel["index"]
        if idx >= CHAMBERS:
            # 防御: 膛打空(理论不会发生, 子弹>=1) → 平局退还
            bet = duel["money"]
            save["food"] += bet
            save["duel"] = None
            await self._save(user_id, save)
            return {"outcome": "draw", "facts": facts,
                    "message": "膛打空了都没分出胜负喵…赌注退回，猫娘和你都松了口气。"}
        hit = duel["bullet"][idx] == 1
        duel["index"] = idx + 1
        facts.append(build_fact("fire", chamber=idx + 1, hit=hit, shooter="neko"))
        if hit:
            return await self._settle(user_id, save, "neko", facts)
        duel["next"] = "player"
        return None

    async def _settle(self, user_id: str, save: Dict[str, Any],
                      loser: str, facts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """结算。loser="player" 玩家输; "neko" 玩家赢。庄家(猫娘)抽 0~5%。"""
        duel = save["duel"]
        bet = duel["money"]
        bullet_str = "".join("·" if b == 0 else "●" for b in duel["bullet"])
        if loser == "neko":
            fee_pct = random.randint(0, int(self._cfg("fee_max_percent", 5)))
            fee = int(bet * fee_pct / 100)
            fee = max(1, fee) if fee_pct else 0
            win_money = bet - fee
            save["food"] += win_money
            save["make_food"] += win_money
            save["win_count"] += 1
            save["streak"] = save.get("streak", 0) + 1
            save["best_streak"] = max(save.get("best_streak", 0), save["streak"])
            save["duel"] = None
            await self._save(user_id, save)
            return {"outcome": "win",
                    "facts": facts + [build_fact("win", money=win_money, fee=fee)],
                    "message": (f"嘭!!!猫娘中弹了喵…(倒地装死)\n"
                                f"你赢了 {win_money} 猫粮"
                                f"(庄家抽水 {fee_pct}% = {fee} 猫粮)!\n"
                                f"子弹分布: {bullet_str}\n"
                                f"猫娘揉着脑门爬起来: 『呜…主人真狠心，"
                                f"不过愿赌服输喵!』(连胜 {save['streak']})")}
        save["lose_food"] += bet
        save["lose_count"] += 1
        save["streak"] = 0
        save["duel"] = None
        await self._save(user_id, save)
        return {"outcome": "lose",
                "facts": facts + [build_fact("lose", money=bet)],
                "message": (f"咔哒…—— 你听到了撞针的声音…\n"
                            f"你输了 {bet} 猫粮…子弹分布: {bullet_str}\n"
                            f"猫娘赶紧扶住你: 『呜…主人别吓我!"
                            f"下次咱不赌了喵!』")}

    async def _h_surrender(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        duel = save.get("duel")
        if not duel:
            return {"outcome": "no_duel",
                    "message": "又没有对决，认什么输喵~"}
        bet = duel["money"]
        save["lose_food"] += bet
        save["lose_count"] += 1
        save["streak"] = 0
        save["duel"] = None
        await self._save(user_id, save)
        return {"outcome": "surrender",
                "facts": [build_fact("surrender", money=bet)],
                "message": (f"主人认输了喵…{bet} 猫粮归猫娘啦(塞进小鱼干罐)。\n"
                            f"猫娘得意地摇摇尾巴: 『这可是主人自己认输的哦!』")}

    async def _h_food(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        return {"outcome": "food",
                "facts": [build_fact("food", food=save["food"])],
                "message": (f"你还有 {save['food']} 猫粮喵"
                            f"(累计赢取 {save['make_food']}，输掉 {save['lose_food']})")}

    async def _h_record(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        return {"outcome": "record",
                "facts": [build_fact("record", win=save["win_count"],
                                     lose=save["lose_count"])],
                "message": (f"战绩喵: 胜 {save['win_count']} 场 / 负 {save['lose_count']} 场\n"
                            f"累计赢取 {save['make_food']} 猫粮，输掉 {save['lose_food']} 猫粮\n"
                            f"最高连胜: {save.get('best_streak', 0)}")}

    async def _h_rank(self, user_id: str, cmd: str) -> Dict[str, Any]:
        """五类排行(单人化: 玩家 + NPC 虚拟榜)。"""
        save = await self._load(user_id)
        if "胜场" in cmd:
            key, label = "win_count", "胜场"
        elif "败场" in cmd:
            key, label = "lose_count", "败场"
        elif "欧皇" in cmd:
            key, label = "make_food", "赢取猫粮"
        elif "慈善家" in cmd:
            key, label = "lose_food", "输掉猫粮"
        else:
            key, label = "food", "猫粮"
        my_val = save.get(key, 0)
        entries: List[Dict[str, Any]] = []
        for i, name in enumerate(NPC_NAMES):
            if my_val > 0:
                val = int(my_val * random.uniform(0.2, 1.8))
            else:
                val = random.randint(50, 5000)
            entries.append({"name": name, "is_npc": True, "value": val})
        entries.append({"name": "你", "is_npc": False, "value": my_val})
        entries.sort(key=lambda e: e["value"], reverse=True)
        lines = [f"🏆 {label}排行榜"]
        for i, e in enumerate(entries[:10], 1):
            mark = "⭐" if not e["is_npc"] else "🎀"
            lines.append(f"{i}. {mark} {e['name']}: {e['value']}")
        return {"outcome": "rank",
                "facts": [build_fact("rank", key=label, value=my_val)],
                "message": "\n".join(lines)}

    # ── 状态/面板 ─────────────────────────

    async def get_status(self, user_id: str = "default") -> Dict[str, Any]:
        save = await self._load(user_id)
        return {"food": save.get("food", 0),
                "win_count": save.get("win_count", 0),
                "lose_count": save.get("lose_count", 0),
                "best_streak": save.get("best_streak", 0),
                "in_duel": bool(save.get("duel"))}

    def support_panel(self) -> Optional[Dict[str, Any]]:
        return {"schemas": [
            {"field": "sign_food_min", "label": "签到猫粮下限", "component": "InputNumber",
             "props": {"min": 1}, "help": "每日轮盘签到最少给几猫粮"},
            {"field": "sign_food_max", "label": "签到猫粮上限", "component": "InputNumber",
             "props": {"min": 1}, "help": "每日轮盘签到最多给几猫粮"},
            {"field": "default_bet", "label": "默认赌注", "component": "InputNumber",
             "props": {"min": 1}, "help": "装弹时未指定金额的默认猫粮数"},
            {"field": "max_bet", "label": "最大赌注", "component": "InputNumber",
             "props": {"min": 1}, "help": "单局下注猫粮上限"},
            {"field": "max_bullets", "label": "最大子弹数", "component": "InputNumber",
             "props": {"min": 1, "max": 6}, "help": "7 膛左轮最多装几发子弹"},
            {"field": "fee_max_percent", "label": "庄家最大抽水%", "component": "InputNumber",
             "props": {"min": 0, "max": 100}, "help": "结算时庄家(猫娘)随机抽 0~N% 手续费"},
            {"field": "duel_timeout", "label": "对局超时秒数", "component": "InputNumber",
             "props": {"min": 60}, "help": "装弹后多久不开枪自动取消并退回赌注"},
        ]}
