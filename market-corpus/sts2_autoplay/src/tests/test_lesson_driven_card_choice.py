"""复盘教训 → 选卡质量 的契约。

★ 背景（2026-10-17）：
用户指出「强化/删除哪张卡片、卡牌流派的抉择」是败因。
但此前跨局复盘教训**只**经 `_apply_run_lessons` 影响通用排序
（`ordered_candidates` 的扰动候选池），**完全不参与 `_score_reward_card`**
⇒ **复盘无论学到什么，都不改变「选哪张卡」**。

本文件把新通道钉死：
  教训文本 → 方向信号（带褒贬） → 候选卡加权。

★ 关键设计：教训常**同时**提到两个方向
（如「输出不够，每回合都在垫格挡」同时含"输出"和"格挡"），
所以必须区分褒贬，否则两边都加分、净效果为 0。
"""
from __future__ import annotations

from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner


def _dv(**kw):
    return [{"name": k, "current_value": v} for k, v in kw.items()]


STRIKE = {"index": 0, "name": "打击", "type": "ATTACK",
          "resolved_rules_text": "造成6点伤害。", "dynamic_values": _dv(Damage=6)}
DEFEND = {"index": 1, "name": "防御", "type": "SKILL",
          "resolved_rules_text": "获得5点格挡。", "dynamic_values": _dv(Block=5)}
DRAW = {"index": 2, "name": "思虑", "type": "SKILL",
        "resolved_rules_text": "抽2张牌。", "dynamic_values": _dv(Cards=2)}


def _ctx(cards: list[dict], lessons: list[str]) -> dict:
    """★ 生产形状（`service.py:549-557`）：`summary_context` 与 `strategy_context` 是**兄弟键**。

    ⚠️ 不要把 `strategy_directives` 塞进 `summary_context` —— 那样测的就不是生产行为了。
    运行时它**只**在 `strategy_context` 里；塞错层会让"传错层"这类缺陷逃逸
    （本轮真实踩过：`_reward_lesson_signals(summary_context)` 恒返回 {}）。
    """
    return {
        "classification": {"state_name": "reward"},
        "snapshot": {"screen": "reward",
                     "available_actions": [{"type": "choose_reward_card"}, {"type": "skip_reward_cards"}]},
        "summary_context": {"payload": {"reward": {"card_options": cards}, "deck": {"cards": []}}},
        "strategy_context": {"preferences": {},
                             "strategy_directives": {"must": [], "prefer": [], "avoid": lessons}},
    }


def _remove_ctx(cards: list[dict], lessons: list[str]) -> dict:
    """删牌屏的生产形状。"""
    return {
        "classification": {"state_name": "card_selection"},
        "snapshot": {"screen": "card_selection",
                     "available_actions": [{"type": "select_deck_card"}]},
        "summary_context": {"payload": {"selection_cards": cards,
                                        "selection_kind": "deck_select_remove"}},
        "strategy_context": {"preferences": {},
                             "strategy_directives": {"must": [], "prefer": [], "avoid": lessons}},
    }


def _pick_index(cards: list[dict], lessons: list[str]) -> int | None:
    planner = STS2HeuristicPlanner()
    op = planner.plan(_ctx(cards, lessons))
    assert op is not None
    d = op.as_dict() if hasattr(op, "as_dict") else op
    return (d.get("kwargs") or {}).get("option_index")


def _signals(lessons: list[str]) -> dict[str, int]:
    """★ 必须用 `strategy_context`（生产位置），不能用 `summary_context`。"""
    planner = STS2HeuristicPlanner()
    return planner._reward_lesson_signals(_ctx([], lessons)["strategy_context"])


def _remove_index(cards: list[dict], lessons: list[str]) -> int | None:
    """走**真实** `plan()` 调用链选该删哪张（这样才测得到 strategy_context 的传递）。"""
    planner = STS2HeuristicPlanner()
    op = planner.plan(_remove_ctx(cards, lessons))
    assert op is not None
    d = op.as_dict() if hasattr(op, "as_dict") else op
    return (d.get("kwargs") or {}).get("option_index")


# --------------------------------------------------------------------------
# 1) 零教训必须完全不变（回归保护）
# --------------------------------------------------------------------------

