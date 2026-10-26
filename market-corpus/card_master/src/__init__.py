"""card_master — 让 YUI 自主学习打牌（德州扑克 / Kards / 掼蛋）。

提供：
- 德州扑克：完整牌局模拟、牌型评估、胜率计算（蒙特卡洛）、出牌建议、自我对弈
- Kards：真实规则查询（来自官方 wiki）、能力关键词、国家特色、卡组合法性检查
- 掼蛋：规则查询
- 通用：任意 5/7 张牌型评估

设计对齐 neko-plugin-dev 规范：
- 工具签名 async def xxx(self, **_)，返回 dict
- 牌局状态存内存（插件重启后丢失，属预期）
- 描述写全"何时用/前置/限制"，错误给"下一步做什么"
"""
from __future__ import annotations

import asyncio
import json
import threading

from plugin.sdk.plugin import (
    NekoPluginBase,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
    ui,
)

from . import kards as kards_mod
from . import kards_ai as kards_ai_mod
from . import live as live_mod
from .doudizhu import (
    DouDizhuGame,
)
from .doudizhu import (
    ai_bid as dd_ai_bid,
)
from .doudizhu import (
    card_str as dd_card_str,
)
from .doudizhu import (
    hand_str as dd_hand_str,
)
from .doudizhu import (
    identify_hand as dd_identify,
)
from .doudizhu import (
    suggest_play as dd_suggest_play,
)
from .engine import (
    TexasHoldemGame,
    calculate_odds,
    evaluate_hand,
    hand_str,
    parse_card,
    self_play,
    suggest_action,
)

# ══════════ 掼蛋规则（简要，标准规则） ══════════

GUANDAN_RULES = """## 掼蛋基本规则

**人数**：4 人，对家组队（2 对 2）。
**牌数**：两副扑克牌共 108 张（含 4 张王：2 小王 + 2 大王）。
**目标**：升级制，从打 2 开始，先打过 A 的一方获胜。

### 牌型（从大到小）
- **四王炸**：4 张王（最大牌型）
- **炸弹**：4 张及以上同点数牌（张数越多越大；6张以上炸弹叫"钢板"，可压同花顺）
- **同花顺**：5 张及以上同花色连续牌
- **顺子**：5 张及以上连续牌（不限花色）
- **三带二**：3 张同点 + 2 张同点（如 888+55）
- **三张**：3 张同点
- **对子**：2 张同点
- **单张**：1 张牌

### 核心机制
- **逢人配（百搭）**：当前所打级牌的红桃牌是"逢人配"，可当任意牌使用（不能配王）。例如打 2 时，红桃 2 是逢人配。
- **升级**：一对中两人分获上游（第1名）和二游（第2名）→ 连升 3 级；上游+三游 → 升 2 级；上游+下游 → 升 1 级。
- **双下**：一对两人分列三、四名（双下游）→ 对方升级，本方下局需进贡。
- **进贡/还贡**：下游向上游进贡最大的单张牌（王除外），上游还一张任意牌。
- **抗贡**：下游手中有 2 张大王可抗贡（不进贡）。

### 出牌规则
- 上游先出，逆时针轮流出牌
- 只能用同牌型压（炸弹/四王炸可压任何牌型）
- 一轮中所有人都不要则该轮最大者获得下一轮出牌权
"""

# ══════════ 斗地主规则 ══════════

DOUDIZHU_RULES = """## 斗地主基本规则

**人数**：3 人，一副牌 54 张（含大小王）。
**目标**：地主一方 vs 两个农民一方，先出完牌的一方获胜。

### 发牌与叫地主
- 每人发 17 张，留 3 张底牌
- 轮流叫分（1/2/3 分）或不叫，叫分最高者当地主并拿 3 张底牌（共 20 张）
- 叫 3 分直接成为地主
- 都不叫则重新发牌

### 牌型（从大到小）
- **王炸**：大王 + 小王（最大，可压任何牌型）
- **炸弹**：4 张同点数（可压除王炸外任何牌型）
- **四带二**：4 张同点 + 2 张单牌（或 2 对）
- **飞机带翅膀**：2 组以上连续三张 + 等量单牌/对子
- **飞机**：2 组以上连续三张（如 333+444）
- **连对**：3 对以上连续对子（如 33+44+55）
- **顺子**：5 张以上连续单牌（如 3-4-5-6-7）
- **三带二**：3 张同点 + 1 对
- **三带一**：3 张同点 + 1 张单牌
- **三张**：3 张同点
- **对子**：2 张同点
- **单张**：1 张牌

### 核心规则
- **点数大小**：3 < 4 < 5 < 6 < 7 < 8 < 9 < 10 < J < Q < K < A < 2 < 小王 < 大王
- **顺子/连对/飞机**：只能用 3-A，不能含 2 和王
- **同牌型才能压**：除炸弹和王炸外，必须同牌型且同长度才能压，比主要点数
- **不要**：压不过可以选择"不要"，连续两人不要则出牌权回到最后出牌者
- **胜利**：地主先出完 → 地主胜；任一农民先出完 → 农民方胜
"""

# ══════════ 插件主类 ══════════


