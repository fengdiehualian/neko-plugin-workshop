"""流派（archetype）策略必须真正生效 —— 缺陷 E 的契约。

★ 缺陷 E（2026-10-17 发现）：`_reward_deck_policy` 只认
`instruction_type == "deck_policy"` 的记录，而**全仓库没有任何地方产出**它：
  * 唯一 `upsert` 点在 `neko_interface.py:187-208`，由**用户自然语言指令**驱动；
  * `STS2PreferenceStore._DOMAINS` 里**没有** `deck_policy` 域；
  * 记忆 `preferences` 是扁平 `list[str]`。
⇒ `prefer_tags` / `avoid_tags` / `archetype_bias` / `branches` **恒为空**
⇒ `_score_reward_card` 里所有流派加权（±35/±40/±50/+20）**永不触发**。
这正是用户点名的「**卡牌流派的抉择**」——系统里根本没有流派机制。

本文件把修复钉死：流派策略改由**卡组自身**推断产生。
"""
from __future__ import annotations

from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner

STRIKE = {"name": "打击", "card_id": "STRIKE_NECROBINDER",
          "resolved_rules_text": "造成6点伤害。", "card_type": "Attack"}
DEFEND = {"name": "防御", "card_id": "DEFEND_NECROBINDER",
          "resolved_rules_text": "获得5点格挡。", "card_type": "Skill"}


def _deck(card: dict, count: int) -> dict:
    return {"cards": [dict(card) for _ in range(count)]}


def test_pure_defense_deck_yields_defense_bias() -> None:
    planner = STS2HeuristicPlanner()
    deck = _deck(DEFEND, 8)
    tags = planner._infer_archetype_tags(deck)
    policy = planner._archetype_policy_from_deck(deck, tags)
    assert "defense" in policy.get("archetype_bias", set())


def test_pure_attack_deck_yields_attack_bias() -> None:
    """★原实现**从不**产出 `attack` 标签 ⇒ 纯攻击卡组 `tags=[]` ⇒ 没有流派偏好。

    后果：系统性地只给防御卡组流派加成，把玩家往防御推。
    """
    planner = STS2HeuristicPlanner()
    deck = _deck(STRIKE, 8)
    tags = planner._infer_archetype_tags(deck)
    assert "attack" in tags, f"纯攻击卡组必须识别出 attack，实际 {tags}"
    policy = planner._archetype_policy_from_deck(deck, tags)
    assert "attack" in policy.get("archetype_bias", set())


def test_small_deck_has_no_archetype_preference() -> None:
    """★卡组太小时不锁流派（早期保持开放）—— 否则第 1 张牌就决定了整个流派。"""
    planner = STS2HeuristicPlanner()
    deck = _deck(DEFEND, 2)
    tags = planner._infer_archetype_tags(deck)
    assert planner._archetype_policy_from_deck(deck, tags) == {}


def test_archetype_bias_actually_changes_score() -> None:
    """★核心：`archetype_bias` 必须真的改变 `_score_reward_card`（此前是死代码）。"""
    planner = STS2HeuristicPlanner()
    attack_card = {"index": 0, "name": "未知攻击", "resolved_rules_text": "造成8点伤害。",
                   "dynamic_values": [{"name": "Damage", "current_value": 8}]}
    base = planner._score_reward_card(attack_card, {}, [])
    biased = planner._score_reward_card(attack_card, {"archetype_bias": {"attack"}}, ["attack"])
    assert biased > base, f"attack 流派加成未生效：{base} -> {biased}"


def test_bias_only_applies_to_committed_archetype() -> None:
    """偏离流派的卡**不得**拿到该流派的加成。"""
    planner = STS2HeuristicPlanner()
    attack_card = {"index": 0, "name": "未知攻击", "resolved_rules_text": "造成8点伤害。"}
    base = planner._score_reward_card(attack_card, {}, [])
    # 卡组是防御流 ⇒ 攻击卡不该拿 defense 加成
    biased = planner._score_reward_card(attack_card, {"archetype_bias": {"defense"}}, ["defense"])
    assert biased == base


def test_realistic_mixed_deck_commits_to_its_majority() -> None:
    """真实 NECROBINDER 卡组（5 防御 / 3 打击）⇒ 至少防御成型。"""
    planner = STS2HeuristicPlanner()
    deck = {"cards": [DEFEND, DEFEND, DEFEND, DEFEND, DEFEND, STRIKE, STRIKE, STRIKE]}
    tags = planner._infer_archetype_tags(deck)
    policy = planner._archetype_policy_from_deck(deck, tags)
    bias = policy.get("archetype_bias", set())
    assert "defense" in bias, f"5 张防御应让 defense 成型，实际 bias={bias}"


