"""card_master — 实时打牌决策引擎（live 模式）。

让 YUI 在获得电脑鼠标/键盘控制权后，通过插件决策"自己打牌"：
1. YUI 用屏幕视觉读取真实游戏画面（截图/OCR/读界面元素）
2. 把局面结构化后调用 live_report 上报给插件
3. 插件用真实规则引擎计算建议动作 + 鼠标操作指引（steps）
4. YUI 执行操作后调用 live_action_done 确认，进入下一步

支持游戏：
- poker（德州扑克）：基于 engine.suggest_from_state（胜率+底池赔率+位置）
- doudizhu（斗地主）：基于 doudizhu.suggest_play（完整候选枚举+身份策略）
- kards：基于 kards.py 官方 wiki 规则的真实启发式回合建议

设计原则：
- 插件只做"决策"，不做屏幕识别和鼠标控制（那是 YUI/N.E.K.O 的能力）
- 所有建议附带可执行的 steps（点击什么/怎么验证），YUI 照做即可
- 输出明确区分"已确认的规则事实"与"启发式建议"
"""
from __future__ import annotations

import time
import uuid

try:
    # 插件运行时：作为 card_master 包的一部分加载
    from . import kards as kards_mod
    from .doudizhu import suggest_play as dd_suggest_play
    from .engine import (
        parse_card,
        suggest_from_state,
    )
except ImportError:
    # 测试环境：card_master 目录在 sys.path 上，顶层导入
    import kards as kards_mod
    from doudizhu import suggest_play as dd_suggest_play
    from engine import (
        parse_card,
        suggest_from_state,
    )

# ══════════ 会话 ══════════


class LiveSession:
    """一次实时打牌会话的状态。"""

    def __init__(self, game_type: str, role: str, params: dict):
        self.session_id = uuid.uuid4().hex[:12]
        self.game_type = game_type  # poker / doudizhu / kards
        self.role = role            # 玩家身份描述
        self.params = params or {}
        self.created_at = time.time()
        self.state: dict = {}       # 最近一次上报的局面
        self.history: list[dict] = []
        self.turn_counter = 0
        self.last_advice: dict | None = None

    def record(self, event: str, detail: dict | None = None):
        self.history.append({
            "t": time.strftime("%H:%M:%S"),
            "event": event,
            "detail": detail or {},
        })


def new_session(game_type: str, role: str = "", params: dict | None = None) -> LiveSession:
    return LiveSession(game_type, role, params)


# ══════════ 德州扑克：局面决策 + 操作指引 ══════════

def _poker_steps(advice: dict) -> list[dict]:
    """把德州扑克决策翻译成鼠标操作步骤。"""
    act = advice["suggestion"]
    steps = []
    if act == "fold":
        steps.append({"what": "点击 FOLD / 弃牌 按钮（通常在底池右侧或手牌上方）",
                      "verify": "画面出现'已弃牌'提示，或轮次跳到下一位玩家"})
    elif act == "check":
        steps.append({"what": "点击 CHECK / 过牌 按钮", "verify": "轮次跳到下一位玩家"})
    elif act == "call":
        steps.append({"what": "点击 CALL / 跟注 按钮（金额通常已自动填好）",
                      "verify": "筹码数减少跟注额，轮次跳到下一位玩家"})
    elif act == "raise":
        steps.append({"what": "点击 RAISE / 加注 按钮，拖动滑块或输入金额",
                      "verify": "下注条显示加注金额，确认后轮次跳到下一位玩家"})
    steps.append({"what": "执行后截图确认：轮到下一位玩家，你的筹码/下注已更新",
                  "verify": "确认无误后再调用 live_report 上报新局面"})
    return steps


def advise_poker(state: dict, simulations: int = 1500) -> dict:
    """德州扑克实时决策。state 为 YUI 从屏幕读取的结构化局面。"""
    try:
        hand_cards = [parse_card(c) for c in (state.get("hole_cards") or "").split()]
        comm_cards = [parse_card(c) for c in (state.get("community") or "").split()] \
            if state.get("community") else []
    except ValueError as e:
        return {"ok": False, "error": f"牌格式错误：{e}。应使用 'As Kh' 格式（点数+花色）。",
                "action": "修正 hole_cards/community 后重新上报"}

    if len(hand_cards) != 2:
        return {"ok": False, "error": f"hole_cards 需要恰好2张，当前 {len(hand_cards)} 张。",
                "action": "确认屏幕上你的两张底牌后重新上报"}

    try:
        to_call = int(state.get("to_call", 0))
        pot = int(state.get("pot", 0))
        chips = int(state.get("my_chips", 0))
        active = int(state.get("active_players", 2))
        bb = int(state.get("big_blind", 20))
        cur_bet = int(state.get("current_bet", 0))
    except (TypeError, ValueError):
        return {"ok": False, "error": "to_call/pot/my_chips/active_players 必须是整数。",
                "action": "从屏幕上读取准确数值后重新上报"}

    advice = suggest_from_state(
        hand_cards, comm_cards, to_call, pot, chips, active, bb,
        current_bet=cur_bet, simulations=simulations,
    )
    advice["steps"] = _poker_steps(advice)
    return {"ok": True, "advice": advice}


