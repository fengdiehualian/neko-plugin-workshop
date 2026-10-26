"""游玩模式：认真（serious，默认）/ 捣蛋（mischievous）/ 笨蛋（silly）。

这三档由插件自己实现（插件是唯一的决策者）：规划器照常算出最优操作，扰动层再按概率把它换成
**合法**的次优或最差选择。这里钉住四件事：

1. ``serious`` **零影响**——扰动层连候选排序都不算，规划器的结果按**对象同一性**原样传下去；
2. 概率与落点确定：20% 换成次优、60% 换成最差（用可注入的确定性随机源）；
3. **永远合法**——扰动出的操作能通过 ``action_engine`` 校验，且下标只来自合法集合；
4. 没有合法替代时**退回最优**（放弃扰动），绝不发非法动作、也不会什么都不做。
   另外：玩家亲口授权的那一步（``sts2_execute_planned_operation``）不受游玩模式影响。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from plugin.plugins.sts2_autoplay.action_registry import STS2ActionRegistry
from plugin.plugins.sts2_autoplay.play_style import (
    PLAY_STYLE_MISCHIEVOUS,
    PLAY_STYLE_SERIOUS,
    PLAY_STYLE_SILLY,
    choose_candidate,
    normalize_play_style,
    perturbation_probability,
)
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService


class DummyLogger:
    def debug(self, *a, **k):
        return None

    def info(self, *a, **k):
        return None

    def warning(self, *a, **k):
        return None

    def exception(self, *a, **k):
        return None


class ScriptedRandom:
    """确定性随机源：按脚本依次吐出预先定好的 [0,1) 数值，用完后一律返回 0.0。

    用 0.0 兜底意味着"之后每次都扰动"，所以测试要给足脚本，避免误判。
    """

    def __init__(self, *values: float) -> None:
        self._values = list(values)
        self.calls = 0

    def random(self) -> float:
        self.calls += 1
        return self._values.pop(0) if self._values else 0.0


def _service(*scripted: float) -> tuple[STS2AutoplayService, ScriptedRandom]:
    rng = ScriptedRandom(*scripted)
    service = STS2AutoplayService(
        DummyLogger(), lambda payload: None, lambda **kwargs: None, play_style_random=rng
    )
    return service, rng


# ---- 事件屏的测试夹具 ----------------------------------------------------------------
#
# 启发式评分（hp 70/80、gold 50）：选项 1 遗物 +14 > 选项 2 金币 +10 > 选项 0 掉血 -8
# 所以排序是 [1, 2, 0]，最优 = 1、次优 = 2、最差 = 0。

_EVENT_OPTIONS = [
    {"index": 0, "text": "lose hp"},
    {"index": 1, "text": "gain relic"},
    {"index": 2, "text": "gain gold"},
]
_EVENT_ACTION = {"type": "choose_event_option", "raw": {"name": "choose_event_option", "requires_index": True}}


def _event_snapshot(options: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "screen": "EVENT",
        "available_actions": [_EVENT_ACTION],
        "classification": {"state_name": "event"},
        "strategy_context": {"preferences": {}},
        "summary_context": {
            "payload": {
                "event_options": options if options is not None else _EVENT_OPTIONS,
                "current_hp": 70,
                "max_hp": 80,
                "gold": 50,
            }
        },
    }
    snapshot["action_registry"] = STS2ActionRegistry().build(snapshot)
    return snapshot


def _event_operation(option_index: int = 1) -> dict[str, Any]:
    return {
        "action_type": "choose_event_option",
        "kwargs": {"option_index": option_index},
        "source": "heuristic",
        "reason": "event_preference_or_default",
    }


# ---- 1. serious 零影响 ---------------------------------------------------------------


@pytest.mark.unit
def test_serious_returns_the_planner_result_verbatim() -> None:
    """同一对象、不复制、不改写、不产生扰动信息。"""
    service, rng = _service(0.0)
    service._state.play_style = PLAY_STYLE_SERIOUS
    snapshot = _event_snapshot()
    operation = _event_operation()

    out, info = service._apply_play_style(snapshot, operation)

    assert out is operation
    assert info == {}
    assert operation["kwargs"] == {"option_index": 1}
    assert rng.calls == 0, "serious 档不该掷任何随机数"


@pytest.mark.unit
def test_serious_never_even_computes_the_candidate_ranking(monkeypatch: pytest.MonkeyPatch) -> None:
    """这是"零影响"的硬保证：serious 下连候选排序都不算（不是"算了但没换"）。"""
    service, _rng = _service(0.0)
    service._state.play_style = PLAY_STYLE_SERIOUS
    calls: list[Any] = []
    original = service._planner.ordered_candidates

    def spy(context, operation):
        calls.append(operation)
        return original(context, operation)

    monkeypatch.setattr(service._planner, "ordered_candidates", spy)
    service._apply_play_style(_event_snapshot(), _event_operation())

    assert calls == []


@pytest.mark.unit
def test_serious_probability_is_zero_and_unknown_style_degrades_to_serious() -> None:
    assert perturbation_probability(PLAY_STYLE_SERIOUS) == 0.0
    assert normalize_play_style("胡说八道") == PLAY_STYLE_SERIOUS
    assert normalize_play_style(None) == PLAY_STYLE_SERIOUS
    assert perturbation_probability("胡说八道") == 0.0


# ---- 2. 档位别名与概率 ---------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("serious", PLAY_STYLE_SERIOUS),
        ("认真", PLAY_STYLE_SERIOUS),
        ("认真打", PLAY_STYLE_SERIOUS),
        ("好好打", PLAY_STYLE_SERIOUS),
        ("我要通关", PLAY_STYLE_SERIOUS),
        ("mischievous", PLAY_STYLE_MISCHIEVOUS),
        ("捣蛋", PLAY_STYLE_MISCHIEVOUS),
        ("闹着玩", PLAY_STYLE_MISCHIEVOUS),
        ("别太认真", PLAY_STYLE_MISCHIEVOUS),
        ("silly", PLAY_STYLE_SILLY),
        ("笨蛋", PLAY_STYLE_SILLY),
        ("你傻啦", PLAY_STYLE_SILLY),
        ("随便打打", PLAY_STYLE_SILLY),
        # 未知取值必须退化为 serious（写错配置不能让它变傻）
        ("???", PLAY_STYLE_SERIOUS),
        ("", PLAY_STYLE_SERIOUS),
    ],
)
def test_play_style_aliases(raw: str, expected: str) -> None:
    assert normalize_play_style(raw) == expected


@pytest.mark.unit
def test_perturbation_probabilities_are_0_20_60_percent() -> None:
    assert perturbation_probability(PLAY_STYLE_SERIOUS) == 0.0
    assert perturbation_probability(PLAY_STYLE_MISCHIEVOUS) == pytest.approx(0.2)
    assert perturbation_probability(PLAY_STYLE_SILLY) == pytest.approx(0.6)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("style", "roll", "expected", "reason"),
    [
        (PLAY_STYLE_SERIOUS, 0.0, "a", "serious"),
        (PLAY_STYLE_SERIOUS, 0.99, "a", "serious"),
        (PLAY_STYLE_MISCHIEVOUS, 0.0, "b", "mischievous_second"),
        (PLAY_STYLE_MISCHIEVOUS, 0.19, "b", "mischievous_second"),
        (PLAY_STYLE_MISCHIEVOUS, 0.2, "a", "kept_best"),  # roll >= 概率 -> 不扰动
        (PLAY_STYLE_MISCHIEVOUS, 0.99, "a", "kept_best"),
        (PLAY_STYLE_SILLY, 0.0, "c", "silly_worst"),
        (PLAY_STYLE_SILLY, 0.59, "c", "silly_worst"),
        (PLAY_STYLE_SILLY, 0.6, "a", "kept_best"),
        (PLAY_STYLE_SILLY, 0.99, "a", "kept_best"),
    ],
)
def test_choose_candidate_is_a_pure_function_of_the_roll(
    style: str, roll: float, expected: str, reason: str
) -> None:
    assert choose_candidate(["a", "b", "c"], style=style, roll=roll) == (expected, reason)


@pytest.mark.unit
def test_choose_candidate_degrades_gracefully() -> None:
    # 只有一个候选：扰动也换不了，退回它本身。
    assert choose_candidate(["only"], style=PLAY_STYLE_SILLY, roll=0.0) == ("only", "no_alternative")
    # 没有候选：交给调用方放弃扰动。
    assert choose_candidate([], style=PLAY_STYLE_SILLY, roll=0.0) == (None, "no_candidates")


# ---- 3. 落点与合法性 ----------------------------------------------------------------


@pytest.mark.unit
def test_mischievous_swaps_for_the_runner_up() -> None:
    service, _rng = _service(0.0)  # 0.0 < 0.2 -> 扰动
    service._state.play_style = PLAY_STYLE_MISCHIEVOUS
    snapshot = _event_snapshot()

    out, info = service._apply_play_style(snapshot, _event_operation(1))

    assert info["applied"] is True
    assert info["reason"] == "mischievous_second"
    assert out["kwargs"]["option_index"] == 2  # 排序 [1, 2, 0] 的第 1 个
    assert service._action_engine.validate(snapshot, out) is not None


@pytest.mark.unit
def test_silly_swaps_for_the_worst() -> None:
    service, _rng = _service(0.0)  # 0.0 < 0.6 -> 扰动
    service._state.play_style = PLAY_STYLE_SILLY
    snapshot = _event_snapshot()

    out, info = service._apply_play_style(snapshot, _event_operation(1))

    assert info["applied"] is True
    assert info["reason"] == "silly_worst"
    assert out["kwargs"]["option_index"] == 0  # 排序 [1, 2, 0] 的最后一个
    assert service._action_engine.validate(snapshot, out) is not None


@pytest.mark.unit
@pytest.mark.parametrize(("style", "roll"), [(PLAY_STYLE_MISCHIEVOUS, 0.5), (PLAY_STYLE_SILLY, 0.9)])
def test_high_roll_keeps_the_best(style: str, roll: float) -> None:
    service, _rng = _service(roll)
    service._state.play_style = style
    snapshot = _event_snapshot()

    out, info = service._apply_play_style(snapshot, _event_operation(1))

    assert info["applied"] is False
    assert info["reason"] == "kept_best"
    assert out["kwargs"]["option_index"] == 1


@pytest.mark.unit
def test_perturbed_operation_stays_legal_and_is_never_out_of_range() -> None:
    """把顺序里每一个候选都试一遍，全部必须落在合法下标集合内。"""
    service, _rng = _service(0.0)
    service._state.play_style = PLAY_STYLE_SILLY
    snapshot = _event_snapshot()
    context = {
        "snapshot": snapshot,
        "classification": snapshot["classification"],
        "summary_context": snapshot["summary_context"],
        "strategy_context": snapshot["strategy_context"],
    }
    legal = set(service._planner.legal_option_indices(context, _event_operation(1)))
    ordered = service._planner.ordered_candidates(context, _event_operation(1))

    assert ordered[0] == 1
    assert set(ordered) <= legal
    # 要求"换成最优自己"等于没换，必须返回 None（调用方据此放弃扰动）。
    assert service._planner.build_perturbed_operation(context, _event_operation(1), 1) is None
    for candidate in ordered[1:]:
        built = service._planner.build_perturbed_operation(context, _event_operation(1), candidate)
        assert built is not None
        assert service._action_engine.validate(snapshot, built) is not None


@pytest.mark.unit
def test_no_legal_alternative_keeps_the_best_instead_of_failing() -> None:
    """只有一个选项：扰动无处可去，必须**退回最优**而不是崩掉或返回空。"""
    service, _rng = _service(0.0)
    service._state.play_style = PLAY_STYLE_SILLY
    snapshot = _event_snapshot(options=[{"index": 0, "text": "gain relic"}])

    out, info = service._apply_play_style(snapshot, _event_operation(0))

    assert info["applied"] is False
    assert info["reason"] == "no_alternative"
    assert out["kwargs"]["option_index"] == 0
    assert service._action_engine.validate(snapshot, out) is not None


@pytest.mark.unit
def test_operation_without_an_index_is_left_alone() -> None:
    """proceed / claim_reward 这类没有"选第几个"的动作不参与扰动。"""
    service, _rng = _service(0.0)
    service._state.play_style = PLAY_STYLE_SILLY
    snapshot = _event_snapshot()
    operation = {"action_type": "proceed", "kwargs": {}, "source": "heuristic", "reason": "x"}

    out, info = service._apply_play_style(snapshot, operation)

    assert out is operation
    assert info["applied"] is False
    assert info["reason"] == "no_alternative"


# ---- 覆盖范围：每个决策屏都能给出候选 ----------------------------------------------------


def _generic_snapshot(
    *, state_name: str, action_type: str, payload: dict[str, Any], raw_state: dict[str, Any] | None = None
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "screen": state_name.upper(),
        "available_actions": [{"type": action_type, "raw": {"name": action_type, "requires_index": True}}],
        "classification": {"state_name": state_name},
        "strategy_context": {"preferences": {}},
        "summary_context": {"payload": payload},
        "raw_state": raw_state or {},
    }
    snapshot["action_registry"] = STS2ActionRegistry().build(snapshot)
    return snapshot


def _context_of(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "snapshot": snapshot,
        "classification": snapshot["classification"],
        "summary_context": snapshot["summary_context"],
        "strategy_context": snapshot["strategy_context"],
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("state_name", "action_type", "payload", "raw_state"),
    [
        # 有专门评分 -> 按该屏原有分数排序
        (
            "event",
            "choose_event_option",
            {"event_options": _EVENT_OPTIONS, "current_hp": 70, "max_hp": 80, "gold": 50},
            None,
        ),
        (
            "map",
            "choose_map_node",
            {
                "travelable_nodes": [
                    {"index": 0, "node_type": "Monster"},
                    {"index": 1, "node_type": "Rest"},
                ],
                "current_hp": 70,
                "max_hp": 80,
                "gold": 50,
            },
            None,
        ),
        (
            "reward",
            "choose_reward_card",
            {"reward_cards": [{"index": 0, "name": "Strike"}, {"index": 1, "name": "Bash"}], "deck": {}},
            None,
        ),
        # 没有专门评分 -> 退化为"最优 + 其它合法下标"
        ("rest", "choose_rest_option", {"rest_options": [{"index": 0}, {"index": 1}]}, None),
        ("shop", "buy_card", {"shop_cards": [{"index": 0}, {"index": 1}, {"index": 2}]}, None),
        ("card_selection", "select_deck_card", {"selection_cards": [{"index": 0}, {"index": 1}]}, None),
        ("chest", "choose_treasure_relic", {}, {"chest": {"relic_options": [{"index": 0}, {"index": 1}]}}),
        ("bundle_selection", "choose_bundle", {}, {"bundles": [{"index": 0}, {"index": 1}]}),
    ],
)
def test_every_decision_screen_offers_at_least_one_alternative(
    state_name: str, action_type: str, payload: dict[str, Any], raw_state: dict[str, Any] | None
) -> None:
    service, _rng = _service(0.0)
    snapshot = _generic_snapshot(state_name=state_name, action_type=action_type, payload=payload, raw_state=raw_state)
    operation = {"action_type": action_type, "kwargs": {"option_index": 0}}

    ordered = service._planner.ordered_candidates(_context_of(snapshot), operation)

    assert ordered[0] == 0, "第 0 个必须是最优本身"
    assert len(ordered) >= 2, f"{state_name} 屏应当至少有一个合法替代"
    # 每一个候选都必须是合法下标（不可选/越界的不会进来）
    legal = set(service._planner.legal_option_indices(_context_of(snapshot), operation))
    assert set(ordered) <= legal


@pytest.mark.unit
def test_capstone_screen_uses_the_planners_own_sweep_range() -> None:
    """capstone 在 payload 里没有列表，只有按钮；规划器自己的游标就扫 0.._MAX_CAPSTONE_SWEEP-1。"""
    service, _rng = _service(0.0)
    snapshot = _generic_snapshot(state_name="capstone_selection", action_type="choose_capstone_option", payload={})
    operation = {"action_type": "choose_capstone_option", "kwargs": {"option_index": 0}}

    ordered = service._planner.ordered_candidates(_context_of(snapshot), operation)

    assert ordered[0] == 0
    assert ordered == list(range(service._planner._MAX_CAPSTONE_SWEEP))


@pytest.mark.unit
def test_unselectable_items_are_excluded_from_the_candidates() -> None:
    """买不起/不可选的商品不能成为扰动候选（否则扰动会发出一个被游戏拒绝的动作）。"""
    service, _rng = _service(0.0)
    snapshot = _generic_snapshot(
        state_name="shop",
        action_type="buy_card",
        payload={"shop_cards": [{"index": 0, "is_enabled": True}, {"index": 1, "is_enabled": False}]},
    )
    operation = {"action_type": "buy_card", "kwargs": {"option_index": 0}}

    ordered = service._planner.ordered_candidates(_context_of(snapshot), operation)

    assert ordered == [0], "没有可选的替代，就只剩最优自己（= 放弃扰动）"


# ---- 战斗：目标必须跟着新卡走 ----------------------------------------------------------


_COMBAT_ACTION = {"type": "play_card", "raw": {"name": "play_card", "requires_index": True}}
_COMBAT_HAND = [
    {"index": 0, "playable": True, "valid_target_indices": [0], "name": "Strike"},
    {"index": 1, "playable": True, "valid_target_indices": [1], "name": "Bash"},
    {"index": 2, "playable": False, "valid_target_indices": [0], "name": "Curse"},
]
_COMBAT_ENEMIES = [
    {"current_hp": 20, "intent_damage": 5},
    {"current_hp": 30, "intent_damage": 9},
]


def _combat_snapshot() -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "screen": "COMBAT",
        "available_actions": [_COMBAT_ACTION],
        "classification": {"state_name": "combat"},
        "strategy_context": {"preferences": {}},
        "summary_context": {"payload": {}},
        "raw_state": {
            "combat": {
                "hand": [dict(card) for card in _COMBAT_HAND],
                "enemies": [dict(enemy) for enemy in _COMBAT_ENEMIES],
            }
        },
    }
    snapshot["action_registry"] = STS2ActionRegistry().build(snapshot)
    return snapshot


def _combat_operation() -> dict[str, Any]:
    return {"action_type": "play_card", "kwargs": {"card_index": 0, "target_index": 0}, "source": "heuristic", "reason": "play"}


@pytest.mark.unit
def test_combat_candidates_exclude_unplayable_cards() -> None:
    service, _rng = _service(0.0)
    snapshot = _combat_snapshot()
    context = {
        "snapshot": snapshot,
        "classification": snapshot["classification"],
        "summary_context": snapshot["summary_context"],
        "strategy_context": snapshot["strategy_context"],
    }
    ordered = service._planner.ordered_candidates(context, _combat_operation())
    assert ordered[0] == 0
    assert 2 not in ordered, "不可打出的牌不能成为扰动候选"
    assert ordered == [0, 1]


@pytest.mark.unit
def test_combat_perturbation_retargets_the_new_card() -> None:
    """目标必须重算：沿用旧卡的目标可能超出新卡的 valid_target_indices（= 非法动作）。"""
    service, _rng = _service(0.0)
    service._state.play_style = PLAY_STYLE_SILLY
    snapshot = _combat_snapshot()

    out, info = service._apply_play_style(snapshot, _combat_operation())

    assert info["applied"] is True
    assert out["kwargs"]["card_index"] == 1
    assert out["kwargs"]["target_index"] == 1, "换卡必须连带换目标"
    assert service._action_engine.validate(snapshot, out) is not None


@pytest.mark.unit
def test_build_perturbed_operation_rejects_an_unplayable_card() -> None:
    """直接要求换成不可打出的牌时必须返回 None（调用方据此放弃扰动）。"""
    service, _rng = _service(0.0)
    snapshot = _combat_snapshot()
    context = {
        "snapshot": snapshot,
        "classification": snapshot["classification"],
        "summary_context": snapshot["summary_context"],
        "strategy_context": snapshot["strategy_context"],
    }
    assert service._planner.build_perturbed_operation(context, _combat_operation(), 2) is None


# ---- 4. 接线：自动一步会被扰动，显式授权不会 --------------------------------------------


def _wire_autoplay(service: STS2AutoplayService, snapshot: dict[str, Any]) -> dict[str, Any]:
    """把 service 摆成"可以走一步"的状态，并记录真正被执行的操作。"""
    service._state.snapshot = snapshot
    service._state.interrupt_requested = False
    executed: dict[str, Any] = {}

    async def fake_execute(operation: Any) -> dict[str, Any]:
        executed["operation"] = operation
        return {"status": "ok"}

    service.execute_operation = fake_execute  # type: ignore[method-assign]
    return executed


@pytest.mark.unit
def test_autoplay_step_perturbs_and_reports_it() -> None:
    service, _rng = _service(0.0)
    service._state.play_style = PLAY_STYLE_SILLY
    snapshot = _event_snapshot()
    snapshot["agent_operation"] = _event_operation(1)
    executed = _wire_autoplay(service, snapshot)

    result = asyncio.run(service.run_autoplay_step())

    assert result.get("perturbation", {}).get("applied") is True
    assert executed["operation"]["kwargs"]["option_index"] == 0


@pytest.mark.unit
def test_explicit_authorisation_is_not_perturbed() -> None:
    """``sts2_execute_planned_operation`` 是玩家亲口点的，不能被换成别的。"""
    service, rng = _service(0.0)
    service._state.play_style = PLAY_STYLE_SILLY
    snapshot = _event_snapshot()
    snapshot["agent_operation"] = _event_operation(1)
    executed = _wire_autoplay(service, snapshot)

    result = asyncio.run(service.run_autoplay_step(force=True))

    assert "perturbation" not in result
    assert executed["operation"]["kwargs"]["option_index"] == 1
    assert rng.calls == 0


@pytest.mark.unit
def test_serious_autoplay_step_executes_the_planners_choice() -> None:
    service, _rng = _service(0.0)
    service._state.play_style = PLAY_STYLE_SERIOUS
    snapshot = _event_snapshot()
    snapshot["agent_operation"] = _event_operation(1)
    executed = _wire_autoplay(service, snapshot)

    result = asyncio.run(service.run_autoplay_step())

    assert "perturbation" not in result
    assert executed["operation"]["kwargs"]["option_index"] == 1


@pytest.mark.unit
def test_set_play_style_normalizes_and_exposes_it() -> None:
    service, _rng = _service(0.0)
    result = service.set_play_style("笨蛋")
    assert result["play_style"] == PLAY_STYLE_SILLY
    assert service._state.play_style == PLAY_STYLE_SILLY

    result = service.set_play_style("胡说八道")
    assert result["play_style"] == PLAY_STYLE_SERIOUS
