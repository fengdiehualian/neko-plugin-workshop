"""地图选路的**链式递推**（lookahead）测试。

背景：旧代码其实也写了"前瞻"（`_score_map_node` 里累加 `_collect_route_descendants`），但它是**假前瞻**：

* 交给规划器的 `available_nodes`（mod 的 map.available_nodes）**没有 children 字段**；
* 就算读全图 `map.nodes`，它的 `children/parents` 是 `{row,col}` **坐标对、没有 node_type**，
  而旧代码取的是 `descendant.get("node_type")` —— 取出来是空串，权重 0。

于是前瞻恒等于 0，实际只是"只看眼前一步"的贪心。更糟的是节点类型对不上：mod 下发的是游戏枚举名
`RestSite`，而权重表的键是 `rest`，`"restsite"` 永远匹配不到 —— **休息点权重恒为 0**，
加上 `_preferred_map_index` 过去要求"分数 > 0 才采纳"，猫娘根本不可能主动去营地回血。

这一组测试把新的递归评估钉死：坐标要能解出类型、分支取 max、同类收益饱和、逐层折扣、
临门（boss 前）加权、精英随血量缓和，并且**拿不到图时必须退回原来的单步评分**。
"""

from __future__ import annotations

import pytest
from plugin.plugins.sts2_autoplay.heuristic_planner import (
    ROUTE_BOSS_PREP_MULTIPLIER,
    ROUTE_DISCOUNT_PER_ROW,
    ROUTE_REPEAT_DECAY,
    STS2HeuristicPlanner,
)


def _node(row, col, node_type, children=(), **extra):
    node = {
        "row": row,
        "col": col,
        "node_type": node_type,
        "children": [{"row": r, "col": c} for r, c in children],
    }
    node.update(extra)
    return node


def _graph() -> dict:
    """一条"两条分支未来完全不同"的图。

    row1 的两个候选**都是 Monster**（单步评分完全相同、旧逻辑只能靠下标分胜负），
    但左边通向 RestSite->Treasure，右边通向 Elite->Elite。真正的路线评估必须选左边。
    """
    nodes = [
        _node(0, 1, "Ancient", [(1, 0), (1, 2)], is_start=True),
        _node(1, 0, "Monster", [(2, 0)]),
        _node(1, 2, "Monster", [(2, 2)]),
        _node(2, 0, "RestSite", [(3, 0)]),
        _node(2, 2, "Elite", [(3, 2)]),
        _node(3, 0, "Treasure", [(4, 1)]),
        _node(3, 2, "Elite", [(4, 1)]),
        _node(4, 1, "Boss", [], is_boss=True),
    ]
    return {"nodes": nodes, "rows": 5, "boss_node": {"row": 4, "col": 1}}


def _snapshot_with_graph(graph=None) -> dict:
    game_map = {"nodes": (graph or _graph())["nodes"], "available_nodes": [], "rows": 5, "boss_node": {"row": 4, "col": 1}}
    return {"screen": "MAP", "raw_state": {"map": game_map}}


def _map_context(*, gold=0, hp=80, max_hp=80, candidates=None) -> dict:
    candidates = candidates if candidates is not None else [
        # 注意顺序：**index 0 是那条通向精英的坏路**，旧逻辑同分取小下标会选它。
        {"index": 0, "row": 1, "col": 2, "node_type": "Monster", "state": "Travelable"},
        {"index": 1, "row": 1, "col": 0, "node_type": "Monster", "state": "Travelable"},
    ]
    return {
        "classification": {"state_name": "map"},
        "summary_context": {
            "payload": {
                "travelable_nodes": candidates,
                "available_nodes": candidates,
                "current_hp": hp,
                "max_hp": max_hp,
                "gold": gold,
                "is_multiplayer": False,
            }
        },
        "strategy_context": {"preferences": {}},
        "snapshot": {
            "screen": "MAP",
            "available_actions": [{"type": "choose_map_node", "raw": {"name": "choose_map_node", "requires_index": True}}],
        },
    }


# ---- 核心：递归真的改变了选择 -----------------------------------------------------------


@pytest.mark.unit
def test_recursion_picks_the_branch_with_the_better_future() -> None:
    """两个候选单步分完全相同，只有看到后面才能分出高下 —— 必须选通向休息+宝箱的那条。"""
    planner = STS2HeuristicPlanner()
    context = _map_context()
    context["snapshot"].update(_snapshot_with_graph())

    operation = planner.plan(context)

    assert operation is not None and operation.action_type == "choose_map_node"
    assert operation.kwargs["option_index"] == 1, "应选通向 RestSite->Treasure 的那条（index 1）"