# ══════════ 斗地主：局面决策 + 操作指引 ══════════

def _doudizhu_steps(sug: dict) -> list[dict]:
    steps = []
    if sug.get("should_pass"):
        steps.append({"what": "点击 不出 / 过 按钮", "verify": "轮次跳到下一位玩家"})
    elif sug.get("suggestion"):
        cards_txt = sug["suggestion"]["cards"]
        steps.append({"what": f"点击手牌中的：{cards_txt}", "verify": "对应牌被选中/高亮"})
        steps.append({"what": "点击 出牌 按钮", "verify": "牌打出，轮次跳到下一位玩家"})
    else:
        steps.append({"what": "先截图确认当前界面（是否轮到你、上一手是什么）",
                      "verify": "确认后重新上报局面"})
    steps.append({"what": "执行后截图确认：你的手牌减少、轮次变化正确",
                  "verify": "确认无误后再调用 live_report 上报新局面"})
    return steps


def advise_doudizhu(state: dict) -> dict:
    """斗地主实时决策。state 为 YUI 从屏幕读取的结构化局面。"""
    hand_txt = state.get("my_hand") or ""
    last_txt = state.get("last_play") or ""
    role = state.get("my_role", "farmer")
    role_norm = "landlord" if role in ("landlord", "地主") else "farmer"

    # 解析手牌
    hand_cards = []
    if hand_txt.strip():
        for token in hand_txt.split():
            matched = _parse_dd_token(token)
            if matched is None:
                return {"ok": False,
                        "error": f"无法识别手牌中的 '{token}'。牌格式：3♠ 10♥ J♦ A♣ 2 小王 大王",
                        "action": "修正 my_hand 后重新上报"}
            hand_cards.append(matched)

    # 解析上一手
    last_cards = []
    if last_txt.strip() and last_txt not in ("（无）", "无", "-"):
        for token in last_txt.split():
            matched = _parse_dd_token(token)
            if matched is None:
                return {"ok": False,
                        "error": f"无法识别上一手牌中的 '{token}'。",
                        "action": "修正 last_play 后重新上报"}
            last_cards.append(matched)

    sug = dd_suggest_play(hand_cards, last_cards if last_cards else None, role=role_norm)
    sug["steps"] = _doudizhu_steps(sug)

    lines = [f"建议：{sug['reason']}"]
    if sug.get("suggestion"):
        lines.append(f"要出的牌：{sug['suggestion']['cards']}（{sug['suggestion']['type_name']}）")
    if sug.get("should_pass"):
        lines.append("这一手建议'不要'。")
    if sug.get("candidates"):
        lines.append("")
        lines.append("其他可出候选（按推荐度排序）：")
        for i, c in enumerate(sug["candidates"][:5], 1):
            lines.append(f"  {i}. {c['cards']}（{c['type_name']}）")
    if not last_cards:
        lines.append("")
        lines.append("你是先手，按上面建议出牌后重新上报。")

    return {"ok": True, "output": "\n".join(lines), "suggestion": sug}


def _parse_dd_token(token: str) -> int | None:
    """解析斗地主单张牌文字（支持 3♠/10♥/J/小王 等）。"""
    if token in ("小王", "joker_s", "小"):
        return 16 * 4
    if token in ("大王", "joker_b", "大"):
        return 17 * 4
    rank_map = {"3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "10": 10,
                "J": 11, "Q": 12, "K": 13, "A": 14, "2": 15,
                "j": 11, "q": 12, "k": 13, "a": 14}
    rank_part = token.rstrip("♠♥♦♣shdc")
    suit_part = token[len(rank_part):] if len(rank_part) < len(token) else ""
    r = rank_map.get(rank_part)
    if r is None:
        return None
    suit_map = {"♠": 0, "♥": 1, "♦": 2, "♣": 3, "s": 0, "h": 1, "d": 2, "c": 3}
    s = suit_map.get(suit_part, 0)
    return r * 4 + s


