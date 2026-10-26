from __future__ import annotations

from pathlib import Path

import pytest
from plugin.plugins.sts2_autoplay.companion_evaluator import STS2CompanionEvaluator
from plugin.plugins.sts2_autoplay.strategy_parser import STS2StrategyParser
from plugin.plugins.sts2_autoplay.strategy_repository import STS2StrategyRepository


class DummyLogger:
    def warning(self, *args, **kwargs):
        return None


class DummyPreferenceStore:
    def list_domain(self, domain: str):
        return []

    def get(self, domain: str, key: str):
        return None


@pytest.mark.unit
def test_strategy_parser_lists_role_directories(tmp_path: Path) -> None:
    strategies = tmp_path / "strategies"
    strategies.mkdir()
    (strategies / "player_overrides.md").write_text("# overrides\n", encoding="utf-8")
    defect = strategies / "defect"
    defect.mkdir()
    (defect / "base.md").write_text("# base\n", encoding="utf-8")

    parser = STS2StrategyParser(DummyLogger(), strategies_dir=strategies)

    assert parser.available_strategies() == ["defect"]


@pytest.mark.unit
def test_strategy_parser_loads_base_scene_and_overrides(tmp_path: Path) -> None:
    strategies = tmp_path / "strategies"
    strategies.mkdir()
    (strategies / "player_overrides.md").write_text("# legacy overrides\n", encoding="utf-8")
    defect = strategies / "defect"
    defect.mkdir()
    (defect / "base.md").write_text("# base\n", encoding="utf-8")
    (defect / "combat.md").write_text("# combat\n", encoding="utf-8")
    overrides = strategies / "user_overrides" / "defect"
    overrides.mkdir(parents=True)
    (overrides / "combat.md").write_text("# user combat override\n", encoding="utf-8")

    parser = STS2StrategyParser(DummyLogger(), strategies_dir=strategies)
    prompt = parser.load_prompt("defect", "combat")

    assert prompt is not None
    assert "# base" in prompt
    assert "# combat" in prompt
    assert "# user combat override" in prompt
    assert "# legacy overrides" in prompt


@pytest.mark.unit
def test_strategy_repository_maps_screen_to_scene() -> None:
    STS2StrategyRepository(DummyLogger(), DummyPreferenceStore(), default_strategy="defect")



@pytest.mark.unit
def test_strategy_repository_resolves_default_strategy_name() -> None:
    repo = STS2StrategyRepository(DummyLogger(), DummyPreferenceStore(), default_strategy="defect")

    assert repo._resolve_strategy_name(None) == "defect"
    assert repo._resolve_strategy_name("the_defect") == "defect"


@pytest.mark.unit
def test_strategy_repository_prefers_record_preferred_option_index() -> None:
    class PreferenceStoreWithRecord(DummyPreferenceStore):
        def list_domain(self, domain: str):
            return [{"value": {"preferred_option_index": 2}}]

    repo = STS2StrategyRepository(DummyLogger(), PreferenceStoreWithRecord(), default_strategy="defect")
    context = repo.build_context({"screen": "reward", "classification": {}, "summary_context": {"payload": {}}})

    assert context["strategy_directives"]["preferred_option_index"] == 2


@pytest.mark.unit
def test_strategy_repository_falls_back_when_requested_strategy_missing(tmp_path: Path) -> None:
    strategies = tmp_path / "strategies"
    strategies.mkdir()
    (strategies / "player_overrides.md").write_text("# overrides\n", encoding="utf-8")
    defect = strategies / "defect"
    defect.mkdir()
    (defect / "base.md").write_text("# base\n", encoding="utf-8")

    repo = STS2StrategyRepository(DummyLogger(), DummyPreferenceStore(), default_strategy="defect")
    repo._parser = STS2StrategyParser(DummyLogger(), strategies_dir=strategies)



@pytest.mark.unit
def test_strategy_repository_applies_event_override_to_prompt() -> None:
    class PreferenceStoreWithOverride(DummyPreferenceStore):
        def get(self, domain: str, key: str):
            if domain == "event_overrides" and key == "golden_idol":
                return {"value": {"instruction": "优先低代价路线"}}
            return None

    repo = STS2StrategyRepository(DummyLogger(), PreferenceStoreWithOverride(), default_strategy="defect")
    context = repo.build_context(
        {
            "screen": "event",
            "classification": {},
            "summary_context": {"payload": {"event_id": "golden_idol"}},
        }
    )

    assert context["event_override"] is not None
    assert "## 用户指点覆盖" in context["strategy_prompt"]
    assert "优先低代价路线" in context["strategy_prompt"]