@pytest.mark.unit
def test_single_step_scoring_cannot_tell_the_two_branches_apart() -> None:
    """反证：单步评分对两个候选给出同一个分数（旧逻辑只能靠下标分胜负，必然选错）。"""
    planner = STS2HeuristicPlanner()
    payload = _map_context()["summary_context"]["payload"]
    left, right = payload["travelable_nodes"][1], payload["travelable_nodes"][0]

    assert planner._score_map_node(left, {}, 1.0, 0) == planner._score_map_node(right, {}, 1.0, 0)


@pytest.mark.unit
def test_route_score_matches_hand_computed_value() -> None:
    """金样例：手算 (1,0) 分支 = 3 + γ*(24 + γ*5) = 25.4（γ=0.8，临门休息 ×2）。"""
    planner = STS2HeuristicPlanner()
    graph = _graph()

    score = planner.evaluate_map_route(graph, {"row": 1, "col": 0}, hp_ratio=1.0, gold=0)

    expected = 3 + ROUTE_DISCOUNT_PER_ROW * (12 * ROUTE_BOSS_PREP_MULTIPLIER + ROUTE_DISCOUNT_PER_ROW * 5)
    assert score == pytest.approx(expected)
    assert score == pytest.approx(25.4)


# ---- 节点类型必须能对上（mod 发的是游戏枚举名）------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_type", "weight_key"),
    [
        ("RestSite", "rest"),
        ("Monster", "monster"),
        ("Elite", "elite"),
        ("Shop", "shop"),
        ("Treasure", "treasure"),
        ("Unknown", "question"),
        ("Event", "event"),
        ("Boss", "boss"),
    ],
)
def test_mod_node_type_maps_to_weight_key(raw_type: str, weight_key: str) -> None:
    assert STS2HeuristicPlanner._map_node_weight_key({"node_type": raw_type}) == weight_key


@pytest.mark.unit
def test_rest_site_is_no_longer_worthless() -> None:
    """`RestSite` 与 `rest` 拼不上时休息点恒为 0 分，怪物（3 分）永远压过它 —— 猫娘从不回血。"""
    planner = STS2HeuristicPlanner()

    rest = planner._score_map_node({"node_type": "RestSite"}, {}, 0.5, 0)
    monster = planner._score_map_node({"node_type": "Monster"}, {}, 0.5, 0)

    assert rest > 0, "休息点必须有正的权重"
    assert rest > monster, "血少时休息点应压过普通怪"


@pytest.mark.unit
def test_low_hp_switches_to_the_rest_weighted_table_even_without_player_config() -> None:
    """低血替换现在对自动选路也生效（过去只有玩家手点的 safe 路线才带 conditions）。"""
    planner = STS2HeuristicPlanner()

    healthy = planner._score_map_node({"node_type": "RestSite"}, {}, 0.9, 0)
    hurt = planner._score_map_node({"node_type": "RestSite"}, {}, 0.2, 0)

    assert hurt > healthy


# ---- 饱和 / 折扣 / 临门 / 精英缓和 ------------------------------------------------------


@pytest.mark.unit
def test_repeat_decay_makes_the_second_rest_worth_less() -> None:
    """两个休息点 ≠ 两倍休息：同类收益要饱和。"""
    planner = STS2HeuristicPlanner()
    two_rests = {
        "nodes": [_node(2, 0, "RestSite", [(3, 0)]), _node(3, 0, "RestSite", [])],
        "boss_node": {"row": 9, "col": 0},
    }
    one_rest = {
        "nodes": [_node(2, 0, "RestSite", [])],
        "boss_node": {"row": 9, "col": 0},
    }

    both = planner.evaluate_map_route(two_rests, {"row": 2, "col": 0}, hp_ratio=0.3, gold=0)
    single = planner.evaluate_map_route(one_rest, {"row": 2, "col": 0}, hp_ratio=0.3, gold=0)

    assert both is not None and single is not None
    assert both < single * 2, "第二个休息点必须打折"
    assert both > single, "但多一个休息点仍然更好"
    assert ROUTE_REPEAT_DECAY < 1.0


@pytest.mark.unit
def test_rest_adjacent_to_the_boss_beats_a_far_one() -> None:
    """临门一脚：紧邻 boss 的休息点价值远高于中途的。"""
    planner = STS2HeuristicPlanner()
    near = {"nodes": [_node(7, 0, "RestSite", []), _node(8, 0, "Boss", [], is_boss=True)], "boss_node": {"row": 8, "col": 0}}
    far = {"nodes": [_node(2, 0, "RestSite", []), _node(8, 0, "Boss", [], is_boss=True)], "boss_node": {"row": 8, "col": 0}}

    near_score = planner.evaluate_map_route(near, {"row": 7, "col": 0}, hp_ratio=1.0, gold=0)
    far_score = planner.evaluate_map_route(far, {"row": 2, "col": 0}, hp_ratio=1.0, gold=0)

    assert near_score is not None and far_score is not None
    assert near_score > far_score


