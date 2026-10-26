"""猫猫进化路: GameAdapter 实现。

玩法: 你是一只小猫, 通过探索猫猫森林、打败小妖、吞食技能不断进化,
最终进化成猫娘!猫娘全程陪玩解说。

核心闭环: 领养小猫 → 探索地图 → 遇敌战斗 → 吞食进化/拿战利品 → 成长 → 进化成猫娘
辅助: 背包 / 商店 / 技能管理 / 任务 / 成就

数据层: 全部用插件 store(JSON 存档), 无外部数据库。
"""

from __future__ import annotations

import json
import os
import random
from typing import Any, Dict, List, Optional, Tuple

from ...core.contracts import GameAdapter, build_fact
from .battle import GameBoard, Logger, MsgManager, Pokemon
from .battle.tools.tools import get_container

game_class = "CatEvolutionGame"

# 进化阶段: 阶段名 → (所需等级, 属性加成)
EVOLUTION_STAGES = [
    (1, "小猫"),
    (5, "灵猫"),
    (10, "猫娘"),
    (15, "猫娘大姐姐"),
]

# 默认玩家初始属性(基础等级 1)
BASE_STATS = {"atk": 20, "def": 10, "hp": 100, "spd": 5, "lv": 1}

# 进程级配置缓存
_data: Dict[str, Any] = {}