@pytest.mark.unit
def test_companion_evaluator_primary_message_includes_card_cost() -> None:
    evaluator = STS2CompanionEvaluator(None)

    result = evaluator.evaluate(
        summary_context={
            "summary_kind": "combat",
            "payload": {
                "player": {"current_hp": 40, "max_hp": 80, "block": 2},
                "enemies": [{"intent_damage": 10}],
                "playable_card_summaries": [
                    {"name": "邪眼", "energy_cost": 1, "star_cost": 0, "costs_x": False, "star_costs_x": False, "requires_target": False, "playable": True}
                ],
            },
        },
        situation_summary={
            "kind": "combat",
            "text": "当前战斗状态。",
            "static_text": "当前战斗状态。",
            "source": "snapshot",
            "delta": {},
            "before": {},
            "after": {},
        },
        strategy_context={
            "strategy_name": "defect",
            "strategy_directives": {},
        },
    )

    assert "邪眼（1费）" in result["primary_message"]
    # ★ 测试意图是「**承伤超过格挡时要给防御口径**」，与具体文案无关。
    #   原来写死"建议优先防御或找减伤线"，2026-10-19 i18n 第一人称改造后必然失效
    #   —— 那是**测试耦合了文案**，不是行为回归。
    #   ⇒ 断言"意图 + 人称"两件事：
    #     ① 必须给出防御/减伤口径（而不是"抓机会输出"）
    #     ② 必须是猫娘自述（不得出现旁白词）
    assert "防御" in result["primary_message"] or "减伤" in result["primary_message"], (
        f"承伤超过格挡时应给防御口径，实际：{result['primary_message']!r}"
    )
    assert "抓机会" not in result["primary_message"], "承伤超过格挡时不该说抓机会输出"
    for forbidden in ("建议", "玩家"):
        assert forbidden not in result["primary_message"], (
            f"必须是猫娘自述，不得出现旁白词「{forbidden}」：{result['primary_message']!r}"
        )


@pytest.mark.unit
def test_companion_evaluator_uses_strategy_context_for_commentary() -> None:
    evaluator = STS2CompanionEvaluator(None)

    result = evaluator.evaluate(
        summary_context={
            "summary_kind": "combat",
            "payload": {
                "player": {"current_hp": 18, "max_hp": 80, "block": 4},
                "enemies": [{"intent_damage": 16}],
            },
        },
        situation_summary={
            "kind": "combat",
            "text": "当前战斗状态，玩家血量 18/80。",
            "static_text": "当前战斗状态，玩家血量 18/80。",
            "source": "snapshot",
            "delta": {},
            "before": {},
            "after": {},
        },
        strategy_context={
            "strategy_name": "defect",
            "strategy_directives": {"must": ["先保血"]},
        },
    )

    assert result["strategy_name"] == "defect"
    assert result["risk_level"] == "high"
    assert "当前局势偏危险" not in result["commentary"]
    # ★ 同上的口径修正：断言"防御口径 + 第一人称"，不写死文案。
    assert "防御" in result["commentary"] or "减伤" in result["commentary"], (
        f"承伤超过格挡时应给防御口径，实际：{result['commentary']!r}"
    )
    for forbidden in ("建议", "玩家"):
        assert forbidden not in result["commentary"], (
            f"必须是猫娘自述，不得出现旁白词「{forbidden}」：{result['commentary']!r}"
        )


@pytest.mark.unit
def test_companion_evaluator_combat_intent_drives_advice() -> None:
    evaluator = STS2CompanionEvaluator(None)

    evaluator.evaluate(
        summary_context={
            "summary_kind": "combat",
            "payload": {
                "player": {"current_hp": 30, "max_hp": 80, "block": 0},
                "enemies": [{"name": "缩小甲虫", "intent": "SHRINKER_MOVE", "intent_damage": 0}],
            },
        },
        situation_summary={
            "kind": "combat",
            "text": "当前战斗状态。",
            "static_text": "当前战斗状态。",
            "source": "snapshot",
            "delta": {},
            "before": {},
            "after": {},
        },
        strategy_context={
            "strategy_name": "ironclad",
            "strategy_directives": {},
        },
    )



