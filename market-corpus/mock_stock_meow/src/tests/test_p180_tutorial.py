"""P18.0 守卫：分档次动态教程 + 反诈祝福。

这一版把《玩法说明》弹窗换成了**跟着玩家走的动态教程**：

* **后端状态机** ``IDLE → GREET → STEP_i → EPILOGUE → DONE``；
* **分档次** —— 街机 4 步 / 命运挑战（进阶·地狱）3 步 / 无尽 6 步
  （含一个「等玩家真的遇到那件事才讲」的延迟节点）；
* **动态聚焦 UI** —— 宿主原生 ``Modal`` + ``Steps``/``Step`` + 目标描边；
* **AI 驱动** —— 角色台词 100% 由 Yui 生成，插件只给**事实**与**内容约束**。

这几条都是「说错了就出事」的硬契约，所以逐条钉死。
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from conftest import engine_mode_code_changes, run

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_TUTORIAL = _PLUGIN_ROOT / "tutorial.py"
_I18N = _PLUGIN_ROOT / "i18n" / "zh-CN.json"


def _panel_code() -> str:
    """面板源码去掉注释后的正文（保留 CSS —— CSS 也要查）。"""
    source = _PANEL.read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"//[^\n]*", "", source)
    return source


def _i18n() -> dict[str, str]:
    return json.loads(_I18N.read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════════════════
# ① 分档位的步骤规格
# ══════════════════════════════════════════════════════════════════════


def test_the_tier_step_counts_match_the_spec() -> None:
    """街机 4 步 / 命运挑战 3 步 / 无尽 6 步。"""
    from plugins.mock_stock_meow.tutorial import TIER_STEPS

    assert len(TIER_STEPS["classic_arcade"]) == 4
    assert len(TIER_STEPS["classic_advanced"]) == 3
    assert len(TIER_STEPS["classic_hell"]) == 3
    assert len(TIER_STEPS["infinite"]) == 6


def test_the_hell_level_does_not_teach_the_basics() -> None:
    """命运挑战**不教玩法** —— 只教「水位是命线」「别压一只」和风险声明。"""
    from plugins.mock_stock_meow.tutorial import TIER_STEPS

    anchors = [step.anchor for step in TIER_STEPS["classic_hell"]]
    assert "market_grid" not in anchors, "这一档不教看盘"
    assert "trade_console" not in anchors, "这一档不教下单"
    assert "gauge" in anchors
    assert "position_grid" in anchors
    assert anchors[-1] == "status_bar", "最后一步是风险声明"


def test_the_endless_loan_lesson_waits_for_the_distress_event() -> None:
    """无尽档的网贷课**等玩家真的遇到那件事**才开课（延迟节点）。"""
    from plugins.mock_stock_meow.tutorial import TIER_STEPS

    gates = [step.gate for step in TIER_STEPS["infinite"]]
    assert "loan_modal" in gates, "网贷那一步必须是门槛节点"
    loan_step = next(step for step in TIER_STEPS["infinite"] if step.gate == "loan_modal")
    assert loan_step.anchor == "loan_button"


# ══════════════════════════════════════════════════════════════════════
# ② 后端状态机
# ══════════════════════════════════════════════════════════════════════


def test_the_tutorial_state_machine_walks_from_greet_to_epilogue(plugin: Any) -> None:
    """开局 ⇒ GREET ⇒ 逐步推进 ⇒ 结课小结。"""
    run(plugin.join(mode="classic_arcade", seed=7101))

    payload = run(plugin.market_context())
    tutorial = payload["tutorial"]
    assert tutorial["active"] is True
    assert tutorial["phase"] == "greet"
    assert tutorial["tier"] == "classic_arcade"
    assert tutorial["total"] == 4

    run(plugin.tutorial_begin())
    tutorial = run(plugin.market_context())["tutorial"]
    assert tutorial["phase"] == "step"
    assert tutorial["index"] == 0
    assert tutorial["step"]["anchor"] == "market_grid"

    # 逐步推进到最后一步。
    for expected_index in (1, 2, 3):
        run(plugin.tutorial_advance())
        tutorial = run(plugin.market_context())["tutorial"]
        assert tutorial["phase"] == "step"
        assert tutorial["index"] == expected_index

    # 走完 ⇒ 结课小结。
    run(plugin.tutorial_advance())
    tutorial = run(plugin.market_context())["tutorial"]
    assert tutorial["phase"] == "epilogue"


def test_skipping_the_tutorial_silences_it(plugin: Any) -> None:
    """跳过 ⇒ 记进度，这一局不再打扰。"""
    run(plugin.join(mode="classic_arcade", seed=7102))
    run(plugin.tutorial_skip())
    tutorial = run(plugin.market_context())["tutorial"]
    assert tutorial["phase"] == "done"
    assert tutorial["active"] is False


def test_a_second_game_in_the_same_tier_does_not_restart_the_tutorial(
    plugin: Any,
) -> None:
    """**全局闸门**：这一档教过了就不再来一遍（教程不是骚扰）。"""
    run(plugin.join(mode="classic_arcade", seed=7103))
    run(plugin.tutorial_skip())
    run(plugin.settle())
    run(plugin.join(mode="classic_arcade", seed=7104))
    tutorial = run(plugin.market_context())["tutorial"]
    assert tutorial["active"] is False, "同一档不该再教一遍"


def test_the_tutorial_never_carries_numbers_into_the_payload(plugin: Any) -> None:
    """教程块里**只有** i18n key 与锚点 —— 没有任何数值。"""
    run(plugin.join(mode="classic_arcade", seed=7105))
    run(plugin.tutorial_begin())
    tutorial = run(plugin.market_context())["tutorial"]
    blob = json.dumps(tutorial, ensure_ascii=False)
    assert "heat" not in blob.lower()
    assert not re.search(r"\d+\.\d+", blob), "教程块里不许出现数值"


def test_a_real_trade_auto_advances_the_tutorial_step(plugin: Any) -> None:
    """**真实操作**推进：完成第一笔成交 ⇒「试一次下单」那一步自动过。"""
    run(plugin.join(mode="classic_arcade", seed=7106))
    run(plugin.tutorial_begin())
    run(plugin.tutorial_advance())
    tutorial = run(plugin.market_context())["tutorial"]
    assert tutorial["index"] == 1
    assert tutorial["step"]["key"] == "arcade_place_order"

    stocks = run(plugin.market_context())["stocks"]
    stock_id = str(stocks[0]["id"])
    run(plugin.buy(stock_id, 1.0))

    tutorial = run(plugin.market_context())["tutorial"]
    assert tutorial["index"] == 2, "第一笔成交之后这一步就过"


def test_a_step_times_out_and_moves_on(plugin: Any) -> None:
    """超时自动推进 —— 教程**不许**把玩家卡住。"""
    from plugins.mock_stock_meow.tutorial import PHASE_STEP, STEP_TIMEOUT_SECONDS

    run(plugin.join(mode="classic_arcade", seed=7107))
    run(plugin.tutorial_begin())
    before = run(plugin.market_context())["tutorial"]["index"]
    # 把超时时间推到过去 ⇒ 下一次轮询就该推进。
    plugin._tutorial["deadline"] = 1.0
    run(plugin.market_context())
    tutorial = run(plugin.market_context())["tutorial"]
    assert tutorial["phase"] == PHASE_STEP
    assert tutorial["index"] == before + 1
    assert STEP_TIMEOUT_SECONDS > 0


# ══════════════════════════════════════════════════════════════════════
# ③ 提示词：只给事实，不给台词
# ══════════════════════════════════════════════════════════════════════


def test_the_step_prompt_carries_the_hard_rules() -> None:
    """步骤提示词必须带齐硬性要求（含「不要照抄」）。"""
    from plugins.mock_stock_meow.tutorial import TIER_STEPS, build_step_prompt

    step = TIER_STEPS["classic_arcade"][0]
    prompt = build_step_prompt("classic_arcade", step, "玩家刚进场。")
    for needle in (
        "【这是什么】",
        "【玩家现在什么样】",
        "【这一轮要讲的事】",
        "【硬性要求】",
        "不要照抄",
        "不要引用任何具体数值",
    ):
        assert needle in prompt, f"提示词里缺 {needle!r}"


def test_the_epilogue_prompt_asks_for_the_three_things() -> None:
    """结课小结：三件事都要说到 + 风险提示 + 祝福。"""
    from plugins.mock_stock_meow.tutorial import build_epilogue_prompt

    prompt = build_epilogue_prompt("玩家这一局没能扛过去，被清出了场子")
    assert "①" in prompt and "②" in prompt and "③" in prompt
    assert "虚构" in prompt
    assert "风险" in prompt
    assert "都要说到" in prompt
    assert "不要照抄" in prompt


def test_the_prompts_never_contain_a_performance_instruction() -> None:
    """P13 契约：只给处境，**不给表演指令**。"""
    from plugins.mock_stock_meow.tutorial import (
        TIER_STEPS,
        build_epilogue_prompt,
        build_greet_prompt,
        build_step_prompt,
    )

    prompts = [build_greet_prompt("classic_arcade")]
    for tier, steps in TIER_STEPS.items():
        for step in steps:
            prompts.append(build_step_prompt(tier, step))
    prompts.append(build_epilogue_prompt("玩家走完了这一局。"))

    for prompt in prompts:
        for banned in ("请用", "扮演", "假装", "用傲娇", "用嘲讽的语气", "请模仿"):
            assert banned not in prompt, f"提示词里出现了表演指令 {banned!r}"


def test_the_banned_extreme_words_are_built_at_runtime() -> None:
    """禁词按码点构造 —— **构造处**源码零明文，守卫强度不减。

    查的是 ``emotion.py``（构造处）：``tutorial.py`` 只是调用方。
    另外顺带确认：提示词里**真的**带上了这些禁词（不然约束就是空的）。
    """
    from plugins.mock_stock_meow.emotion import banned_extreme_words
    from plugins.mock_stock_meow.tutorial import TIER_STEPS, build_step_prompt

    source = (_PLUGIN_ROOT / "emotion.py").read_text(encoding="utf-8")
    words = banned_extreme_words()
    assert words
    for word in words:
        assert word not in source, "禁词不许以明文出现在构造处源码里"

    prompt = build_step_prompt("classic_arcade", TIER_STEPS["classic_arcade"][0])
    for word in words:
        assert word in prompt, "提示词里必须真的带上禁词（否则约束是空的）"


# ══════════════════════════════════════════════════════════════════════
# ④ 面板：零危险 CSS + 锚点齐全 + 静态合规实体
# ══════════════════════════════════════════════════════════════════════


def test_the_tutorial_highlight_uses_only_outline_and_box_shadow() -> None:
    """目标圈 = outline + box-shadow（两个都不在禁词表里）。"""
    code = _panel_code()
    block = re.search(r"\.rrm-tutorial-target\s*\{[^}]*\}", code)
    assert block, "面板里必须有 .rrm-tutorial-target"
    body = block.group(0)
    assert "outline" in body
    assert "box-shadow" in body
    for banned in ("position", "z-index", "filter", "clip-path", "opacity", "animation", "keyframes"):
        assert banned not in body, f"目标圈里不许出现 {banned}"


def test_the_panel_does_not_highlight_while_waiting_for_the_gate() -> None:
    """等门槛事件的那一步**不圈**目标 —— 课还没开课。

    这里守的是一个真实的坑：门槛打开**前后**锚点相同（都是
    ``loan_button``），若等待时也圈，高亮那个 effect 只认锚点变化，
    压根不会重新生效 ⇒ 网贷那一步永远没有描边。
    """
    code = _panel_code()
    assert "tutorial.waiting_gate" in code, "面板得知道这一步在等门槛"
    assert "!tutorialWaiting" in code, "等门槛时必须把锚点置空"
    assert "!tutorialWaiting ? tutorialAnchor : null" in code


def test_the_tutorial_css_has_no_dangerous_properties() -> None:
    """教程新增的 CSS 里**零危险属性**。"""
    code = _panel_code()
    for marker in (".rrm-tutorial-target", ".rrm-tutorial-coach", ".rrm-tutorial-steps"):
        block = re.search(re.escape(marker) + r"[^{]*\{[^}]*\}", code)
        assert block, f"面板里必须有 {marker}"
        body = block.group(0)
        for banned in ("position", "z-index", "filter", "clip-path", "opacity", "keyframes"):
            assert banned not in body, f"{marker} 里不许出现 {banned}"


def test_every_tutorial_anchor_has_a_matching_class_in_the_panel() -> None:
    """每个步骤的锚点，面板里**真的**有那个 class（否则圈不到东西）。"""
    from plugins.mock_stock_meow.tutorial import TIER_STEPS

    code = _panel_code()
    for steps in TIER_STEPS.values():
        for step in steps:
            expected = "rrm-anchor-" + step.anchor.replace("_", "-")
            assert expected in code, f"面板里没有 {expected}（步骤 {step.key}）"


def test_the_settlement_page_carries_the_static_risk_note() -> None:
    """结算页留一条**静态合规实体**（台词仍然 100% AI 生成）。"""
    code = _panel_code()
    assert "panel.settlement_risk_note" in code
    assert "panel.settlement_risk_note" in _i18n()
    note = _i18n()["panel.settlement_risk_note"]
    assert "虚构" in note
    assert "风险" in note


def test_the_panel_has_no_character_lines_from_the_tutorial() -> None:
    """教程的**台词不许进 i18n** —— 面板上只有功能性 UI 文案。"""
    table = _i18n()
    for key in table:
        if not key.startswith("tutorial."):
            continue
        assert key.endswith(
            (
                ".title",
                ".body",
                ".progress",
                ".next",
                ".skip",
                ".begin",
                ".close",
                ".wait",
                ".note",
                ".risk_note",
                ".step_title",
            )
        ), f"教程 i18n 的键名不符合「功能性 UI 文案」约定：{key}"


def test_every_tutorial_step_copy_exists_in_i18n() -> None:
    """每个步骤的标题与正文都必须在 i18n 里存在。"""
    from plugins.mock_stock_meow.tutorial import TIER_STEPS

    table = _i18n()
    for steps in TIER_STEPS.values():
        for step in steps:
            assert step.title_key in table, f"i18n 缺 {step.title_key}"
            assert step.body_key in table, f"i18n 缺 {step.body_key}"


# ══════════════════════════════════════════════════════════════════════
# ⑤ 红线
# ══════════════════════════════════════════════════════════════════════


def test_the_tutorial_module_never_writes_engine_state() -> None:
    """教程**不许**写引擎状态（现金 / heat / 持仓一个都不许动）。"""
    tree = ast.parse(_TUTORIAL.read_text(encoding="utf-8"))
    forbidden = {"cash", "heat", "positions", "avg_cost", "alive", "day"}
    offenders: list[str] = []
    for node in ast.walk(tree):
        targets: list[Any] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr in forbidden:
                offenders.append(target.attr)
    assert not offenders, f"教程模块不许写引擎状态：{sorted(set(offenders))}"


def test_the_engine_and_mode_layers_are_untouched() -> None:
    """引擎层 / 模式层的**可执行代码**一个字节都不许改。"""
    assert engine_mode_code_changes() == []


def test_the_version_is_bumped_to_0_10_1() -> None:
    """版本号 = 0.10.5。"""
    from plugins.mock_stock_meow import PLUGIN_VERSION

    assert PLUGIN_VERSION == "0.10.5"
    toml = (_PLUGIN_ROOT / "plugin.toml").read_text(encoding="utf-8")
    assert 'version = "0.10.5"' in toml