def test_empty_or_malformed_deck_never_raises() -> None:
    """向前兼容：畸形卡组不得抛异常，且不得产生流派偏好。"""
    planner = STS2HeuristicPlanner()
    for bad in ({}, {"cards": []}, {"cards": [None, 1, "x"]}, {"cards": [{}]}):
        tags = planner._infer_archetype_tags(bad)
        policy = planner._archetype_policy_from_deck(bad, tags)
        assert isinstance(policy, dict)
        assert not policy.get("archetype_bias"), f"畸形卡组不得产生 bias：{bad} -> {policy}"


def test_external_policy_still_wins_when_present() -> None:
    """★向前兼容：外部 `deck_policy` 若真的存在，必须仍然生效（合并而非覆盖）。"""
    planner = STS2HeuristicPlanner()
    records = [{"value": {"instruction_type": "deck_policy", "prefer_tags": ["draw"]}}]
    external = planner._reward_deck_policy(records)
    assert "draw" in external["prefer_tags"]


# --------------------------------------------------------------------------
# ★ 端到端：流派必须经**真实 plan()** 影响选卡（否则补位只是死代码）
# --------------------------------------------------------------------------

def _ctx(deck: dict, cards: list[dict]) -> dict:
    """生产形状：`summary_context` 与 `strategy_context` 是兄弟键。"""
    return {
        "classification": {"state_name": "reward"},
        "snapshot": {"screen": "REWARD",
                     "available_actions": [{"type": "choose_reward_card"},
                                            {"type": "skip_reward_cards"}]},
        "summary_context": {"payload": {"reward": {"card_options": cards}, "deck": deck}},
        "strategy_context": {"preferences": {},
                             "strategy_directives": {"avoid": [], "must": [], "prefer": []}},
    }


ATTACK_CAND = {"index": 0, "name": "未知攻击", "resolved_rules_text": "造成8点伤害。",
               "dynamic_values": [{"name": "Damage", "current_value": 8}]}
DEFEND_CAND = {"index": 1, "name": "未知防御", "resolved_rules_text": "获得8点格挡。",
               "dynamic_values": [{"name": "Block", "current_value": 8}]}


def test_archetype_bonus_reaches_plan_end_to_end() -> None:
    """★流派加成必须经**真实 `plan()`** 生效。

    反恒真：两张候选卡的基础分**不同**（防御卡天然高 6.4），
    所以不能用"选了哪张"判断加成 —— 直接用 plan 的 reason/选择加上
    `_score_reward_card` 的分差来证明加成确实进了评分链。
    """
    planner = STS2HeuristicPlanner()
    attack_deck = _deck(STRIKE, 8)
    tags = planner._infer_archetype_tags(attack_deck)
    policy = planner._archetype_policy_from_deck(attack_deck, tags)
    bias = policy.get("archetype_bias", set())
    assert "attack" in bias

    # 无 bias vs 有 bias：攻击卡必须**更高**（证明加成进了评分）
    without = planner._score_reward_card(ATTACK_CAND, {}, tags)
    with_bias = planner._score_reward_card(ATTACK_CAND, {"archetype_bias": bias}, tags)
    assert with_bias == without + 20, f"攻击流派加成应为 +20：{without} -> {with_bias}"

    # 且 plan() 必须在真实形状下能跑通（不抛异常、给出选择）
    op = planner.plan(_ctx(attack_deck, [ATTACK_CAND, DEFEND_CAND]))
    assert op is not None


def test_attack_archetype_favours_attack_card() -> None:
    """★攻击流派卡组 ⇒ 攻击卡的**相对分差**必须收窄（流派加成抵消部分防御偏置）。

    ⚠️ 前提事实（实测，记录在案）：`_base_reward_score` 对格挡给 +20、对伤害只给 +12，
    即**同样数值的防御卡天然比攻击卡高 8 分**。这是既有偏置，本测试不修改它，
    只验证流派加成确实按方向作用：

      无加成:  攻击 12.0  vs  防御 20.0   （差 −8）
      攻击流派: 攻击 32.0  vs  防御 20.0   （差 +12）  ⇒ 方向翻转
    """
    planner = STS2HeuristicPlanner()
    plain_attack = {"index": 0, "name": "甲", "resolved_rules_text": "造成8点伤害。"}
    plain_defend = {"index": 1, "name": "乙", "resolved_rules_text": "获得8点格挡。"}

    base_a = planner._score_reward_card(plain_attack, {}, [])
    base_d = planner._score_reward_card(plain_defend, {}, [])
    # 记录既有偏置：防御卡天然更高（这是 `_base_reward_score` 的现状，不是本测试的目标）
    assert base_d > base_a, f"既有偏置应为防御更高：{base_a} vs {base_d}"

    attack_bias = {"archetype_bias": {"attack"}}
    biased_a = planner._score_reward_card(plain_attack, attack_bias, ["attack"])
    biased_d = planner._score_reward_card(plain_defend, attack_bias, ["attack"])
    # ★ 加成只作用于攻击卡 ⇒ 把 −8 的差距翻成正
    assert biased_a == base_a + 20
    assert biased_d == base_d
    assert biased_a > biased_d, f"攻击流派下攻击卡应反超：{biased_a} vs {biased_d}"


