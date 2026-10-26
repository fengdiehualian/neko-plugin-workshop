"""诸天修仙游戏入口：GameAdapter 实现 + 指令分发。

单人化：所有玩法围绕「玩家 + 猫娘」，多人玩法由 NPC 顶替(见 docs/SINGLE_PLAYER_DESIGN.md)。
未移植模块返回「移植中」占位，保证功能清单在分发表里完整可见、逐模块填充。
"""

from __future__ import annotations

import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from ...core.contracts import GameAdapter
from .core import (
    NPC,
    Achievement,
    CombatEngine,
    CraftService,
    DailyTask,
    ItemCatalog,
    MarketManager,
    MoShen,
    NekoCompanion,
    NPCPool,
    Occupation,
    PetSystem,
    PlayerSave,
    Ranking,
    RealmSystem,
    XiaoShiJie,
    Zhutian,
)
from .core.guild import HERB_GROW_SECONDS, GuildManager, GuildSave
from .core.pet import QUALITY_ORDER
from .core.player import NEKO_ID, PlayerStore
from .core.prompts import SCENE_TEMPLATES, build_neko_prompt


def _pick(templates: List[str]) -> str:
    return random.choice(templates) if templates else "喵~"


class XiuxianGame(GameAdapter):
    id = "xiuxian"
    name = "诸天修仙"
    description = "单人修仙 + 猫娘陪玩：境界突破、道侣、师徒、宗门、秘境，猫娘与你并肩修仙。"
    version = "0.1.0"
    icon = "⛩️"

    def __init__(self, plugin: Any) -> None:
        super().__init__(plugin)
        self.store = PlayerStore(self)
        self.neko: Optional[NekoCompanion] = None

    def _cfg(self, key: str, default: Any = None) -> Any:
        """读取 config.json 配置(支持 a.b 嵌套键), 缺省回退默认值。(与 fishing 一致)"""
        cfg = getattr(self, "_config", None) or {}
        cur = cfg
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    # ── 生命周期 ───────────────────────────

    async def on_register(self) -> None:
        self.neko = NekoCompanion(self, self._cfg("neko", {}))
        # LLM 只走主插件统一接口(主插件配置, 所有游戏共用)
        self.guilds = GuildManager(self)
        self.catalog = ItemCatalog()
        self.market = MarketManager(self, self.catalog)
        self.occupation = Occupation(self, self.catalog)
        self.pets = PetSystem(self)
        self.tasks = DailyTask(self)
        self.achievements = Achievement(self)
        self.ranking = Ranking(self)
        self.zhutian = Zhutian(self)
        self.xiaoshijie = XiaoShiJie(self)
        self.moshen = MoShen(self)
        self.craft = CraftService(self)

    async def on_unload(self) -> None:
        self.store.clear_cache()

    # ── 指令分发 ───────────────────────────

    # (关键词, 说明, handler名)；按顺序匹配
    _RULES: List[Tuple[List[str], str, str]] = [
        (["踏入仙途", "再入仙途"], "创建角色开始修仙", "start"),
        (["改名", "更名", "设置姓名"], "修改道号(1000灵石)", "rename"),
        (["修仙状态", "状态", "我的练气"], "查看境界属性", "status"),
        (["修仙签到", "签到"], "每日签到领灵石", "sign"),
        (["修炼"], "修炼获得修为", "cultivate"),
        (["闭关"], "闭关修炼(定时结算,猫娘守关)", "seclusion"),
        (["突破"], "突破境界", "breakthrough"),
        (["幸运突破"], "幸运突破(成功率更高)", "breakthrough_lucky"),
        (["破体"], "炼体突破", "body_breakthrough"),
        (["我的纳戒", "纳戒", "背包"], "查看背包", "bag"),
        (["结为道侣"], "与猫娘缔结道侣", "daolv_propose"),
        (["赠予百合花篮"], "给猫娘送礼提升亲密度", "daolv_gift"),
        (["查询亲密度", "亲密度"], "查看与猫娘的好感度", "daolv_qinmidu"),
        (["拜师", "收徒"], "与猫娘结为师徒", "shitu"),
        (["开宗立派"], "创建宗门", "sect_create"),
        (["我的宗门"], "查看宗门", "sect_show"),
        (["宗门俸禄"], "领取宗门俸禄", "sect_salary"),
        (["宗门仓库"], "宗门仓库(第二背包)存取", "sect_store"),
        (["宗门贡献"], "灌注经验贡献宗门", "sect_contribute"),
        (["宗门药田"], "种植/收获药田", "sect_field"),
        (["探索秘境", "秘境"], "进入秘境探险(猫娘陪玩)", "secret"),
        (["和猫娘说话", "猫娘说话", "陪陪我"], "与猫娘闲聊", "chat"),
        # ── 战斗/PK(单人化: NPC 顶替真人) ──
        (["打劫"], "打劫散修NPC", "rob"),
        (["切磋", "比武", "以武会友"], "切磋挑战NPC", "duel"),
        (["妖王", "黑暗动乱", "天骄", "龙王"], "讨伐BOSS(猫娘助阵)", "boss"),
        (["讨伐", "帝尊"], "讨伐BOSS(猫娘助阵)", "boss"),
        (["团本", "讨伐帝尊", "讨伐队伍"], "团本(猫娘+队友合攻)", "tuanben"),
        (["宗门大战"], "攻打NPC宗门", "sect_war"),
        # ── 市场/职业(NPC 顶替真人) ──
        (["市场", "商店", "交易"], "市场NPC商店(买)", "shop"),
        (["出售"], "出售物品给市场NPC", "sell"),
        (["拍卖"], "星阁拍卖行", "auction"),
        (["竞价"], "拍卖出价", "bid"),
        (["采药"], "采药(职业·采集)", "gather_herb"),
        (["采矿"], "采矿(职业·采集)", "gather_ore"),
        (["炼丹"], "炼丹(按配方)", "refine_pill"),
        (["炼器", "锻造"], "炼器(按配方)", "forge_equip"),
        (["合成"], "按配方合成", "combine"),
        (["加工"], "食材加工", "process"),
        (["配方"], "查看配方", "recipe"),
        # ── 装备/丹药 ──
        (["卸下"], "卸下装备", "unequip"),
        (["我的装备", "装备栏"], "查看装备", "equip_view"),
        (["装备"], "穿戴装备", "equip"),
        (["服用"], "服用丹药", "use_pill"),
        # ── 宠物(仙宠) ──
        (["领养仙宠"], "领养仙宠(灵石)", "pet_adopt"),
        (["出战仙宠"], "仙宠出战", "pet_active"),
        (["喂给仙宠"], "喂食仙宠升级", "pet_feed"),
        (["进阶仙宠"], "仙宠进阶(灵石)", "pet_evolve"),
        (["派仙宠寻宝"], "仙宠寻宝(定时)", "pet_explore"),
        (["结束仙宠寻宝"], "召回仙宠", "pet_explore_end"),
        (["仙宠", "宠物"], "仙宠面板", "pet"),
        # ── 任务/成就/排行 ──
        (["提交每日任务", "领取每日奖励"], "提交每日任务领奖", "task_claim"),
        (["每日任务", "我的任务", "任务"], "每日任务面板", "task"),
        (["成就", "修仙助手"], "成就面板", "achievement"),
        (["天榜", "天地榜", "封神榜"], "天榜(虚拟榜)", "ranking"),
        (["比试"], "挑战天榜NPC", "rank_challenge"),
        # ── 诸天/小世界 ──
        (["投影诸天", "诸天"], "投影诸天探索", "zhutian"),
        (["开辟小世界"], "开辟小世界", "xsj_open"),
        (["小世界栽种"], "小世界栽种作物", "xsj_plant"),
        (["收获小世界作物", "收获小世界"], "收获小世界作物", "xsj_harvest"),
        (["演化小世界"], "演化小世界升级", "xsj_evolve"),
        (["我的小世界", "小世界"], "小世界面板", "xsj"),
        # ── 魔道/神界/周年 ──
        (["供奉魔石"], "供奉魔石(灵石→魔石)", "offer_moshi"),
        (["修炼魔功"], "修炼魔功(消耗魔石)", "xiu_mogong"),
        (["堕入魔界", "魔界"], "魔界状态", "mojie"),
        (["供奉神石"], "供奉神石(灵石→神石)", "offer_shenshi"),
        (["参悟神石"], "参悟神石(消耗神石)", "canwu"),
        (["踏入神界", "神界"], "神界状态", "shenjie"),
        (["周年签到"], "周年签到", "anniversary"),
    ]

    async def handle_action(self, user_id: str, cmd: str,
                            args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # 统一规范化: 去首部 # 和空白(用户带不带 # 都能识别)
        cmd = (cmd or "").strip().lstrip("#").strip()
        if not cmd:
            return {"message": "想做什么喵?试试「修仙状态」「修炼」「突破」", "outcome": "idle"}

        # 匹配指令: 最长关键词优先(零误判, 解决「修炼魔功vs修炼」「周年签到vs签到」等前缀冲突)
        handler_name = None
        best_len = 0
        for keywords, _, fn in self._RULES:
            for kw in keywords:
                if kw and kw in cmd and len(kw) > best_len:
                    best_len = len(kw)
                    handler_name = fn
                    break

        if not handler_name:
            # 契约(rules.md §2.3): 不认识指令返回 outcome="unknown", 大脑触发邀请流程
            return {"facts": [], "outcome": "unknown", "message": ""}

        handler = getattr(self, f"_h_{handler_name}", None)
        if not handler:
            return {"message": "该功能尚未实现喵", "outcome": "error"}
        result = await handler(user_id, cmd)
        return await self._post_event(user_id, result)

    async def _post_event(self, user_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
        """集中式任务/成就记录(按 outcome 归类), 新成就追加到消息。"""
        oc = result.get("outcome", "")
        task_id = None
        stat_key = None
        if oc == "sign":
            task_id, stat_key = "sign", "sign"
        elif oc == "cultivate":
            task_id, stat_key = "cultivate", "cultivate"
        elif oc in ("breakthrough_ok", "breakthrough_fail",
                    "body_breakthrough_ok", "body_breakthrough_fail"):
            task_id, stat_key = "breakthrough", "breakthrough"
        elif oc in ("rob_win", "rob_lose", "duel_win", "duel_lose",
                    "boss_win", "boss_lose", "tuanben_win", "tuanben_lose",
                    "sectwar_win", "sectwar_lose"):
            task_id, stat_key = "battle", "battle"
        elif oc.startswith("secret_"):
            task_id, stat_key = "secret", "secret"
        elif oc == "seclusion_start":
            task_id = "seclusion"
        if oc in ("boss_win", "tuanben_win"):
            stat_key = "boss_win"
        # start 等无计数事件也要跑成就检查(如「初入仙途」)
        if not (task_id or stat_key) and oc != "start":
            return result
        try:
            save = await self._load(user_id)
            if task_id:
                self.tasks.record(save, task_id)
            if stat_key:
                self.achievements.record(save, stat_key)
            # 猫娘修仙记忆: 记录值得提起的共同经历(近 5 条)
            self._record_neko_mem(save, oc, result.get("facts", []))
            news = self.achievements.check_all(save)
            if news:
                ach = "🏆 成就解锁: " + "、".join(a["name"] for a in news)
                result["message"] = (result.get("message", "") + "\n" + ach)
                result["summary"] = (result.get("summary", "") + f"|成就:{len(news)}")
            await self.store.save(save)
        except Exception:
            pass
        return result

    # ── 玩家核心 ───────────────────────────

    async def _load(self, user_id: str) -> PlayerSave:
        return await self.store.load(user_id)

    async def _h_start(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.created_at:
            return {"message": "你已是修仙者了喵,无需重新入仙途(想改名用「改名 新名字」)",
                    "outcome": "already"}
        gcfg = self._cfg("game", {})
        save = PlayerSave(user_id=user_id)
        save.name = "无名散修"
        save.lingshi_low = gcfg.get("initial_lingshi", 100)
        save.created_at = time.time()
        save.refresh_stats()
        await self.store.save(save)
        msg = (f"【踏入仙途】{save.name} 踏入修仙之路喵!\n"
               f"境界：{save.realm_name()} | 炼体：{save.body_name()}\n"
               f"气血 {save.hp}/{save.max_hp} 攻击 {save.attack} 防御 {save.defense}\n"
               f"灵石 ×{save.lingshi_low}\n"
               f"猫娘 {self.neko.name} 也在你身边喵～\n"
               f"试试「修炼」「突破」「修仙签到」吧!")
        # 渲染角色卡(游戏负责生成数据, brain 负责推送)
        # 「游戏适配插件」: 返回 images 数据, 不直接 push
        images = []
        try:
            lines = [
                (f"境界：{save.realm_name()}", "common"),
                (f"炼体：{save.body_name()}", "common"),
                (f"气血 {save.hp}/{save.max_hp} | 攻击 {save.attack} | 防御 {save.defense}", "rare"),
                (f"灵石 ×{save.lingshi_low}", "gold"),
                (f"猫娘 {self.neko.name} 与你同行", "epic"),
            ]
            img = await self.render_page(title="踏入仙途",
                                         subtitle=f"道友 {save.name}",
                                         rows=[[a, b] for a, b in lines], theme="light")
            if img:
                images.append(self.build_image(msg, img, "image/png"))
                msg = ""
        except Exception:
            pass
        return {"message": msg, "outcome": "start",
                "facts": [{"kind": "start", "name": save.name, "realm": save.realm_name()}],
                "images": images}

    async def _h_rename(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        name = cmd.replace("改名", "", 1).replace("更名", "", 1).replace("设置姓名", "", 1).strip()
        if not name or len(name) > 8:
            return {"message": "道号要 1-8 个字喵(如「改名 玄天」)", "outcome": "rename_bad"}
        if save.lingshi_total() < 1000:
            return {"message": f"改名需 1000 灵石(当前 {save.lingshi_total()})", "outcome": "no_lingshi"}
        save.add_lingshi(-1000)
        old = save.name
        save.name = name
        await self.store.save(save)
        return {"message": f"道号已改: {old} → {name}(花费1000灵石)喵",
                "outcome": "rename",
                "facts": [{"kind": "rename", "name": name}],
                "summary": f"改名{name}"}

    async def _h_status(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "你尚未踏入仙途,发「踏入仙途」开始修仙喵", "outcome": "no_player"}
        save.refresh_stats()
        rel = self.neko.relationship_summary(save)
        rel_txt = "、".join(k for k, v in rel.items()
                            if v and k != "qinmidu") or "伙伴"
        eq = save.extra.get("equips", {}) or {}
        eq_txt = "、".join(f"{s}:{n}" for s, n in eq.items() if n) or "无"
        eqs = self._equip_stats(save)
        pb = self.pets.combat_bonus(save)
        # 实际战力(含装备绝对值 + 仙宠百分比)
        eff_atk = int((save.attack + eqs["attack"]) * (1 + pb["attack"]))
        eff_def = int((save.defense + eqs["defense"]) * (1 + pb["defense"]))
        eff_hp = int((save.max_hp + eqs["hp"]) * (1 + pb["hp"]))
        eq_bonus = (f"(装备攻+{eqs['attack']} 防+{eqs['defense']} 血+{eqs['hp']})"
                    if any(eqs.values()) else "")
        msg = (f"【{save.name}】({save.gender})\n"
               f"境界：{save.realm_name()} | 炼体：{save.body_name()}\n"
               f"修为：{save.exp}\n"
               f"气血：{save.hp}/{save.max_hp}(实战{eff_hp}) | 灵力：{save.mana}/{save.max_mana}\n"
               f"攻击：{save.attack}(实战{eff_atk}) | 防御：{save.defense}(实战{eff_def})\n"
               f"装备：{eq_txt}{eq_bonus}\n"
               f"灵石：{save.lingshi_total()}\n"
               f"宗门：{save.sect or '无'} | 与猫娘关系：{rel_txt}(亲密度{save.qinmidu})")
        return {"message": msg, "outcome": "status",
                "game_result": save.snapshot(),
                "summary": f"{save.name}当前{save.realm_name()}"}

    async def _h_sign(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」再签到喵", "outcome": "no_player"}
        today = time.strftime("%Y-%m-%d")
        if save.last_sign == today:
            return {"message": "今天已经签到过啦喵,明天再来～", "outcome": "signed"}
        if save.last_sign == time.strftime("%Y-%m-%d",
                                           time.localtime(time.time() - 86400)):
            save.sign_days += 1
        else:
            save.sign_days = 1
        save.last_sign = today
        reward = 100 + save.sign_days * 10
        save.add_lingshi(reward)
        await self.store.save(save)
        return {"message": f"签到成功喵!连续签到 {save.sign_days} 天,获得 {reward} 灵石",
                "outcome": "sign",
                "facts": [{"kind": "sign", "days": save.sign_days, "reward": reward}],
                "summary": f"签到成功,连续{save.sign_days}天,获得{reward}灵石"}

    async def _h_cultivate(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」再修炼喵", "outcome": "no_player"}
        secl = save.extra.get("seclusion")
        if secl and secl.get("end", 0) > time.time():
            return {"message": "正在闭关中喵,出关后再修炼更专注(闭关收益会按时结算)",
                    "outcome": "secluding"}
        # 平衡: 产出与"离开当前境界的需求"挂钩(8%+), 各境界稳定 ~10-12 次突破一次
        need = RealmSystem.realm_require(save.realm_idx)
        gain = max(100, int(need * 0.08)) + random.randint(0, 50)
        save.exp += gain
        save.refresh_stats()
        await self.store.save(save)
        return {"message": f"修炼一轮,修为 +{gain}(当前 {save.exp},距突破还需 {max(0, need - save.exp)})。继续「修炼」或「突破」喵",
                "outcome": "cultivate",
                "facts": [{"kind": "cultivate", "gain": gain, "exp": save.exp}],
                "summary": f"修炼获得{gain}修为"}

    async def _h_breakthrough(self, user_id: str, cmd: str) -> Dict[str, Any]:
        return await self._do_breakthrough(user_id, lucky=False)

    async def _h_breakthrough_lucky(self, user_id: str, cmd: str) -> Dict[str, Any]:
        return await self._do_breakthrough(user_id, lucky=True)

    async def _do_breakthrough(self, user_id: str, lucky: bool) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        secl = save.extra.get("seclusion")
        if secl and secl.get("end", 0) > time.time():
            return {"message": "正在闭关中喵,出关后再尝试突破更稳", "outcome": "secluding"}
        bcfg = self._cfg("breakthrough", {})
        rate = bcfg.get("base_rate", 0.8) + (0.1 if lucky else 0)
        # 魔性代价: 每点魔性 -0.5% 突破成功率(堕魔的代价, 最低 0.5)
        modao = int((save.extra.get("moshi", {}) or {}).get("modao", 0))
        if modao > 0:
            rate -= modao * 0.005
        rate = max(0.5, min(0.98, rate))
        result = RealmSystem.try_breakthrough(
            save.realm_idx, save.exp,
            rate=rate,
            neko_assist=self.neko.enabled,
            assist_bonus=self.neko.assist_bonus(),
            fail_penalty_ratio=bcfg.get("fail_penalty_ratio", 0.1))
        save.exp -= result["exp_lost"]
        if result["success"]:
            save.realm_idx = result["new_idx"]
            save.refresh_stats()
        await self.store.save(save)
        return {"message": result["msg"],
                "outcome": "breakthrough_ok" if result["success"] else "breakthrough_fail",
                "facts": [{"kind": "breakthrough_ok" if result["success"] else "breakthrough_fail",
                           "success": result["success"], "realm": save.realm_name()}],
                "summary": f"{'突破成功:' + save.realm_name() if result['success'] else '突破失败'}"}

    async def _h_body_breakthrough(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        bcfg = self._cfg("breakthrough", {})
        result = RealmSystem.try_body_breakthrough(
            save.body_idx, save.exp,
            rate=bcfg.get("base_rate", 0.8),
            fail_penalty_ratio=bcfg.get("fail_penalty_ratio", 0.1))
        save.exp -= result["exp_lost"]
        if result["success"]:
            save.body_idx = result["new_idx"]
            save.refresh_stats()
        await self.store.save(save)
        return {"message": result["msg"],
                "outcome": "body_breakthrough_ok" if result["success"] else "body_breakthrough_fail",
                "facts": [{"kind": "body_breakthrough_ok" if result["success"] else "body_breakthrough_fail",
                           "success": result["success"], "body": save.body_name()}],
                "summary": f"{'炼体突破:' + save.body_name() if result['success'] else '炼体失败'}"}

    async def _h_bag(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if not save.bag:
            return {"message": "纳戒空空如也喵,去「探索秘境」或市场看看吧(移植中)",
                    "outcome": "bag", "game_result": {"bag": []}}
        lines = [f"【{save.name}的纳戒】"]
        for item_id, count in save.bag.items():
            lines.append(f"  {item_id} ×{count}")
        return {"message": "\n".join(lines), "outcome": "bag",
                "game_result": {"bag": list(save.bag.items())},
                "summary": f"纳戒里有{len(save.bag)}种物品"}

    # ── 道侣(猫娘) ─────────────────────────

    async def _h_daolv_propose(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.daolv == NEKO_ID:
            return {"message": f"你和{self.neko.name}已是道侣啦喵～", "outcome": "already"}
        if save.qinmidu < 300:
            return {"message": f"亲密度不足300,无法与{self.neko.name}心意相通(当前{save.qinmidu})。"
                               "先送「百合花篮」提升好感吧喵",
                    "outcome": "qinmidu_low"}
        save.daolv = NEKO_ID
        await self.store.save(save)
        return {"message": f"与{self.neko.name}结为道侣喵!",
                "outcome": "daolv",
                "facts": [{"kind": "daolv", "name": self.neko.name}],
                "summary": f"与猫娘{self.neko.name}结为道侣"}

    async def _h_daolv_gift(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.bag.get("百合花篮", 0) <= 0:
            return {"message": "没有百合花篮喵,去「商店 买 百合花篮」吧(2000灵石/个)",
                    "outcome": "no_item"}
        save.bag["百合花篮"] -= 1
        if save.bag["百合花篮"] <= 0:
            del save.bag["百合花篮"]
        gain = self.neko.qinmidu_per_gift
        self.neko.add_qinmidu(save, gain)
        await self.store.save(save)
        return {"message": f"赠予{self.neko.name}百合花篮,亲密度 +{gain}(当前{save.qinmidu})",
                "outcome": "gift",
                "facts": [{"kind": "gift", "qinmidu": save.qinmidu}],
                "summary": f"送礼提升亲密度至{save.qinmidu}"}

    async def _h_daolv_qinmidu(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        return {"message": f"与{self.neko.name}的亲密度：{save.qinmidu}"
                           f"({ '已是道侣喵~' if save.daolv == NEKO_ID else '继续努力喵' })",
                "outcome": "qinmidu", "game_result": {"qinmidu": save.qinmidu},
                "summary": f"亲密度{save.qinmidu}"}

    # ── 师徒(猫娘) ─────────────────────────

    async def _h_shitu(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if NEKO_ID in save.disciples and save.master == NEKO_ID:
            return {"message": "你和猫娘互为师徒了喵,这关系好乱～", "outcome": "already"}
        if NEKO_ID in save.disciples:
            return {"message": f"{self.neko.name}已是你的徒弟喵", "outcome": "already"}
        if save.master == NEKO_ID:
            return {"message": f"{self.neko.name}已是你的师父喵", "outcome": "already"}
        save.disciples.append(NEKO_ID)
        await self.store.save(save)
        return {"message": f"你收{self.neko.name}为徒了喵!要好好教她修仙哦～",
                "outcome": "shitu",
                "facts": [{"kind": "shitu", "role": "disciple"}],
                "summary": "收猫娘为徒"}

    # ── 宗门(单机洞府: 懒结算 + 第二背包 + 经验灌注) ──

    async def _guild_of(self, save: PlayerSave) -> Optional[GuildSave]:
        """由玩家存档解析宗门(无则 None)。"""
        if not save.sect:
            return None
        guild = await self.guilds.store.load(save.sect)
        if guild is None:
            guild = await self.guilds.store.load_or_create(
                save.sect, save.sect, save.user_id)
        return guild

    async def _h_sect_create(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.sect:
            return {"message": f"你已有宗门 {save.sect} 喵", "outcome": "already"}
        if save.lingshi_total() < 10000:
            return {"message": f"开宗立派需要一万灵石喵(当前 {save.lingshi_total()})",
                    "outcome": "no_lingshi"}
        save.add_lingshi(-10000)
        guild_id = f"{user_id}_sect"               # 存档 key
        guild_name = f"{save.name}的宗门"           # 显示名
        save.sect = guild_id                       # 玩家存档存 key, 查询一致
        await self.guilds.store.load_or_create(guild_id, guild_name, user_id)
        await self.store.save(save)
        return {"message": f"开宗立派成功喵!宗门【{guild_name}】建立,{self.neko.name}随你入宗。\n"
                           "宗门是咱们的洞府:仓库/贡献/俸禄/药田 都可用喵",
                "outcome": "sect_create",
                "facts": [{"kind": "sect_create", "name": guild_name}],
                "summary": f"创建宗门{guild_name}"}

    async def _h_sect_show(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        guild = await self._guild_of(save)
        if guild is None:
            return {"message": "你还没有宗门,发「开宗立派」创建喵(需一万灵石)",
                    "outcome": "no_sect"}
        gained = self.guilds.settle(guild)   # 懒结算
        await self.guilds.store.save(guild)
        hf = guild.herb_field
        if hf and hf.get("harvest_time", 0) > time.time():
            herb_txt = f"药田: {hf['plant']} 生长中(剩{int(hf['harvest_time'] - time.time())}s)"
        elif hf:
            herb_txt = "药田: 可收获喵!"
        else:
            herb_txt = "药田: 可种植(如「宗门药田 种灵芝」)"
        gain_txt = f"(懒结算+{gained}灵石)" if gained else ""
        return {"message": (f"【{guild.name}】Lv.{guild.level}(单机洞府)\n"
                            f"成员: {len(guild.members)}人(你+{self.neko.name})\n"
                            f"灵石池: {guild.spirit_stones}{gain_txt}\n"
                            f"仓库: {len(guild.store)}种物品\n{herb_txt}"),
                "outcome": "sect_show",
                "game_result": guild.to_dict(),
                "facts": [{"kind": "sect_show", "name": guild.name}],
                "summary": f"宗门{guild.name} Lv.{guild.level}"}

    async def _h_sect_salary(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        guild = await self._guild_of(save)
        if guild is None:
            return {"message": "没有宗门领什么俸禄喵～", "outcome": "no_sect"}
        today = time.strftime("%Y-%m-%d")
        if save.extra.get("sect_salary_date") == today:
            return {"message": "今日俸禄已领过喵,明天再来(宗门懒结算会继续产出)",
                    "outcome": "sect_salary_cd"}
        gained = self.guilds.settle(guild)   # 懒结算先补齐产出
        salary = 500 + guild.level * 100
        if guild.spirit_stones < salary:
            await self.guilds.store.save(guild)
            return {"message": f"灵石池不足(当前{guild.spirit_stones},需{salary})喵,等产出或「宗门贡献」吧",
                    "outcome": "sect_salary_low"}
        guild.spirit_stones -= salary
        await self.guilds.store.save(guild)
        save.extra["sect_salary_date"] = today
        save.add_lingshi(salary)
        await self.store.save(save)
        return {"message": f"宗门俸禄 {salary} 灵石已入账喵(懒结算产出{gained},每日限领)",
                "outcome": "sect_salary",
                "facts": [{"kind": "sect_salary", "amount": salary}],
                "summary": f"领取俸禄{salary}灵石"}

    async def _h_sect_store(self, user_id: str, cmd: str) -> Dict[str, Any]:
        """宗门仓库 = 第二背包: 查看 / 存入 / 取出。"""
        save = await self._load(user_id)
        guild = await self._guild_of(save)
        if guild is None:
            return {"message": "没有宗门,仓库无从谈起喵～", "outcome": "no_sect"}
        text = cmd.replace("宗门仓库", "", 1).strip()
        if not text:
            if not guild.store:
                return {"message": "宗门仓库空空如也喵(这是你的第二背包)",
                        "outcome": "sect_store", "game_result": {}}
            lines = ["【宗门仓库】"] + [f"  {k} ×{v}" for k, v in list(guild.store.items())[:20]]
            return {"message": "\n".join(lines), "outcome": "sect_store",
                    "game_result": guild.store,
                    "summary": f"仓库{len(guild.store)}种物品"}
        if "存入" in text or "存" in text:
            item, n = self._parse_item(text.replace("存入", "").replace("存", "").strip())
            if save.bag.get(item, 0) < n:
                return {"message": f"背包里没有 {n} 个{item}喵", "outcome": "no_item"}
            save.bag[item] -= n
            if save.bag[item] <= 0:
                del save.bag[item]
            ok = await self.guilds.store_put(guild, item, n)
            if not ok:
                return {"message": "宗门仓库满啦喵", "outcome": "store_full"}
            await self.store.save(save)
            return {"message": f"存入 {item} ×{n} 到宗门仓库喵", "outcome": "sect_store_in",
                    "facts": [{"kind": "sect_store", "op": "in", "item": item, "count": n}],
                    "summary": f"存入{item}x{n}"}
        if "取出" in text or "取" in text:
            item, n = self._parse_item(text.replace("取出", "").replace("取", "").strip())
            take = await self.guilds.store_take(guild, item, n)
            if take <= 0:
                return {"message": "宗门仓库没有这个喵", "outcome": "no_item"}
            save.bag[item] = save.bag.get(item, 0) + take
            await self.store.save(save)
            return {"message": f"从宗门仓库取出 {item} ×{take} 喵", "outcome": "sect_store_out",
                    "facts": [{"kind": "sect_store", "op": "out", "item": item, "count": take}],
                    "summary": f"取出{item}x{take}"}
        return {"message": "宗门仓库用法: 查看「宗门仓库」/ 存「宗门仓库 存入 玄铁剑x2」/ 取「宗门仓库 取出 玄铁剑」",
                "outcome": "sect_store_help"}

    async def _h_sect_contribute(self, user_id: str, cmd: str) -> Dict[str, Any]:
        """宗门贡献 = 灌注经验(溢出修为换灵石, 有 CD, 防止刷灵石)。"""
        save = await self._load(user_id)
        guild = await self._guild_of(save)
        if guild is None:
            return {"message": "没有宗门喵,先「开宗立派」吧", "outcome": "no_sect"}
        cd = self._action_cd(save, "cd_contribute", 600)
        if cd > 0:
            return {"message": f"灌注在冷却中,剩 {cd}s 喵", "outcome": "cd"}
        text = cmd.replace("宗门贡献", "", 1).strip()
        amount = 0
        for ch in text:
            if ch.isdigit():
                amount = amount * 10 + int(ch)
        if amount <= 0:
            amount = save.exp   # 默认全部灌注
        amount = min(amount, save.exp)
        if amount <= 0:
            return {"message": "没有修为可灌注喵,先「修炼」吧", "outcome": "no_exp"}
        self._set_action_cd(save, "cd_contribute")
        save.exp -= amount
        gain = await self.guilds.contribute_exp(guild, amount)
        await self.store.save(save)
        return {"message": f"灌注 {amount} 修为,宗门灵石 +{gain}喵(当前{guild.spirit_stones})",
                "outcome": "sect_contribute",
                "facts": [{"kind": "sect_contribute", "exp": amount, "gain": gain}],
                "summary": f"灌注{amount}修为,灵石+{gain}"}

    async def _h_sect_field(self, user_id: str, cmd: str) -> Dict[str, Any]:
        """宗门药田: 种植 / 收获(懒结算生长)。"""
        save = await self._load(user_id)
        guild = await self._guild_of(save)
        if guild is None:
            return {"message": "没有宗门喵,先「开宗立派」吧", "outcome": "no_sect"}
        text = cmd.replace("宗门药田", "", 1).strip()
        if "收" in text or "摘" in text or not text:
            result = await self.guilds.harvest_herb(guild)
            if result is None:
                return {"message": "药田还没种东西喵,试试「宗门药田 种灵芝」", "outcome": "no_herb"}
            if not result.get("ready"):
                return {"message": f"{result['plant']} 还在生长,剩 {int(result['remain'])}s 喵",
                        "outcome": "growing"}
            # 药田产出真进背包(修复: 之前只显示"收获"但没给东西)
            plant = result["plant"]
            save.bag[plant] = save.bag.get(plant, 0) + result["yield"]
            await self.store.save(save)
            return {"message": f"收获 {plant} ×{result['yield']} 入背包喵(药田产出)",
                    "outcome": "sect_harvest",
                    "facts": [{"kind": "sect_harvest", "plant": plant, "yield": result["yield"]}],
                    "summary": f"收获{result['plant']}x{result['yield']}"}
        plant = text.strip()
        # 去掉「种/栽/种植」前缀(修复: 之前会把"种"字带进作物名)
        for p in ("种植", "种", "栽"):
            if plant.startswith(p):
                plant = plant[len(p):].strip()
                break
        plant = plant or "灵芝"
        ok = await self.guilds.plant_herb(guild, plant)
        if not ok:
            return {"message": "药田还在生长中喵,等收获吧", "outcome": "growing"}
        return {"message": f"种下 {plant} 喵,{HERB_GROW_SECONDS // 60}分钟后可收获",
                "outcome": "sect_plant",
                "facts": [{"kind": "sect_plant", "plant": plant}],
                "summary": f"种植{plant}"}

    # ── 秘境(猫娘陪玩, 骨架) ───────────────

    async def _h_secret(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        cd = self._action_cd(save, "cd_secret", 60)
        if cd > 0:
            return {"message": f"秘境入口在封闭,剩 {cd}s 可再探索喵", "outcome": "cd"}
        self._set_action_cd(save, "cd_secret")
        events = [
            ("发现一处灵泉,灵石 +300", 300, "lingshi"),
            ("遭遇妖兽,险胜!", 0, "material"),
            ("找到前辈遗蜕,修为 +500", 500, "exp"),
            ("一无所获,但猫娘陪你走了一路", 0, "nothing"),
        ]
        ev, val, kind = random.choice(events)
        if kind == "lingshi":
            save.add_lingshi(val)
        elif kind == "exp":
            save.exp += val
        elif kind == "material":
            mat = self.catalog.random_of_class("材料")
            if mat:
                save.bag[mat["name"]] = save.bag.get(mat["name"], 0) + 1
                ev = f"遭遇妖兽,险胜!获得材料「{mat['name']}」×1"
        await self.store.save(save)
        return {"message": f"【探索秘境】{ev}", "outcome": f"secret_{kind}",
                "facts": [{"kind": f"secret_{kind}", "event": kind, "value": val}],
                "summary": f"秘境探索:{ev}"}

    # ── 闭关(定时结算, 猫娘守关) ────────────

    async def _h_seclusion(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        secl = save.extra.get("seclusion")
        if secl and secl.get("end", 0) > time.time():
            remain = int(secl["end"] - time.time())
            return {"message": f"正在闭关中喵,还有 {remain} 秒出关",
                    "outcome": "secluding", "summary": "闭关中"}
        gcfg = self._cfg("game", {})
        duration = gcfg.get("seclusion_seconds", 600)  # 默认10分钟
        # 平衡: 闭关产出挂钩当前境界需求(10分钟 ≈ 40% 突破需求, 两次闭关可突破)
        need = RealmSystem.realm_require(save.realm_idx)
        gain_per_sec = max(1, int(need * 0.0007))
        save.extra["seclusion"] = {
            "end": time.time() + duration,
            "gain_per_sec": gain_per_sec,
            "start_realm": save.realm_idx,
            "duration": duration,
        }
        await self.store.save(save)
        return {"message": (f"开始闭关 {duration // 60} 分钟喵,{self.neko.name}给你守关!\n"
                            f"预计获得 {int(duration * gain_per_sec)} 修为"),
                "outcome": "seclusion_start",
                "facts": [{"kind": "seclusion_start", "duration": duration}],
                "summary": "开始闭关"}

    async def on_tick(self, user_id: str) -> None:
        lines: List[str] = []          # 收集本 tick 要说的话, 统一返回
        """每秒 tick：闭关到期/仙宠寻宝/每日任务提醒(注意: 不能用 return 提前退出)。"""
        # 闭关到期结算(猫娘喊出关)
        try:
            save = await self._load(user_id)
            secl = save.extra.get("seclusion")
            if secl and secl.get("end", 0) <= time.time():
                duration = secl.get("duration", 0) or 600
                gain_per_sec = secl.get("gain_per_sec", 10)
                gain = int(duration * gain_per_sec)
                save.exp += gain
                save.extra.pop("seclusion", None)
                await self.store.save(save)
                neko_line = _pick(SCENE_TEMPLATES.get("seclusion_done", []))
                lines.append(
                    f"【出关】主人闭关结束,修为 +{gain}!\n{neko_line}")
        except Exception:
            pass
        # 仙宠寻宝到期结算
        try:
            save = await self._load(user_id)
            pets = save.extra.get("pets", {})
            for name in list(pets.keys()):
                r = await self.pets.explore_settle(save, name, force=False)
                if r and r.get("ready"):
                    save.bag[r["item"]] = save.bag.get(r["item"], 0) + r["count"]
                    await self.store.save(save)
                    lines.append(
                        f"【寻宝】{name}寻宝归来,带回 {r['item']} ×{r['count']} 喵!")
        except Exception:
            pass
        # 每日任务提醒(每天一次, 猫娘主动关心)
        try:
            save = await self._load(user_id)
            if save.created_at:
                v = self.tasks.view(save)
                today = time.strftime("%Y-%m-%d")
                if (not v["claimed"] and not v["all_done"]
                        and save.extra.get("task_remind") != today):
                    save.extra["task_remind"] = today
                    await self.store.save(save)
                    lines.append(
                        "喵~主人,今天的每日任务还没做完哦,发「每日任务」看看喵!")
        except Exception:
            pass
        # 修仙专属主动提议(每天各一次, 猫娘陪伴感)
        try:
            save = await self._load(user_id)
            if save.created_at:
                today = time.strftime("%Y-%m-%d")
                stats = save.extra.get("stats", {})
                # 1. 修炼邀请: 今天还没修炼过
                if stats.get("cultivate", 0) == 0 and save.extra.get("invite_cultivate") != today:
                    save.extra["invite_cultivate"] = today
                    await self.store.save(save)
                    lines.append(
                        "主人,今天还没修炼喵~和喵喵一起闭关/修炼,离飞升更进一步喵!")
                    return "\n".join(lines)
                # 2. 亲密度撒娇: 亲密度低且没结道侣
                if save.qinmidu < 300 and save.daolv != NEKO_ID and save.extra.get("invite_gift") != today:
                    save.extra["invite_gift"] = today
                    await self.store.save(save)
                    lines.append(
                        "主人…喵喵想要个「百合花篮」喵,商店里就有,亲密度会涨哦~")
                    return "\n".join(lines)
                # 3. 突破预告: 修为接近突破线
                need = RealmSystem.realm_require(save.realm_idx)
                if need > 0 and save.exp >= need * 0.8 and save.extra.get("invite_break") != today:
                    save.extra["invite_break"] = today
                    await self.store.save(save)
                    lines.append(
                        "主人修为快到突破线啦喵!再修炼一下就能突破,喵喵给你护法!")
        except Exception:
            pass
        return "\n".join(lines) if lines else None   # 推送统一交本体

    # ── 猫娘闲聊 ───────────────────────────

    async def _h_chat(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        ctx = self._ctx(save)
        ctx["event"] = f"主人说：{cmd}"
        # 走主插件统一 LLM 接口(配置的新 LLM → 宿主)
        result = await self.call_llm(build_neko_prompt("chat", ctx), scene="xiuxian.chat")
        if result and result.strip():
            return {"message": result.strip(), "outcome": "chat",
                    "summary": f"猫娘回应:{result.strip()[:20]}"}
        return {"message": _pick(SCENE_TEMPLATES.get("chat", [])),
                "outcome": "chat", "summary": "猫娘闲聊"}

    # ── 战斗/PK(单人化: NPC 顶替真人) ──────

    @staticmethod
    def _npc_stats(npc: NPC) -> Dict[str, Any]:
        return {"hp": npc.hp, "attack": npc.attack,
                "defense": npc.defense, "crit_rate": npc.crit_rate}

    def _neko_helper(self, save: PlayerSave) -> Optional[NPC]:
        """猫娘助阵(作为战斗队友)。"""
        if not self.neko.enabled:
            return None
        ns = self.neko.compute_save(save)
        return NPC(nid=NEKO_ID, name=self.neko.name, role="队友",
                   realm_idx=ns.realm_idx, hp=ns.max_hp,
                   attack=ns.attack, defense=ns.defense, crit_rate=ns.crit_rate)

    async def _h_rob(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        cd = self._action_cd(save, "cd_rob", 60)
        if cd > 0:
            return {"message": f"打劫在冷却中,剩 {cd}s 喵", "outcome": "cd"}
        self._set_action_cd(save, "cd_rob")
        npc = NPCPool.spawn_sanxiu(save, "散修")
        side = CombatEngine.player_side(save)   # 打劫是个人行为,不叫猫娘
        side = self._pet_bonus_side(save, side)
        result = CombatEngine.fight(side, self._npc_stats(npc))
        if result["win"]:
            reward = npc.reward_lingshi
            save.add_lingshi(reward)
            await self.store.save(save)
            return {"message": f"【打劫】{npc.quote}\n{result['detail']},打劫成功!获得 {reward} 灵石",
                    "outcome": "rob_win",
                    "facts": [{"kind": "rob_win", "npc": npc.name, "reward": reward}],
                    "summary": f"打劫{npc.name}成功,获{reward}灵石"}
        lost = int(save.lingshi_total() * 0.05)
        save.add_lingshi(-lost)
        await self.store.save(save)
        return {"message": f"【打劫】{npc.quote}\n{result['detail']},反被收拾,损失 {lost} 灵石",
                "outcome": "rob_lose",
                "facts": [{"kind": "rob_lose", "npc": npc.name, "lost": lost}],
                "summary": f"打劫{npc.name}失败,损{lost}灵石"}

    async def _h_duel(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        cd = self._action_cd(save, "cd_duel", 30)
        if cd > 0:
            return {"message": f"切磋在冷却中,剩 {cd}s 喵", "outcome": "cd"}
        self._set_action_cd(save, "cd_duel")
        npc = NPCPool.spawn_sanxiu(save, "散修")
        side = CombatEngine.player_side(save)
        side = self._pet_bonus_side(save, side)
        result = CombatEngine.fight(side, self._npc_stats(npc))
        if result["win"]:
            reward = 50 + save.realm_idx * 20
            save.add_lingshi(reward)
            await self.store.save(save)
            return {"message": f"【切磋】{npc.quote}\n{result['detail']},切磋获胜!获得 {reward} 灵石",
                    "outcome": "duel_win",
                    "facts": [{"kind": "duel_win", "npc": npc.name, "reward": reward}],
                    "summary": f"切磋胜{npc.name}"}
        return {"message": f"【切磋】{npc.quote}\n{result['detail']},切磋落败,胜败乃兵家常事喵",
                "outcome": "duel_lose",
                "facts": [{"kind": "duel_lose", "npc": npc.name}],
                "summary": f"切磋败于{npc.name}"}

    async def _h_boss(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        cd = self._action_cd(save, "cd_boss", 600)
        if cd > 0:
            return {"message": f"大妖还没缓过来,剩 {cd}s 可再讨伐喵", "outcome": "cd"}
        self._set_action_cd(save, "cd_boss")
        # BOSS 轮换: 按当天/次数选
        boss_idx = (save.sign_days + save.realm_idx) % len(NPCPool.BOSS_TEMPLATES)
        npc = NPCPool.spawn_boss(save, boss_idx)
        helper = self._neko_helper(save)   # 猫娘助阵
        side = CombatEngine.player_side(save, helper)
        side = self._pet_bonus_side(save, side)
        result = CombatEngine.fight(side, self._npc_stats(npc))
        neko_note = f"{self.neko.name}助阵" if helper else ""
        if result["win"]:
            for item in npc.loot:
                save.bag[item] = save.bag.get(item, 0) + 1
            save.add_lingshi(npc.reward_lingshi)
            await self.store.save(save)
            loot_txt = "、".join(f"{i}x1" for i in npc.loot) or "无"
            return {"message": (f"【讨伐·{npc.name}】{npc.quote}\n"
                                f"{result['detail']}({neko_note}),大妖伏诛!\n"
                                f"战利: {loot_txt}, 灵石+{npc.reward_lingshi}"),
                    "outcome": "boss_win",
                    "facts": [{"kind": "boss_win", "npc": npc.name,
                               "loot": npc.loot, "reward": npc.reward_lingshi}],
                    "summary": f"讨伐{npc.name}成功"}
        lost = int(save.lingshi_total() * 0.03)
        save.add_lingshi(-lost)
        await self.store.save(save)
        return {"message": (f"【讨伐·{npc.name}】{npc.quote}\n"
                            f"{result['detail']}({neko_note}),不敌败退,损失 {lost} 灵石"),
                "outcome": "boss_lose",
                "facts": [{"kind": "boss_lose", "npc": npc.name, "lost": lost}],
                "summary": f"讨伐{npc.name}失败"}

    # ── 团本(猫娘+队友NPC 合攻) ────────────

    async def _h_tuanben(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        today = time.strftime("%Y-%m-%d")
        if save.extra.get("tuanben_cd") == today:
            return {"message": "今日团本已讨伐过喵,明日再来!", "outcome": "cd"}
        boss = NPCPool.spawn_tuanben_boss(save)
        helpers = [h for h in [self._neko_helper(save),
                               NPCPool.spawn_teammate(save),
                               NPCPool.spawn_teammate(save)] if h]
        side = CombatEngine.player_side(save, *helpers)
        side = self._pet_bonus_side(save, side)
        result = CombatEngine.fight(side, self._npc_stats(boss))
        save.extra["tuanben_cd"] = today
        if result["win"]:
            for item in boss.loot:
                save.bag[item] = save.bag.get(item, 0) + 1
            save.add_lingshi(boss.reward_lingshi)
            exp_gain = 2000 + save.realm_idx * 500
            save.exp += exp_gain
            await self.store.save(save)
            return {"message": (f"【团本·{boss.name}】{boss.quote}\n"
                                f"{result['detail']}(猫娘+队友合攻),帝尊伏诛!\n"
                                f"战利: {'、'.join(boss.loot)} + 灵石{boss.reward_lingshi} + 修为{exp_gain}"),
                    "outcome": "tuanben_win",
                    "facts": [{"kind": "tuanben_win", "npc": boss.name,
                               "loot": boss.loot, "exp": exp_gain}],
                    "summary": f"团本讨伐{boss.name}成功"}
        lost = int(save.lingshi_total() * 0.03)
        save.add_lingshi(-lost)
        await self.store.save(save)
        return {"message": f"【团本·{boss.name}】{boss.quote}\n{result['detail']},团灭败退,损失{lost}灵石",
                "outcome": "tuanben_lose",
                "facts": [{"kind": "tuanben_lose", "npc": boss.name, "lost": lost}],
                "summary": f"团本讨伐{boss.name}失败"}

    # ── 宗门大战(玩家宗门 vs NPC宗门) ──────

    async def _h_sect_war(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        guild = await self._guild_of(save)
        if guild is None:
            return {"message": "没有宗门打什么宗门大战喵,先「开宗立派」", "outcome": "no_sect"}
        cd = self._action_cd(save, "cd_sectwar", 3600)
        if cd > 0:
            return {"message": f"宗门大战在休整中,剩 {cd}s 喵", "outcome": "cd"}
        self._set_action_cd(save, "cd_sectwar")
        npc_sect = NPCPool.spawn_npc_sect(save)
        helpers = [self._neko_helper(save)]
        if self.neko.sect_enabled:
            helpers.append(NPCPool.spawn_teammate(save))   # NPC 弟子
        side = CombatEngine.player_side(save, *[h for h in helpers if h])
        side = self._pet_bonus_side(save, side)
        result = CombatEngine.fight(side, self._npc_stats(npc_sect))
        if result["win"]:
            gain = npc_sect.reward_lingshi
            guild.spirit_stones += gain
            for item in npc_sect.loot:
                guild.store[item] = guild.store.get(item, 0) + 1
            await self.guilds.store.save(guild)
            return {"message": (f"【宗门大战】攻打{npc_sect.name}!\n"
                                f"{result['detail']},攻破山门!\n"
                                f"缴获: 灵石池+{gain}, 秘藏入宗门仓库"),
                    "outcome": "sectwar_win",
                    "facts": [{"kind": "sectwar_win", "npc": npc_sect.name, "gain": gain}],
                    "summary": f"宗门大战胜{npc_sect.name}"}
        lost = int(guild.spirit_stones * 0.05)
        guild.spirit_stones = max(0, guild.spirit_stones - lost)
        await self.guilds.store.save(guild)
        return {"message": f"【宗门大战】攻打{npc_sect.name}!\n{result['detail']},兵败,宗门损失{lost}灵石",
                "outcome": "sectwar_lose",
                "facts": [{"kind": "sectwar_lose", "npc": npc_sect.name, "lost": lost}],
                "summary": f"宗门大战败于{npc_sect.name}"}

    # ── 市场(NPC 商店/拍卖) ────────────────

    async def _h_shop(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        text = cmd.replace("市场", "", 1).replace("商店", "", 1).replace("交易", "", 1).strip()
        if not text:
            goods = self.market.shop_goods()
            lines = ["【市场·NPC商店】"]
            for g in goods:
                lines.append(f"  {g['name']}({g.get('class','')}) {self.catalog.buy_price(g)}灵石")
            lines.append("用法: 市场 买 丹药名xN / 出售 物品名xN")
            return {"message": "\n".join(lines), "outcome": "shop_view",
                    "game_result": [g["name"] for g in goods],
                    "summary": "查看商店"}
        if "买" in text or "购" in text:
            item_name, n = self._parse_item(text.replace("买", "").replace("购", "").strip())
            item = self.catalog.search(item_name)
            if not item:
                return {"message": f"商店没有「{item_name}」喵", "outcome": "no_item"}
            # 道侣定情物定价(原数据售价0 → 1灵石就买, 结道侣变儿戏; 设为真价格)
            price = None
            if item["name"] == self.neko.gift_item:
                price = int(self._cfg("neko.gift_price", 2000))
            r = await self.market.buy(save, item, n, price=price)
            if r["ok"]:
                await self.store.save(save)
                return {"message": r["msg"], "outcome": "shop_buy",
                        "facts": [{"kind": "shop_buy", "item": item_name, "count": n}],
                        "summary": r["msg"]}
            return {"message": r["msg"], "outcome": "no_lingshi"}
        return {"message": "市场用法: 市场 买 丹药名xN / 出售 物品名xN", "outcome": "shop_help"}

    async def _h_sell(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        item_name, n = self._parse_item(cmd.replace("出售", "", 1).strip())
        if save.bag.get(item_name, 0) < n:
            return {"message": f"背包里没有 {n} 个{item_name}", "outcome": "no_item"}
        item = self.catalog.search(item_name)
        if not item:
            # 非目录物品(BOSS掉落等)按通用价出售, 避免"死掉落"
            price = 50 * n
            save.bag[item_name] -= n
            if save.bag[item_name] <= 0:
                del save.bag[item_name]
            save.add_lingshi(price)
            await self.store.save(save)
            return {"message": f"出售 {item_name} ×{n}(获得 {price} 灵石)",
                    "outcome": "sell",
                    "facts": [{"kind": "sell", "item": item_name, "count": n, "price": price}],
                    "summary": f"出售{item_name}x{n}"}
        r = await self.market.sell(save, item, n)
        if r["ok"]:
            await self.store.save(save)
            return {"message": r["msg"], "outcome": "sell",
                    "facts": [{"kind": "sell", "item": item_name, "count": n, "price": r["price"]}],
                    "summary": r["msg"]}
        return {"message": r["msg"], "outcome": "no_item"}

    async def _h_auction(self, user_id: str, cmd: str) -> Dict[str, Any]:
        r = self.market.open_auction()
        if not r["ok"]:
            return {"message": r["msg"], "outcome": "no_auction"}
        a = r["auction"]
        ceil = int(a["base"] * self.market.AUCTION_NPC_CEIL_RATIO)
        return {"message": (f"【星阁拍卖行】\n拍品: {a['item']['name']}({a['item'].get('class','')})\n"
                            f"底价: {a['base']} 灵石 | 当前: 星阁 {a['npc_bid']}\n"
                            f"出价「竞价 N」,超过 {ceil} 即成交喵"),
                "outcome": "auction_view", "game_result": a,
                "summary": f"拍卖:{a['item']['name']}"}

    async def _h_bid(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        text = cmd.replace("竞价", "", 1).strip()
        amount = 0
        for ch in text:
            if ch.isdigit():
                amount = amount * 10 + int(ch)
        if amount <= 0:
            return {"message": "用法: 竞价 5000", "outcome": "bid_help"}
        r = await self.market.bid(save, amount)
        if not r["ok"]:
            return {"message": r["msg"], "outcome": "bid_low"}
        await self.store.save(save)
        if r.get("won"):
            return {"message": r["msg"], "outcome": "auction_win",
                    "facts": [{"kind": "auction_win", "item": r["item"], "price": r["price"]}],
                    "summary": r["msg"]}
        return {"message": r["msg"], "outcome": "auction_bid"}

    # ── 职业(采集/炼丹/炼器) ───────────────

    async def _do_gather(self, user_id: str, kind: str, label: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        r = await self.occupation.gather(save, kind)
        if r["ok"]:
            await self.store.save(save)
            return {"message": r["msg"], "outcome": "gather",
                    "facts": [{"kind": "gather", "item": r["item"], "label": label}],
                    "summary": r["msg"]}
        return {"message": r["msg"], "outcome": "cd"}

    async def _h_gather_herb(self, user_id: str, cmd: str) -> Dict[str, Any]:
        return await self._do_gather(user_id, "药", "草药")

    async def _h_gather_ore(self, user_id: str, cmd: str) -> Dict[str, Any]:
        return await self._do_gather(user_id, "矿", "材料")

    async def _h_refine_pill(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        text = cmd.replace("炼丹", "", 1).strip()
        if text:
            recipe = self.craft.recipes.find_combine(text)
            if recipe and recipe.get("class") == "丹药":
                r = await self.craft.combine(save, text)
                if r["ok"]:
                    await self.store.save(save)
                    return {"message": r["msg"], "outcome": "refine",
                            "facts": [{"kind": "refine", "item": r["item"]}],
                            "summary": r["msg"]}
                return {"message": r["msg"], "outcome": "no_material"}
        # 无指定名称/无配方 → 简化炼丹(消耗草药)
        r = await self.occupation.refine_pill(save)
        if r["ok"]:
            await self.store.save(save)
            return {"message": r["msg"], "outcome": "refine",
                    "facts": [{"kind": "refine", "item": r["item"], "consumed": r["consumed"]}],
                    "summary": r["msg"]}
        return {"message": r["msg"], "outcome": "no_herb"}

    async def _h_forge_equip(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        text = cmd.replace("炼器", "", 1).replace("锻造", "", 1).strip()
        if text:
            recipe = self.craft.recipes.find_combine(text)
            if recipe and recipe.get("class") == "装备":
                r = await self.craft.combine(save, text)
                if r["ok"]:
                    await self.store.save(save)
                    return {"message": r["msg"], "outcome": "forge",
                            "facts": [{"kind": "forge", "item": r["item"]}],
                            "summary": r["msg"]}
                return {"message": r["msg"], "outcome": "no_material"}
        # 无指定名称/无配方 → 简化炼器(消耗材料)
        r = await self.occupation.forge_equip(save)
        if r["ok"]:
            await self.store.save(save)
            return {"message": r["msg"], "outcome": "forge",
                    "facts": [{"kind": "forge", "item": r["item"], "consumed": r["consumed"]}],
                    "summary": r["msg"]}
        return {"message": r["msg"], "outcome": "no_material"}

    # ── 配方合成/加工 ─────────────────────

    async def _h_combine(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        text = cmd.replace("合成", "", 1).strip()
        if not text:
            rows = self.craft.recipes.list_combine(limit=15)
            lines = ["【合成配方】"]
            lines += [f"  {self.craft.recipes.describe(r)}" for r in rows]
            lines.append("用法: 合成 物品名(如「合成 玄铁剑」)")
            return {"message": "\n".join(lines), "outcome": "recipe_view",
                    "summary": f"合成配方{len(rows)}个"}
        r = await self.craft.combine(save, text)
        if r["ok"]:
            await self.store.save(save)
            return {"message": r["msg"], "outcome": "combine",
                    "facts": [{"kind": "combine", "item": r["item"], "count": r["count"]}],
                    "summary": r["msg"]}
        return {"message": r["msg"], "outcome": "no_material"}

    async def _h_process(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        text = cmd.replace("加工", "", 1).strip()
        if not text:
            rows = self.craft.recipes.process[:15]
            lines = ["【加工配方】"]
            for r in rows:
                ins = "、".join(i["name"] for i in r.get("inputs", []))
                outs = "、".join(o["name"] for o in r.get("outputs", []))
                lines.append(f"  {r.get('name')}: {ins} → {outs}")
            lines.append("用法: 加工 物品名")
            return {"message": "\n".join(lines), "outcome": "recipe_view",
                    "summary": f"加工配方{len(rows)}个"}
        r = await self.craft.process(save, text)
        if r["ok"]:
            await self.store.save(save)
            return {"message": r["msg"], "outcome": "process",
                    "facts": [{"kind": "process", "item": r["item"]}],
                    "summary": r["msg"]}
        return {"message": r["msg"], "outcome": "no_material"}

    async def _h_recipe(self, user_id: str, cmd: str) -> Dict[str, Any]:
        text = cmd.replace("配方", "", 1).strip()
        if not text:
            return {"message": "用法: 配方 物品名(如「配方 玄铁剑」),或「合成」「加工」看列表",
                    "outcome": "recipe_help"}
        r = self.craft.recipes.find_combine(text) or self.craft.recipes.find_process(text)
        if not r:
            return {"message": f"没有「{text}」的配方喵", "outcome": "no_recipe"}
        if r.get("materials"):
            mats = "、".join(f"{m['name']}x{m.get('amount', 1)}" for m in r["materials"])
            return {"message": f"{r['name']}({r.get('class', '')}) ← {mats}",
                    "outcome": "recipe_view", "summary": f"{r['name']}配方"}
        ins = "、".join(f"{i['name']}x{i.get('amount', 1)}" for i in r.get("inputs", []))
        outs = "、".join(f"{o['name']}x{o.get('amount', 1)}" for o in r.get("outputs", []))
        return {"message": f"{r.get('name')}: {ins} → {outs}",
                "outcome": "recipe_view", "summary": f"{r.get('name')}配方"}

    # ── 宠物(仙宠) ─────────────────────────

    @staticmethod
    def _slot_of(item: Dict[str, Any]) -> str:
        """装备类型 → 槽位(武器/护甲/饰品)。"""
        t = str(item.get("type", ""))
        if any(k in t for k in ("武器", "剑", "刀", "枪", "杖", "弓", "戟")):
            return "weapon"
        if any(k in t for k in ("甲", "袍", "衣", "护", "铠", "服")):
            return "armor"
        return "accessory"

    def _equip_stats(self, save: PlayerSave) -> Dict[str, Any]:
        """已穿戴装备的加成总和(atk/def/HP/bao)。"""
        total = {"attack": 0, "defense": 0, "hp": 0, "crit": 0.0}
        for item_name in (save.extra.get("equips", {}) or {}).values():
            if not item_name:
                continue
            item = self.catalog.search(item_name)
            if not item:
                continue
            total["attack"] += int(item.get("atk", 0))
            total["defense"] += int(item.get("def", 0))
            total["hp"] += int(item.get("HP", 0))
            total["crit"] += float(item.get("bao", 0))
        return total

    def _pet_bonus_side(self, save: PlayerSave, side: Dict[str, Any]) -> Dict[str, Any]:
        """出战仙宠(百分比)+ 穿戴装备(绝对值)加成应用到战斗侧。"""
        b = self.pets.combat_bonus(save)
        eq = self._equip_stats(save)
        side = dict(side)
        side["attack"] = int(side["attack"] * (1 + b["attack"]) + eq["attack"])
        side["defense"] = int(side["defense"] * (1 + b["defense"]) + eq["defense"])
        side["hp"] = int(side["hp"] * (1 + b["hp"]) + eq["hp"])
        side["crit_rate"] = min(0.9, side.get("crit_rate", 0.05) + eq["crit"])
        return side

    async def _h_pet(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        pets = self.pets._pets(save)
        if not pets:
            return {"message": "还没有仙宠喵,发「领养仙宠」看可领养列表(灵石)", "outcome": "no_pet"}
        active = self.pets.active_name(save)
        lines = ["【仙宠】"]
        for n, p in pets.items():
            info = self.pets.pets.get(n, {})
            # 品质 = 基础品质 + 进阶次数(显示真实品质, 与「进阶仙宠」一致)
            q0 = str(info.get("品质", "仙胎"))
            qi = QUALITY_ORDER.index(q0) if q0 in QUALITY_ORDER else 0
            q = QUALITY_ORDER[min(qi + int(p.get("evolve", 0)), len(QUALITY_ORDER) - 1)]
            mark = "▶" if n == active else "  "
            lines.append(f"{mark} {n}({q}) Lv.{p.get('level', 1)}")
        b = self.pets.combat_bonus(save)
        if active:
            lines.append(f"出战加成: 攻击+{b['attack'] * 100:.1f}% 防御+{b['defense'] * 100:.1f}%")
        lines.append("用法: 出战仙宠 X / 喂给仙宠 X / 进阶仙宠 X / 派仙宠寻宝")
        return {"message": "\n".join(lines), "outcome": "pet_view",
                "game_result": pets, "summary": f"仙宠{len(pets)}只"}

    async def _h_pet_adopt(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        name = cmd.replace("领养仙宠", "", 1).strip()
        if not name:
            # 无参数 → 列出可领养仙宠(前 10 只按价格)
            rows = sorted(self.pets.pets.values(), key=lambda x: int(x.get("售价") or 10**9))[:10]
            lines = ["【可领养仙宠】"]
            lines += [f"  {p['name']}({p.get('品质', '')}) {int(p.get('售价') or 1000)}灵石" for p in rows]
            lines.append("用法: 领养仙宠 名字")
            return {"message": "\n".join(lines), "outcome": "pet_list",
                    "summary": f"可领养{len(rows)}只"}
        r = await self.pets.adopt(save, name)
        if r["ok"]:
            await self.store.save(save)
            return {"message": r["msg"], "outcome": "pet_adopt",
                    "facts": [{"kind": "pet_adopt", "item": name}], "summary": r["msg"]}
        return {"message": r["msg"], "outcome": "no_pet"}

    async def _h_pet_active(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        name = cmd.replace("出战仙宠", "", 1).strip()
        if not self.pets.set_active(save, name):
            return {"message": f"没有仙宠「{name}」喵", "outcome": "no_pet"}
        await self.store.save(save)
        return {"message": f"「{name}」出战喵!", "outcome": "pet_active",
                "facts": [{"kind": "pet_active", "item": name}],
                "summary": f"{name}出战"}

    async def _h_pet_feed(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        name = cmd.replace("喂给仙宠", "", 1).strip() or self.pets.active_name(save) or ""
        r = await self.pets.feed(save, name)
        if r["ok"]:
            await self.store.save(save)
            return {"message": r["msg"], "outcome": "pet_feed",
                    "facts": [{"kind": "pet_feed", "item": name, "level": r["level"]}],
                    "summary": r["msg"]}
        return {"message": r["msg"], "outcome": "no_food"}

    async def _h_pet_evolve(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        name = cmd.replace("进阶仙宠", "", 1).strip() or self.pets.active_name(save) or ""
        r = await self.pets.evolve(save, name)
        if r["ok"]:
            await self.store.save(save)
            return {"message": r["msg"], "outcome": "pet_evolve",
                    "facts": [{"kind": "pet_evolve", "item": name}], "summary": r["msg"]}
        return {"message": r["msg"], "outcome": "no_lingshi"}

    async def _h_pet_explore(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        name = self.pets.active_name(save) or ""
        if not name:
            return {"message": "先「出战仙宠」再派寻宝喵", "outcome": "no_pet"}
        r = await self.pets.explore_start(save, name)
        if r["ok"]:
            await self.store.save(save)
            return {"message": r["msg"], "outcome": "pet_explore",
                    "facts": [{"kind": "pet_explore", "item": name}], "summary": r["msg"]}
        return {"message": r["msg"], "outcome": "exploring"}

    async def _h_pet_explore_end(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        name = self.pets.active_name(save) or ""
        # 提前召回不结算战利品(修复: 之前"派-立即结束"可瞬间刷高价值奖励)
        pets = save.extra.get("pets", {})
        p = pets.get(name) or {}
        end = p.get("explore_end")
        if end and end > time.time():
            remain = int(end - time.time())
            return {"message": f"「{name}」还在寻宝中,剩 {remain}s(提前召回无收获喵)",
                    "outcome": "exploring"}
        r = await self.pets.explore_settle(save, name, force=True)
        if not r or not r.get("ready"):
            return {"message": "仙宠没有在寻宝喵", "outcome": "no_explore"}
        save.bag[r["item"]] = save.bag.get(r["item"], 0) + r["count"]
        await self.store.save(save)
        return {"message": f"「{name}」寻宝归来,带回 {r['item']} ×{r['count']} 喵!",
                "outcome": "pet_explore_done",
                "facts": [{"kind": "pet_explore_done", "item": r["item"], "count": r["count"]}],
                "summary": f"寻宝获得{r['item']}x{r['count']}"}

    # ── 任务/成就/排行 ─────────────────────

    async def _h_task(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        v = self.tasks.view(save)
        lines = [f"【每日任务 · {v['date']}】"]
        for r in v["rows"]:
            mark = "✅" if r["done"] else "⬜"
            lines.append(f"{mark} {r['name']} {r['cur']}/{r['target']}")
        if v["all_done"] and not v["claimed"]:
            lines.append("全部完成!发「提交每日任务」领奖喵")
        elif v["claimed"]:
            lines.append("今日奖励已领取喵")
        return {"message": "\n".join(lines), "outcome": "task_view",
                "game_result": v,
                "summary": f"每日任务{sum(1 for r in v['rows'] if r['done'])}/{len(v['rows'])}"}

    async def _h_task_claim(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        r = self.tasks.claim(save)
        if r["ok"]:
            await self.store.save(save)
            return {"message": r["msg"], "outcome": "task_claim",
                    "facts": [{"kind": "task_claim", "reward": r["reward"]}],
                    "summary": r["msg"]}
        return {"message": r["msg"], "outcome": "task_incomplete"}

    async def _h_achievement(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        # 先检查一次新成就
        news = self.achievements.check_all(save)
        if news:
            await self.store.save(save)
        v = self.achievements.view(save)
        lines = [f"【成就】{v['unlocked']}/{v['total']}"]
        for r in v["rows"]:
            mark = "🏆" if r["unlocked"] else "🔒"
            lines.append(f"{mark} {r['name']}: {r['desc']}")
        return {"message": "\n".join(lines), "outcome": "achievement_view",
                "game_result": v, "summary": f"成就{v['unlocked']}/{v['total']}"}

    async def _h_ranking(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        lb = self.ranking.leaderboard(save)
        lines = ["【天榜】"]
        for e in lb[:10]:
            mark = "▶" if not e["is_npc"] else "  "
            lines.append(f"{e['rank']:>2}. {mark} {e['name']} 战力{e['power']}")
        my = next(e for e in lb if not e["is_npc"])
        lines.append(f"你在第 {my['rank']} 名,发「比试 名次」挑战上位喵")
        return {"message": "\n".join(lines), "outcome": "ranking_view",
                "game_result": lb, "summary": f"天榜第{my['rank']}名"}

    async def _h_rank_challenge(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        cd = self._action_cd(save, "cd_rank", 300)
        if cd > 0:
            return {"message": f"天榜比试在冷却中,剩 {cd}s 喵", "outcome": "cd"}
        self._set_action_cd(save, "cd_rank")
        text = cmd.replace("比试", "", 1).strip()
        rank = 0
        for ch in text:
            if ch.isdigit():
                rank = rank * 10 + int(ch)
        if rank <= 0:
            return {"message": "用法: 比试 名次(如「比试 3」挑战天榜第3)", "outcome": "rank_help"}
        npc = self.ranking.npc_at_rank(save, rank)
        if npc is None:
            return {"message": f"天榜第{rank}名不是可挑战的NPC喵(或名次不存在)", "outcome": "no_target"}
        side = CombatEngine.player_side(save)
        side = self._pet_bonus_side(save, side)
        result = CombatEngine.fight(side, self._npc_stats(npc))
        if result["win"]:
            reward = npc.reward_lingshi
            save.add_lingshi(reward)
            await self.store.save(save)
            return {"message": f"【比试】挑战天榜第{rank} {npc.name}!\n{result['detail']},获胜!排名上升,灵石+{reward}",
                    "outcome": "rank_win",
                    "facts": [{"kind": "rank_win", "npc": npc.name, "rank": rank, "reward": reward}],
                    "summary": f"天榜挑战胜{npc.name}"}
        lost = int(save.lingshi_total() * 0.02)
        save.add_lingshi(-lost)
        await self.store.save(save)
        return {"message": f"【比试】挑战天榜第{rank} {npc.name}!\n{result['detail']},落败,损失{lost}灵石",
                "outcome": "rank_lose",
                "facts": [{"kind": "rank_lose", "npc": npc.name, "rank": rank}],
                "summary": f"天榜挑战败于{npc.name}"}

    # ── 诸天/小世界 ───────────────────────

    async def _h_zhutian(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        cd = self._action_cd(save, "cd_zhutian", 300)
        if cd > 0:
            return {"message": f"诸天通道还在凝聚,剩 {cd}s 可再投影喵", "outcome": "cd"}
        self._set_action_cd(save, "cd_zhutian")
        r = await self.zhutian.project(save)
        await self.store.save(save)
        if r["danger"]:
            return {"message": r["msg"], "outcome": "zhutian_danger",
                    "facts": [{"kind": "zhutian_danger", "world": r["world"]["name"], "lost": r["lost"]}],
                    "summary": f"投影{r['world']['name']}遇险"}
        return {"message": r["msg"], "outcome": "zhutian_ok",
                "facts": [{"kind": "zhutian_ok", "world": r["world"]["name"],
                           "type": r["type"], "value": r["value"]}],
                "summary": f"投影{r['world']['name']}收获{r['detail']}"}

    async def _h_xsj_open(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        r = await self.xiaoshijie.open_world(save)
        if r["ok"]:
            await self.store.save(save)
            return {"message": r["msg"], "outcome": "xsj_open",
                    "facts": [{"kind": "xsj_open"}], "summary": r["msg"]}
        return {"message": r["msg"], "outcome": "no_lingshi"}

    async def _h_xsj(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        st = await self.xiaoshijie.status(save)
        return {"message": st["msg"] if st.get("opened") else st["msg"],
                "outcome": "xsj_view", "game_result": st,
                "summary": "小世界" + ("Lv." + str(st["level"]) if st.get("opened") else "未开辟")}

    async def _h_xsj_plant(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        crop = cmd.replace("小世界栽种", "", 1).strip() or "灵稻"
        r = await self.xiaoshijie.plant(save, crop)
        if r["ok"]:
            await self.store.save(save)
            return {"message": r["msg"], "outcome": "xsj_plant",
                    "facts": [{"kind": "xsj_plant", "crop": crop}], "summary": r["msg"]}
        return {"message": r["msg"], "outcome": "xsj_err"}

    async def _h_xsj_harvest(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        r = await self.xiaoshijie.harvest(save)
        if r["ok"]:
            save.add_lingshi(r["yield"] * 5)
            await self.store.save(save)
            return {"message": r["msg"] + f"(灵稻折算灵石+{r['yield'] * 5})",
                    "outcome": "xsj_harvest",
                    "facts": [{"kind": "xsj_harvest", "crop": r["crop"], "yield": r["yield"]}],
                    "summary": r["msg"]}
        return {"message": r["msg"], "outcome": "xsj_err"}

    async def _h_xsj_evolve(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        r = await self.xiaoshijie.evolve(save)
        if r["ok"]:
            await self.store.save(save)
            return {"message": r["msg"], "outcome": "xsj_evolve",
                    "facts": [{"kind": "xsj_evolve", "level": r["level"]}], "summary": r["msg"]}
        return {"message": r["msg"], "outcome": "no_lingshi"}

    # ── 魔道/神界/周年 ────────────────────

    async def _do_moshi(self, user_id: str, fn: str, n: int = 1) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        if fn == "offer":
            r = await self.moshen.offer_moshi(save, n)
            if not r["ok"]:
                return {"message": r["msg"], "outcome": "no_lingshi"}
            await self.store.save(save)
            return {"message": r["msg"], "outcome": "moshi_offer",
                    "facts": [{"kind": "moshi_offer", "count": n}], "summary": r["msg"]}
        r = await self.moshen.xiu_mogong(save, n)
        if not r["ok"]:
            return {"message": r["msg"], "outcome": "no_moshi"}
        await self.store.save(save)
        return {"message": r["msg"], "outcome": "mogong",
                "facts": [{"kind": "mogong", "gain": r["gain"]}], "summary": r["msg"]}

    async def _h_offer_moshi(self, user_id: str, cmd: str) -> Dict[str, Any]:
        n = self._parse_num(cmd.replace("供奉魔石", "", 1), 1)
        return await self._do_moshi(user_id, "offer", n)

    async def _h_xiu_mogong(self, user_id: str, cmd: str) -> Dict[str, Any]:
        n = self._parse_num(cmd.replace("修炼魔功", "", 1), 1)
        return await self._do_moshi(user_id, "xiu", n)

    async def _h_mojie(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        st = self.moshen.mojie_status(save)
        return {"message": st["msg"], "outcome": "mojie_view",
                "game_result": st, "summary": st["msg"]}

    async def _h_offer_shenshi(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        n = self._parse_num(cmd.replace("供奉神石", "", 1), 1)
        r = await self.moshen.offer_shenshi(save, n)
        if not r["ok"]:
            return {"message": r["msg"], "outcome": "no_lingshi"}
        await self.store.save(save)
        return {"message": r["msg"], "outcome": "shenshi_offer",
                "facts": [{"kind": "shenshi_offer", "count": n}], "summary": r["msg"]}

    async def _h_canwu(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        n = self._parse_num(cmd.replace("参悟神石", "", 1), 1)
        r = await self.moshen.canwu(save, n)
        if not r["ok"]:
            return {"message": r["msg"], "outcome": "no_shenshi"}
        await self.store.save(save)
        return {"message": r["msg"], "outcome": "canwu",
                "facts": [{"kind": "canwu", "gain": r["gain"]}], "summary": r["msg"]}

    async def _h_shenjie(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        st = self.moshen.shenjie_status(save)
        return {"message": st["msg"], "outcome": "shenjie_view",
                "game_result": st, "summary": st["msg"]}

    async def _h_anniversary(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        r = await self.moshen.anniversary_sign(save)
        if not r["ok"]:
            return {"message": r["msg"], "outcome": "signed"}
        await self.store.save(save)
        return {"message": r["msg"], "outcome": "anniversary",
                "facts": [{"kind": "anniversary", "days": r["days"]}], "summary": r["msg"]}

    @staticmethod
    def _parse_num(text: str, default: int = 1) -> int:
        n = 0
        for ch in text:
            if ch.isdigit():
                n = n * 10 + int(ch)
        return n if n > 0 else default

    @staticmethod
    def _action_cd(save: PlayerSave, key: str, seconds: int) -> int:
        """通用行动冷却: 返回剩余秒数(0=可用)。"""
        last = save.extra.get(key, 0)
        return max(0, int(seconds - (time.time() - last)))

    @staticmethod
    def _set_action_cd(save: PlayerSave, key: str) -> None:
        save.extra[key] = time.time()

    # ── 装备穿戴 / 丹药服用 ───────────────

    async def _h_equip(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        name = cmd.replace("装备", "", 1).strip()
        item = self.catalog.search(name)
        if not item or item.get("class") != "装备":
            return {"message": f"「{name}」不是可穿戴的装备,用「合成」「炼器」或商店获取",
                    "outcome": "no_equip"}
        if save.bag.get(name, 0) < 1:
            return {"message": f"背包里没有「{name}」", "outcome": "no_item"}
        slot = self._slot_of(item)
        eq = save.extra.setdefault("equips", {})
        old = eq.get(slot)
        # 替换时旧装备先回背包
        if old:
            save.bag[old] = save.bag.get(old, 0) + 1
        # 穿戴消耗背包 1 件(修复: 之前不扣背包 + 卸下再加回 = 无限复制漏洞)
        save.bag[name] -= 1
        if save.bag[name] <= 0:
            del save.bag[name]
        eq[slot] = name
        await self.store.save(save)
        return {"message": f"装备「{name}」到{slot}栏" + (f",替换「{old}」已回背包" if old else "") + "喵",
                "outcome": "equip",
                "facts": [{"kind": "equip", "item": name, "slot": slot}],
                "summary": f"装备{name}"}

    async def _h_unequip(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        name = cmd.replace("卸下", "", 1).strip()
        eq = save.extra.get("equips", {})
        slot = None
        for s, n in eq.items():
            if n == name:
                slot = s
                break
        if slot is None:
            return {"message": f"没有穿戴「{name}」喵", "outcome": "no_equip"}
        eq[slot] = None
        save.bag[name] = save.bag.get(name, 0) + 1
        await self.store.save(save)
        return {"message": f"卸下「{name}」,回到背包喵", "outcome": "unequip",
                "facts": [{"kind": "unequip", "item": name}],
                "summary": f"卸下{name}"}

    async def _h_equip_view(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        eq = save.extra.get("equips", {}) or {}
        eqs = self._equip_stats(save)
        lines = ["【装备栏】"]
        for slot in ("weapon", "armor", "accessory"):
            n = eq.get(slot)
            lines.append(f"  {slot}: {n or '空'}")
        if any(eqs.values()):
            lines.append(f"加成: 攻+{eqs['attack']} 防+{eqs['defense']} 血+{eqs['hp']} 暴击+{eqs['crit']*100:.1f}%")
        lines.append("用法: 装备 X / 卸下 X / 服用 X")
        return {"message": "\n".join(lines), "outcome": "equip_view",
                "game_result": {"equips": eq, "bonus": eqs},
                "summary": "查看装备"}

    async def _h_use_pill(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        if save.name == "无名散修" and not save.created_at:
            return {"message": "先「踏入仙途」喵", "outcome": "no_player"}
        name = cmd.replace("服用", "", 1).strip()
        item = self.catalog.search(name)
        if not item or item.get("class") != "丹药":
            return {"message": f"「{name}」不是丹药,用「商店」或「炼丹」获取", "outcome": "no_pill"}
        if save.bag.get(name, 0) < 1:
            return {"message": f"背包里没有「{name}」", "outcome": "no_item"}
        save.bag[name] -= 1
        if save.bag[name] <= 0:
            del save.bag[name]
        effects = []
        if item.get("xueqi"):
            heal = int(item["xueqi"])
            save.hp = min(save.max_hp, save.hp + heal)
            effects.append(f"气血+{heal}")
        if item.get("HP"):
            v = int(item["HP"])
            save.max_hp += v
            save.hp += v
            effects.append(f"气血上限+{v}")
        if item.get("HPp"):
            v = int(save.max_hp * float(item["HPp"]))
            save.max_hp += v
            save.hp += v
            effects.append(f"气血上限+{v}({float(item['HPp'])*100:.0f}%)")
        if item.get("exp"):
            v = int(item["exp"])
            save.exp += v
            effects.append(f"修为+{v}")
        if not effects:
            # 兜底: 无效果字段的丹药按售价折算修为(避免"买了没用")
            v = max(200, int(item.get("售价", 1000)) // 20)
            save.exp += v
            effects.append(f"修为+{v}(丹药精粹)")
        await self.store.save(save)
        return {"message": f"服用「{name}」: {'、'.join(effects)}", "outcome": "use_pill",
                "facts": [{"kind": "use_pill", "item": name, "effects": effects}],
                "summary": f"服用{name}"}

    # ── 移植中占位 ─────────────────────────
    # (全部玩法已移植; 保留占位以防未来新增规则误路由, 按契约返回 unknown)

    async def _h_todo(self, user_id: str, cmd: str) -> Dict[str, Any]:
        return {"facts": [], "outcome": "unknown", "message": ""}

    # ── 工具 ───────────────────────────────

    def _record_neko_mem(self, save: PlayerSave, oc: str,
                         facts: List[Dict[str, Any]]) -> None:
        """记录猫娘能提起的共同经历(近 5 条, 供闲聊注入)。"""
        memo = {
            "breakthrough_ok": lambda f: f"主人突破到了{next((x['realm'] for x in f if 'realm' in x), '新境界')}",
            "daolv": lambda f: "主人和喵喵结为了道侣",
            "boss_win": lambda f: f"主人讨伐了{next((x['npc'] for x in f if 'npc' in x), '大妖')}",
            "tuanben_win": lambda f: f"主人团本击败了{next((x['npc'] for x in f if 'npc' in x), '帝尊')}",
            "gift": lambda f: f"主人送了喵喵礼物(亲密度{next((x['qinmidu'] for x in f if 'qinmidu' in x), 0)})",
            "pet_adopt": lambda f: f"主人领养了仙宠{next((x['item'] for x in f if 'item' in x), '')}",
            "sect_create": lambda f: "主人开宗立派建立了自己的宗门",
            "sectwar_win": lambda f: f"主人宗门大战攻破了{next((x['npc'] for x in f if 'npc' in x), 'NPC宗门')}",
            "secret_lingshi": lambda f: "主人秘境里发现了灵泉",
            "secret_exp": lambda f: "主人秘境里找到前辈遗蜕",
            "xsj_open": lambda f: "主人开辟了小世界",
        }
        gen = memo.get(oc)
        if gen:
            mem = save.extra.setdefault("neko_mem", [])
            mem.append(gen(facts))
            save.extra["neko_mem"] = mem[-5:]

    def _ctx(self, save: PlayerSave) -> Dict[str, Any]:
        rel = self.neko.relationship_summary(save)
        return {
            "neko_name": self.neko.name,
            "relation": rel,
            "qinmidu": save.qinmidu,
            "player_state": save.snapshot(),
            "memory": save.extra.get("neko_mem", []),
        }

    @staticmethod
    def _parse_item(text: str) -> Tuple[str, int]:
        """解析「物品名」或「物品名x3/×3/*3」→ (名称, 数量)。"""
        text = text.strip()
        n = 1
        m = re.search(r"[x×*](\d+)$", text)
        if m:
            n = int(m.group(1))
            text = text[: m.start()].strip()
        return text or "未知物品", n

    # ── 面板/元数据 ────────────────────────

    def classify_event(self, outcome: str, facts: List[Dict[str, Any]]) -> str:
        """修仙语义的事件分级(供大脑 persona 情绪弧线 + emotion 渲染)。

        highlight = 值得渲染卡片的高光事件(与 wants_card 白名单对齐);
        start(踏入仙途)由游戏内自绘角色卡推送, 不进 highlight 避免双卡。
        """
        oc = (outcome or "").lower()
        if any(k in oc for k in (
                "breakthrough_ok", "daolv", "sect_create", "gift",
                "secret_lingshi", "secret_exp", "body_breakthrough_ok",
                "boss_win", "tuanben_win", "sectwar_win", "task_claim",
                "rank_win", "pet_evolve", "zhutian_ok", "xsj_evolve",
                "sign", "anniversary", "xsj_open")):
            return "highlight"
        if any(k in oc for k in ("breakthrough_fail", "body_breakthrough_fail",
                                 "lose", "lowlight")):
            return "lowlight"
        return "routine"

    def format_fact_for_card(self, fact: Dict[str, Any]) -> tuple:
        """修仙专属卡片行格式化(主项目大脑渲染高光卡片时调用)。"""
        kind = fact.get("kind", "")
        if kind in ("breakthrough_ok", "breakthrough_fail"):
            return (f"突破 → {fact.get('realm', '?')}",
                    "gold" if kind == "breakthrough_ok" else "gray")
        if kind in ("boss_win", "tuanben_win"):
            loot = "、".join(fact.get("loot", [])) or "无"
            return (f"讨伐 {fact.get('npc', '?')} 成功 · 战利: {loot}", "legendary")
        if kind == "daolv":
            return (f"与 {fact.get('name', '猫娘')} 结为道侣 ♥", "legendary")
        if kind == "gift":
            return (f"亲密度 → {fact.get('qinmidu', 0)}", "rare")
        if kind == "sectwar_win":
            return (f"攻破 {fact.get('npc', '?')} 山门 · 灵石+{fact.get('gain', 0)}", "rare")
        if kind == "sign":
            return (f"连续签到 {fact.get('days', 1)} 天", "common")
        if kind in ("pet_adopt", "pet_evolve"):
            return (f"仙宠「{fact.get('item', '?')}」", "rare")
        if kind in ("zhutian_ok",):
            return (f"投影 {fact.get('world', '?')} 收获", "rare")
        if kind in ("task_claim",):
            return (f"完成每日任务 · 灵石+{fact.get('reward', 0)}", "rare")
        return super().format_fact_for_card(fact)

    def wants_card(self, outcome: str, facts: List[Dict[str, Any]]) -> bool:
        """高光修仙事件生成卡片。"""
        if outcome in ("breakthrough_ok", "boss_win", "tuanben_win",
                       "daolv", "sectwar_win", "gift", "task_claim"):
            return True
        return bool(facts)

    async def get_status(self, user_id: str = "default") -> Dict[str, Any]:
        save = await self._load(user_id)
        started = bool(save.created_at)
        # 注意：面板每 3s 轮询，这里绝不调 LLM(否则刷爆调用)，只用模板
        neko_line = _pick(SCENE_TEMPLATES.get("greet", [])) if started else ""
        return {
            "started": started,
            "player": save.snapshot() if started else None,
            "neko": self.neko.name if self.neko else "喵喵",
            "neko_line": neko_line,
        }

    def get_meta(self) -> Dict[str, Any]:
        meta = super().get_meta()
        meta["start_cmd"] = "踏入仙途"
        return meta

    def support_panel(self) -> Optional[Dict[str, Any]]:
        """游戏配置面板(锅巴风格 schema)。

        注意: LLM 配置属于主插件全局(插件 UI「全部游戏」下方), 不在这里配。
        """
        return {
            "schemas": [
                {"label": "游戏参数", "component": "Group"},
                {"field": "breakthrough.base_rate", "label": "突破成功率", "component": "InputNumber",
                 "props": {"min": 0.1, "max": 1, "step": 0.05}},
                {"field": "neko.name", "label": "猫娘名字", "component": "Input"},
                {"field": "neko.realm_bonus", "label": "猫娘战力加成", "component": "InputNumber",
                 "props": {"min": 0, "max": 1, "step": 0.05}},
            ]
        }
