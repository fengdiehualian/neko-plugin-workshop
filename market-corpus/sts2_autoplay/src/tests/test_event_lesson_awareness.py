"""事件屏必须受【跨局复盘教训】影响（用户点名的缺陷 A / B 所在屏）。

★ 背景：

用户点名的两个真实缺陷都发生在**事件屏**：
  - 缺陷 A：某些事件可**重复选择**且**消耗生命上限** ⇒ 脚本反复选 ⇒ 上限被烧到只剩 1。
  - 缺陷 B：涉及变化卡/遗物的事件会选到**诅咒** ⇒ 只是把一种诅咒换成另一种，期望为负。

而此前的实现里，事件屏只读 `guidance_override`（payload 里的临时指示）与启发分，
**完全不读跨局教训** ⇒ 复盘无论学到"别再吃永久扣上限的选项"都无法阻止重犯。

★ 分层陷阱（本会话已踩过一次，必须固定住）：

`strategy_directives` 在 **`strategy_context`**（`service.py:549-557` 把它与
`summary_context` 作为**兄弟键**放进 snapshot），**不在** `summary_context` 里
（`summary_context_builder` 从不添加该键）。
⇒ 若把教训从 `summary_context` 里读，运行时**恒为空**，改动是**白改**。
本文件的 `test_lesson_read_from_strategy_context_not_summary_context` 就是钉住这一点的。
"""
from __future__ import annotations

from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner

#: 可重复选择、且**永久扣生命上限**的选项（缺陷 A 的真实形态）
SACRIFICE = {"index": 0, "text": "献祭：失去6点生命上限，获得一件遗物和50金币。"}
LEAVE = {"index": 1, "text": "离开。"}


def _planner() -> STS2HeuristicPlanner:
    return STS2HeuristicPlanner()


def _ctx(options: list[dict], *, hp: int = 60, max_hp: int = 80, gold: int = 100) -> dict:
    return {
        "payload": {
            "event_options": options,
            "current_hp": hp,
            "max_hp": max_hp,
            "gold": gold,
        },
        "classification": {"state_name": "event"},
        "snapshot": {"available_actions": [{"type": "choose_event_option"}]},
    }


def _strategy(avoid: list[str]) -> dict:
    """生产形状：`strategy_context` 里放 `strategy_directives`。"""
    return {"strategy_directives": {"avoid": avoid, "must": [], "prefer": []}}


def _plan_ctx() -> dict:
    """`plan()` 的真实入参形状 —— 两个**兄弟键**（`service.py:549-557`）。

    ★ 故意**不**把 `strategy_directives` 放进 `summary_context`：
      生产代码里它只在 `strategy_context` 里，放错层会让"教训生效"假绿。
    """
    return {
        "classification": {"state_name": "event", "summary_kind": "event"},
        "snapshot": {"available_actions": [{"type": "choose_event_option"}]},
        "summary_context": {
            "payload": {
                "event_options": [SACRIFICE, LEAVE],
                "current_hp": 60,
                "max_hp": 80,
                "gold": 100,
            }
        },
        "strategy_context": _strategy(["别再吃永久扣生命上限的选项，血上限烧低必死"]),
    }


def test_event_option_text_covers_max_hp_loss() -> None:
    """`_option_loses_max_hp` 必须识别"失去N点生命上限"（缺陷 A 的判定核心）。"""
    p = _planner()
    assert p._option_loses_max_hp("失去6点生命上限，获得一件遗物。") is True
    assert p._option_loses_max_hp("离开。") is False


def test_max_hp_loss_is_deterred_without_any_lesson() -> None:
    """即使没有教训，扣上限选项也要被基础启发分压制（这是既有正确行为）。"""
    p = _planner()
    assert p._score_event_option(SACRIFICE, 0.75, 100) < 0
    assert p._score_event_option(LEAVE, 0.75, 100) >= 0


def test_lesson_is_read_from_strategy_context_not_summary_context() -> None:
    """★★★ 分层钉死：教训只在 `strategy_context` 里，`summary_context` 里没有。

    这条测试是**回归闸门**：若有人把读教训的位置改回 `summary_context`，
    运行时将恒空 ⇒ 本测试立刻转红。
    """
    p = _planner()
    signals = p._reward_lesson_signals(_strategy(["别再吃永久扣生命上限的选项"]))
    assert signals.get("self_damage"), "从 strategy_context 必须能读到 self_damage"

    # summary_context 形状（无 strategy_directives）必须读不到任何信号
    assert p._reward_lesson_signals(_ctx([SACRIFICE])) == {}, (
        "summary_context 里本就没有 strategy_directives；若这里非空说明分层被改坏了"
    )


def test_self_damage_lesson_adds_extra_penalty_to_max_hp_option() -> None:
    """★有血上限教训时，扣上限选项的最终分必须**低于**无教训时。

    这是"教训真的参与了事件屏决策"的直接判据。
    """
    p = _planner()
    ctx = _ctx([SACRIFICE, LEAVE])
    no_lesson = p._event_final_scores(ctx, None)
    with_lesson = p._event_final_scores(ctx, _strategy(["别再吃永久扣生命上限的选项，血上限烧低必死"]))
    assert with_lesson[0] < no_lesson[0], (
        f"扣上限选项分数未被教训压低：无教训={no_lesson[0]} 有教训={with_lesson[0]}"
    )


