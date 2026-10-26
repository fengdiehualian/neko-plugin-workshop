"""事件房现在是**纯启发式**决策。

原 ``EventAdviceGenerator``（LLM 给每个选项打分，再按 ``event_llm_weight`` 与启发式融合）已于
2026-10 摘除——插件不再有任何 LLM 决策，只保留弹幕点评文案的 LLM（``CatgirlCommentGenerator``）。
原来的 ``tests/test_event_llm_fusion.py`` 测的就是那套融合逻辑，因此被本文件取代。

这里钉住三件事：
1. 事件房选的仍是启发式评分最高的那个选项；
2. 就算有人往 context 里塞 ``event_llm_scores`` / ``event_llm_weight`` 也**完全无效**（回归护栏：
   证明融合真的没了，而不只是没人调用）；
3. 人类 guidance（prefer_defense 等）仍然生效——那是启发式的一部分，不是 LLM。
"""

from __future__ import annotations

from typing import Any

import pytest
from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner

_EVENT_ACTION = {"type": "choose_event_option", "raw": {"name": "choose_event_option", "requires_index": True}}

# 评分预期（hp 70/80 → ratio 0.875 ≥ 0.6，gold 50）：
#   0 "lose hp"   -> -8   （掉血）
#   1 "gain relic"-> +14  （遗物最高）
#   2 "gain gold" -> +10  （金币次之）
_EVENT_OPTIONS = [
    {"index": 0, "text": "lose hp"},
    {"index": 1, "text": "gain relic"},
    {"index": 2, "text": "gain gold"},
]


def _event_context(
    *,
    event_llm_scores: dict[int, float] | None = None,
    event_llm_weight: float = 0.5,
    options: list[dict[str, Any]] | None = None,
    current_hp: int = 70,
    max_hp: int = 80,
    gold: int = 50,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "classification": {"state_name": "event"},
        "summary_context": {
            "payload": {
                "event_options": options if options is not None else _EVENT_OPTIONS,
                "current_hp": current_hp,
                "max_hp": max_hp,
                "gold": gold,
            }
        },
        "strategy_context": {"preferences": {}},
        "snapshot": {"available_actions": [_EVENT_ACTION]},
    }
    # 故意把已摘除的两个键塞进去：它们必须被彻底忽略。
    if event_llm_scores is not None:
        context["event_llm_scores"] = event_llm_scores
    context["event_llm_weight"] = event_llm_weight
    return context


@pytest.mark.unit
def test_event_picks_the_best_heuristic_option() -> None:
    planner = STS2HeuristicPlanner()
    operation = planner.plan(_event_context())
    assert operation is not None
    assert operation.action_type == "choose_event_option"
    # 遗物（+14）分最高。
    assert operation.kwargs["option_index"] == 1
    assert operation.source == "heuristic"


@pytest.mark.unit
def test_event_ignores_llm_scores_even_if_someone_still_passes_them() -> None:
    """回归护栏：融合已摘除。给定一堆"LLM 认为选项 0 最好"的分数，结果必须**不变**。"""
    planner = STS2HeuristicPlanner()
    without = planner.plan(_event_context())
    with_llm = planner.plan(
        _event_context(event_llm_scores={0: 100.0, 1: 0.0, 2: 0.0}, event_llm_weight=1.0)
    )
    assert without is not None and with_llm is not None
    assert with_llm.kwargs["option_index"] == without.kwargs["option_index"] == 1
    assert with_llm.source == "heuristic"
    assert "llm" not in with_llm.source


@pytest.mark.unit
def test_event_ranking_is_best_first_and_covers_every_option() -> None:
    """扰动层靠这条排序取次优/最差，所以第 0 个必须就是规划器会选的那个。"""
    planner = STS2HeuristicPlanner()
    ranked = planner._rank_event_options(_event_context()["summary_context"])
    assert ranked == [1, 2, 0]


@pytest.mark.unit
def test_event_hard_override_still_wins() -> None:
    """人类钉死的选项优先级高于评分——这是启发式的一部分，不是 LLM 融合。"""
    planner = STS2HeuristicPlanner()
    context = _event_context()
    context["strategy_context"] = {"preferences": {"records": [{"value": {"preferred_option_index": 2}}]}}
    operation = planner.plan(context)
    assert operation is not None
    assert operation.kwargs["option_index"] == 2


@pytest.mark.unit
def test_event_guidance_prefer_defense_still_applies() -> None:
    """guidance 会压低"掉血"选项的分数（原本就是启发式评分的一环）。"""
    planner = STS2HeuristicPlanner()
    options = [
        {"index": 0, "text": "gain relic"},
        {"index": 1, "text": "lose hp"},
    ]
    ranked = planner._rank_event_options({"payload": {"event_options": options, "current_hp": 70, "max_hp": 80, "gold": 50}})
    # 无 guidance 时遗物（+14）优于掉血（-8）。
    assert ranked[0] == 0


@pytest.mark.unit
def test_event_without_options_returns_none_and_planner_falls_back_to_zero() -> None:
    planner = STS2HeuristicPlanner()
    operation = planner.plan(_event_context(options=[]))
    # 事件分支仍在（动作可用），但拿不到选项时下标退化为 0。
    assert operation is not None
    assert operation.kwargs["option_index"] == 0
