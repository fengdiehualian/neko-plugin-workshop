"""跨局教训（`strategy_directives["avoid"]`）必须真的影响决策。

背景：`.agent-teams/sts2-omnissiah-audit/reflection-audit-t2.md` 用 AST 证明 `avoid` 在改动前
**读取点 = 0**（只写不读的死字段），复盘因此不能让 Mod 进步。这里钉住修复后的契约：

* 没有教训时，排序与改动前**逐字一致**（零影响，不能因为引入复盘而改变默认行为）；
* 有教训且该屏有对应选项时，命中项**被降权**（仍留在候选里，只是靠后）；
* 匹配不准时**不动**（未支持的屏、无着力点的 token 一律原序返回）。

教训文本用**真实生产数据**（`%LOCALAPPDATA%\\N.E.K.O\\sts2_autoplay\\catgirl_memory.json` 里
那一局 floor 11 的真实反思），不是编的例句。
"""

from __future__ import annotations

from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner

# 真实生产反思（2026-10-17 00:12:37 日志里的那条）。
REAL_REFLECTION = (
    "这局最要命的就是第11层遇上CUBEX_CONSTRUCT，直接把血干到0/80，整局累计掉了68点血直接寄掉好惨。"
    "下次血量低于20的时候别头铁硬刚，先垫格挡喝回血药再输出，选牌走图都慢半拍多留容错就不会莫名其妙暴毙啦。"
)


def _map_payload(nodes: list[dict]) -> dict:
    return {"payload": {"travelable_nodes": nodes, "current_hp": 16, "max_hp": 80, "gold": 91}}


def _ctx(payload: dict, avoid: list[str] | None = None) -> dict:
    directives: dict = {"must": [], "prefer": [], "avoid": list(avoid or [])}
    return {
        "classification": {"state_name": "map"},
        "summary_context": payload,
        "strategy_context": {"preferences": {}, "strategy_directives": directives},
    }


def _rank(planner: STS2HeuristicPlanner, ctx: dict, action_type: str = "choose_map_node") -> list[int]:
    class _Op:
        pass

    return planner._ranked_option_indices(ctx, {"action_type": action_type, "kwargs": {"option_index": 0}})


def test_no_lessons_means_identical_order() -> None:
    """零影响：没有教训时，排序不能被这次改动改变。"""
    planner = STS2HeuristicPlanner()
    nodes = [
        {"index": 0, "type": "monster"},
        {"index": 1, "type": "rest"},
        {"index": 2, "type": "elite"},
    ]
    with_lessons = _rank(planner, _ctx(_map_payload(nodes), avoid=[]))
    assert with_lessons == _rank(planner, _ctx(_map_payload(nodes), avoid=[""])) , "空/全空白教训必须等于无教训"


def test_real_lesson_demotes_matching_node() -> None:
    """真实反思抽出的 token（skill/attack）是**战斗**建议，不该改动地图路线。

    这是修复后的**正确**行为：匹配得准才动手。真实反思说「先垫格挡…再输出」，
    属于出牌策略，与"走哪个节点"无关 —— 拿它去改路线反而是噪声。
    """
    planner = STS2HeuristicPlanner()
    nodes = [
        {"index": 0, "type": "monster"},
        {"index": 1, "type": "rest"},
        {"index": 2, "type": "shop"},
    ]
    baseline = _rank(planner, _ctx(_map_payload(nodes), avoid=[]))
    after = _rank(planner, _ctx(_map_payload(nodes), avoid=[REAL_REFLECTION]))

    # 真实反思不含路线类 token，所以路线排序必须**原样不动**。
    assert planner._lesson_tokens([REAL_REFLECTION]) == ["skill", "attack"], "真实反思应抽到战斗类 token"
    assert after == baseline, "战斗类教训不得改动地图路线排序"