def test_no_lessons_means_zero_bonus() -> None:
    """★零教训 ⇒ 信号为空 ⇒ 加权为 0 ⇒ 行为与改动前一致。

    ★ 必须覆盖 `_score_reward_card + bonus` 的**端到端**等于原分：
      只断言 `_reward_lesson_bonus(x, {}, []) == 0` 会漏掉
      「signals 为空时不再提前 return、而是继续按方向表加权」这类变异。
    """
    assert _signals([]) == {}
    planner = STS2HeuristicPlanner()
    assert planner._reward_lesson_bonus(STRIKE, {}, []) == 0
    # 端到端：零信号下加分必须严格为 0（且与主评分一致）
    for card in (STRIKE, DEFEND, DRAW):
        base = planner._score_reward_card(card, {}, [])
        with_bonus = base + planner._reward_lesson_bonus(card, {}, [])
        assert with_bonus == base, f"{card.get('name')} 零教训下分数不得改变"
    assert planner._reward_lesson_bonus(DEFEND, {}, []) == 0


def test_no_lessons_selection_unchanged() -> None:
    """无教训时的选择必须等于纯评分结果（防御 24 > 打击 18）。"""
    assert _pick_index([STRIKE, DEFEND], []) == 1


# --------------------------------------------------------------------------
# 2) 方向性：必须能区分褒贬，而不是"'输出'出现在句子里就加攻击分"
# --------------------------------------------------------------------------

def test_drawback_wording_prefers_attack() -> None:
    """★「输出不够，每回合都在垫格挡」方向是【攻击】，不是防御。

    句子同时含"输出"和"格挡"。若只做关键词命中，两边都加分 ⇒ 净效果 0（错）。
    """
    lessons = ["输出不够，每回合都在垫格挡"]
    sig = _signals(lessons)
    assert sig.get("attack", 0) > 0, f"应识别为偏攻击，实际 {sig}"
    assert sig.get("defense", 0) <= 0, f"不得同时给防御加权，实际 {sig}"
    assert _pick_index([STRIKE, DEFEND], lessons) == 0, "应选中打击"


def test_defensive_lesson_prefers_defense() -> None:
    lessons = ["先垫够格挡再输出，别头铁硬刚"]
    assert _signals(lessons).get("defense", 0) > 0
    assert _pick_index([STRIKE, DEFEND, DRAW], lessons) == 1, "应选中防御"


def test_explicit_negative_defense_prefers_attack() -> None:
    """「别光垫格挡」⇒ 防御为【负】权重 ⇒ 打击胜出。"""
    lessons = ["别光垫格挡，要打输出"]
    assert _signals(lessons).get("defense", 0) < 0
    assert _pick_index([STRIKE, DEFEND], lessons) == 0


# --------------------------------------------------------------------------
# 3) 各方向都能被识别
# --------------------------------------------------------------------------

def test_draw_shortage_prefers_draw() -> None:
    lessons = ["抽牌不够老是卡手"]
    assert _signals(lessons).get("draw", 0) > 0
    assert _pick_index([STRIKE, DEFEND, DRAW], lessons) == 2


def test_self_damage_concern_penalizes_hp_loss_card() -> None:
    """★对应用户缺陷 A「烧血上限」：教训表达担忧时，有自损的卡应被降权。"""
    hp_loss = {"index": 3, "name": "御血术", "type": "ATTACK",
               "resolved_rules_text": "失去3点生命，造成15点伤害。",
               "dynamic_values": _dv(Damage=15, HpLoss=3)}
    lessons = ["血上限被烧光了，别再乱掉血"]
    sig = _signals(lessons)
    assert sig.get("self_damage", 0) > 0
    planner = STS2HeuristicPlanner()
    assert planner._reward_lesson_bonus(hp_loss, sig, []) < 0


# --------------------------------------------------------------------------
# 4) 保守性：教训加权不得压过主判据（不能把诅咒/垃圾卡救回来）
# --------------------------------------------------------------------------

def test_lesson_bonus_cannot_rescue_curse() -> None:
    """★教训加权不得把诅咒卡变成"可以拿"的卡。"""
    from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner as P

    planner = P()
    curse = {"index": 9, "name": "痛苦", "type": "CURSE", "description": "无法打出"}
    base = planner._score_reward_card(curse, {}, [])
    lessons = ["要打输出", "格挡不够", "抽牌不够", "能量不够", "要力量", "要电球"]
    bonus = planner._reward_lesson_bonus(curse, _signals(lessons), [])
    # 诅咒本身无正向关键词 ⇒ 教训加权应为 0；即便不是，也必须仍为负分
    assert base + bonus < 0, f"诅咒不得被教训救回：base={base} bonus={bonus}"