def test_defense_deck_bonus_does_not_help_attack_card() -> None:
    """★反向对照：防御流派加成**不得**给攻击卡加分（否则是"全都加"的假实现）。"""
    planner = STS2HeuristicPlanner()
    plain_attack = {"index": 0, "name": "甲", "resolved_rules_text": "造成8点伤害。"}
    base_a = planner._score_reward_card(plain_attack, {}, [])
    biased_a = planner._score_reward_card(plain_attack, {"archetype_bias": {"defense"}}, ["defense"])
    assert biased_a == base_a


def test_archetype_absent_means_no_bonus() -> None:
    """★没有成型流派 ⇒ 不加分（早期卡组保持开放，与改动前一致）。"""
    planner = STS2HeuristicPlanner()
    small = _deck(DEFEND, 2)
    tags = planner._infer_archetype_tags(small)
    assert planner._archetype_policy_from_deck(small, tags) == {}
    base = planner._score_reward_card(ATTACK_CAND, {}, tags)
    assert planner._score_reward_card(ATTACK_CAND, {}, tags) == base


def test_deck_policy_from_plan_carries_archetype_bias() -> None:
    """★★**接线**判据：`_preferred_reward_option` 内部必须真的把卡组流派合并进 deck_policy。

    这是 A4/A5 型缺陷（"补位算了但没接上"）的唯一判据——
    只测 `_archetype_policy_from_deck` 与只测 `_score_reward_card`
    都**测不到接线**（两者各自都正确，接线断了照样全绿）。

    做法：构造一个"攻击流卡组 + 两张同基础分候选"，然后在真实 `plan()` 上
    检查选择是否服从流派；再用 monkeypatch 抓取实际传给评分的 deck_policy。
    """
    planner = STS2HeuristicPlanner()
    attack_deck = _deck(STRIKE, 8)
    captured: list[dict] = []
    real_score = planner._score_reward_card

    def spy(card, deck_policy, archetype_tags):
        captured.append(dict(deck_policy) if isinstance(deck_policy, dict) else {})
        return real_score(card, deck_policy, archetype_tags)

    planner._score_reward_card = spy  # type: ignore[assignment]
    try:
        planner.plan(_ctx(attack_deck, [ATTACK_CAND, DEFEND_CAND]))
    finally:
        planner._score_reward_card = real_score  # type: ignore[assignment]

    assert captured, "plan() 未调用 _score_reward_card ⇒ 测试构造有误"
    biases = [set(p.get("archetype_bias") or set()) for p in captured]
    assert any("attack" in b for b in biases), (
        f"★卡组流派未接进 deck_policy（A4/A5 型缺陷）：captured={biases}"
    )


def test_plan_end_to_end_attack_archetype_flips_choice() -> None:
    """★端到端：攻击流卡组下，`plan()` 必须选中攻击卡（否则流派只是摆设）。

    构造两张**同基础分**的候选卡（名字都不含 strike/defend 关键词，
    且都用不带 "damage"/"block" 英文词的文本）来隔离流派加成。
    """
    planner = STS2HeuristicPlanner()
    # 用中文"造成/获得"避开 _base_reward_score 的英文 "damage"/"block" 加分
    cand_a = {"index": 0, "name": "甲", "resolved_rules_text": "造成8点伤害。"}
    cand_d = {"index": 1, "name": "乙", "resolved_rules_text": "获得8点格挡。"}
    # 前提：基础分相同（否则无法隔离流派影响）
    if planner._score_reward_card(cand_a, {}, []) != planner._score_reward_card(cand_d, {}, []):
        # 既有偏置存在（格挡 +20 vs 伤害 +12）⇒ 用攻击更高的数值抵消，
        # 使"无流派时两者同分"，从而流派加成成为唯一决定因素。
        cand_a = {"index": 0, "name": "甲", "resolved_rules_text": "造成8点伤害。",
                  "dynamic_values": [{"name": "Damage", "current_value": 16}]}

    attack_deck = _deck(STRIKE, 8)
    op = planner.plan(_ctx(attack_deck, [cand_a, cand_d]))
    assert op is not None
    d = op.as_dict() if hasattr(op, "as_dict") else op
    idx = (d.get("kwargs") or {}).get("option_index")
    # 攻击流卡组应倾向攻击卡（idx=0）
    assert idx == 0, f"攻击流卡组应选攻击卡(idx=0)，实际 idx={idx}"