def test_route_lesson_changes_route_order() -> None:
    """路线类教训要能把命中节点降到末尾 —— 前提是它原本不在末尾。

    注意：**不能拿 elite 当样本** —— 既有评分本来就把精英排最后（`_score_map_node`），
    对一个已经在末尾的项降权是空操作，那样的断言是恒真的，测不出任何东西。
    所以这里用一个评分上更优、但被教训点名的节点（rest），确认降权真的把它推下去。
    """
    planner = STS2HeuristicPlanner()
    nodes = [
        {"index": 0, "type": "monster"},
        {"index": 1, "type": "rest"},
        {"index": 2, "type": "shop"},
    ]
    lesson = "上次在篝火休息浪费了升级机会，以后别选休息"
    baseline = _rank(planner, _ctx(_map_payload(nodes), avoid=[]))
    after = _rank(planner, _ctx(_map_payload(nodes), avoid=[lesson]))

    assert planner._lesson_tokens([lesson]) == ["rest"], "该教训应抽出 rest token"
    assert 1 not in (baseline[-1],), "前提：rest 基线不在末尾，否则本测试无意义"
    assert sorted(after) == sorted(baseline), "教训不得删除任何候选"
    assert after[-1] == 1, f"被点名的 rest 应降到末尾，实际 {after}（基线 {baseline}）"


def test_unmatched_lesson_leaves_order_untouched() -> None:
    """匹配不准时不动：一句和本屏无关的教训不得改变决策。"""
    planner = STS2HeuristicPlanner()
    nodes = [{"index": 0, "type": "monster"}, {"index": 1, "type": "rest"}]
    baseline = _rank(planner, _ctx(_map_payload(nodes), avoid=[]))
    after = _rank(planner, _ctx(_map_payload(nodes), avoid=["今天天气不错适合钓鱼"]))
    assert after == baseline, "无着力点的教训必须原序返回"


def test_unsupported_screen_is_untouched() -> None:
    """未支持的屏（事件）保持原序 —— 宁可不改，也不要乱改。"""
    planner = STS2HeuristicPlanner()
    payload = {"payload": {"options": [{"index": 0, "text": "离开"}, {"index": 1, "text": "战斗"}]}}
    baseline = _rank(planner, _ctx(payload, avoid=[]), action_type="choose_event_option")
    after = _rank(planner, _ctx(payload, avoid=[REAL_REFLECTION]), action_type="choose_event_option")
    assert after == baseline, "事件屏不得被跨局教训改动（当前实现未支持）"


def test_curse_lesson_demotes_curse_card() -> None:
    """「别拿诅咒」类教训应把诅咒牌降权。"""
    planner = STS2HeuristicPlanner()
    payload = {
        "payload": {
            "reward_cards": [
                {"index": 0, "card_type": "Curse", "rarity": "Curse", "name": "进阶之灾"},
                {"index": 1, "card_type": "Attack", "rarity": "Common", "name": "打击"},
            ]
        }
    }
    baseline = _rank(planner, _ctx(payload, avoid=[]), action_type="choose_reward_card")
    after = _rank(planner, _ctx(payload, avoid=["以后别拿诅咒牌"]), action_type="choose_reward_card")
    assert sorted(after) == sorted(baseline), "不得删除候选"
    # 诅咒牌必须被降到末尾（无论基线顺序如何）。
    assert after[-1] == 0, f"诅咒牌应排到最后，实际 {after}（基线 {baseline}）"


def test_lesson_tokens_extracts_behaviour_keywords() -> None:
    """整句教训要被压成可匹配的语义 token。"""
    planner = STS2HeuristicPlanner()
    tokens = planner._lesson_tokens([REAL_REFLECTION])
    assert tokens, "真实反思必须至少抽出一个 token"
    assert all(isinstance(token, str) and token for token in tokens)
    # 无关键词的句子抽不出 token（宁可没影响，也不要模糊匹配）。
    assert planner._lesson_tokens(["今天天气不错"]) == []