def test_lesson_bonus_capped() -> None:
    """单方向加权有上限，避免噪声压过 `_score_reward_card` 的主判据。"""
    planner = STS2HeuristicPlanner()
    huge = {"defense": 99}
    assert planner._reward_lesson_bonus(DEFEND, huge, []) <= 36 + 1


# --------------------------------------------------------------------------
# 5) 真实教训原文（生产实测那条）必须给出防御方向
# --------------------------------------------------------------------------

def test_real_production_reflection_yields_defense() -> None:
    """生产实测反思原文 ⇒ 防御方向（与它「先垫够格挡再输出」的字面含义一致）。"""
    real = (
        "这局最要命的就是第11层遇上CUBEX_CONSTRUCT，直接把血干到0/80，整局累计掉了68点血直接寄掉好惨。"
        "下次血量低于20的时候别头铁硬刚，先垫格挡喝回血药再输出，选牌走图都慢半拍多留容错就不会莫名其妙暴毙啦。"
    )
    sig = _signals([real])
    assert sig.get("defense", 0) > 0, f"应识别为偏防御，实际 {sig}"


def test_malformed_lessons_never_raise() -> None:
    """向前兼容：畸形避免项不得抛异常。"""
    planner = STS2HeuristicPlanner()
    for bad in ([None, 1, {}], ["", "   "], [123]):
        ctx = _ctx([], [])
        ctx["strategy_context"]["strategy_directives"] = {"avoid": bad}
        assert planner._reward_lesson_signals(ctx["strategy_context"]) == {}


# --------------------------------------------------------------------------
# 6) ★ 层间的传递（最容易静默失效的地方）
# --------------------------------------------------------------------------

def test_signals_read_from_strategy_context_not_summary() -> None:
    """★`strategy_directives` 只在 `strategy_context` 里。

    若实现去 `summary_context` 里找（运行时那里**没有**这个键）⇒ 恒返回 {} ⇒
    整个教训通道静默失效（本轮真实缺陷：`_reward_lesson_signals(summary_context)`）。
    """
    planner = STS2HeuristicPlanner()
    only_in_strategy = _ctx([], ["先垫够格挡"])["strategy_context"]
    assert planner._reward_lesson_signals(only_in_strategy) == {"defense": 1}
    # 只有 payload 的 summary_context 里没有 directives ⇒ 必须为空
    assert planner._reward_lesson_signals({"payload": {}}) == {}


def test_remove_screen_lessons_reach_plan_end_to_end() -> None:
    """★删牌屏：教训必须经**真实 `plan()`** 影响「该删哪张」。

    这是 M6 型缺陷的判据 —— 只调 `_preferred_remove_option` 绕过了
    `context["strategy_context"]` 的传递，测不出"删牌屏没传 strategy_context"。

    两张基础分相同的卡（都是"未见过的普通卡"= 10 分）：
      偏防御教训 ⇒ 保护格挡卡 ⇒ 该删**攻击**卡(idx=0)
      偏攻击教训 ⇒ 保护攻击卡 ⇒ 该删**防御**卡(idx=1)
    """
    attack = {"index": 0, "name": "未知甲", "type": "ATTACK",
              "resolved_rules_text": "造成6点伤害。"}
    defend = {"index": 1, "name": "未知乙", "type": "SKILL",
              "resolved_rules_text": "获得5点格挡。"}
    base = _remove_index([attack, defend], [])
    assert _remove_index([attack, defend], ["先垫够格挡再输出"]) == 0, "偏防御 ⇒ 该删攻击卡"
    assert _remove_index([attack, defend], ["输出不够，每回合都在垫格挡"]) == 1, "偏攻击 ⇒ 该删防御卡"
    assert base is not None


def test_remove_screen_lessons_absent_means_unchanged() -> None:
    """零教训时删牌选择必须与改动前一致（回归保护）。"""
    attack = {"index": 0, "name": "未知甲", "type": "ATTACK",
              "resolved_rules_text": "造成6点伤害。"}
    defend = {"index": 1, "name": "未知乙", "type": "SKILL",
              "resolved_rules_text": "获得5点格挡。"}
    planner = STS2HeuristicPlanner()
    direct = planner._preferred_remove_option(_remove_ctx([attack, defend], [])["summary_context"], {})
    assert _remove_index([attack, defend], []) == direct
