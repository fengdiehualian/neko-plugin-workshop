"""人生重开模拟器: GameAdapter 实现。

玩法: 随机10天赋 → 选3(或随机) → 分配属性(或随机) → 逐年模拟 → 人生总结图。
交互: 多轮状态机(选天赋 → 分属性 → 出图), 猫娘旁白点评这一世。
"""

from __future__ import annotations

import itertools
import random
import re
from typing import Any, Dict, List, Optional, Tuple

from ...core.contracts import GameAdapter, build_fact
from .life import Life
from .talent import Talent

game_class = "RemakeGame"

# 进程级共享: 人生数据解析一次, 全用户复用(age.json 1.9MB / events.json 377KB 解析很贵)
_SHARED: Dict[str, Any] = {}


class RemakeGame(GameAdapter):
    id = "remake"
    name = "人生重开"
    description = "人生重开模拟器: 选天赋、分配属性, 猫娘陪你看完这一世。"
    version = "0.1.0"
    icon = "🔄"

    # ── 指令表 (关键词, 说明, handler名) ──
    _RULES: List[Tuple[List[str], str, str]] = [
        (["随机人生"], "随机天赋+随机属性直接重开", "random_life"),
        (["人生重开", "人生重来", "重开"], "人生重开模拟器(选天赋/属性)", "start"),
        (["放弃重开", "取消重开"], "取消当前重开", "cancel"),
        (["我的重开纪录", "重开纪录", "记录"], "查看重开纪录", "record"),
    ]

    def __init__(self, plugin: Any) -> None:
        super().__init__(plugin)
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lives: Dict[str, Life] = {}  # 每用户进行中的 Life

    # ── 配置/存档 ─────────────────────────

    def _cfg(self, key: str, default: Any = None) -> Any:
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
            "state": None,           # None / "talent" / "prop"
            "pending": [],           # 10 个候选天赋 id
            "chosen": [],            # 已选 3 个天赋 id
            "lifes": 0,              # 重开次数
            "best_age": 0,           # 最高享年
            "best_sum": 0,           # 最高总评
            "last": "",              # 上一世简评
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
        self._lives.clear()

    # ── Life 工厂(共享解析数据) ───────────

    def _new_life(self) -> Life:
        global _SHARED
        life = Life()
        if not _SHARED:
            life.load()
            _SHARED["ages"] = life.age.ages
            _SHARED["events"] = life.event.events
            _SHARED["talent_dict"] = life.talent.talent_dict
            _SHARED["loaded"] = True
        else:
            life.age.ages = _SHARED["ages"]
            life.event.events = _SHARED["events"]
            life.talent.talent_dict = _SHARED["talent_dict"]
        return life

    # ── 核心接口 ─────────────────────────

    async def handle_action(self, user_id: str, cmd: str,
                            args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        cmd = (cmd or "").strip().lstrip("#").strip()
        save = await self._load(user_id)

        # 状态机续接优先: 选天赋/分属性阶段, 处理数字/随机/确认词
        # (必须先于 _RULES, 否则「随机人生」等词会在多轮中走错分支)
        if save.get("state") in ("talent", "prop"):
            r = await self._handle_state(user_id, cmd, save)
            if r is not None:
                return r

        # 常规指令(放弃重开/随机人生/记录等)
        handler_name = None
        best_len = 0
        for keywords, _, fn in self._RULES:
            for kw in keywords:
                if kw and kw in cmd and len(kw) > best_len:
                    best_len = len(kw)
                    handler_name = fn
                    break
        if handler_name:
            handler = getattr(self, f"_h_{handler_name}", None)
            if handler:
                return await handler(user_id, cmd)

        if not cmd:
            return {"message": "想重开人生喵?发「人生重开」选天赋,或「随机人生」直接开!",
                    "outcome": "idle"}
        return {"facts": [], "outcome": "unknown", "message": ""}

    def classify_event(self, outcome: str, facts: List[Dict[str, Any]]) -> str:
        """人生结局分级: 好=highlight(LLM 祝贺), 差=lowlight(LLM 安慰)。"""
        if outcome in ("life_good",):
            return "highlight"
        if outcome in ("life_bad", "life_mid"):
            return "lowlight"
        return super().classify_event(outcome, facts)

    def wants_card(self, outcome: str, facts: List[Dict[str, Any]]) -> bool:
        # 总结图已经由游戏自己推(render_life), 不重复出卡片
        return False

    # ── 状态机续接 ───────────────────────

    async def _handle_state(self, user_id: str, cmd: str,
                            save: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """处理选天赋/分属性阶段的输入。返回 None 表示不匹配, 走常规指令。"""
        state = save.get("state")
        # 显式指令(放弃重开/随机人生/记录)优先于状态机续接
        for keywords, _, fn in self._RULES:
            if fn in ("cancel", "random_life", "record"):
                for kw in keywords:
                    if kw and kw in cmd:
                        return None
        life = self._lives.get(user_id)
        if life is None:
            # 状态残留但 Life 丢了 → 重置
            save["state"] = None
            await self._save(user_id, save)
            return None

        if state == "talent":
            return await self._pick_talents(user_id, cmd, save, life)
        if state == "prop":
            return await self._pick_props(user_id, cmd, save, life)
        return None

    def _conflict_talents(self, talents: List[Talent]) -> Optional[Tuple[Talent, Talent]]:
        for t1, t2 in itertools.combinations(talents, 2):
            if t1.exclusive_with(t2):
                return t1, t2
        return None

    def _random_talents(self, talents: List[Talent]) -> List[Talent]:
        while True:
            nums = sorted(random.sample(range(len(talents)), 3))
            selected = [talents[n] for n in nums]
            if not self._conflict_talents(selected):
                return selected

    @staticmethod
    def _wants_random(cmd: str) -> bool:
        """判断输入是否表达「随机/随便/你决定」(含 AI 代玩的确认词)。

        仅精确词或明确意图词, 避免「随便说点什么」被误判为随机。
        """
        c = (cmd or "").strip().lower()
        if c in ("随机", "random", "r", "随便", "你决定", "都可以", "都行",
                 "可以", "好的", "好", "嗯", "行", "帮我", "帮帮我",
                 "对", "对的", "对的喵", "是", "是的", "没错", "确定", "确认"):
            return True
        for w in ("随机", "你决定", "帮我选", "帮我挑", "你来选", "你来挑"):
            if w in c:
                return True
        return False

    async def _pick_talents(self, user_id: str, cmd: str, save: Dict[str, Any],
                            life: Life) -> Dict[str, Any]:
        """第 2 轮: 从 10 候选选 3 个天赋。"""
        # 按 save.pending 的顺序重建候选(与 rand_talents 返回顺序一致)
        talent_by_id = {t.id: t for t in self._all_talents(life)}
        pool = [talent_by_id[i] for i in save.get("pending", []) if i in talent_by_id]
        c = cmd.strip()
        if self._wants_random(c):
            selected = self._random_talents(pool)
        else:
            m = re.fullmatch(r"\s*(\d)\s*(\d)\s*(\d)\s*", c)
            if not m:
                return {"outcome": "talent_prompt", "message": "",
                        "facts": [build_fact("talent_prompt")]}
            nums = sorted(int(x) for x in m.groups())
            if nums[-1] >= len(pool):
                return {"outcome": "talent_prompt", "message": "编号超出范围喵,重新发~",
                        "facts": [build_fact("talent_prompt")]}
            selected = [pool[n] for n in nums]
            conflict = self._conflict_talents(selected)
            if conflict:
                return {"outcome": "talent_prompt",
                        "message": f"「{conflict[0].name}」和「{conflict[1].name}」不能同时选喵,重新发~",
                        "facts": [build_fact("talent_prompt")]}
        for t in selected:
            life.talent.add_talent(t)
        life.talent.update_talent_prop()
        save["chosen"] = [t.id for t in selected]
        save["state"] = "prop"
        await self._save(user_id, save)
        total = life.total_property()
        return {"outcome": "prop_prompt",
                "facts": [build_fact("prop_prompt", total=total)],
                "message": (f"天赋选定: {'、'.join(t.name for t in selected)}。\n"
                            f"现在分配属性: 发 4 个数字(颜值 智力 体质 家境), "
                            f"总和 {total}, 每个≤10, "
                            f"如「{' '.join(str(x) for x in self._example_props(total))}」; "
                            f"或发「随机」")}

    @staticmethod
    def _example_props(total: int) -> List[int]:
        """给 total 造一组合法示例属性(每项≤10, 和=total)。

        天赋加成会改变可分配总和(可能≠20), 示例必须跟着真实 total 走,
        否则玩家照示例输入必然被「属性之和需为 {total}」拒绝。
        """
        n1 = min(total, 10)
        n2 = min(max(total - n1, 0), 10)
        n3 = min(max(total - n1 - n2, 0), 10)
        n4 = max(total - n1 - n2 - n3, 0)
        return [n1, n2, n3, n4]

    async def _pick_props(self, user_id: str, cmd: str, save: Dict[str, Any],
                          life: Life) -> Dict[str, Any]:
        """第 3 轮: 分配属性 → 开跑人生。"""
        total = life.total_property()
        c = cmd.strip()
        if self._wants_random(c):
            nums = self._random_nums(total)
        else:
            m = re.fullmatch(r"\s*(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})\s*", c)
            if not m:
                return {"outcome": "prop_prompt", "message": "",
                        "facts": [build_fact("prop_prompt", total=total)]}
            nums = [int(x) for x in m.groups()]
            if sum(nums) != total:
                return {"outcome": "prop_prompt",
                        "message": f"属性之和需为 {total} 喵,重新发~",
                        "facts": [build_fact("prop_prompt", total=total)]}
            if max(nums) > 10:
                return {"outcome": "prop_prompt",
                        "message": "每个属性不能超过 10 喵,重新发~",
                        "facts": [build_fact("prop_prompt", total=total)]}
        prop = {"CHR": nums[0], "INT": nums[1], "STR": nums[2], "MNY": nums[3]}
        life.apply_property(prop)
        save["state"] = None
        await self._save(user_id, save)
        return await self._run_life(user_id, save, life)

    def _random_nums(self, total: int) -> List[int]:
        half1 = int(total / 2)
        half2 = total - half1
        n1 = random.randint(0, half1)
        n2 = random.randint(0, half2)
        nums = [n1, n2, half1 - n1, half2 - n2]
        random.shuffle(nums)
        return nums

    def _all_talents(self, life: Life) -> List[Talent]:
        lst = []
        for i in range(life.talent.grade_count):
            lst.extend(life.talent.talent_dict.get(i, []))
        return lst

    # ── 指令实现 ─────────────────────────

    async def _h_start(self, user_id: str, cmd: str) -> Dict[str, Any]:
        """第 1 轮: 随机 10 候选天赋, 进入选天赋阶段。"""
        save = await self._load(user_id)
        life = self._new_life()
        self._lives[user_id] = life
        talents = list(life.rand_talents(10))
        save["pending"] = [t.id for t in talents]
        save["chosen"] = []
        save["state"] = "talent"
        await self._save(user_id, save)
        lines = ["新的人生开始喵!从这 10 个天赋里选 3 个——"
                 "发编号(如「0 1 2」),或发「随机」让猫娘帮你挑:"]
        lines += [f"{i}. {t}" for i, t in enumerate(talents)]
        return {"outcome": "talent_prompt",
                "facts": [build_fact("talent_select", count=len(talents))],
                "message": "\n".join(lines)}

    async def _h_random_life(self, user_id: str, cmd: str,
                             auto: bool = False) -> Dict[str, Any]:
        """一步到位: 随机天赋 + 随机属性直接跑完。auto=True 为 LLM/AI 代玩模式。"""
        save = await self._load(user_id)
        life = self._new_life()
        self._lives[user_id] = life
        talents = list(life.rand_talents(10))
        selected = self._random_talents(talents)
        for t in selected:
            life.talent.add_talent(t)
        life.talent.update_talent_prop()
        nums = self._random_nums(life.total_property())
        life.apply_property({"CHR": nums[0], "INT": nums[1],
                             "STR": nums[2], "MNY": nums[3]})
        save["chosen"] = [t.id for t in selected]
        save["state"] = None
        await self._save(user_id, save)
        return await self._run_life(user_id, save, life, random=True)

    async def _h_cancel(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        save["state"] = None
        save["pending"] = []
        save["chosen"] = []
        self._lives.pop(user_id, None)
        await self._save(user_id, save)
        return {"outcome": "cancel", "facts": [build_fact("cancel")],
                "message": "这一世放弃了喵…下次重开记得选好天赋哦~"}

    async def _h_record(self, user_id: str, cmd: str) -> Dict[str, Any]:
        save = await self._load(user_id)
        return {"outcome": "record",
                "facts": [build_fact("record", lifes=save["lifes"],
                                     best_age=save["best_age"], best_sum=save["best_sum"])],
                "message": (f"重开纪录喵: 已重开 {save['lifes']} 世\n"
                            f"最高享年: {save['best_age']} 岁\n"
                            f"最高总评: {save['best_sum']}\n"
                            f"上一世: {save.get('last') or '还没有'}"
                            if save["lifes"] else "还没重开过喵,发「人生重开」试试~")}

    # ── 开跑人生 ─────────────────────────

    async def _run_life(self, user_id: str, save: Dict[str, Any], life: Life,
                        random: bool = False) -> Dict[str, Any]:
        """跑完人生: 逐年模拟 + 渲染总结图 + 推送。"""
        init_prop = life.get_property()
        results = list(life.run())
        summary = life.gen_summary()
        age = summary.AGE.value
        sum_val = summary.SUM.value

        save["lifes"] += 1
        save["best_age"] = max(save["best_age"], age)
        save["best_sum"] = max(save["best_sum"], sum_val)
        save["last"] = f"享年{age}岁, 总评{sum_val}({summary.SUM.judge})"
        await self._save(user_id, save)

        # 结局分级
        if sum_val >= 100 or age >= 80:
            outcome = "life_good"
        elif sum_val <= 40 or age <= 20:
            outcome = "life_bad"
        else:
            outcome = "life_mid"

        talents = [t for t in self._all_talents(life) if t.id in save.get("chosen", [])]
        text = (f"这一世结束了喵~ 享年 {age} 岁, 总评 {sum_val}({summary.SUM.judge})\n"
                + str(summary))

        # 渲染人生总结图(游戏负责生成数据, brain 负责推送)
        # 「游戏适配插件」: 返回 images 数据, 不直接 push
        img_bytes = await self._render_life(talents, init_prop, results, summary)
        images = []
        if img_bytes:
            images.append(self.build_image(text, img_bytes, "image/png"))
        return {"outcome": outcome,
                "facts": [build_fact("life_end", age=age, sum=sum_val,
                                     grade=summary.SUM.judge,
                                     lifes=save["lifes"])],
                "message": text, "images": images}

    async def _render_life(self, talents: List[Talent], init_prop, results,
                           summary) -> Optional[bytes]:
        """人生总结图 —— 走插件渲染桥接(游戏只给数据, 不自己画图)。

        版式块由插件解释: cards(天赋) / stats(属性进度条) / table(人生轨迹)。
        桥接不可用或渲染失败返回 None, 调用方降级纯文本。
        """
        try:
            def _attr(obj, key: str, default: str = "") -> str:
                """统一取值: 有的字段是 property, 有的是普通属性。"""
                val = getattr(obj, key, default)
                return str(val() if callable(val) else val)

            prop_items = [
                {"icon": "heart", "label": _attr(s, "name"), "value": s.value, "max": 10,
                 "note": _attr(s, "judge")}
                for s in (summary.CHR, summary.INT, summary.STR, summary.MNY,
                          summary.SPR)
            ]
            talent_items = [
                {"icon": "star", "name": getattr(t, "name", ""),
                 "desc": getattr(t, "description", "") or getattr(t, "desc", "")}
                for t in talents
            ]
            log_rows = []
            for r in results:
                p = r.property
                logs = list(getattr(r, "event_log", []) or []) + \
                    list(getattr(r, "talent_log", []) or [])
                if logs:
                    log_rows.append([f"{p.AGE}岁", "；".join(logs[:3])])
            blocks: List[Dict[str, Any]] = []
            if talent_items:
                blocks.append({"type": "cards", "title": "天赋", "cols": 3,
                               "items": talent_items})
            blocks.append({"type": "stats", "title": "最终属性", "items": prop_items})
            blocks.append({"type": "stats", "title": "总评", "items": [
                {"icon": "star", "label": "享年", "value": summary.AGE.value, "max": 100,
                 "note": _attr(summary.AGE, "judge")},
                {"icon": "star", "label": "总评", "value": summary.SUM.value, "max": 150,
                 "note": _attr(summary.SUM, "judge")},
            ]})
            if log_rows:
                blocks.append({"type": "table", "title": "人生轨迹",
                               "headers": ["年龄", "事件"], "rows": log_rows[-14:]})
            return await self.render_page(
                f"人生重开 · 享年 {summary.AGE.value} 岁",
                subtitle=f"总评 {summary.SUM.value}（{summary.SUM.judge}）",
                blocks=blocks,
                commands=[["随机人生", "再来一次（随机天赋+属性）"],
                          ["我的重开纪录", "看重开次数/最高享年"]],
                tip="想再来一世 → 发「随机人生」；想看纪录 → 发「我的重开纪录」")
        except Exception as exc:
            import logging
            logging.getLogger("neko_arcade.remake").warning("渲染人生图失败: %s", exc)
            return None

    # ── 状态/面板 ─────────────────────────

    async def get_status(self, user_id: str = "default") -> Dict[str, Any]:
        save = await self._load(user_id)
        return {"lifes": save.get("lifes", 0),
                "best_age": save.get("best_age", 0),
                "best_sum": save.get("best_sum", 0),
                "in_progress": save.get("state") is not None}

    def support_panel(self) -> Optional[Dict[str, Any]]:
        return None