# ══════════ Kards：局面决策 + 操作指引 ══════════

def advise_kards(state: dict) -> dict:
    """Kards 实时回合建议（基于官方 wiki 真实规则 + 启发式）。

    state 字段（YUI 从屏幕读取）：
    - kredits: 当前指挥点（int）
    - my_hq / opp_hq: 双方总部防御（int）
    - my_hand: 手牌列表 [{name, cost, type(unit/order), attack, defense, keywords[]}]
    - front_line / support_line: 己方前线/支援线单位列表
    - opp_front / opp_support: 敌方前线/支援线单位列表
    - is_my_turn: 是否我的回合（bool）
    说明：本建议是"规则级启发式"，不等同于最优解；YUI 结合卡面
    实际数值与对局经验执行。
    """
    try:
        kredits = int(state.get("kredits", 0))
        my_hq = int(state.get("my_hq", 20))
        opp_hq = int(state.get("opp_hq", 20))
    except (TypeError, ValueError):
        return {"ok": False, "error": "kredits/my_hq/opp_hq 必须是整数。",
                "action": "从屏幕上读取准确数值后重新上报"}

    my_hand = state.get("my_hand") or []
    front = state.get("front_line") or []
    support = state.get("support_line") or []
    opp_front = state.get("opp_front") or []
    opp_support = state.get("opp_support") or []
    is_my_turn = bool(state.get("is_my_turn", True))

    checklist: list[str] = []
    actions: list[dict] = []
    reasons: list[str] = []

    def unit_attack(u: dict) -> int:
        try:
            return int(u.get("attack", 0))
        except (TypeError, ValueError):
            return 0

    def unit_def(u: dict) -> int:
        try:
            return int(u.get("defense", 0))
        except (TypeError, ValueError):
            return 0

    def card_cost(c: dict):
        """卡牌费用；None=未识别（不按可部署/可使用处理，宁缺勿猜）。"""
        try:
            return int(c.get("cost"))
        except (TypeError, ValueError):
            return None

    if not is_my_turn:
        return {
            "ok": True,
            "output": "当前是对手回合，不需要操作。等待对手行动结束后截图确认轮到你，再调用 live_report 上报。",
            "steps": [
                {"what": "等待对手回合结束（注意对手是否有反制指令触发）",
                 "verify": "界面显示轮到你的回合/你的按钮可用"}
            ],
            "checklist": ["若对手回合触发了反制指令，截图记录后重新上报局面"],
        }

    # ── 1. 攻击阶段：前线单位先攻击 ──
    if front:
        targets = opp_front if opp_front else []
        if not targets:
            actions.append({
                "priority": 1,
                "action": "攻击敌方总部",
                "detail": f"敌方前线为空，用己方前线单位直接攻击敌方总部（{opp_hq} 点防御）。",
                "steps": [{"what": "依次点击己方前线单位，再点击敌方总部",
                          "verify": "总部防御下降，单位进入攻击状态"}],
            })
            reasons.append("敌方前线无阻挡，前线单位应直接攻击总部争取胜利。")
        else:
            # 优先清掉攻击力高/防御低的威胁单位
            threats = sorted(targets, key=lambda u: (-unit_attack(u), unit_def(u)))
            for t in threats[:3]:
                actions.append({
                    "priority": 1,
                    "action": f"攻击敌方单位 {t.get('name', '?')}",
                    "detail": (f"目标攻击 {unit_attack(t)} / 防御 {unit_def(t)}，是前线威胁，"
                               f"优先清除保护己方总部。"),
                    "steps": [{"what": f"点击己方前线单位，再点击敌方单位 {t.get('name', '?')}",
                              "verify": "敌方单位防御下降或被消灭"}],
                })
            reasons.append("敌方前线有单位：优先消灭攻击力高的单位（攻击>防御的优先）。")

    # ── 2. 部署阶段：用剩余指挥点部署单位（费用未识别的不盲目部署） ──
    deployable = [c for c in my_hand if c.get("type") in ("unit", "单位")
                  and card_cost(c) is not None and card_cost(c) <= kredits]
    if deployable:
        # 优先部署高战力单位；有闪击的单位可立即行动
        deployable_sorted = sorted(deployable, key=lambda c: -int(c.get("cost", 0)))
        top = deployable_sorted[0]
        kw = [k for k in (top.get("keywords") or []) if k in kards_mod.KEYWORDS]
        flash = any(k in ("闪击", "blitz") for k in kw)
        actions.append({
            "priority": 2,
            "action": f"部署单位 {top.get('name', '?')}",
            "detail": f"花费 {top.get('cost')} 指挥点部署到己方支援阵线。"
                      + ("该单位有「闪击」，部署后可直接移动/攻击。"
                         if flash else "部署后通常有召唤眩晕，本回合不能行动，先防守。"),
            "steps": [{"what": f"从手牌中点击 {top.get('name', '?')}，再点击己方支援阵线空位",
                      "verify": "单位出现在支援阵线，指挥点减少"}],
        })
        reasons.append(f"还有 {kredits} 指挥点，优先部署战力最高的 {top.get('name', '?')}。")
        if flash:
            reasons.append("注意该单位有「闪击」，部署后可立即上前线攻击，别浪费。")
        else:
            reasons.append("部署的单位本回合召唤眩晕（除非「闪击」），用于防守/为下回合准备。")

    # ── 3. 命令卡：有盈余指挥点且手牌有 order 时使用（费用未识别的不盲目使用） ──
    orders = [c for c in my_hand if c.get("type") in ("order", "指令", "反制指令")
              and card_cost(c) is not None and card_cost(c) <= kredits]
    if orders:
        cheap = sorted(orders, key=lambda c: int(c.get("cost", 0)))
        c0 = cheap[0]
        actions.append({
            "priority": 3,
            "action": f"使用命令卡 {c0.get('name', '?')}",
            "detail": f"花费 {c0.get('cost')} 指挥点使用该指令效果（一次性，使用后进墓地）。",
            "steps": [{"what": f"点击手牌中的 {c0.get('name', '?')}，再选择目标",
                      "verify": "指令效果生效，卡牌进入墓地"}],
        })
        reasons.append(f"有可用的指令 {c0.get('name', '?')}，按当前局面需要决定是否使用；"
                       "若局势不急可留到更关键回合。")

    # ── 4. 战线管理提醒 ──
    if not front and not support:
        checklist.append("你的两条战线都空着：务必部署单位，否则总部直接暴露。")
    if len(support) >= 4:
        checklist.append("支援阵线已满（4个单位），新部署单位需先从前线撤退或留手牌。")
    if opp_front:
        has_fighter = any("战斗机" in (u.get("type", "") or "") or "fighter" in str(u.get("keywords", "")).lower()
                          for u in opp_front)
        if has_fighter and any("轰炸机" in (u.get("type", "") or "") for u in front):
            checklist.append("敌方前线有战斗机，你的轰炸机不能攻击该战线中战斗机以外的目标。")
    if opp_support:
        checklist.append(f"敌方支援线还有 {len(opp_support)} 个单位：用炮兵/轰炸机（无视守护）或清空前线后继续施压。")
    if kredits >= 12:
        checklist.append("指挥点接近上限（12），每回合剩余指挥点不结转，尽量用满。")

    # 组装输出
    lines = [f"指挥点 {kredits} | 己方总部 {my_hq} | 敌方总部 {opp_hq}"]
    lines.append("")
    lines.append("行动建议（按优先级）：")
    for i, a in enumerate(actions[:5], 1):
        lines.append(f"  {i}. [{a['priority']}] {a['action']}：{a['detail']}")
    if not actions:
        lines.append("  暂无建议行动（指挥点不足或手牌无可部署单位）。")
    lines.append("")
    lines.append("理由：")
    for r in reasons[:5]:
        lines.append(f"  • {r}")
    if checklist:
        lines.append("")
        lines.append("⚠️ 检查清单：")
        for c in checklist:
            lines.append(f"  • {c}")

    return {
        "ok": True,
        "output": "\n".join(lines),
        "actions": actions,
        "checklist": checklist,
    }


# ══════════ 会话处理主入口 ══════════

def handle_report(session: LiveSession, state: dict, simulations: int = 1500) -> dict:
    """live_report 的统一处理：按会话游戏类型分发决策，并记录会话状态。"""
    session.turn_counter += 1
    session.record("report", {"turn": session.turn_counter, "state_keys": list(state.keys())})

    if session.game_type == "poker":
        result = advise_poker(state, simulations=simulations)
    elif session.game_type == "doudizhu":
        result = advise_doudizhu(state)
    elif session.game_type == "kards":
        result = advise_kards(state)
    else:
        return {"ok": False, "error": f"未知游戏类型 {session.game_type}。",
                "action": "用 live_session 重新开启会话（poker/doudizhu/kards）"}

    if result.get("ok"):
        session.state = dict(state)
        session.last_advice = result
        session.record("advice", {"summary": (result.get("output") or "")[:120]})
    return result