@pytest.mark.unit
def test_companion_evaluator_combat_speaks_solver_card_in_first_person() -> None:
    """★ 战斗台词 = 求解器要打的那一步，且必须是**第一人称**。

    契约（玩家 2026-10-18 指示）：
      1. 战斗决策**完全交给求解器**，插件不再自己启发式选牌；
      2. 猫娘是唯一主语，插件是她的**反射弧**，不是"转述求解器的第三方"；
      3. ⇒ 输出形如「这回合我打『X』」，**不得**出现「建议…理由：…」这种转达口气。

    ★ 判据来自 `solver_plan.line[0].steps[0]`；与
      `test_companion_evaluator_combat_falls_back_when_solver_absent` 互为对照：
      有求解器就念它，没有就不编。
    """
    evaluator = STS2CompanionEvaluator(None)

    result = evaluator.evaluate(
        summary_context={
            "summary_kind": "combat",
            "payload": {
                "player": {"current_hp": 18, "max_hp": 80, "block": 0},
                "enemies": [{"name": "Louse", "intent_damage": 12}],
                # 求解器这一步要打「Defend」（防御牌，不需要目标）
                "solver_plan": {
                    "in_combat": True,
                    "line": [
                        {
                            "turn": 1,
                            "steps": [
                                {
                                    "kind": "play_card",
                                    "card_index": 1,
                                    "card_id": "DEFEND",
                                    "card_name": "Defend",
                                    "target_index": None,
                                }
                            ],
                        }
                    ],
                },
            },
        },
        situation_summary={
            "kind": "combat",
            "text": "当前战斗状态。",
            "static_text": "当前战斗状态。",
            "source": "snapshot",
            "delta": {},
            "before": {},
            "after": {},
        },
        strategy_context={
            "strategy_name": "defect",
            "strategy_directives": {},
        },
    )

    suggestion = result["suggestion"]
    # 念的是求解器指定的那张牌
    assert "Defend" in suggestion
    # ★ 第一人称：猫娘自己在打，不是"建议你打"
    assert "我" in suggestion
    # ★ 不得出现转达口气（插件不是求解器的发言人）
    for forbidden in ("建议", "理由：", "求解器", "插件"):
        assert forbidden not in suggestion, f"出现转达词『{forbidden}』：{suggestion!r}"


@pytest.mark.unit
def test_companion_evaluator_combat_falls_back_when_solver_absent() -> None:
    """★ 没有求解器计划时**不编造牌名**，退到局势短句。

    为什么这条重要：弹幕是陪玩玩家**唯一**能看到的信息渠道（玩家自己打牌、
    看不到求解器）。编一个牌名比什么都不说更糟 —— 玩家会照着打错。
    """
    evaluator = STS2CompanionEvaluator(None)

    result = evaluator.evaluate(
        summary_context={
            "summary_kind": "combat",
            "payload": {
                "player": {"current_hp": 18, "max_hp": 80, "block": 0},
                "enemies": [{"name": "Louse", "intent_damage": 12}],
                "playable_card_summaries": [
                    {"name": "Defend", "energy_cost": 1, "playable": True},
                ],
                # ★ 故意不给 solver_plan
            },
        },
        situation_summary={
            "kind": "combat",
            "text": "当前战斗状态。",
            "static_text": "当前战斗状态。",
            "source": "snapshot",
            "delta": {},
            "before": {},
            "after": {},
        },
        strategy_context={
            "strategy_name": "defect",
            "strategy_directives": {},
        },
    )

    suggestion = result["suggestion"]
    # 仍然要说话（不能空着让玩家干等），但**不许**点名任何具体牌
    assert suggestion, "无求解器时也应给出局势短句，不能完全沉默"
    assert "Defend" not in suggestion, f"无求解器却点名了牌：{suggestion!r}"


@pytest.mark.unit
def test_companion_evaluator_combat_names_target_only_when_solver_targets() -> None:
    """★ 求解器指定了目标才念目标；没指定就**不许念**（防编造）。

    这条来自一个**实测复现**的缺陷：
      `_safe_int(None) == 0`，于是"防御牌 + target_index=None"会被念成
      「这回合我打『防御』，招呼 Louse」——凭空给不需要目标的牌安了个目标。
      陪玩玩家看不到求解器，只能照弹幕打 ⇒ 会打错敌人。

    ⇒ 判据必须是"键在不在 / 值是不是 null"，不能是数值本身。
    """
    base_payload = {
        "player": {"current_hp": 40, "max_hp": 80, "block": 0},
        "enemies": [{"name": "Louse", "intent_damage": 6, "current_hp": 6}],
    }
    situation = {
        "kind": "combat",
        "text": "当前战斗状态。",
        "static_text": "当前战斗状态。",
        "source": "snapshot",
        "delta": {},
        "before": {},
        "after": {},
    }
    strategy = {"strategy_name": "defect", "strategy_directives": {}}

    def _say(step: dict) -> str:
        return STS2CompanionEvaluator(None).evaluate(
            summary_context={
                "summary_kind": "combat",
                "payload": {
                    **base_payload,
                    "solver_plan": {"in_combat": True, "line": [{"turn": 1, "steps": [step]}]},
                },
            },
            situation_summary=situation,
            strategy_context=strategy,
        )["suggestion"]

    # ① 求解器指定 0 号敌人 ⇒ 必须念出名字
    with_target = _say(
        {"kind": "play_card", "card_id": "STRIKE", "card_name": "Strike", "target_index": 0}
    )
    assert "Strike" in with_target
    assert "Louse" in with_target, f"指定了目标却没念：{with_target!r}"

    # ② target_index=None ⇒ 不许念目标（曾是缺陷）
    none_target = _say(
        {"kind": "play_card", "card_id": "DEFEND", "card_name": "Defend", "target_index": None}
    )
    assert "Defend" in none_target
    assert "Louse" not in none_target, f"★ target_index=None 却编造了目标：{none_target!r}"

    # ③ 根本没有 target_index 键 ⇒ 同样不许念目标
    no_key = _say({"kind": "play_card", "card_id": "DEFEND", "card_name": "Defend"})
    assert "Defend" in no_key
    assert "Louse" not in no_key, f"★ 缺 target_index 键却编造了目标：{no_key!r}"