@neko_plugin
class CardMasterPlugin(NekoPluginBase):
    """卡牌大师 — 让 YUI 自主学习和练习各类卡牌游戏。"""

    def __init__(self, ctx):
        super().__init__(ctx)
        self._game: TexasHoldemGame | None = None
        self._lock = threading.RLock()
        self._game_log: list[dict] = []
        self._dd_game: DouDizhuGame | None = None
        self._dd_stats: dict = {"wins": 0, "losses": 0, "role": "auto"}
        self._live_sessions: dict[str, live_mod.LiveSession] = {}

    # ══════════ 生命周期 ══════════

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        try:
            self.register_static_ui("static")  # 斗地主浏览器面板（static/index.html）
        except Exception:
            pass  # 静态 UI 注册失败不影响插件主体功能
        return {"status": "ready", "plugin": "card_master"}

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        with self._lock:
            self._game = None
            self._game_log.clear()
        return {"status": "shutdown"}

    # ══════════ 工具：规则查询 ══════════

    @llm_tool(
        name="card_rules",
        description="查询卡牌游戏规则。何时用：用户问'德州扑克怎么玩''Kards规则''掼蛋怎么打'或需要了解某游戏规则时。参数 game：poker（德州扑克）/ kards / guandan（掼蛋）/ all；topic：可选子主题（Kards 支持 basic/resource/battlefield/card_types/unit_types/learning，不填返回全部）。",
        parameters={"type": "object", "properties": {
            "game": {"type": "string", "description": "游戏：poker / kards / guandan / all"},
            "topic": {"type": "string", "description": "可选子主题（Kards 用）"},
        }, "required": ["game"]},
        timeout=30.0,
    )
    async def card_rules(self, *, game: str = "", topic: str = "", **_):
        params = _.get("parameters") or {}
        game = (game or params.get("game") or "").lower().strip()
        topic = (topic or params.get("topic") or "").strip()

        if game in ("poker", "德州扑克", "texas", "德扑"):
            return {"output": self._poker_rules(), "is_error": False}
        if game in ("kards",):
            return {"output": kards_mod.get_rules(topic), "is_error": False}
        if game in ("guandan", "掼蛋"):
            return {"output": GUANDAN_RULES, "is_error": False}
        if game in ("doudizhu", "斗地主", "ddz"):
            return {"output": DOUDIZHU_RULES, "is_error": False}
        if game in ("all", "全部", ""):
            return {"output": self._poker_rules() + "\n\n---\n\n" + kards_mod.get_rules("all") + "\n\n---\n\n" + GUANDAN_RULES + "\n\n---\n\n" + DOUDIZHU_RULES,
                    "is_error": False}
        return {"output": f"未知游戏 '{game}'。支持：poker（德州扑克）/ kards / guandan（掼蛋）/ doudizhu（斗地主）/ all",
                "is_error": True, "error": "UNKNOWN_GAME"}

    def _poker_rules(self) -> str:
        return """## 德州扑克（Texas Hold'em）基本规则

**人数**：2-10 人。
**目标**：赢取底池——要么让对手弃牌，要么摊牌时拥有最大牌型。

**牌型（从大到小）**：
皇家同花顺 > 同花顺 > 四条 > 葫芦（三带二）> 同花 > 顺子 > 三条 > 两对 > 一对 > 高牌

**游戏流程**：
1. **翻牌前（Preflop）**：每人发 2 张底牌；小盲/大盲下盲注；从大盲下家开始行动（弃牌/跟注/加注）
2. **翻牌（Flop）**：发 3 张公共牌，再次下注轮
3. **转牌（Turn）**：发第 4 张公共牌，下注轮
4. **河牌（River）**：发第 5 张公共牌，最后下注轮
5. **摊牌（Showdown）**：未弃牌玩家用 2 张底牌 + 5 张公共牌中选最佳 5 张比大小

**行动选项**：弃牌（fold）/ 过牌（check，无需跟注时）/ 跟注（call）/ 加注（raise）/ 全下（all-in）

**关键概念**：
- 底池赔率（Pot Odds）：跟注额 / (底池 + 跟注额)，胜率高于底池赔率则跟注有利
- 位置（Position）：越后行动越有信息优势
- 底牌范围（Range）：推测对手可能持有的牌组合
"""

    # ══════════ 工具：牌型评估 ══════════

    @llm_tool(
        name="card_evaluate",
        description="评估手牌牌型。何时用：用户问'我这手牌是什么牌型''这7张牌能组成什么'或需要分析手牌强度时。参数 hand：手牌字符串，空格分隔，如 'As Kh'；community：公共牌（可选），如 'Td Js Qh'；自动从 5 或 7 张中选最佳 5 张。牌格式：点数(2-9,T,J,Q,K,A)+花色(s=♠,h=♥,d=♦,c=♣)，如 As=黑桃A, Td=方块10。",
        parameters={"type": "object", "properties": {
            "hand": {"type": "string", "description": "手牌，空格分隔，如 'As Kh'"},
            "community": {"type": "string", "description": "公共牌（可选），空格分隔"},
        }, "required": ["hand"]},
        timeout=30.0,
    )
    async def card_evaluate(self, *, hand: str = "", community: str = "", **_):
        params = _.get("parameters") or {}
        hand = hand or params.get("hand", "")
        community = community or params.get("community", "")
        try:
            hand_cards = [parse_card(c) for c in hand.split()]
            comm_cards = [parse_card(c) for c in community.split()] if community else []
            all_cards = hand_cards + comm_cards
            if len(all_cards) < 5:
                return {"output": f"至少需要 5 张牌，当前 {len(all_cards)} 张。请补充手牌或公共牌。",
                        "is_error": True, "error": "NOT_ENOUGH_CARDS"}
            result = evaluate_hand(all_cards)
            return {
                "output": f"牌型：{result['name']}\n最佳5张：{result['cards_str']}\n输入：{hand_str(all_cards)}",
                "is_error": False,
                "detail": result,
            }
        except ValueError as e:
            return {"output": f"牌格式错误：{e}。正确格式如 'As Kh'（点数+花色，s=♠ h=♥ d=♦ c=♣）",
                    "is_error": True, "error": "INVALID_CARD"}

    # ══════════ 工具：创建牌局 ══════════

    @llm_tool(
        name="card_new_game",
        description="创建一局新的德州扑克牌局并发牌。何时用：用户说'开一局德州扑克''我们来打牌'或需要开始模拟牌局时。参数 players：玩家列表，默认6个AI（可指定名字和筹码）；small_blind：小盲注，默认10；big_blind：大盲注，默认20；seed：随机种子（可选，相同种子可复现牌局）。创建后用 card_action 出牌、card_state 看状态、card_suggest 获取建议。",
        parameters={"type": "object", "properties": {
            "players": {"type": "string", "description": "可选，JSON数组，如 '[{\"name\":\"玩家1\",\"chips\":1000},{\"name\":\"玩家2\",\"chips\":1000}]'。不填则默认6个AI"},
            "small_blind": {"type": "integer", "description": "小盲注，默认10"},
            "big_blind": {"type": "integer", "description": "大盲注，默认20"},
            "seed": {"type": "integer", "description": "随机种子（可选）"},
        }},
        timeout=30.0,
    )
    async def card_new_game(self, *, players: str = "", small_blind: int = 10,
                             big_blind: int = 20, seed: int = 0, **_):
        params = _.get("parameters") or {}
        players_str = players or params.get("players", "")
        sb = int(small_blind or params.get("small_blind", 10))
        bb = int(big_blind or params.get("big_blind", 20))
        sd = int(seed or params.get("seed", 0)) or None

        try:
            if players_str:
                player_list = json.loads(players_str)
            else:
                player_list = [{"name": f"AI_{i+1}", "chips": 1000} for i in range(6)]
        except (json.JSONDecodeError, TypeError) as e:
            return {"output": f"players 格式错误：{e}。应为 JSON 数组，如 '[{{\"name\":\"A\",\"chips\":1000}}]'",
                    "is_error": True, "error": "INVALID_PLAYERS"}

        try:
            with self._lock:
                self._game = TexasHoldemGame(player_list, sb, bb, sd)
                self._game_log = []
            state = self._game.get_state()
            return {
                "output": f"新牌局已创建！{len(player_list)} 人，小盲{sb}/大盲{bb}。\n当前阶段：{state['stage']}\n当前玩家：{state['current_player']}（手牌 {state['current_player_hand']}，筹码 {state['current_player_chips']}）\n底池：{state['pot']}，需跟注：{state['to_call']}\n用 card_action 出牌，或 card_suggest 获取建议。",
                "is_error": False,
                "game_state": state,
            }
        except Exception as e:
            return {"output": f"创建牌局失败：{e}", "is_error": True, "error": "CREATE_FAILED"}

    # ══════════ 工具：出牌 ══════════

    @llm_tool(
        name="card_action",
        description="在当前德州扑克牌局中执行出牌行动。何时用：需要打出一手牌时（弃牌/过牌/跟注/加注/全下）。参数 action：fold（弃牌）/ check（过牌）/ call（跟注）/ raise（加注，需指定amount为加注后总额）/ all_in（全下）；amount：加注时的总额（含已下注，如当前bet=20，加注到60则amount=60）。前置：需先用 card_new_game 创建牌局。",
        parameters={"type": "object", "properties": {
            "action": {"type": "string", "description": "fold / check / call / raise / all_in"},
            "amount": {"type": "integer", "description": "raise 时的加注后总额"},
        }, "required": ["action"]},
        timeout=30.0,
    )
    async def card_action(self, *, action: str = "", amount: int = 0, **_):
        params = _.get("parameters") or {}
        action = (action or params.get("action", "")).lower().strip()
        amt = int(amount or params.get("amount", 0))

        with self._lock:
            if self._game is None:
                return {"output": "当前没有牌局，请先用 card_new_game 创建。", "is_error": True, "error": "NO_GAME"}
            result = self._game.action(action, amount=amt)

        if not result.get("ok"):
            return {"output": f"行动失败：{result.get('error')}", "is_error": True, "error": "ACTION_FAILED"}

        msg = result.get("message", "")
        if result.get("result"):
            r = result["result"]
            if r.get("winners"):
                winners = ", ".join(f"{w['name']}({w['hand']})" for w in r["winners"])
                msg += f"\n摊牌结果：{winners} 赢得底池 {r['pot']}"
            elif r.get("winner"):
                msg += f"\n{r['winner']} 赢得底池 {r['pot']}"

        state = result.get("game_state") or (self._game.get_state() if self._game else None)
        if state and state.get("stage") != "showdown":
            msg += f"\n\n当前：{state['current_player']}（{state['current_player_hand']}），阶段 {state['stage']}，底池 {state['pot']}，需跟注 {state['to_call']}"

        return {"output": msg, "is_error": False, "game_state": state, "result": result.get("result")}

    # ══════════ 工具：牌局状态 ══════════

    @llm_tool(
        name="card_state",
        description="查询当前德州扑克牌局状态。何时用：需要了解当前牌局进展（阶段、底池、玩家手牌、筹码、该谁行动）时。前置：需先用 card_new_game 创建牌局。",
        parameters={"type": "object", "properties": {}},
        timeout=30.0,
    )
    async def card_state(self, **_):
        with self._lock:
            if self._game is None:
                return {"output": "当前没有牌局，请先用 card_new_game 创建。", "is_error": True, "error": "NO_GAME"}
            state = self._game.get_state()

        lines = [
            f"第 {state['hand_number']} 手牌 | 阶段：{state['stage']}",
            f"公共牌：{state['community']}（{state['community_count']}张）",
            f"底池：{state['pot']} | 当前下注：{state['current_bet']} | 需跟注：{state['to_call']}",
            f"当前玩家：{state['current_player']}（手牌 {state['current_player_hand']}，筹码 {state['current_player_chips']}）",
            "",
            "玩家状态：",
        ]
        for p in state["players"]:
            status = []
            if p["folded"]:
                status.append("已弃牌")
            if p["all_in"]:
                status.append("全下")
            if p["has_acted"]:
                status.append("已行动")
            hand = p["hand"] if p["name"] == state["current_player"] else "（背面）"
            lines.append(f"  {p['name']}: 筹码{p['chips']} 下注{p['bet']} 手牌{hand} {' '.join(status)}")

        return {"output": "\n".join(lines), "is_error": False, "game_state": state}

    # ══════════ 工具：胜率计算 ══════════

    @llm_tool(
        name="card_odds",
        description="计算当前手牌的胜率（蒙特卡洛模拟）。何时用：需要知道'我这手牌赢的概率有多少'或做出牌决策前评估胜率时。参数 opponents：对手数量，默认1；simulations：模拟次数，默认2000（越多越准但越慢）。前置：需先用 card_new_game 创建牌局，自动读取当前玩家手牌和公共牌。也可直接传 hand 和 community 参数手动指定。",
        parameters={"type": "object", "properties": {
            "opponents": {"type": "integer", "description": "对手数量，默认1"},
            "simulations": {"type": "integer", "description": "模拟次数，默认2000"},
            "hand": {"type": "string", "description": "可选，手动指定手牌如 'As Kh'（不填则用当前牌局）"},
            "community": {"type": "string", "description": "可选，手动指定公共牌"},
        }},
        timeout=120.0,
    )
    async def card_odds(self, *, opponents: int = 1, simulations: int = 2000,
                         hand: str = "", community: str = "", **_):
        params = _.get("parameters") or {}
        opp = int(opponents or params.get("opponents", 1))
        sim = int(simulations or params.get("simulations", 2000))
        hand_str_in = hand or params.get("hand", "")
        comm_str_in = community or params.get("community", "")

        try:
            if hand_str_in:
                hand_cards = [parse_card(c) for c in hand_str_in.split()]
                comm_cards = [parse_card(c) for c in comm_str_in.split()] if comm_str_in else []
            else:
                with self._lock:
                    if self._game is None:
                        return {"output": "当前没有牌局，请先用 card_new_game 创建，或手动传 hand 参数。",
                                "is_error": True, "error": "NO_GAME"}
                    p = self._game.players[self._game.current]
                    hand_cards = list(p["hand"])
                    comm_cards = list(self._game.community)

            if len(hand_cards) != 2:
                return {"output": f"需要恰好2张底牌，当前 {len(hand_cards)} 张。",
                        "is_error": True, "error": "INVALID_HAND"}

            result = calculate_odds(hand_cards, comm_cards, opponents=opp, simulations=sim)
            return {
                "output": (f"手牌 {result['your_hand']} | 公共牌 {result['community']}\n"
                           f"对手数：{result['opponents']} | 模拟：{result['simulations']} 次\n"
                           f"胜率：{result['win_rate']}% | 平局：{result['tie_rate']}% | 落败：{result['lose_rate']}%"),
                "is_error": False,
                "detail": result,
            }
        except ValueError as e:
            return {"output": f"牌格式错误：{e}", "is_error": True, "error": "INVALID_CARD"}

    # ══════════ 工具：出牌建议 ══════════

    @llm_tool(
        name="card_suggest",
        description="基于当前牌局给出出牌建议（综合胜率、底池赔率、位置分析）。何时用：不知道该怎么出牌、需要AI建议时。返回建议行动（fold/check/call/raise）+ 理由 + 胜率 + 底池赔率。前置：需先用 card_new_game 创建牌局。",
        parameters={"type": "object", "properties": {
            "simulations": {"type": "integer", "description": "胜率模拟次数，默认1500"},
        }},
        timeout=120.0,
    )
    async def card_suggest(self, *, simulations: int = 1500, **_):
        params = _.get("parameters") or {}
        sim = int(simulations or params.get("simulations", 1500))

        with self._lock:
            if self._game is None:
                return {"output": "当前没有牌局，请先用 card_new_game 创建。", "is_error": True, "error": "NO_GAME"}
            if self._game.stage == "showdown":
                return {"output": "牌局已结束，请用 card_new_game 开新一局。", "is_error": True, "error": "GAME_OVER"}
            sug = suggest_action(self._game, simulations=sim)

        action_map = {"fold": "弃牌", "check": "过牌", "call": "跟注", "raise": "加注"}
        action_cn = action_map.get(sug["suggestion"], sug["suggestion"])
        raise_info = f"，加注到 {sug['raise_to']}" if sug.get("raise_to") else ""
        exec_hint = f"action={sug['suggestion']}"
        if sug.get("raise_to"):
            exec_hint += f", amount={sug['raise_to']}"

        return {
            "output": (f"建议：{action_cn}{raise_info}\n"
                       f"理由：{sug['reason']}\n"
                       f"手牌 {sug['your_hand']} | 公共牌 {sug['community']}\n"
                       f"胜率 {sug['win_rate']}% | 平局 {sug['tie_rate']}% | 有效胜率 {sug['equity']}%\n"
                       f"底池 {sug['pot']} | 需跟注 {sug['to_call']} | {sug['pot_odds_detail']}\n"
                       f"（用 card_action 执行：{exec_hint}）"),
            "is_error": False,
            "suggestion": sug,
        }

    # ══════════ 工具：自我对弈 ══════════

    @llm_tool(
        name="card_selfplay",
        description="AI 自我对弈训练，让 YUI 通过观察 AI 对局学习打牌。何时用：用户说'自我对弈''自己打一局学习''训练AI打牌'时。参数 game：目前支持 poker（德州扑克）；rounds：对弈几手牌，默认1；players：玩家数（2-10），默认6；seed：随机种子（可选）；verbose：是否输出详细每步日志，默认true。返回每手牌结果、最终筹码排名、赢家。",
        parameters={"type": "object", "properties": {
            "game": {"type": "string", "description": "游戏类型，目前支持 poker"},
            "rounds": {"type": "integer", "description": "对弈手数，默认1"},
            "players": {"type": "integer", "description": "玩家数2-10，默认6"},
            "seed": {"type": "integer", "description": "随机种子（可选）"},
            "verbose": {"type": "boolean", "description": "是否输出详细日志，默认true"},
        }},
        timeout=300.0,
    )
    async def card_selfplay(self, *, game: str = "poker", rounds: int = 1,
                             players: int = 6, seed: int = 0, verbose: bool = True, **_):
        params = _.get("parameters") or {}
        g = (game or params.get("game", "poker")).lower()
        r = int(rounds or params.get("rounds", 1))
        p = int(players or params.get("players", 6))
        sd = int(seed or params.get("seed", 0)) or None
        vb = bool(verbose if verbose is not None else params.get("verbose", True))

        if g not in ("poker", "texas", "德州扑克"):
            return {"output": f"目前自我对弈仅支持 poker（德州扑克），'{g}' 暂不支持。",
                    "is_error": True, "error": "UNSUPPORTED_GAME"}
        if p < 2 or p > 10:
            return {"output": f"玩家数需 2-10，当前 {p}。", "is_error": True, "error": "INVALID_PLAYERS"}

        player_list = [{"name": f"AI_{i+1}", "chips": 1000} for i in range(p)]
        result = await asyncio.to_thread(self_play, player_list, rounds=r, seed=sd, verbose=vb)

        lines = [
            f"自我对弈完成：{result['rounds_played']} 手牌，{p} 名 AI",
            f"最终赢家：{result['winner']}",
            "",
            "最终筹码排名：",
        ]
        for i, c in enumerate(result["final_chips"], 1):
            lines.append(f"  {i}. {c['name']}: {c['chips']} 筹码")

        if result.get("results"):
            lines.append("")
            lines.append("各手牌结果：")
            for res in result["results"]:
                r = res.get("result", {})
                if r.get("winners"):
                    w = ", ".join(f"{x['name']}({x['hand']})" for x in r["winners"])
                    lines.append(f"  第{res['round']}手：{w} 赢 {r.get('pot', '?')}")
                elif r.get("winner"):
                    lines.append(f"  第{res['round']}手：{r['winner']} 赢 {r.get('pot', '?')}（弃牌结束）")

        return {"output": "\n".join(lines), "is_error": False, "detail": result}

    # ══════════ 工具：下一手牌 ══════════

    @llm_tool(
        name="card_next_hand",
        description="开始下一手德州扑克牌（移动庄家位、重新洗牌发牌）。何时用：一手牌结束后想继续打下一手时。前置：需先用 card_new_game 创建牌局且当前牌局已结束。",
        parameters={"type": "object", "properties": {}},
        timeout=30.0,
    )
    async def card_next_hand(self, **_):
        with self._lock:
            if self._game is None:
                return {"output": "当前没有牌局，请先用 card_new_game 创建。", "is_error": True, "error": "NO_GAME"}
            result = self._game.next_hand()
        state = result.get("game_state")
        return {
            "output": (f"第 {state['hand_number']} 手牌开始！\n"
                       f"当前玩家：{state['current_player']}（手牌 {state['current_player_hand']}，筹码 {state['current_player_chips']}）\n"
                       f"底池：{state['pot']}，需跟注：{state['to_call']}"),
            "is_error": False,
            "game_state": state,
        }

    # ══════════ Kards 专用工具 ══════════

    @llm_tool(
        name="kards_keyword",
        description="查询 Kards 卡牌游戏的能力关键词含义。何时用：遇到不认识的 Kards 关键词（闪击/部署/亡计/守护/伏击/冲击/重甲/烟幕/压制/钳击等）时。参数 name：关键词名称（不填则列出全部20+个关键词）。数据来自 Kards 官方 wiki。",
        parameters={"type": "object", "properties": {
            "name": {"type": "string", "description": "关键词名称，如 '闪击'。不填则列出全部"},
        }},
        timeout=30.0,
    )
    async def kards_keyword(self, *, name: str = "", **_):
        params = _.get("parameters") or {}
        n = name or params.get("name", "")
        return {"output": kards_mod.get_keyword(n), "is_error": False}

    @llm_tool(
        name="kards_nation",
        description="查询 Kards 国家阵营特色。何时用：想了解某个国家（德国/英国/苏联/美国/日本/法国/意大利/波兰/芬兰）的玩法风格和特色时。参数 name：国家名称（不填则列出全部国家）。数据来自 Kards 官方 wiki。",
        parameters={"type": "object", "properties": {
            "name": {"type": "string", "description": "国家名称，如 '德国'。不填则列出全部"},
        }},
        timeout=30.0,
    )
    async def kards_nation(self, *, name: str = "", **_):
        params = _.get("parameters") or {}
        n = name or params.get("name", "")
        return {"output": kards_mod.get_nation(n), "is_error": False}

    @llm_tool(
        name="kards_deck_check",
        description="检查 Kards 卡组是否合法（40张=1总部+39张，1主国+1盟国，国家有效性）。何时用：构筑或修改 Kards 卡组后想验证是否符合规则时。参数 deck：JSON数组，每个元素 {name, type, nation, count}，type 为 HQ/unit/order，nation 为国家名。返回是否合法、错误列表、警告、统计摘要。",
        parameters={"type": "object", "properties": {
            "deck": {"type": "string", "description": "JSON数组，如 '[{\"name\":\"德国总部\",\"type\":\"HQ\",\"nation\":\"德国\",\"count\":1},...]'"},
        }, "required": ["deck"]},
        timeout=30.0,
    )
    async def kards_deck_check(self, *, deck: str = "", **_):
        params = _.get("parameters") or {}
        deck_str = deck or params.get("deck", "")
        if not deck_str:
            return {"output": "请提供 deck 参数（JSON数组）。", "is_error": True, "error": "MISSING_DECK"}
        try:
            deck_list = json.loads(deck_str)
            if not isinstance(deck_list, list):
                raise ValueError("deck 必须是数组")
        except (json.JSONDecodeError, ValueError) as e:
            return {"output": f"deck 格式错误：{e}。应为 JSON 数组。", "is_error": True, "error": "INVALID_DECK"}

        result = kards_mod.check_deck(deck_list)
        s = result["summary"]
        lines = [
            f"卡组检查：{'✅ 合法' if result['valid'] else '❌ 不合法'}",
            f"总计 {s['total']} 张 | 总部 {s['headquarters']} | 单位 {s['units']} | 指令 {s['orders']}",
            f"主要国：{', '.join(s['main_nations']) or '（未检测到）'} | 盟国：{', '.join(s['ally_nations']) or '（无）'}",
        ]
        if result["errors"]:
            lines.append("")
            lines.append("错误：")
            for e in result["errors"]:
                lines.append(f"  ❌ {e}")
        if result["warnings"]:
            lines.append("")
            lines.append("警告：")
            for w in result["warnings"]:
                lines.append(f"  ⚠️ {w}")
        return {"output": "\n".join(lines), "is_error": False, "detail": result}

    # ══════════ 斗地主专用工具 ══════════

    @llm_tool(
        name="doudizhu_new",
        description="创建一局斗地主并发牌。何时用：用户说'开一局斗地主''来局斗地主'时。3人一副牌54张，每人17张+3张底牌。发牌后进入叫地主阶段，用 doudizhu_bid 叫分。参数 players：3个玩家名字（可选，默认['玩家','AI_左','AI_右']）；seed：随机种子（可选）。",
        parameters={"type": "object", "properties": {
            "players": {"type": "string", "description": "可选，JSON数组3个名字，如 '[\"我\",\"左\",\"右\"]'"},
            "seed": {"type": "integer", "description": "随机种子（可选）"},
        }},
        timeout=30.0,
    )
    async def doudizhu_new(self, *, players: str = "", seed: int = 0, **_):
        params = _.get("parameters") or {}
        players_str = players or params.get("players", "")
        sd = int(seed or params.get("seed", 0)) or None
        try:
            if players_str:
                name_list = json.loads(players_str)
                if len(name_list) != 3:
                    return {"output": "斗地主必须3人，请提供3个名字。", "is_error": True, "error": "INVALID_PLAYERS"}
            else:
                name_list = ["玩家", "AI_左", "AI_右"]
        except (json.JSONDecodeError, TypeError) as e:
            return {"output": f"players 格式错误：{e}", "is_error": True, "error": "INVALID_PLAYERS"}

        with self._lock:
            self._dd_game = DouDizhuGame(name_list, seed=sd)
        state = self._dd_game.get_state()
        return {
            "output": (f"新斗地主牌局已创建！玩家：{' / '.join(name_list)}\n"
                       f"每人17张，底牌3张（叫地主后揭晓）\n"
                       f"轮到 {state['current_player']} 叫地主，用 doudizhu_bid 叫分（0=不叫, 1/2/3=叫分）"),
            "is_error": False,
            "game_state": state,
        }

    @llm_tool(
        name="doudizhu_bid",
        description="叫地主。何时用：斗地主发牌后需要叫分时。参数 score：0=不叫, 1/2/3=叫分（叫3分直接当地主）。叫分最高者当地主并拿3张底牌。前置：需先用 doudizhu_new 创建牌局。",
        parameters={"type": "object", "properties": {
            "score": {"type": "integer", "description": "0=不叫, 1/2/3=叫分"},
        }, "required": ["score"]},
        timeout=30.0,
    )
    async def doudizhu_bid(self, *, score: int = 0, **_):
        params = _.get("parameters") or {}
        s = int(score if score != 0 else params.get("score", 0))
        with self._lock:
            if self._dd_game is None:
                return {"output": "当前没有斗地主牌局，请先用 doudizhu_new 创建。", "is_error": True, "error": "NO_GAME"}
            result = self._dd_game.bid(self._dd_game.current, s)
        if not result.get("ok"):
            return {"output": f"叫分失败：{result.get('error')}", "is_error": True, "error": "BID_FAILED"}
        state = self._dd_game.get_state()
        extra = ""
        if self._dd_game.stage == "playing":
            extra = f"\n\n地主 {state['landlord']} 获得底牌：{state['bottom_cards']}\n轮到地主先出牌，用 doudizhu_play 出牌（传牌的文字如 '3♠ 4♥'，空=不要）"
        return {"output": result["message"] + extra, "is_error": False, "game_state": state}

    @llm_tool(
        name="doudizhu_play",
        description="斗地主出牌。何时用：轮到出牌时。参数 cards：要出的牌的文字，空格分隔，如 '3♠ 4♥ 5♦ 6♣ 7♠'（顺子）；传空字符串表示'不要'。牌格式：点数+花色，点数 3-10,J,Q,K,A,2，花色 ♠♥♦♣，小王=小王，大王=大王。也可以只传点数让AI帮你选花色，如 '3 3 3 4'（三带一）。前置：需先用 doudizhu_new 创建并完成叫地主。",
        parameters={"type": "object", "properties": {
            "cards": {"type": "string", "description": "要出的牌，空格分隔，如 '3♠ 4♥'；空字符串=不要"},
        }, "required": ["cards"]},
        timeout=30.0,
    )
    async def doudizhu_play(self, *, cards: str = "", **_):
        params = _.get("parameters") or {}
        cards_str = cards if cards != "" else params.get("cards", "")
        with self._lock:
            if self._dd_game is None:
                return {"output": "当前没有斗地主牌局，请先用 doudizhu_new 创建。", "is_error": True, "error": "NO_GAME"}
            if self._dd_game.stage == "bidding":
                return {"output": "当前是叫地主阶段，请先用 doudizhu_bid 叫分。", "is_error": True, "error": "BIDDING_STAGE"}
            if self._dd_game.stage == "over":
                return {"output": "牌局已结束，请用 doudizhu_new 开新一局。", "is_error": True, "error": "GAME_OVER"}

            if not cards_str.strip():
                card_list = []
            else:
                parse_result = self._parse_doudizhu_cards(cards_str, self._dd_game.players[self._dd_game.current]["hand"])
                if parse_result.get("error"):
                    return {"output": parse_result["error"], "is_error": True, "error": "INVALID_CARDS"}
                card_list = parse_result["cards"]

            result = self._dd_game.play(self._dd_game.current, card_list)

        if not result.get("ok"):
            return {"output": f"出牌失败：{result.get('error')}", "is_error": True, "error": "PLAY_FAILED"}

        msg = result["message"]
        if result.get("result"):
            r = result["result"]
            msg += f"\n\n游戏结束！{r['winner']}（{r['role']}）获胜！地主是 {r['landlord']}。"
        else:
            state = self._dd_game.get_state()
            msg += f"\n\n轮到 {state['current_player']}（{state['current_role']}），手牌 {state['current_hand_count']} 张"
            if state["last_play"] != "（无）":
                msg += f"，上一手：{state['last_play']}（{state['last_player']}）"

        return {"output": msg, "is_error": False, "result": result.get("result"),
                "game_state": self._dd_game.get_state() if self._dd_game else None}

    def _parse_doudizhu_cards(self, text: str, hand: list[int]) -> dict:
        """解析用户输入的牌文字为整数列表。支持 '3♠' 格式和纯点数（自动从手中选）。"""
        tokens = text.split()
        cards = []
        hand_remaining = list(hand)
        for token in tokens:
            matched = None
            for c in hand_remaining:
                if dd_card_str(c) == token:
                    matched = c
                    break
            if matched is None:
                rank_token = token.rstrip("♠♥♦♣")
                rank_name_map = {3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9",
                                  10: "10", 11: "J", 12: "Q", 13: "K", 14: "A", 15: "2",
                                  16: "小王", 17: "大王"}
                for c in hand_remaining:
                    r = c // 4
                    if rank_name_map.get(r, "") == rank_token:
                        matched = c
                        break
            if matched is not None:
                cards.append(matched)
                hand_remaining.remove(matched)
            else:
                return {"error": f"无法识别牌 '{token}'，或手中没有这张牌。请用格式如 '3♠'、'10♥'、'J♦'、'小王'。"}
        return {"cards": cards}

    @llm_tool(
        name="doudizhu_state",
        description="查询当前斗地主牌局状态。何时用：需要了解当前轮到谁、各人手牌数、上一手出了什么时。前置：需先用 doudizhu_new 创建牌局。",
        parameters={"type": "object", "properties": {}},
        timeout=30.0,
    )
    async def doudizhu_state(self, **_):
        with self._lock:
            if self._dd_game is None:
                return {"output": "当前没有斗地主牌局，请先用 doudizhu_new 创建。", "is_error": True, "error": "NO_GAME"}
            state = self._dd_game.get_state()
        lines = [f"阶段：{state['stage']}"]
        if state["landlord"]:
            lines.append(f"地主：{state['landlord']} | 底牌：{state['bottom_cards']}")
        lines.append(f"当前玩家：{state['current_player']}（{state['current_role']}），手牌 {state['current_hand_count']} 张")
        if state["current_hand"]:
            lines.append(f"当前手牌：{state['current_hand']}")
        if state["last_play"] != "（无）":
            lines.append(f"上一手：{state['last_play']}（{state['last_player']}）")
        lines.append("")
        lines.append("玩家状态：")
        for p in state["players"]:
            lines.append(f"  {p['name']}（{p['role']}）：{p['card_count']} 张")
        return {"output": "\n".join(lines), "is_error": False, "game_state": state}

    @llm_tool(
        name="doudizhu_identify",
        description="识别一副牌的斗地主牌型。何时用：用户问'这是什么牌型''这手牌能不能出'时。参数 cards：牌的文字，空格分隔，如 '3♠ 3♥ 3♦ 4♣'。返回牌型名称（单张/对子/三张/三带一/三带二/顺子/连对/飞机/四带二/炸弹/王炸等）或'非法牌型'。",
        parameters={"type": "object", "properties": {
            "cards": {"type": "string", "description": "牌的文字，空格分隔，如 '3♠ 3♥ 3♦ 4♣'"},
        }, "required": ["cards"]},
        timeout=30.0,
    )
    async def doudizhu_identify(self, *, cards: str = "", **_):
        params = _.get("parameters") or {}
        cards_str = cards or params.get("cards", "")
        if not cards_str.strip():
            return {"output": "请提供要识别的牌。", "is_error": True, "error": "MISSING_CARDS"}
        tokens = cards_str.split()
        card_list = []
        rank_map = {"3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "10": 10,
                    "J": 11, "Q": 12, "K": 13, "A": 14, "2": 15}
        suit_map = {"♠": 0, "♥": 1, "♦": 2, "♣": 3}
        for token in tokens:
            if token == "小王":
                card_list.append(16 * 4)
            elif token == "大王":
                card_list.append(17 * 4)
            else:
                rank_part = token.rstrip("♠♥♦♣")
                suit_part = token[len(rank_part):] if len(rank_part) < len(token) else "♠"
                r = rank_map.get(rank_part)
                s = suit_map.get(suit_part, 0)
                if r is None:
                    return {"output": f"无法识别牌 '{token}'。格式如 '3♠'、'10♥'、'J♦'、'小王'。",
                            "is_error": True, "error": "INVALID_CARD"}
                card_list.append(r * 4 + s)
        result = dd_identify(card_list)
        if result is None:
            return {"output": f"{dd_hand_str(card_list)} → 非法牌型（不符合斗地主任何牌型规则）",
                    "is_error": False, "valid": False}
        return {"output": f"{dd_hand_str(card_list)} → {result['name']}（主要点数：{result['main_rank']}，长度：{result['length']}）",
                "is_error": False, "valid": True, "detail": result}

    # ══════════ live 模式：实时打牌决策（YUI 控制电脑鼠标自己打牌） ══════════

    @llm_tool(
        name="live_session",
        description="开启一次实时打牌会话。何时用：YUI 获得电脑控制权、在真实游戏（德州扑克/斗地主/Kards）窗口中准备自己打牌时，第一步必须先调用本工具创建会话。参数 game：poker（德州扑克）/ doudizhu（斗地主）/ kards；role：玩家身份（如 '1号位'/'地主'/'农民'/'德国'）；params：JSON 对象，游戏参数（poker: {small_blind,big_blind,my_chips}；doudizhu: {my_role}；kards: {main_nation,ally_nation}）。返回 session_id，后续 live_report / live_action_done 都需带上。",
        parameters={"type": "object", "properties": {
            "game": {"type": "string", "description": "poker / doudizhu / kards"},
            "role": {"type": "string", "description": "玩家身份描述"},
            "params": {"type": "string", "description": "可选 JSON 对象，游戏参数"},
        }, "required": ["game"]},
        timeout=30.0,
    )
    async def live_session(self, *, game: str = "", role: str = "", params: str = "", **_):
        prm = _.get("parameters") or {}
        g = (game or prm.get("game", "")).lower().strip()
        r = role or prm.get("role", "")
        p_str = params or prm.get("params", "")
        p_obj = {}
        if p_str:
            try:
                p_obj = json.loads(p_str)
                if not isinstance(p_obj, dict):
                    raise ValueError("params 必须是 JSON 对象")
            except (json.JSONDecodeError, ValueError) as e:
                return {"output": f"params 格式错误：{e}。应为 JSON 对象，如 '{{\"small_blind\":10}}'",
                        "is_error": True, "error": "INVALID_PARAMS"}

        if g not in ("poker", "德州扑克", "texas"):
            if g in ("德州扑克", "texas"):
                g = "poker"
        if g not in ("poker", "doudizhu", "斗地主", "kards"):
            return {"output": f"未知游戏 '{g}'。支持：poker（德州扑克）/ doudizhu（斗地主）/ kards。",
                    "is_error": True, "error": "UNKNOWN_GAME"}
        if g in ("德州扑克",):
            g = "poker"
        if g == "斗地主":
            g = "doudizhu"

        with self._lock:
            session = live_mod.new_session(g, r, p_obj)
            self._live_sessions[session.session_id] = session

        usage = {
            "poker": "流程：live_report 上报局面（hole_cards/community/pot/to_call/my_chips/active_players/big_blind/current_bet）→ 插件给动作建议+点击步骤 → live_action_done 确认 → 重复。",
            "doudizhu": "流程：live_report 上报局面（my_hand/last_play/my_role）→ 插件给候选出牌+推荐 → live_action_done 确认 → 重复。",
            "kards": "流程：live_report 上报局面（kredits/my_hq/opp_hq/my_hand/front_line/support_line/opp_front/opp_support/is_my_turn）→ 插件给按优先级的行动建议+点击步骤 → live_action_done 确认 → 重复。",
        }[g]
        return {
            "output": f"实时打牌会话已开启！\n游戏：{g} | 身份：{r or '未指定'}\nsession_id：{session.session_id}\n\n{usage}\n\n注意：先截图确认你在游戏窗口中的当前局面，把读到的状态填入 live_report 的 state JSON。",
            "is_error": False,
            "session_id": session.session_id,
        }

    @llm_tool(
        name="live_report",
        description="上报实时牌局局面并获得打牌建议。何时用：YUI 获得电脑控制权后，从真实游戏屏幕读取当前局面（自己的手牌、公共牌、底池、对方行动等），把状态结构化后调用本工具，插件返回该怎么做+鼠标操作步骤。参数 session_id：live_session 返回的会话 ID；state：JSON 对象，游戏相关字段见 live_session 的说明。每次对手/自己行动后都要重新截图上报。",
        parameters={"type": "object", "properties": {
            "session_id": {"type": "string", "description": "live_session 返回的会话 ID"},
            "state": {"type": "string", "description": "局面 JSON 对象，如 poker: '{\"hole_cards\":\"As Kh\",\"community\":\"Td Js Qh\",\"pot\":120,\"to_call\":40,\"my_chips\":980,\"active_players\":4}'"},
        }, "required": ["session_id", "state"]},
        timeout=120.0,
    )
    async def live_report(self, *, session_id: str = "", state: str = "", **_):
        prm = _.get("parameters") or {}
        sid = session_id or prm.get("session_id", "")
        state_str = state or prm.get("state", "")
        if not sid:
            return {"output": "缺少 session_id。请先用 live_session 创建会话。",
                    "is_error": True, "error": "MISSING_SESSION"}
        if not state_str:
            return {"output": "缺少 state（局面 JSON）。请从屏幕上读取局面后填入。",
                    "is_error": True, "error": "MISSING_STATE"}
        try:
            state_obj = json.loads(state_str)
            if not isinstance(state_obj, dict):
                raise ValueError("state 必须是 JSON 对象")
        except (json.JSONDecodeError, ValueError) as e:
            return {"output": f"state 格式错误：{e}。应为 JSON 对象。",
                    "is_error": True, "error": "INVALID_STATE"}

        with self._lock:
            session = self._live_sessions.get(sid)
            if session is None:
                return {"output": f"会话 {sid} 不存在或已过期。请用 live_session 重新创建。",
                        "is_error": True, "error": "SESSION_NOT_FOUND"}
            result = live_mod.handle_report(session, state_obj)

        if not result.get("ok"):
            return {"output": f"无法给出建议：{result.get('error')}\n下一步：{result.get('action')}",
                    "is_error": True, "error": "ADVICE_FAILED"}

        advice = result.get("advice")
        output_lines: list[str] = []
        if advice:
            action_map = {"fold": "弃牌", "check": "过牌", "call": "跟注", "raise": "加注"}
            cn = action_map.get(advice["suggestion"], advice["suggestion"])
            output_lines.append(
                f"建议：{cn}"
                + (f"（加注到 {advice['raise_to']}）" if advice.get("raise_to") else "")
                + f"\n理由：{advice['reason']}"
                + f"\n胜率 {advice['win_rate']}% | 底池赔率 {advice['pot_odds']}%"
            )
            steps = advice.get("steps") or []
        elif result.get("suggestion"):
            output_lines.append(result.get("output") or "")
            steps = (result["suggestion"].get("steps") or [])
        else:
            output_lines.append(result.get("output") or "")
            steps = []
            for a in result.get("actions") or []:
                steps.extend(a.get("steps") or [])
            if result.get("checklist"):
                output_lines.append("⚠️ 检查清单：")
                for c in result["checklist"]:
                    output_lines.append(f"  • {c}")

        if steps:
            output_lines.append("操作步骤：")
            for i, s in enumerate(steps, 1):
                output_lines.append(f"  {i}. {s['what']}（验证：{s['verify']}）")

        return {"output": "\n".join(output_lines), "is_error": False,
                "session_id": sid, "advice": advice or result.get("suggestion")}

    @llm_tool(
        name="live_action_done",
        description="确认已执行建议动作。何时用：YUI 按 live_report 的建议在真实游戏中完成鼠标/键盘操作后，调用本工具确认，插件记录动作并给出下一步指引。参数 session_id：会话 ID；action：执行的动作描述；result：可选，执行结果（如 '已弃牌'/'出牌成功'/'按钮未找到'）。若执行失败（按钮找不到、点错），如实填写 result，插件会给出补救指引。",
        parameters={"type": "object", "properties": {
            "session_id": {"type": "string", "description": "live_session 返回的会话 ID"},
            "action": {"type": "string", "description": "执行的动作描述"},
            "result": {"type": "string", "description": "可选，执行结果"},
        }, "required": ["session_id", "action"]},
        timeout=30.0,
    )
    async def live_action_done(self, *, session_id: str = "", action: str = "", result: str = "", **_):
        prm = _.get("parameters") or {}
        sid = session_id or prm.get("session_id", "")
        act = action or prm.get("action", "")
        res = result or prm.get("result", "")
        if not sid:
            return {"output": "缺少 session_id。", "is_error": True, "error": "MISSING_SESSION"}
        if not act:
            return {"output": "缺少 action（执行的动作描述）。", "is_error": True, "error": "MISSING_ACTION"}

        with self._lock:
            session = self._live_sessions.get(sid)
            if session is None:
                return {"output": f"会话 {sid} 不存在或已过期。请用 live_session 重新创建。",
                        "is_error": True, "error": "SESSION_NOT_FOUND"}
            session.record("action_done", {"action": act, "result": res})

        failed = any(k in res for k in ("未找到", "失败", "找不到", "点错", "没反应", "error", "Error"))
        if failed:
            return {
                "output": "动作执行失败。补救指引：\n1. 先截图确认当前游戏界面（按钮布局可能变化/弹窗遮挡）\n2. 重新调用 live_report 上报最新局面\n3. 插件会基于新局面重新给建议。\n\n同时检查：游戏窗口是否在前台？是否处于你的回合？",
                "is_error": False,
            }
        return {
            "output": f"已记录动作：{act}\n下一步：等待对手行动或轮到你后，重新截图并调用 live_report 上报新局面，获取下一步建议。",
            "is_error": False,
        }

    @llm_tool(
        name="live_status",
        description="查看实时打牌会话状态与动作历史。何时用：需要确认当前会话的回合数、已执行动作、最近一次局面时。参数 session_id：会话 ID。",
        parameters={"type": "object", "properties": {
            "session_id": {"type": "string", "description": "live_session 返回的会话 ID"},
        }, "required": ["session_id"]},
        timeout=30.0,
    )
    async def live_status(self, *, session_id: str = "", **_):
        prm = _.get("parameters") or {}
        sid = session_id or prm.get("session_id", "")
        if not sid:
            return {"output": "缺少 session_id。", "is_error": True, "error": "MISSING_SESSION"}
        with self._lock:
            session = self._live_sessions.get(sid)
            if session is None:
                return {"output": f"会话 {sid} 不存在或已过期。", "is_error": True, "error": "SESSION_NOT_FOUND"}
            history = list(session.history)
            state_keys = list(session.state.keys())
        lines = [
            f"会话 {sid} | 游戏：{session.game_type} | 身份：{session.role or '未指定'}",
            f"回合数：{session.turn_counter} | 最近局面字段：{', '.join(state_keys) or '（未上报）'}",
            "",
            "动作历史：",
        ]
        if not history:
            lines.append("  （暂无记录）")
        for h in history[-15:]:
            lines.append(f"  [{h['t']}] {h['event']}：{h['detail']}")
        return {"output": "\n".join(lines), "is_error": False}

    @llm_tool(
        name="doudizhu_live_advice",
        description="斗地主实时出牌建议（独立工具，不依赖会话）。何时用：YUI 在斗地主真实窗口中轮到出牌，把读到的局面传入即可获得候选出牌和推荐。参数 my_hand：你的手牌文字，空格分隔，如 '3♠ 5♥ 5♦ 9♣ 9♠ J♥ J♣ 2♣ 大王'；last_play：上一手出的牌（空字符串=你是先手）；my_role：landlord（地主）/ farmer（农民）/ 不填自动按农民。返回所有可出候选（按推荐度排序）+ 推荐 + 是否建议'不要'。",
        parameters={"type": "object", "properties": {
            "my_hand": {"type": "string", "description": "手牌文字，如 '3♠ 5♥ 5♦ 9♣ 9♠ J♥'"},
            "last_play": {"type": "string", "description": "上一手出的牌，空=先手"},
            "my_role": {"type": "string", "description": "landlord/farmer"},
        }, "required": ["my_hand"]},
        timeout=30.0,
    )
    async def doudizhu_live_advice(self, *, my_hand: str = "", last_play: str = "",
                                   my_role: str = "", **_):
        prm = _.get("parameters") or {}
        hand_txt = my_hand or prm.get("my_hand", "")
        last_txt = last_play or prm.get("last_play", "")
        role = (my_role or prm.get("my_role", "farmer")).lower()
        role_norm = "landlord" if role in ("landlord", "地主") else "farmer"
        if not hand_txt.strip():
            return {"output": "请提供 my_hand（你的手牌）。", "is_error": True, "error": "MISSING_HAND"}

        from .live import _parse_dd_token
        hand_cards = []
        for token in hand_txt.split():
            m = _parse_dd_token(token)
            if m is None:
                return {"output": f"无法识别手牌中的 '{token}'。格式：3♠ 10♥ J♦ A♣ 2 小王 大王",
                        "is_error": True, "error": "INVALID_CARD"}
            hand_cards.append(m)
        last_cards = []
        if last_txt.strip() and last_txt not in ("（无）", "无", "-"):
            for token in last_txt.split():
                m = _parse_dd_token(token)
                if m is None:
                    return {"output": f"无法识别上一手牌中的 '{token}'。",
                            "is_error": True, "error": "INVALID_CARD"}
                last_cards.append(m)

        sug = dd_suggest_play(hand_cards, last_cards if last_cards else None, role=role_norm)
        lines = [f"建议：{sug['reason']}"]
        if sug.get("suggestion"):
            lines.append(f"要出的牌：{sug['suggestion']['cards']}（{sug['suggestion']['type_name']}）")
        if sug.get("should_pass"):
            lines.append("这一手建议'不要'。")
        if sug.get("candidates"):
            lines.append("")
            lines.append("可出候选（按推荐度排序）：")
            for i, c in enumerate(sug["candidates"][:6], 1):
                lines.append(f"  {i}. {c['cards']}（{c['type_name']}）")
        return {"output": "\n".join(lines), "is_error": False, "detail": sug}

    @llm_tool(
        name="kards_live_advice",
        description="Kards 实时回合建议（独立工具，不依赖会话）。何时用：YUI 在 Kards 真实窗口中轮到自己的回合，把从屏幕读到的局面传入，插件基于官方 wiki 真实规则给出按优先级的行动建议（攻击/部署/命令卡）+ 操作步骤 + 检查清单。参数 kredits：当前指挥点；my_hq/opp_hq：双方总部防御；my_hand：手牌 JSON 数组 [{name,cost,type,attack,defense,keywords}]；front_line/support_line：己方前线/支援线单位数组；opp_front/opp_support：敌方单位数组；is_my_turn：是否你的回合。注意：本建议是规则级启发式，YUI 需结合卡面实际数值判断。",
        parameters={"type": "object", "properties": {
            "kredits": {"type": "integer", "description": "当前指挥点"},
            "my_hq": {"type": "integer", "description": "己方总部防御，默认20"},
            "opp_hq": {"type": "integer", "description": "敌方总部防御，默认20"},
            "my_hand": {"type": "string", "description": "手牌 JSON 数组"},
            "front_line": {"type": "string", "description": "己方前线单位 JSON 数组"},
            "support_line": {"type": "string", "description": "己方支援线单位 JSON 数组"},
            "opp_front": {"type": "string", "description": "敌方前线单位 JSON 数组"},
            "opp_support": {"type": "string", "description": "敌方支援线单位 JSON 数组"},
            "is_my_turn": {"type": "boolean", "description": "是否我的回合，默认true"},
        }, "required": ["kredits"]},
        timeout=30.0,
    )
    async def kards_live_advice(self, *, kredits: int = 0, my_hq: int = 20, opp_hq: int = 20,
                                my_hand: str = "", front_line: str = "", support_line: str = "",
                                opp_front: str = "", opp_support: str = "",
                                is_my_turn: bool = True, **_):
        prm = _.get("parameters") or {}
        try:
            k = int(kredits if kredits != 0 else prm.get("kredits", 0))
        except (TypeError, ValueError):
            k = int(prm.get("kredits", 0) or 0)
        mh = int(my_hq or prm.get("my_hq", 20))
        oh = int(opp_hq or prm.get("opp_hq", 20))
        turn = bool(is_my_turn if is_my_turn is not None else prm.get("is_my_turn", True))

        def _parse_list(s: str, field: str):
            if not s:
                return []
            try:
                v = json.loads(s)
                if not isinstance(v, list):
                    raise ValueError(f"{field} 必须是 JSON 数组")
                return v
            except (json.JSONDecodeError, ValueError) as e:
                return {"__error__": str(e)}

        state = {
            "kredits": k, "my_hq": mh, "opp_hq": oh, "is_my_turn": turn,
            "my_hand": _parse_list(my_hand or prm.get("my_hand", ""), "my_hand"),
            "front_line": _parse_list(front_line or prm.get("front_line", ""), "front_line"),
            "support_line": _parse_list(support_line or prm.get("support_line", ""), "support_line"),
            "opp_front": _parse_list(opp_front or prm.get("opp_front", ""), "opp_front"),
            "opp_support": _parse_list(opp_support or prm.get("opp_support", ""), "opp_support"),
        }
        for field in ("my_hand", "front_line", "support_line", "opp_front", "opp_support"):
            if isinstance(state[field], dict) and "__error__" in state[field]:
                return {"output": f"{field} 格式错误：{state[field]['__error__']}。应为 JSON 数组。",
                        "is_error": True, "error": "INVALID_JSON"}

        result = live_mod.advise_kards(state)
        if not result.get("ok"):
            return {"output": f"无法给出建议：{result.get('error')}\n下一步：{result.get('action')}",
                    "is_error": True, "error": "ADVICE_FAILED"}
        return {"output": result.get("output", ""), "is_error": False, "detail": result}

    @llm_tool(
        name="kards_auto_play",
        description="Kards 自动对局执行（读局面→决策→真实操作）。何时用：Kards 游戏窗口已打开且在对局中，YUI 要自己打牌时调用本工具，插件会自动：定位并前置窗口→识别局面（指挥点/总部/手牌/战线）→基于官方规则给出行动建议→执行（优先 KARDS_API 内存通道：读真数据+纯代码 play_card/attack/end_turn，不碰鼠标；未装则回退读屏+键鼠：拖拽出牌/点选攻击/点结束回合）→等待对手回合→继续。参数 turns：本轮执行几个己方回合（默认1，最大5）。每回合执行前都会真实截图/读内存验证，识别不到就停止如实汇报（绝不盲点）。返回每回合执行日志（操作了什么）、跳过的动作及原因、识别说明、所用通道。注意：需玩家已进入一局对局（主菜单/组牌界面会报 GAME_NOT_RUNNING 之外的识别提示）。",
        parameters={"type": "object", "properties": {
            "turns": {"type": "integer", "description": "执行几个己方回合，默认1，最大5"},
        }, "required": []},
        timeout=180.0,
    )
    async def kards_auto_play(self, *, turns: int = 1, **_):
        try:
            turns = max(1, min(int(turns or 1), 5))
        except (TypeError, ValueError):
            turns = 1
        try:
            win = kards_ai_mod.find_kards_window()
            result = kards_ai_mod.run_game(win, max_turns=turns, max_actions_per_turn=4,
                                           poll_seconds=3.0)
        except kards_ai_mod.KardsError as e:
            return {"output": f"无法开始自动对局：{e}\n下一步：启动 Kards 并进入一局对局后重试。",
                    "is_error": True, "error": e.code}
        lines = []
        for t in result.get("turns", []):
            lines.append(f"**第 {t['turn']} 回合**（指挥点 {t['state'].get('kredits')}，"
                         f"我方 {t['state'].get('my_hq')} / 敌方 {t['state'].get('opp_hq')}）")
            if t["executed"]:
                lines.append(f"  已执行：{'；'.join(t['executed'])}")
            if t["skipped"]:
                lines.append(f"  跳过（不盲点）：{'；'.join(t['skipped'])}")
            lines.append(f"  说明：{t['summary']}")
        if result.get("notes"):
            lines.append(f"识别提示：{'；'.join(result['notes'])}")
        lines.append(f"停止原因：{result.get('stopped') or '正常完成'}"
                     f"（通道：{'KARDS_API 内存读取' if result.get('mode') == 'api' else '读屏+键鼠'}）")
        return {"output": "\n".join(lines), "is_error": False,
                "detail": {"stopped": result.get("stopped"), "turns": len(result.get("turns", [])),
                           "mode": result.get("mode")}}

    @llm_tool(
        name="kards_auto_status",
        description="Kards 当前局面识别（只读，不点击）。何时用：Kards 对局中想确认 AI 当前看到什么（指挥点/双方总部/手牌数/战线占用/是否自己回合），或自动对局卡住时诊断。返回画面识别结果与识别说明；未标定数字模板时相关数值为未识别（需要先跑 kards_calibrate）。",
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=60.0,
    )
    async def kards_auto_status(self, *, dummy: int = 0, **_):
        try:
            win = kards_ai_mod.find_kards_window()
            kards_ai_mod._front_window(win)
            frame = kards_ai_mod.capture_window(win)
            win_w = win["right"] - win["left"]
            win_h = win["bottom"] - win["top"]
            st = kards_ai_mod.vision(frame, win_w, win_h)
        except kards_ai_mod.KardsError as e:
            return {"output": f"无法识别局面：{e}", "is_error": True, "error": e.code}
        lines = [f"指挥点 {st['kredits']} | 我方总部 {st['my_hq']} | 敌方总部 {st['opp_hq']}",
                 f"手牌 {len(st['my_hand'])} 张 | 前线占用 {len(st['front_line'])} | "
                 f"己方支援线占用 {len(st['support_line'])} | 敌方支援线占用 {len(st['opp_support'])}",
                 f"是否我方回合：{'是' if st['is_my_turn'] else '否'}"]
        if st["vision_note"]:
            lines.append("识别提示：" + "；".join(st["vision_note"]))
        lines.append("说明：数值显示「未识别」说明数字模板未标定，先运行 kards_calibrate 完成标定后自动对局才能读准。")
        return {"output": "\n".join(lines), "is_error": False, "detail": st}

    @llm_tool(
        name="kards_calibrate",
        description="Kards 画面标定（截图存档，不点击）。何时用：首次使用自动对局前、或分辨率/窗口缩放变化后。本工具会定位并前置 Kards 窗口，把当前画面及关键区域（指挥点/双方总部/结束回合按钮/手牌行）裁剪保存到插件 templates/kards/ 目录，供人工/后续标定数字模板与校准布局坐标。返回保存的文件清单与当前窗口尺寸。注意：请在游戏处于「对局中且轮到你」的画面调用（不要在加载/结算界面）。",
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=60.0,
    )
    async def kards_calibrate(self, *, dummy: int = 0, **_):
        try:
            win = kards_ai_mod.find_kards_window()
            out = kards_ai_mod.calibrate_capture(win)
        except kards_ai_mod.KardsError as e:
            return {"output": f"无法标定：{e}", "is_error": True, "error": e.code}
        lines = [f"已保存 {len(out['saved'])} 个文件到插件 templates/kards/：{', '.join(out['saved'])}",
                 f"窗口尺寸 {out['win']['w']}×{out['win']['h']}",
                 "下一步：从裁剪图中把单个数字裁成 digit_0.png..digit_9.png 放入同目录即完成数字标定；"
                 "布局坐标如需调整见 kards_layout.json（kards_calibrate 会生成）。"]
        return {"output": "\n".join(lines), "is_error": False, "detail": out}

    # ══════════ 内置斗地主小游戏（对话内文字交互，无需鼠标权限） ══════════

    def _dd_run_to_user(self, game) -> tuple[list[str], dict | None]:
        """推进 AI 回合直到轮到用户或游戏结束。返回 (行动摘要, 最终胜负result?)。

        内置步数上限防异常死循环（正常一局最多几十步）。
        """
        moves: list[str] = []
        final: dict | None = None
        guard = 0
        while game.stage == "playing" and game.current != 0 and guard < 200:
            guard += 1
            idx = game.current
            res = game.ai_play(idx)
            moves.append(res.get("message", ""))
            if res.get("result"):
                final = res["result"]
                break
        if guard >= 200:
            moves.append("⚠️ AI 回合异常（超出步数上限），请用 dd_game_start 重开一局。")
        return moves, final

    def _dd_update_stats(self, result: dict):
        """按小游戏身份更新战绩。"""
        my_role = self._dd_stats.get("role", "auto")
        won = (result["role"] == "地主" and my_role == "landlord") or (
            result["role"] == "农民" and my_role == "farmer")
        if won:
            self._dd_stats["wins"] += 1
        else:
            self._dd_stats["losses"] += 1

    def _dd_build_view(self, game) -> list[str]:
        """拼小游戏局面展示（你的手牌、AI 手牌数、上一手、轮到谁）。

        注意：两个 YUI_AI 的出牌由插件自动完成，你（YUI 助手）只负责出
        "你"这一个玩家的牌，绝不要替 AI 出牌或替 AI 调用工具。
        """
        st = game.get_state()
        if game.stage == "bidding":
            lines = [
                f"叫地主阶段：你的手牌（{st['players'][0]['card_count']}张）：{st['players'][0]['hand']}",
                f"YUI_左 {st['players'][1]['card_count']}张 | YUI_右 {st['players'][2]['card_count']}张",
            ]
            if st["current_player"] == "你":
                lines.append("轮到你先叫地主：用 dd_game_bid 叫分（0=不叫/1/2/3，叫3分直接当地主）。")
            else:
                lines.append(f"轮到 {st['current_player']} 叫分：用 dd_game_bid 提交你的叫分（0=不叫/1/2/3），AI 会自动依次叫。")
            return lines
        lines = [
            f"你的身份：{st['players'][0]['role']} | 底牌：{st['bottom_cards']}",
            f"你的手牌（{st['players'][0]['card_count']}张）：{st['players'][0]['hand']}",
            f"YUI_左 {st['players'][1]['card_count']}张 | YUI_右 {st['players'][2]['card_count']}张",
        ]
        if st["last_play"] != "（无）":
            lines.append(f"上一手：{st['last_play']}（{st['last_player']}）")
        if game.stage == "over":
            lines.append("本局已结束。用 dd_game_start 再来一局。")
        elif st["current_player"] == "你":
            lines.append("轮到你出牌！用 dd_game_play 出牌（如 'dd_game_play 3♠ 5♠ 7♠'），不要就 'dd_game_play 不要'。")
        else:
            lines.append(f"轮到 {st['current_player']}（{st['current_role']}）出牌。"
                         f"AI 的牌由插件自动出：请用 dd_game_advance 推进，轮到你再用 dd_game_play 出牌。"
                         f"（也可以在斗地主面板里直接玩：浏览器/客户端插件面板 → 斗地主）")
        return lines

    @llm_tool(
        name="dd_game_start",
        description="开始一局内置斗地主小游戏：你和两个 YUI 人机对战。何时用：用户说'来一局斗地主''斗地主小游戏''打牌'等。开局自动发牌进入叫地主阶段（不直接分配身份），用 dd_game_bid 叫分（0=不叫/1/2/3，叫3分直接当地主）；地主确定后自动进入出牌。同时打开斗地主浏览器面板（客户端插件面板 → 斗地主，欢乐斗地主风格界面：手牌/底牌/AI手牌数/上一手/叫地主按钮，点选牌出牌，AI 自动出牌）。重要：两个 YUI_AI 的牌由插件自动出，你只负责出'你'这一个玩家的牌，绝不要替 AI 出牌。对话模式：叫分用 dd_game_bid，推进 AI 回合用 dd_game_advance，出牌用 dd_game_play，看局面用 dd_game_state。",
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=30.0,
    )
    async def dd_game_start(self, *, role: str = "", **_):
        # role 参数保留以兼容历史调用，但身份一律由叫地主阶段决定
        with self._lock:
            g = DouDizhuGame(["你", "YUI_左", "YUI_右"])  # 发牌后自动进入 bidding（current=0 你先叫）
            self._dd_game = g
            self._dd_stats["role"] = "auto"

            lines = ["🃏 斗地主小游戏开始！发牌完成，进入叫地主阶段。"]
            lines.append("🌐 已打开斗地主浏览器面板（客户端插件面板 → 斗地主）："
                         "点叫地主按钮选分（不叫/1分/2分/3分），之后点击选牌出牌即可，AI 会自己出。"
                         "你也可以继续用对话叫分（dd_game_bid / 面板里叫地主）。")
            state = g.get_state()

        lines.extend(self._dd_build_view(g))
        return {"output": "\n".join(lines), "is_error": False, "game_state": state}

    @llm_tool(
        name="dd_game_bid",
        description="在斗地主小游戏中叫地主。何时用：小游戏开局后需要叫分时（用户说'我当地主''不叫''叫2分'等）。参数 score：0=不叫，1/2/3=叫分（叫3分直接当地主）。用户叫完后 AI 自动依次叫分，地主确定后自动进入出牌阶段（面板同步更新）。注意：这是叫地主，不是出牌；出牌用 dd_game_play。",
        parameters={"type": "object", "properties": {
            "score": {"type": "integer", "description": "0=不叫，1/2/3=叫分"},
        }, "required": ["score"]},
        timeout=15.0,
    )
    async def dd_game_bid(self, *, score: int = 0, **_):
        prm = _.get("parameters") or {}
        s_raw = score if score != 0 else prm.get("score", 0)
        try:
            s = int(s_raw)
        except (TypeError, ValueError):
            s = 0
        res = await self.dd_ui_bid(score=s)
        out = {"output": res.get("output", ""), "is_error": res.get("is_error", False)}
        with self._lock:
            if self._dd_game is not None:
                out["game_state"] = self._dd_game.get_state()
            else:
                out["game_state"] = None
        if res.get("error"):
            out["error"] = res["error"]
        return out

    @llm_tool(
        name="dd_game_play",
        description="在斗地主小游戏中出牌。何时用：小游戏开局后轮到'你'出牌时。参数 cards：要出的牌，空格分隔（如 '3♠ 5♠ 7♠'），支持纯点数（如 '5 5'）自动从手牌匹配花色；传'不要/过/不出'表示不出（先手时不能不出）。重要边界：两个 YUI_AI 的牌由插件自动出，你只出'你'的牌；若当前不是轮到你（返回提示轮到某 AI），不要替 AI 出牌，先用 dd_game_advance 推进 AI 回合。你出牌成功后插件会自动让两个 AI 依次出到轮到你。斗地主面板（浏览器/客户端插件面板）里可直接点击出牌，无需本工具。",
        parameters={"type": "object", "properties": {
            "cards": {"type": "string", "description": "出牌文字或 '不要'，如 '3♠ 5♠ 7♠'"},
        }, "required": ["cards"]},
        timeout=30.0,
    )
    async def dd_game_play(self, *, cards: str = "", **_):
        prm = _.get("parameters") or {}
        cards_str = cards if cards != "" else prm.get("cards", "")
        with self._lock:
            if self._dd_game is None:
                return {"output": "还没有牌局，请先用 dd_game_start 开一局。", "is_error": True, "error": "NO_GAME"}
            g = self._dd_game
            if g.stage != "playing":
                return {"output": "当前不在出牌阶段，请用 dd_game_start 重新开局。", "is_error": True, "error": "BAD_STAGE"}

            # 交互边界：只有轮到用户才能出牌，绝不由插件代打 AI 回合
            if g.current != 0:
                return {"output": f"当前轮到 {g.players[g.current]['name']}（AI）出牌，"
                                 f"不是轮到你。你只出你自己的牌：请用 dd_game_advance 推进 AI 回合"
                                 f"（斗地主面板里会自动进行，等待轮到你即可），轮到你再用 dd_game_play 出牌。",
                        "is_error": True, "error": "NOT_YOUR_TURN",
                        "game_state": g.get_state()}

            txt = cards_str.strip()
            is_pass = txt in ("", "不要", "过", "不出", "pass", "Pass", "PASS", "-", "无", "不叫")
            card_list: list[int] = []
            if not is_pass:
                parse_result = self._parse_doudizhu_cards(txt, g.players[0]["hand"])
                if parse_result.get("error"):
                    return {"output": parse_result["error"], "is_error": True, "error": "INVALID_CARDS"}
                card_list = parse_result["cards"]

            result = g.play(0, card_list)
            if not result.get("ok"):
                return {"output": f"出牌失败：{result.get('error')}", "is_error": True, "error": "PLAY_FAILED"}

            moves = [result["message"]]
            lines: list[str] = []
            if result.get("result"):
                self._dd_update_stats(result["result"])
                lines.extend(f"  {m}" for m in moves)
                r = result["result"]
                lines.append(f"\n🏁 {r['winner']}（{r['role']}）出完牌获胜！")
                won = r["winner"] == "你"
                lines.append("🎉 你赢了！" if won else "😔 你输了，再接再厉。")
                lines.append(f"📊 战绩：胜 {self._dd_stats['wins']} / 负 {self._dd_stats['losses']}")
                lines.append("用 dd_game_start 再来一局。")
                return {"output": "\n".join(lines), "is_error": False,
                        "result": result["result"], "game_state": g.get_state()}

            # 你出牌成功后，由插件自动推进两个 AI 回合（正常流程）
            ai_moves, final = self._dd_run_to_user(g)
            moves += ai_moves
            lines.extend(f"  {m}" for m in moves)

            if final:
                self._dd_update_stats(final)
                lines.append(f"\n🏁 {final['winner']}（{final['role']}）出完牌获胜！")
                won = final["winner"] == "你"
                lines.append("🎉 你赢了！" if won else "😔 你输了，再接再厉。")
                lines.append(f"📊 战绩：胜 {self._dd_stats['wins']} / 负 {self._dd_stats['losses']}")
                lines.append("用 dd_game_start 再来一局。")
                return {"output": "\n".join(lines), "is_error": False,
                        "result": final, "game_state": g.get_state()}

            lines.append("")
            lines.extend(self._dd_build_view(g))
        return {"output": "\n".join(lines), "is_error": False, "game_state": g.get_state()}

    @llm_tool(
        name="dd_game_state",
        description="查看斗地主小游戏当前局面和战绩。何时用：小游戏中想确认手牌、轮到谁、上一手、胜负统计。",
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=30.0,
    )
    async def dd_game_state(self, **_):
        with self._lock:
            if self._dd_game is None:
                return {"output": "还没有牌局，请先用 dd_game_start 开一局。", "is_error": True, "error": "NO_GAME"}
            g = self._dd_game
            st = g.get_state()
            stats = dict(self._dd_stats)
        lines = self._dd_build_view(g)
        lines.append(f"📊 战绩：胜 {stats['wins']} / 负 {stats['losses']}")
        lines.append("🌐 斗地主面板已就绪：客户端插件面板 → 斗地主，可点击选牌出牌；也可继续用对话玩。")
        return {"output": "\n".join(lines), "is_error": False, "game_state": st}

    @llm_tool(
        name="dd_game_advance",
        description="推进斗地主小游戏的 AI 回合：让插件把轮到 AI 的牌自动出完，直到轮到你或一局结束。何时用：对话模式下局面显示'轮到 YUI_左/YUI_右 出牌'时。注意：AI 的牌由插件自动出，你只是触发推进，不要替 AI 出牌。斗地主面板（浏览器/客户端插件面板）里 AI 会自动出牌，无需调用本工具。",
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=30.0,
    )
    async def dd_game_advance(self, **_):
        with self._lock:
            if self._dd_game is None:
                return {"output": "还没有牌局，请先用 dd_game_start 开一局。", "is_error": True, "error": "NO_GAME"}
            g = self._dd_game
            if g.stage != "playing":
                if g.stage == "bidding":
                    return {"output": "当前是叫地主阶段，请用 dd_game_bid 叫分（0=不叫/1/2/3）。",
                            "is_error": False, "game_state": g.get_state()}
                return {"output": "当前不在出牌阶段，无需推进。", "is_error": False, "game_state": g.get_state()}
            if g.current == 0:
                return {"output": "已经轮到你出牌，直接用 dd_game_play 出牌即可。", "is_error": False, "game_state": g.get_state()}
            moves, final = self._dd_run_to_user(g)
            lines: list[str] = []
            if final:
                self._dd_update_stats(final)
                lines.extend(f"  {m}" for m in moves)
                lines.append(f"\n🏁 {final['winner']}（{final['role']}）出完牌获胜！")
                won = final["winner"] == "你"
                lines.append("🎉 你赢了！" if won else "😔 你输了，再接再厉。")
                lines.append(f"📊 战绩：胜 {self._dd_stats['wins']} / 负 {self._dd_stats['losses']}")
                lines.append("用 dd_game_start 再来一局。")
                return {"output": "\n".join(lines), "is_error": False,
                        "result": final, "game_state": g.get_state()}
            lines.append("AI 回合已自动完成：")
            lines.extend(f"  {m}" for m in moves)
            lines.append("")
            lines.extend(self._dd_build_view(g))
        return {"output": "\n".join(lines), "is_error": False, "game_state": g.get_state()}

    # ── 斗地主浏览器面板（Hosted UI） ─────────────────

    def _dd_ui_state(self, game) -> dict:
        """构建浏览器面板渲染数据（不持锁，调用方保证安全）。"""
        st = game.get_state()
        players = game.players
        last_cards = list(game.last_play) if game.last_play else []
        winner = ""
        if game.stage == "over":
            for pl in players:
                if not pl["hand"]:
                    winner = pl["name"]
                    break
        return {
            "stage": game.stage,
            "user_role": st["players"][0]["role"],  # 引擎已返回中文（地主/农民）
            "user_hand": [dd_card_str(c) for c in players[0]["hand"]],
            "bottom": [dd_card_str(c) for c in (game.bottom_cards if game.stage != "bidding" else [])],
            "last_play": [dd_card_str(c) for c in last_cards],
            "last_player": players[game.last_player]["name"] if game.last_player is not None else "",
            "players": [
                {"name": pl["name"], "role": "地主" if pl["role"] == "landlord" else "农民",
                 "count": len(pl["hand"]), "is_you": i == 0}
                for i, pl in enumerate(players)
            ],
            "current": st["current_player"],
            "must_play": bool(game.stage == "playing" and not game.last_play and game.current == 0),
            "winner": winner,
            "stats": {"wins": self._dd_stats["wins"], "losses": self._dd_stats["losses"]},
            "bid_state": {
                "bidding": game.stage == "bidding",
                "current": game.current,
                "current_name": st["current_player"],
                "bid_history": [
                    {"player": h["player"], "bid": h["bid"]} for h in game._bid_history
                ],
                "max_bid": max((h["bid"] for h in game._bid_history), default=0),
            },
        }

    @ui.context(id="dd_game")
    def dd_ui_context(self) -> dict:
        """斗地主面板状态提供者（宿主在面板加载时读取）。"""
        with self._lock:
            if self._dd_game is None:
                return {"stage": "none"}
            return self._dd_ui_state(self._dd_game)

    @staticmethod
    def _ui_kwargs(kwargs: dict) -> dict:
        """普通 entry 的参数可能是直接 kwargs 或包在 parameters 里，兼容两种。"""
        src = kwargs.get("parameters")
        return src if isinstance(src, dict) else kwargs

    def _dd_ui_do_play(self, cards_text: str) -> dict:
        """面板出牌核心：解析 → 出牌 → 自动推进 AI → 返回（含 state）。须在锁外调用。"""
        with self._lock:
            if self._dd_game is None:
                return {"output": "还没有牌局，请先开一局。", "is_error": True, "error": "NO_GAME",
                        "state": {"stage": "none"}}
            g = self._dd_game
            if g.stage != "playing":
                return {"output": "当前不在出牌阶段，请重新开局。", "is_error": True, "error": "BAD_STAGE",
                        "state": self._dd_ui_state(g)}
            if g.current != 0:
                return {"output": f"当前轮到 {g.players[g.current]['name']}（AI）出牌，面板会自动进行。",
                        "is_error": False, "state": self._dd_ui_state(g)}

            txt = (cards_text or "").strip()
            if txt in ("不要", "不出", "过", "pass", "PASS", "0"):
                result = g.play(0, [])
                if not result.get("ok"):
                    return {"output": f"出牌失败：{result.get('error')}", "is_error": True,
                            "error": "PLAY_FAILED", "state": self._dd_ui_state(g)}
            else:
                cards_str = " ".join(txt.split())
                parse_result = self._parse_doudizhu_cards(cards_str, g.players[0]["hand"])
                if parse_result.get("error"):
                    return {"output": parse_result["error"], "is_error": True,
                            "error": "INVALID_CARDS", "state": self._dd_ui_state(g)}
                card_list = parse_result["cards"]
                result = g.play(0, card_list)
                if not result.get("ok"):
                    return {"output": f"出牌失败：{result.get('error')}", "is_error": True,
                            "error": "PLAY_FAILED", "state": self._dd_ui_state(g)}

            lines = [result["message"]]
            if result.get("result"):
                self._dd_update_stats(result["result"])
                r = result["result"]
                lines.append(f"\n🏁 {r['winner']}（{r['role']}）出完牌获胜！")
                lines.append("🎉 你赢了！" if r["winner"] == "你" else "😔 你输了，再接再厉。")
                return {"output": "\n".join(lines), "is_error": False,
                        "result": result["result"], "state": self._dd_ui_state(g)}

            moves, final = self._dd_run_to_user(g)
            lines.extend(f"  {m}" for m in moves)
            if final:
                self._dd_update_stats(final)
                lines.append(f"\n🏁 {final['winner']}（{final['role']}）出完牌获胜！")
                lines.append("🎉 你赢了！" if final["winner"] == "你" else "😔 你输了，再接再厉。")
                return {"output": "\n".join(lines), "is_error": False,
                        "result": final, "state": self._dd_ui_state(g)}
            return {"output": "\n".join(lines), "is_error": False, "state": self._dd_ui_state(g)}

    @ui.action(id="dd_ui_new_game", label="开一局", icon="plus", order=0)
    @plugin_entry(
        id="dd_ui_new_game", name="斗地主开新局",
        description="在浏览器面板里开始一局新的斗地主。发牌后进入叫地主阶段（不直接分配身份）。",
        kind="action",
        input_schema={"type": "object", "properties": {}},
    )
    async def dd_ui_new_game(self, **_):
        with self._lock:
            g = DouDizhuGame(["你", "YUI_左", "YUI_右"])  # 发牌后自动进入 bidding（current=0 你先叫）
            self._dd_game = g
            self._dd_stats["role"] = "auto"  # 身份由叫地主结果决定
            lines = ["🃏 新一局开始！发牌完成，进入叫地主阶段。",
                     "轮到你先叫地主：不叫 / 叫1分 / 叫2分 / 叫3分（叫3分直接当地主）。"]
            state = self._dd_ui_state(g)
        return {"output": "\n".join(lines), "is_error": False, "state": state}

    @ui.action(id="dd_ui_bid", label="叫地主", icon="flag", order=1, refresh_context=True)
    @plugin_entry(
        id="dd_ui_bid", name="斗地主叫地主",
        description="在浏览器面板里叫地主。参数 score：0=不叫，1/2/3=叫分（叫3分直接当地主）。叫分后 AI 自动依次叫分，地主确定后自动进入出牌阶段。",
        kind="action",
        input_schema={"type": "object", "properties": {
            "score": {"type": "integer", "description": "0=不叫，1/2/3=叫分"},
        }},
    )
    async def dd_ui_bid(self, **_):
        src = self._ui_kwargs(_)
        try:
            score = int(src.get("score", 0))
        except (TypeError, ValueError):
            score = 0
        if score not in (0, 1, 2, 3):
            return {"output": "叫分必须是 0(不叫)/1/2/3。", "is_error": True,
                    "error": "INVALID_SCORE", "state": self._dd_ui_state(self._dd_game)
                    if self._dd_game else {"stage": "none"}}
        with self._lock:
            if self._dd_game is None:
                return {"output": "还没有牌局，请先开一局。", "is_error": True, "error": "NO_GAME",
                        "state": {"stage": "none"}}
            g = self._dd_game
            if g.stage != "bidding":
                return {"output": "当前不是叫地主阶段，请重新开局。", "is_error": True,
                        "error": "BAD_STAGE", "state": self._dd_ui_state(g)}
            if g.current != 0:
                return {"output": f"当前轮到 {g.players[g.current]['name']}（AI）叫分。",
                        "is_error": False, "state": self._dd_ui_state(g)}
            res = g.bid(0, score)
            lines = [res["message"]]
            if res.get("ok"):
                # AI 依次叫到结束或轮回到用户
                guard = 0
                while g.stage == "bidding" and g.current != 0 and guard < 60:
                    guard += 1
                    ai_idx = g.current
                    ai_score = dd_ai_bid(g.players[ai_idx]["hand"])
                    r2 = g.bid(ai_idx, ai_score)
                    lines.append(r2["message"])
                    if r2.get("result"):
                        break
                if g.stage == "bidding" and g.current == 0:
                    max_bid = max((h["bid"] for h in g._bid_history), default=0)
                    lines.append(f"轮到你继续叫分（当前最高 {max_bid} 分）：不叫 / 1分 / 2分 / 3分。")
                elif g.stage == "playing":
                    lines.append(f"🏠 地主是 {g.players[g.landlord_idx]['name']}，"
                                 f"{g.players[g.landlord_idx]['name']} 先出牌。")
            state = self._dd_ui_state(g)
        return {"output": "\n".join(lines), "is_error": False, "state": state}

    @ui.action(id="dd_ui_play", label="出牌", icon="send", order=1, refresh_context=True)
    @plugin_entry(
        id="dd_ui_play", name="斗地主出牌",
        description="在浏览器面板里出牌。参数 cards：要出的牌（字符串数组，如 ['3♠','5♠','7♠'] 或 '3♠ 5♠ 7♠'）；传 '不要' 表示不出。",
        kind="action",
        input_schema={"type": "object", "properties": {
            "cards": {"type": "array", "items": {"type": "string"},
                      "description": "要出的牌，如 ['3♠','5♠','7♠']；或字符串 '不要'"},
        }},
    )
    async def dd_ui_play(self, **_):
        src = self._ui_kwargs(_)
        cards = src.get("cards")
        if isinstance(cards, list):
            cards = " ".join(str(c) for c in cards)
        return self._dd_ui_do_play(str(cards or ""))

    @ui.action(id="dd_ui_pass", label="不要", icon="ban", order=2, refresh_context=True)
    @plugin_entry(
        id="dd_ui_pass", name="斗地主不出",
        description="在浏览器面板里选择'不要'（不出牌）。仅当上一手有牌且轮到你时可用。",
        kind="action",
        input_schema={"type": "object", "properties": {}},
    )
    async def dd_ui_pass(self, **_):
        return self._dd_ui_do_play("不要")

    @ui.action(id="dd_ui_state", label="刷新局面", icon="refresh", order=3)
    @plugin_entry(
        id="dd_ui_state", name="斗地主刷新局面",
        description="读取斗地主面板当前牌局状态。",
        kind="action",
        input_schema={"type": "object", "properties": {}},
    )
    async def dd_ui_state(self, **_):
        with self._lock:
            if self._dd_game is None:
                return {"output": "还没有牌局。", "is_error": False, "state": {"stage": "none"}}
            state = self._dd_ui_state(self._dd_game)
        return {"output": "已刷新", "is_error": False, "state": state}

    # ══════════ 通用入口（plugin_entry run：评估侧主路由） ══════════
    # 4 个 dd_ui_* 入口仅服务浏览器面板场景；通用能力（规则/评估/胜率/建议/对弈/斗地主）
    # 此前只挂在 llm_tool 上。v0.4.5 注册 run 入口按 action 分派，保证评估侧可明确路由。
    # 方法名不能以下划线开头（bili_viewer v0.6.22 教训）。

    @plugin_entry(
        id="run",
        name="卡牌大师",
        description=(
            "卡牌大师（card_master）：让 YUI 学习和练习卡牌游戏（德州扑克、掼蛋、Kards、斗地主）。"
            "用户说'德州扑克怎么玩/这手牌什么牌型/我这手胜率多少/该不该跟注/出什么牌/开一局牌/练练打牌/斗地主'等"
            "卡牌相关请求时使用。"
            "操作：rules（查规则，game=poker/kards/guandan/doudizhu/all）/ evaluate（评牌型，hand+community 手牌与公共牌）/ "
            "new_game（开德州局）/ action（出牌 fold/check/call/raise）/ state（看牌局状态）/ odds（胜率蒙特卡洛模拟）/ "
            "suggest（出牌建议）/ selfplay（自我对弈训练）/ next_hand（换下一手）/ doudizhu（斗地主：new/bid/play/state/identify）。"
            "回复纪律：直接给出规则、牌型、胜率或建议结论，可附简要依据，不加多余话术，不编造牌局结果。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "rules/evaluate/new_game/action/state/odds/suggest/selfplay/next_hand/doudizhu",
                },
                "game": {"type": "string", "description": "规则查询的游戏（rules 用）：poker/kards/guandan/doudizhu"},
                "topic": {"type": "string", "description": "规则子主题（rules 用，Kards 支持）"},
                "hand": {"type": "string", "description": "手牌，空格分隔，如 'As Kh'（evaluate 用）"},
                "community": {"type": "string", "description": "公共牌（evaluate 用，可选）"},
                "players": {"type": "string", "description": "玩家 JSON 数组（new_game/doudizhu 用）"},
                "small_blind": {"type": "integer", "description": "小盲注（new_game 用）"},
                "big_blind": {"type": "integer", "description": "大盲注（new_game 用）"},
                "seed": {"type": "integer", "description": "随机种子（new_game/doudizhu 用）"},
                "card_action": {"type": "string", "description": "德州行动 fold/check/call/raise（action 用）"},
                "amount": {"type": "integer", "description": "加注/跟注金额（action 用）"},
                "opponents": {"type": "integer", "description": "对手人数（odds 用）"},
                "simulations": {"type": "integer", "description": "模拟次数（odds/suggest 用）"},
                "rounds": {"type": "integer", "description": "对弈轮数（selfplay 用）"},
                "cards": {"type": "string", "description": "斗地主出牌（doudizhu 用）"},
                "score": {"type": "integer", "description": "斗地主叫分（doudizhu 用）"},
                "sub_action": {
                    "type": "string",
                    "description": "doudizhu 子操作：new（开新局，默认）/ bid（叫分）/ play（出牌）/ state（看局面）/ identify（识别牌型）",
                },
            },
        },
        timeout=60.0,
    )
    async def entry_run(self, **kwargs):
        params = kwargs.get("parameters") or {}
        action = str(kwargs.get("action") or params.get("action") or "").strip().lower()

        def _s(*names):
            return str(kwargs.get(names[0]) or params.get(names[0]) or "").strip() if names else ""

        def _i(name, default=0):
            try:
                return int(kwargs.get(name) or params.get(name) or default)
            except (TypeError, ValueError):
                return default

        if action in ("rules", "rule"):
            return await self.card_rules(game=_s("game"), topic=_s("topic"), parameters={})
        if action in ("evaluate", "eval", "评估"):
            return await self.card_evaluate(hand=_s("hand"), community=_s("community"), parameters={})
        if action in ("new_game", "new", "开牌局"):
            return await self.card_new_game(
                players=_s("players"), small_blind=_i("small_blind", 10),
                big_blind=_i("big_blind", 20), seed=_i("seed"), parameters={})
        if action in ("action", "act"):
            return await self.card_action(action=_s("card_action"), amount=_i("amount"), parameters={})
        if action in ("state", "status", "局面"):
            return await self.card_state()
        if action in ("odds", "胜率"):
            return await self.card_odds(opponents=_i("opponents", 1), simulations=_i("simulations", 2000), parameters={})
        if action in ("suggest", "建议"):
            return await self.card_suggest(simulations=_i("simulations", 1500), parameters={})
        if action in ("selfplay", "对弈", "train"):
            return await self.card_selfplay(game=_s("game") or "poker", rounds=_i("rounds", 1), parameters={})
        if action in ("next_hand", "next", "换手"):
            return await self.card_next_hand()
        if action in ("doudizhu", "斗地主", "ddz"):
            sub = str(params.get("sub_action") or _s("sub_action") or "").lower()
            if sub in ("bid", "叫分"):
                return await self.doudizhu_bid(score=_i("score"), parameters={})
            if sub in ("play", "出牌"):
                return await self.doudizhu_play(cards=_s("cards"), parameters={})
            if sub in ("state", "局面"):
                return await self.doudizhu_state()
            if sub in ("identify", "识别"):
                return await self.doudizhu_identify(cards=_s("cards"), parameters={})
            return await self.doudizhu_new(players=_s("players"), seed=_i("seed"), parameters={})
        return {
            "output": "请指定操作：rules/evaluate/new_game/action/state/odds/suggest/selfplay/next_hand/doudizhu。",
            "is_error": True,
            "error": "NO_ACTION",
        }