@pytest.mark.unit
def test_boss_prep_shop_needs_gold() -> None:
    """临门商店要有钱才香：金币不足时不该为它加权。"""
    planner = STS2HeuristicPlanner()
    graph = {"nodes": [_node(7, 0, "Shop", []), _node(8, 0, "Boss", [], is_boss=True)], "boss_node": {"row": 8, "col": 0}}

    poor = planner.evaluate_map_route(graph, {"row": 7, "col": 0}, hp_ratio=1.0, gold=10)
    rich = planner.evaluate_map_route(graph, {"row": 7, "col": 0}, hp_ratio=1.0, gold=300)

    assert poor is not None and rich is not None
    assert rich > poor


@pytest.mark.unit
def test_elite_penalty_is_relieved_at_full_hp_but_kept_when_hurt() -> None:
    """精英惩罚随血量缓和，但**不清零**（满血也不该把精英当免费）。"""
    planner = STS2HeuristicPlanner()
    graph = {"nodes": [_node(2, 0, "Elite", [])], "boss_node": {"row": 9, "col": 0}}

    full = planner.evaluate_map_route(graph, {"row": 2, "col": 0}, hp_ratio=1.0, gold=0)
    hurt = planner.evaluate_map_route(graph, {"row": 2, "col": 0}, hp_ratio=0.3, gold=0)

    assert full is not None and hurt is not None
    assert full < 0, "满血也不能把精英算成好事"
    assert hurt < full, "血少时精英惩罚更重"


# ---- 图的读取与退化 ---------------------------------------------------------------------


@pytest.mark.unit
def test_graph_is_read_from_the_snapshot_map_nodes() -> None:
    planner = STS2HeuristicPlanner()

    assert planner._map_graph_from_snapshot(_snapshot_with_graph())["nodes"]
    assert planner._map_graph_from_snapshot(None) == {}
    assert planner._map_graph_from_snapshot({}) == {}
    assert planner._map_graph_from_snapshot({"raw_state": {"map": {"nodes": []}}}) == {}


@pytest.mark.unit
def test_without_a_graph_it_falls_back_to_single_step_scoring() -> None:
    """拿不到图（旧 mod / 非地图 payload）时必须退回原来的单步评分，行为不变。"""
    planner = STS2HeuristicPlanner()
    context = _map_context()  # snapshot 里**没有** map.nodes

    operation = planner.plan(context)

    assert operation is not None and operation.action_type == "choose_map_node"
    # 单步同分 -> 取小下标（index 0），即旧行为。
    assert operation.kwargs["option_index"] == 0


@pytest.mark.unit
def test_player_directed_safe_route_also_uses_the_lookahead() -> None:
    """玩家手点的"定个路线（safe）"必须走**同一套**评估，而不是退回单步。

    它原本调用 `_preferred_map_index` 时没把现场快照传进去，于是这条路上永远是单步评分 ——
    同一条选路逻辑两套口径，玩家很容易发现"我让它选稳的，它却选了眼前分高的"。
    """
    planner = STS2HeuristicPlanner()
    summary_context = _map_context()["summary_context"]

    with_graph = planner.resolve_route(summary_context, mode="safe", snapshot=_snapshot_with_graph())
    without_graph = planner.resolve_route(summary_context, mode="safe")

    assert with_graph["ok"] is True
    assert with_graph["option_index"] == 1, "带图时应评估整条路线（通向 RestSite->Treasure）"
    assert without_graph["option_index"] == 0, "不带图退回单步评分（同分取小下标）"


@pytest.mark.unit
def test_children_coordinates_are_resolved_through_the_index() -> None:
    """children 是坐标对，必须靠索引解出类型，否则权重全是 0（这就是"假前瞻"）。"""
    planner = STS2HeuristicPlanner()
    graph = _graph()
    index = planner._map_graph_index(graph)

    assert index[(2, 0)]["node_type"] == "RestSite"
    assert planner._map_child_keys(index[(1, 0)]) == [(2, 0)]


@pytest.mark.unit
def test_malformed_cycle_does_not_hang() -> None:
    """图数据畸形（子节点指回祖先）时不能无限递归。"""
    planner = STS2HeuristicPlanner()
    graph = {
        "nodes": [
            _node(2, 0, "Monster", [(3, 0)]),
            _node(3, 0, "Monster", [(2, 0)]),  # 指回上面
        ],
        "boss_node": {"row": 9, "col": 0},
    }

    score = planner.evaluate_map_route(graph, {"row": 2, "col": 0}, hp_ratio=1.0, gold=0)

    assert score is not None and score >= 0


@pytest.mark.unit
def test_candidate_outside_the_graph_returns_none() -> None:
    """候选不在图里（坐标对不上）时返回 None，交给调用方退回单步评分。"""
    planner = STS2HeuristicPlanner()

    assert planner.evaluate_map_route(_graph(), {"row": 99, "col": 99}, hp_ratio=1.0, gold=0) is None