def test_lesson_never_lifts_the_harmful_option_above_leave() -> None:
    """★安全性：无论教训怎么写，扣上限选项都不得被抬到"离开"之上。"""
    p = _planner()
    ctx = _ctx([SACRIFICE, LEAVE])
    for lesson in (
        ["别再吃永久扣生命上限的选项"],
        ["多拿遗物", "多拿金币"],
        ["必须输出", "必须攻击"],
    ):
        scores = p._event_final_scores(ctx, _strategy(lesson))
        assert scores[0] <= scores[1], f"教训 {lesson} 把有害选项抬到了离开之上"


def test_curse_lesson_downgrades_curse_option() -> None:
    """★缺陷 B：教训提到诅咒时，拿诅咒的选项必须降权。"""
    p = _planner()
    curse = {"index": 0, "text": "接受：将一张诅咒放入你的卡组，获得一件遗物。"}
    plain = {"index": 1, "text": "接受：获得一件遗物。"}
    ctx = _ctx([curse, plain])
    no_lesson = p._event_final_scores(ctx, None)
    with_lesson = p._event_final_scores(ctx, _strategy(["别拿诅咒卡，诅咒只会变成另一种诅咒"]))
    assert with_lesson[0] < no_lesson[0], (
        f"诅咒选项未被教训降权：无教训={no_lesson[0]} 有教训={with_lesson[0]}"
    )


def test_no_lesson_means_zero_displacement() -> None:
    """★默认零位移：没有教训时，传入/不传 `strategy_context` 结果必须一致。"""
    p = _planner()
    ctx = _ctx([SACRIFICE, LEAVE])
    assert p._rank_event_options(ctx) == p._rank_event_options(ctx, None)
    assert p._rank_event_options(ctx) == p._rank_event_options(ctx, {})


def test_malformed_strategy_context_never_raises() -> None:
    """向前兼容：畸形 `strategy_context` 不得抛异常。"""
    p = _planner()
    ctx = _ctx([SACRIFICE, LEAVE])
    for bad in (None, {}, {"strategy_directives": None},
                {"strategy_directives": {"avoid": "不是列表"}},
                {"strategy_directives": {"avoid": [None, 1, ""]}},
                {"avoid": []}):
        assert p._rank_event_options(ctx, bad) is not None  # type: ignore[arg-type]


def test_rank_and_preferred_agree() -> None:
    """`_rank_event_options[0]` 必须等于 `_preferred_event_option`（同口径不变量）。"""
    p = _planner()
    ctx = _ctx([SACRIFICE, LEAVE])
    ctx["strategy_context"] = _strategy(["别再吃永久扣生命上限的选项"])
    strt = _strategy(["别再吃永久扣生命上限的选项"])
    ranked = p._rank_event_options(ctx, strt)
    preferred = p._preferred_event_option(ctx, {}, strt)
    assert ranked[0] == preferred


def test_plan_end_to_end_threads_lesson_into_event_screen() -> None:
    """★端到端：`plan()` 必须把 `strategy_context` 一路送到事件屏评分。

    若有人漏传参数（`_preferred_event_option(..., preferences)` 少一个实参层），
    函数级测试仍会全绿，但真实调用链是断的 —— 这条测试专门抓那个。

    ★ `plan()` 的入参形状（`service.py` 真实形状）：
      `context["summary_context"]` 与 `context["strategy_context"]` 是**兄弟键**。
    """
    p = _planner()
    op = p.plan(_plan_ctx())
    assert op is not None
    d = op.as_dict() if hasattr(op, "as_dict") else op
    kwargs = d.get("kwargs") or {}
    assert kwargs.get("option_index") == 1, (
        f"应选'离开'(1)，实际 idx={kwargs.get('option_index')}（扣上限选项未被避开）"
    )


def test_plan_signature_receives_lesson_via_spy() -> None:
    """★★**接线判据**（抓 E1/E5 型"算了但没接上"）：`plan()` 真的把教训传到了评分层。

    用 monkeypatch 包住 `_reward_lesson_signals`，断言 `plan()` 调用它时
    拿到的入参**含** `strategy_directives`（即教训确实从 `strategy_context` 流入）。
    """
    p = _planner()
    seen: list[object] = []
    real = p._reward_lesson_signals

    def spy(source):  # type: ignore[no-untyped-def]
        seen.append(source)
        return real(source)

    p._reward_lesson_signals = spy  # type: ignore[assignment]
    try:
        p.plan(_plan_ctx())
    finally:
        p._reward_lesson_signals = real  # type: ignore[assignment]

    assert seen, "plan() 未调用 _reward_lesson_signals ⇒ 接线完全断开"
    assert any(
        isinstance(s, dict) and isinstance(s.get("strategy_directives"), dict)
        for s in seen
    ), f"教训未从 strategy_context 流入（E1/E5 型缺陷）：seen={seen}"


def test_repeat_penalty_still_avoids_the_inversion_trap() -> None:
    """★回归闸门（既有修复，勿踩回）：反复选'离开'不得让有害选项反超。

    历史陷阱：若对**所有**选项累加重复惩罚，"离开"会被逐步压低，
    最终把"扣上限"反派成最优 —— 修复比不修更糟（实测复现过）。
    """
    p = _planner()
    ctx = _ctx([SACRIFICE, LEAVE])
    for times in range(1, 6):
        p._event_choice_counts = {1: times}
        scores = p._event_final_scores(ctx, None)
        assert scores[0] <= scores[1], (
            f"重复选'离开'{times}次后有害选项反超了：{scores}"
        )