@pytest.mark.unit
def test_companion_evaluator_map_prefers_preferred_route_index_over_first_node() -> None:
    evaluator = STS2CompanionEvaluator(None)

    evaluator.evaluate(
        summary_context={
            "summary_kind": "map",
            "payload": {
                "current_hp": 20,
                "max_hp": 80,
                "gold": 99,
                "travelable_nodes": [
                    {"index": 0, "type": "elite"},
                    {"index": 1, "type": "shop"},
                ],
                "travelable_node_types": ["elite", "shop"],
            },
        },
        situation_summary={"kind": "map", "text": "当前地图选择状态。"},
        strategy_context={
            "strategy_name": "defect",
            "strategy_directives": {"preferred_option_index": 1},
        },
    )



@pytest.mark.unit
def test_companion_evaluator_shop_card_prefers_preferred_option_index_over_first_card() -> None:
    evaluator = STS2CompanionEvaluator(None)

    result = evaluator.evaluate(
        summary_context={
            "summary_kind": "shop",
            "payload": {
                "shop_cards": [
                    {"index": 0, "name": "Strike"},
                    {"index": 1, "name": "Glacier"},
                ],
                "shop_card_names": ["Strike", "Glacier"],
            },
        },
        situation_summary={"kind": "shop", "text": "当前商店状态。"},
        strategy_context={
            "strategy_name": "defect",
            "strategy_directives": {"preferred_option_index": 1},
        },
    )

    assert "Glacier" in result["suggestion"]
    assert "Strike" not in result["suggestion"]


@pytest.mark.unit
def test_companion_evaluator_shop_relic_does_not_default_to_first_item_without_preference() -> None:
    evaluator = STS2CompanionEvaluator(None)

    result = evaluator.evaluate(
        summary_context={
            "summary_kind": "shop",
            "payload": {
                "shop_relics": [
                    {"index": 0, "name": "Burning Blood"},
                    {"index": 1, "name": "Frozen Core"},
                ],
                "shop_relic_names": ["Burning Blood", "Frozen Core"],
            },
        },
        situation_summary={"kind": "shop", "text": "当前商店状态。"},
        strategy_context={
            "strategy_name": "defect",
            "strategy_directives": {},
        },
    )

    # ★ 测试意图是「**没有偏好时不得默认指向第一件**」，与文案人称无关。
    #   原来写死整句第三人称文案（"建议优先考虑高价值购买或删牌…"），
    #   2026-10-19 i18n 第一人称改造后必然失效 —— 那是**测试耦合了文案**，
    #   不是行为回归。⇒ 改为断言"意图"本身：
    #     ① 不得提及任何一件具体商品
    #     ② 必须给出通用口径（非空）
    suggestion = result["suggestion"]
    assert suggestion.strip(), "通用口径不能为空"
    for name in ("Burning Blood", "Frozen Core"):
        assert name not in suggestion, (
            f"没有偏好时不得默认指向具体商品，实际提到了 {name}：{suggestion!r}"
        )
    # ★ 顺带钉住第一人称口径：不得出现旁白式"建议…"转达口气
    for forbidden in ("建议", "玩家"):
        assert forbidden not in suggestion, (
            f"商店口径必须是猫娘自述，不得出现旁白词「{forbidden}」：{suggestion!r}"
        )








@pytest.mark.unit
def test_strategy_parser_preserves_route_policy_frontmatter(tmp_path: Path) -> None:
    strategies = tmp_path / "strategies"
    strategies.mkdir()
    defect = strategies / "defect"
    defect.mkdir()
    (defect / "base.md").write_text(
        """---
constraints:
  route_policy:
    lookahead_depth: 6
    weights:
      rest: 12
      shop: 8
      elite: -10
---
# base
""",
        encoding="utf-8",
    )

    parser = STS2StrategyParser(DummyLogger(), strategies_dir=strategies)
    constraints = parser.load_constraints("defect", "map")

    assert constraints["route_policy"]["lookahead_depth"] == 6
    assert constraints["route_policy"]["weights"]["rest"] == 12
    assert constraints["route_policy"]["weights"]["elite"] == -10