class CatEvolutionGame(GameAdapter):
    id = "cat_evolution"
    name = "猫猫进化路"
    description = "你是一只小猫, 探索、战斗、吞食进化, 最终进化成猫娘!"
    version = "0.1.0"
    icon = "🐱"

    _RULES: List[Tuple[List[str], str, str]] = [
        (["领养小猫", "注册", "开始进化"], "领养一只小猫开始进化之路", "register"),
        (["猫猫探索", "探索", "出发"], "探索猫猫森林, 遇敌战斗", "explore"),
        (["返回", "回家"], "结束探索回家", "home"),
        (["猫猫背包", "我的背包", "背包"], "查看背包", "bag"),
        (["猫猫技能", "我的技能", "技能"], "查看已学技能", "skills"),
        (["猫猫状态", "我的状态", "状态"], "查看角色状态", "status"),
        (["猫猫商店", "商店"], "打开商店购买", "shop"),
        (["猫猫任务", "我的任务", "任务"], "查看任务", "mission"),
        (["猫猫成就", "我的成就", "成就"], "查看成就", "achievement"),
    ]

    def __init__(self, plugin: Any) -> None:
        super().__init__(plugin)
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._battle = None  # 惰性初始化战斗系统

    # ── 配置加载 ─────────────────────────

    def _load_data(self) -> Dict[str, Any]:
        """加载猫娘化的游戏配置(地图/敌人/技能/物品/任务)。"""
        if _data:
            return _data
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        out: Dict[str, Any] = {}
        for key, fname in [("maps", "地图.json5"), ("enemies", "敌人.json5"),
                           ("items", "物品.json5"), ("skills", "技能配置.json5"),
                           ("missions", "任务.json5"), ("forge", "制作表.json5"),
                           ("help", "帮助.json5")]:
            p = os.path.join(base, fname)
            if os.path.exists(p):
                try:
                    with open(p, encoding="utf-8") as f:
                        out[key] = json.load(f)
                except Exception:
                    out[key] = {}
        _data.update(out)
        return _data

    # ── 存档 ─────────────────────────

    def _default_save(self) -> Dict[str, Any]:
        return {
            "name": "",              # 猫猫名字
            "stage": "小猫",          # 进化阶段
            "stats": dict(BASE_STATS),  # 属性
            "skill": {},             # 已学技能 {名: 等级}
            "equip_skill": [],       # 已装备技能(战斗用)
            "bag": {"小鱼干": 0},     # 背包
            "missions": {},          # 任务 {名: 完成状态}
            "achievements": [],      # 已获成就
            "kills": 0,              # 击杀数
            "explores": 0,           # 探索次数
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

    # ── 战斗系统初始化 ─────────────────────────

    def _get_battle(self):
        if self._battle:
            return self._battle
        container = get_container()
        container[Logger] = Logger()
        container[MsgManager] = MsgManager(container[Logger])
        container[GameBoard] = GameBoard(container[Logger], container[MsgManager])
        self._battle = container
        return container

    # ── 核心入口 ─────────────────────────

    async def handle_action(self, user_id: str, cmd: str,
                            args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        c = (cmd or "").strip()
        save = await self._load(user_id)
        if not c:
            return {"outcome": "idle", "message": ""}

        # 未注册保护(除领养外都需要先有猫)
        if not save["name"] and not (c in self._RULES[0][0] or any(c.startswith(kw + " ") for kw in self._RULES[0][0])):
            msg = "还没有小猫喵~ 发「领养小猫」开始进化之路!"
            return {"outcome": "need_register", "facts": [], "message": msg}

        # 显式指令: 完全匹配优先, 其次指令名+空格开头(如「探索 1」「领养小猫 咪咪」)
        for kws, _, handler in self._RULES:
            if c in kws:
                return await getattr(self, f"_h_{handler}")(user_id, save, c)
        for kws, _, handler in self._RULES:
            for kw in kws:
                if c.startswith(kw + " "):
                    return await getattr(self, f"_h_{handler}")(user_id, save, c)

        # 探索中选择地图(数字)
        if c.isdigit():
            return await self._h_pick_map(user_id, save, c)

        return await self._h_help(user_id, save, c)

    # ── 指令处理 ─────────────────────────

    async def _h_register(self, user_id: str, save: Dict[str, Any], c: str) -> Dict[str, Any]:
        if save["name"]:
            msg = f"{save['name']}已经是你的小猫了喵~ 发「探索」出发吧!"
            return {"outcome": "already", "facts": [], "message": msg}
        name = c.replace("领养小猫", "").replace("注册", "").replace("开始进化", "").strip()
        if not name:
            name = f"小猫{random.randint(100, 999)}"
        save["name"] = name
        save["bag"]["小鱼干"] = 20  # 初始小鱼干
        await self._save(user_id, save)
        msg = (f"🐱 你领养了一只小猫「{name}」!进化之路开始喵~\n"
               f"初始属性: 攻{save['stats']['atk']} 防{save['stats']['def']} "
               f"命{save['stats']['hp']} 速{save['stats']['spd']}\n"
               f"发「探索」去猫猫森林冒险吧! | 初始小鱼干×20")
        return {"outcome": "register", "facts": [build_fact("register", name=name)],
                "message": msg, "game": self.id}

    async def _h_explore(self, user_id: str, save: Dict[str, Any], c: str) -> Dict[str, Any]:
        data = self._load_data()
        maps = data.get("maps", {})
        # 可达地图(等级足够 + public)
        accessible = {k: v for k, v in maps.items() if v.get("public") and v.get("require_level", 1) <= save["stats"]["lv"]}
        if not accessible:
            msg = "暂时没有可去的地方喵~ 练练级再来!"
            return {"outcome": "no_map", "facts": [], "message": msg}
        if len(accessible) == 1:
            return await self._h_enter_map(user_id, save, list(accessible.keys())[0])
        # 多地图: 列出选择
        lines = ["📍 要去哪里探险喵? 发数字:"]
        for i, name in enumerate(accessible.keys(), 1):
            desc = maps[name].get("description", "")[:30]
            lines.append(f"  {i}. {name} {desc}")
        msg = "\n".join(lines)
        return {"outcome": "map_select", "facts": [], "message": msg,
                "maps": list(accessible.keys())}

    async def _h_pick_map(self, user_id: str, save: Dict[str, Any], c: str) -> Dict[str, Any]:
        data = self._load_data()
        maps = data.get("maps", {})
        accessible = [k for k, v in maps.items() if v.get("public") and v.get("require_level", 1) <= save["stats"]["lv"]]
        idx = int(c) - 1
        if idx < 0 or idx >= len(accessible):
            msg = "数字不对喵~ 重新选一个吧"
            return {"outcome": "bad_map", "facts": [], "message": msg}
        return await self._h_enter_map(user_id, save, accessible[idx])

    async def _h_enter_map(self, user_id: str, save: Dict[str, Any], map_name: str) -> Dict[str, Any]:
        data = self._load_data()
        maps = data.get("maps", {})
        enemies = data.get("enemies", [])
        enemies_by_name = {e["name"]: e for e in enemies}

        # 递归收集本图 + 子地图的敌人(带权重)
        def collect(map_n, depth=0):
            cands = []
            if depth > 5:
                return cands
            m = maps.get(map_n, {})
            cands.extend(list(m.get("enemy_list", []) or []))
            for sub in m.get("owned", []) or []:
                cands.extend(collect(sub.get("name", ""), depth + 1))
            return cands

        candidates = collect(map_name)
        if not candidates:
            msg = f"「{map_name}」里空荡荡的, 什么都没有喵…"
            return {"outcome": "empty_map", "facts": [], "message": msg}
        # 加权随机遇敌
        total = sum(e.get("weight", 1) for e in candidates)
        roll = random.uniform(0, total)
        picked = None
        for e in candidates:
            roll -= e.get("weight", 1)
            if roll <= 0:
                picked = e
                break
        if not picked:
            picked = candidates[-1]
        enemy = enemies_by_name.get(picked["name"])
        if not enemy:
            msg = "遇到神秘的生物喵…但它消失了"
            return {"outcome": "no_enemy", "facts": [], "message": msg}

        save["explores"] += 1
        await self._save(user_id, save)
        neko = self._pick_emotion("encounter", enemy=enemy["name"], map=map_name)
        # 遭遇提示传给 _do_battle, 由战斗结果一起返回 message(brain 统一推)
        # 「游戏适配插件」: 游戏不直接 push, 只返回结构化结果
        return await self._do_battle(user_id, save, enemy, map_name,
                                     intro=f"{neko}\n⚔️ 遭遇 {enemy['name']} (Lv.{picked.get('lv', 1)})!")

    async def _do_battle(self, user_id: str, save: Dict[str, Any],
                         enemy: Dict[str, Any], map_name: str,
                         intro: str = "") -> Dict[str, Any]:
        container = self._get_battle()
        board = container[GameBoard]
        logger = container[Logger]
        msg_mgr = container[MsgManager]
        board.TURN_LIMIT = 30
        msg_mgr.clean()
        logger.clean()

        p1 = container[Pokemon]
        p2 = container[Pokemon]
        s = save["stats"]
        p1.name = save["name"]
        p1.MAX_HP = s["hp"]
        p1.ATK = s["atk"]
        p1.DEF = s["def"]
        p1.SPD = s["spd"]
        p1.lv = s["lv"]
        p1.baselv = 1
        p1.skillGroup = [f"{k}{v}" for k, v in (save.get("equip_skill") or {}).items()] or ["利爪1"]

        p2.name = enemy["name"]
        p2.MAX_HP = int(enemy.get("hp", 50))
        p2.ATK = int(enemy.get("atk", 15))
        p2.DEF = int(enemy.get("def", 5))
        p2.SPD = int(enemy.get("spd", 0))
        p2.lv = int(enemy.get("baselv", 1)) or 1
        p2.baselv = 1
        p2.skillGroup = list(enemy.get("skill", []) or [])

        board.add_ally(p1)
        board.add_enemy(p2)
        board.init()
        result = board.battle()
        log = logger.get_log()

        if result == "我方胜利":
            save["kills"] += 1
            # 吞食进化: 加经验成长
            gain = random.randint(3, 8)
            save["stats"]["lv"] += 1
            s2 = save["stats"]
            s2["atk"] += gain
            s2["def"] += max(1, gain // 2)
            s2["hp"] += gain * 3
            # 吞食技能(概率)
            loot = enemy.get("skill") or []
            gained_skill = None
            if loot and random.random() < 0.6:
                sk = random.choice(loot)
                # 技能名去数字
                base = "".join(ch for ch in sk if not ch.isdigit())
                if base:
                    save["skill"][base] = save["skill"].get(base, 1)
                    gained_skill = base
            # 战利品(小鱼干)
            food = random.randint(5, 20)
            save["bag"]["小鱼干"] = save["bag"].get("小鱼干", 0) + food
            # 进化阶段检查
            old_stage = save["stage"]
            save["stage"] = self._stage_for_lv(s2["lv"])
            evolved = save["stage"] != old_stage
            await self._save(user_id, save)

            lines = [f"🎉 打败了 {enemy['name']}!"]
            lines.append(f"  等级 {s2['lv']} | 攻+{gain} 防+{max(1, gain//2)} 命+{gain*3}")
            if gained_skill:
                lines.append(f"  🍖 吞食技能「{gained_skill}」!")
            lines.append(f"  🐟 获得小鱼干×{food}")
            if evolved:
                lines.append(f"  ✨✨ 进化了! 现在是「{save['stage']}」!! ✨✨")
            neko = self._pick_emotion("win", enemy=enemy["name"], stage=save["stage"])
            # 「游戏适配插件」: 不 push, 返回合并 message(brain 统一推)
            full = "\n".join([intro, neko] + lines) if intro else "\n".join([neko] + lines)
            return {"outcome": "win", "facts": [build_fact("battle", result="win", enemy=enemy["name"])],
                    "message": full, "game": self.id, "log": log}
        else:
            # 战败: 不扣属性, 鼓励重试
            msg = (f"💔 打不过 {enemy['name']}喵… 回去练练再来!\n"
                   f"试试「猫猫状态」看属性, 或去更低级的地图练级")
            neko = self._pick_emotion("lose", enemy=enemy["name"])
            full = "\n".join([intro, neko, msg]) if intro else "\n".join([neko, msg])
            return {"outcome": "lose", "facts": [build_fact("battle", result="lose", enemy=enemy["name"])],
                    "message": full, "game": self.id, "log": log}

    async def _h_bag(self, user_id: str, save: Dict[str, Any], c: str) -> Dict[str, Any]:
        bag = save.get("bag", {})
        items = "、".join(f"{k}×{v}" for k, v in bag.items() if v > 0) or "空空如也"
        msg = f"🎒 {save['name']}的背包: {items}"
        return {"outcome": "bag", "facts": [], "message": msg}

    async def _h_skills(self, user_id: str, save: Dict[str, Any], c: str) -> Dict[str, Any]:
        skills = save.get("skill", {})
        if not skills:
            msg = "还没有学到技能喵~ 打败敌人有概率吞食技能!"
            return {"outcome": "no_skill", "facts": [], "message": msg}
        lines = ["📖 已学技能:"]
        for name, lv in skills.items():
            equipped = " [装备]" if name in (save.get("equip_skill") or {}) else ""
            lines.append(f"  {name} Lv.{lv}{equipped}")
        lines.append("发「装备技能 技能名」来装备(最多3个)")
        msg = "\n".join(lines)
        return {"outcome": "skills", "facts": [], "message": msg}

    async def _h_status(self, user_id: str, save: Dict[str, Any], c: str) -> Dict[str, Any]:
        s = save["stats"]
        msg = (f"🐱 {save['name']} ({save['stage']}) Lv.{s['lv']}\n"
               f"攻击 {s['atk']} | 防御 {s['def']} | 生命 {s['hp']} | 速度 {s['spd']}\n"
               f"击杀 {save['kills']} | 探索 {save['explores']}")
        return {"outcome": "status", "facts": [], "message": msg}

    async def _h_shop(self, user_id: str, save: Dict[str, Any], c: str) -> Dict[str, Any]:
        data = self._load_data()
        items = data.get("items", {})
        lines = ["🛒 猫猫商店 (小鱼干购买):"]
        for name, it in items.items():
            cost = it.get("cost")
            if not cost:
                continue
            lines.append(f"  {name} - {cost}小鱼干: {it.get('des', '')[:30]}")
        msg = "\n".join(lines)
        return {"outcome": "shop", "facts": [], "message": msg}

    async def _h_mission(self, user_id: str, save: Dict[str, Any], c: str) -> Dict[str, Any]:
        data = self._load_data()
        missions = data.get("missions", [])
        lines = ["📋 任务:"]
        for m in missions:
            name = m.get("name", "")
            done = save["missions"].get(name)
            mark = "✅" if done else "⬜"
            lines.append(f"  {mark} {name}: {m.get('des', '')[:40]}")
        msg = "\n".join(lines) if len(lines) > 1 else "暂无任务喵~"
        return {"outcome": "mission", "facts": [], "message": msg}

    async def _h_achievement(self, user_id: str, save: Dict[str, Any], c: str) -> Dict[str, Any]:
        got = save.get("achievements", [])
        msg = "🏆 成就: " + ("、".join(got) if got else "还没有成就喵~ 多战斗吧!")
        return {"outcome": "achievement", "facts": [], "message": msg}

    async def _h_home(self, user_id: str, save: Dict[str, Any], c: str) -> Dict[str, Any]:
        msg = "🏠 回家了喵~ 休息一下, 随时可以再出发!"
        return {"outcome": "home", "facts": [], "message": msg}

    async def _h_help(self, user_id: str, save: Dict[str, Any], c: str) -> Dict[str, Any]:
        msg = ("🐱 猫猫进化路玩法:\n"
               "  领养小猫 → 猫猫探索 → 战斗 → 吞食进化 → 猫娘!\n"
               "指令: 领养小猫 / 猫猫探索 / 猫猫状态 / 猫猫背包 / 猫猫技能 / 猫猫商店 / 猫猫任务 / 猫猫成就")
        return {"outcome": "help", "facts": [], "message": msg}

    # ── 工具 ─────────────────────────

    @staticmethod
    def _stage_for_lv(lv: int) -> str:
        stage = "小猫"
        for need, name in EVOLUTION_STAGES:
            if lv >= need:
                stage = name
        return stage

    def _pick_emotion(self, key: str, **params: str) -> str:
        templates = getattr(self, "_emotion_templates", None) or {}
        pool = templates.get(key) or [f"喵,{key}"]
        text = random.choice(pool)
        for k, v in params.items():
            text = text.replace("{" + k + "}", str(v))
        return text
