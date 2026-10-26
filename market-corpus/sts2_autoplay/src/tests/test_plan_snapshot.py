"""计划快照：算一次 → 按快照执行 → 局面不一致就重算。

玩家口径：「自动游玩主程序时的求解器是如何运行的，能不能改成计算一次，做成快照，直接按快照执行，
每次行动后用当前局面和快照对比，如果一致就继续，不一致就重新算」。

背景：mod 的 `/solver/plan` **每次调用都会重跑一遍 CombatSolver 搜索**（Short 预算 8 秒），
所以能不重算就别重算。但只按"回合号没变"就复用是不够的 —— 抽牌/弃牌会让**手牌位置漂移**，
敌人死了会让目标失效，这些都不改回合号，却足以让快照里的下一步变成非法动作。

所以复用的唯一判据是：**快照里下一步在当前局面下仍然可执行**（`_plan_step_is_executable`）。
"""

from __future__ import annotations

import pytest
from plugin.plugins.sts2_autoplay.loop_runner import (
    _plan_step_is_executable,
    _reusable_plan,
)


def _snapshot(
    *,
    hand: list[dict] | None = None,
    potions: list[dict] | None = None,
    actions: list[str] | None = None,
) -> dict:
    return {
        "screen": "COMBAT",
        "available_actions": [{"type": name, "raw": {"name": name}} for name in (actions or ["play_card", "end_turn"])],
        "raw_state": {
            "combat": {"hand": hand or []},
            "run": {"potions": potions or []},
        },
    }


def _card(card_id: str, *, index: int = 0, playable: bool = True, valid_targets=None, requires_target: bool = False) -> dict:
    card = {"index": index, "card_id": card_id, "playable": playable, "requires_target": requires_target}
    if valid_targets is not None:
        card["valid_target_indices"] = valid_targets
    return card


# ---- 单步校验 ---------------------------------------------------------------------------


@pytest.mark.unit
def test_play_card_step_is_executable_while_the_card_is_still_in_hand() -> None:
    ok, why = _plan_step_is_executable(
        {"kind": "play_card", "card_id": "STRIKE", "card_index": 2},
        _snapshot(hand=[_card("DEFEND", index=1), _card("STRIKE", index=2)]),
    )

    assert ok is True and why == ""


@pytest.mark.unit
def test_play_card_step_fails_when_the_card_left_the_hand() -> None:
    """**手牌漂移**：出牌/弃牌之后快照里的那张牌可能已经不在手上了。"""
    ok, why = _plan_step_is_executable(
        {"kind": "play_card", "card_id": "STRIKE", "card_index": 2},
        _snapshot(hand=[_card("DEFEND", index=1)]),
    )

    assert ok is False and "STRIKE" in why


@pytest.mark.unit
def test_play_card_step_fails_when_the_card_is_unplayable() -> None:
    ok, why = _plan_step_is_executable(
        {"kind": "play_card", "card_id": "STRIKE"},
        _snapshot(hand=[_card("STRIKE", playable=False)]),
    )

    assert ok is False and "可打出" in why


@pytest.mark.unit
def test_play_card_step_fails_when_the_target_died() -> None:
    """敌人死了 -> 快照里的目标失效（这一点和 target_index 修正是互补的两层）。"""
    ok, why = _plan_step_is_executable(
        {"kind": "play_card", "card_id": "STRIKE", "target_index": 1},
        _snapshot(hand=[_card("STRIKE", requires_target=True, valid_targets=[0])]),
    )

    assert ok is False and "目标" in why


@pytest.mark.unit
def test_play_card_step_by_index_fails_when_out_of_range() -> None:
    ok, why = _plan_step_is_executable(
        {"kind": "play_card", "card_index": 5},
        _snapshot(hand=[_card("STRIKE", index=0)]),
    )

    assert ok is False and "越界" in why


@pytest.mark.unit
def test_end_turn_step_needs_end_turn_to_be_available() -> None:
    step = {"kind": "end_turn"}

    assert _plan_step_is_executable(step, _snapshot(actions=["play_card", "end_turn"]))[0] is True
    ok, why = _plan_step_is_executable(step, _snapshot(actions=["play_card"]))
    assert ok is False and "结束回合" in why


@pytest.mark.unit
def test_use_potion_step_needs_a_usable_potion() -> None:
    step = {"kind": "use_potion", "card_id": "FLEX_POTION"}

    assert _plan_step_is_executable(step, _snapshot(potions=[{"potion_id": "FLEX_POTION", "can_use": True}]))[0] is True
    ok, why = _plan_step_is_executable(step, _snapshot(potions=[{"potion_id": "FLEX_POTION", "can_use": False}]))
    assert ok is False and "药水" in why


@pytest.mark.unit
def test_unknown_step_kinds_are_left_to_lower_layers() -> None:
    """拿不准就说"能执行"，让动作层/选牌逻辑兜底 —— 不能因为不认识就反复重算。"""
    ok, why = _plan_step_is_executable({"kind": "select_deck_card", "card_id": "X"}, _snapshot())

    assert ok is True and why == ""


# ---- 快照复用决策 ------------------------------------------------------------------------


def _plan(*steps: dict) -> dict:
    return {"in_combat": True, "line": [{"turn": 1, "steps": list(steps)}]}


@pytest.mark.unit
def test_snapshot_is_reused_when_the_next_step_is_still_executable() -> None:
    plan = _plan({"kind": "play_card", "card_id": "STRIKE"}, {"kind": "end_turn"})
    snapshot = _snapshot(hand=[_card("STRIKE")], actions=["play_card", "end_turn"])

    cached, reason = _reusable_plan(plan, "sig-1", "sig-1", 0, snapshot)

    assert cached is plan and reason == ""


@pytest.mark.unit
def test_snapshot_is_dropped_when_the_state_drifted() -> None:
    """局面和快照不一致 -> 丢掉快照，调用方会去重新算。"""
    plan = _plan({"kind": "play_card", "card_id": "STRIKE"})
    snapshot = _snapshot(hand=[_card("DEFEND")])

    cached, reason = _reusable_plan(plan, "sig-1", "sig-1", 0, snapshot)

    assert cached is None and "STRIKE" in reason


@pytest.mark.unit
def test_snapshot_is_dropped_when_the_turn_or_enemies_changed() -> None:
    plan = _plan({"kind": "play_card", "card_id": "STRIKE"})
    snapshot = _snapshot(hand=[_card("STRIKE")])

    cached, reason = _reusable_plan(plan, "sig-1", "sig-2", 0, snapshot)

    assert cached is None and "变了" in reason


@pytest.mark.unit
def test_snapshot_is_dropped_when_its_steps_run_out() -> None:
    plan = _plan({"kind": "end_turn"})
    snapshot = _snapshot()

    cached, reason = _reusable_plan(plan, "s", "s", 1, snapshot)

    assert cached is None and "用尽" in reason


@pytest.mark.unit
def test_missing_snapshot_falls_back_to_recompute() -> None:
    cached, reason = _reusable_plan(None, None, "s", 0, _snapshot())

    assert cached is None and reason
