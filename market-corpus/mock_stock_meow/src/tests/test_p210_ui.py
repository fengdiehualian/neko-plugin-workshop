"""P21.0 守卫：渐进式 UI 简化 + 全屏沉浸 + 新手容错。

这一版做三件事，每件都是「做错了就伤新手」的契约，所以逐条钉死：

1. **渐进式 UI** —— 街机 / 入门 / 后面三档显示的东西**必须不一样**，
   而且「藏起来的东西不能还在暗中起作用」（台账藏了，占掉的额度也得归零）；
2. **全屏沉浸** —— K 线高度随 iframe 高度自适应（``vh``）、核心数字随宽度放大
   （``vw``），但**只用长度单位**：那几样会让宿主布局崩掉的东西一个不许碰；
3. **新手容错** —— 跳过教程的人第一次动手时有「幽灵提示」指路；名字限 6 字；
   面板的功能文案**彻底去金融化**。

另外钉住一条**结构保证**：教程圈到哪，那块就必须可见 ——
街机档默认不显示水位条，而它的教程第 4 步正是「看水位」。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from conftest import engine_mode_code_changes

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_I18N = _PLUGIN_ROOT / "i18n" / "zh-CN.json"


def _panel_source() -> str:
    return _PANEL.read_text(encoding="utf-8")


def _panel_code() -> str:
    """面板源码去掉注释与 CSS 模板字符串后的正文。

    （沿用 P14.0 起的既有口径：CSS 单独查，正文里不许再出现那些危险写法。）
    """
    source = _panel_source()
    marker = "const RRM_CSS = `"
    if marker in source:
        head, _, rest = source.partition(marker)
        end = rest.find("\n`\n")
        assert end > 0, "找不到 RRM_CSS 的结束反引号"
        source = head + "\n/* <RRM_CSS> */\n" + rest[end + 3 :]
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


def _panel_css() -> str:
    """面板 CSS 字符串（``RRM_CSS`` 那一段）。"""
    source = _panel_source()
    marker = "const RRM_CSS = `"
    assert marker in source, "面板里找不到 RRM_CSS"
    _, _, rest = source.partition(marker)
    end = rest.find("\n`\n")
    assert end > 0, "面板里找不到 RRM_CSS 的结束反引号"
    body = rest[:end]
    assert "`" not in body, "RRM_CSS 里出现了反引号（会当场截断模板字符串）"
    return body


def _i18n() -> dict[str, str]:
    return json.loads(_I18N.read_text(encoding="utf-8"))


def _function_source(source: str, start: str, end: str) -> str:
    """取两个标记之间的源码。"""
    assert start in source, f"源码里找不到 {start}"
    _, _, rest = source.partition(start)
    assert end in rest, f"源码里找不到 {end}"
    body, _, _ = rest.partition(end)
    return body


# ══════════════════════════════════════════════════════════════════════
# ① 红线：引擎层 / 模式层零改动
# ══════════════════════════════════════════════════════════════════════


def test_the_engine_and_the_modes_are_untouched() -> None:
    """P21.0 只改**面板前端**（条件渲染）—— 引擎层与模式层一个字节都不改。"""
    changed = engine_mode_code_changes()
    assert not changed, f"引擎层/模式层被改了：{changed}"


# ══════════════════════════════════════════════════════════════════════
# ② 三档 UI 密度
# ══════════════════════════════════════════════════════════════════════


#: 档位 ⇒ UI 密度（**测试侧的期望值**）。
#:
#: UI 密度是**纯前端**概念（面板自己从 ``state.mode`` 推），
#: 所以这里不 import 后端 —— 后端根本没有这个东西。
#: 守卫要做的是：**面板里的常量必须与这张表一致**。
_UI_LEVELS = {
    "classic_arcade": "minimal",
    "classic_beginner": "guarded",
    "classic_advanced": "full",
    "classic_hell": "full",
    "infinite": "full",
}


def test_there_are_exactly_three_ui_levels() -> None:
    """三档：街机（minimal）/ 入门（guarded）/ 后面三档（full）。"""
    code = _panel_code()
    assert "const MINIMAL_UI_MODES = [\"classic_arcade\"]" in code
    assert "const GUARDED_UI_MODES = [\"classic_beginner\"]" in code
    assert "function uiLevelFor(mode: string): UiLevel" in code
    assert '"minimal"' in code and '"guarded"' in code and '"full"' in code


def test_the_ui_level_table_matches_the_panel() -> None:
    """面板里的两档常量必须与期望表**逐档一致**。"""
    code = _panel_code()
    minimal = re.search(r"const MINIMAL_UI_MODES = \[([^\]]*)\]", code)
    guarded = re.search(r"const GUARDED_UI_MODES = \[([^\]]*)\]", code)
    assert minimal and guarded

    def _modes(match: re.Match[str]) -> list[str]:
        return re.findall(r"\"([^\"]+)\"", match.group(1))

    expected_minimal = sorted(k for k, v in _UI_LEVELS.items() if v == "minimal")
    expected_guarded = sorted(k for k, v in _UI_LEVELS.items() if v == "guarded")
    assert sorted(_modes(minimal)) == expected_minimal
    assert sorted(_modes(guarded)) == expected_guarded
    # 其余的档位一律 full（认不出来的也按 full —— 宁可多给，不给空）。
    assert "return \"full\"" in code


def test_every_conditional_block_is_actually_gated() -> None:
    """每一块「要藏起来的东西」都必须是**条件渲染**，不能是无条件渲染。"""
    code = _panel_code()
    for gate in (
        "showCombo ? h(ComboBadge",
        "showGauge",
        "showBillAlert ? (",
        "showConcentration &&",
        "showPlanLedger ? (",
        "showFamilyBadge ?",
        "showKillLine ? (",
    ):
        assert gate in code, f"没有条件渲染：{gate}"


def test_the_arcade_tier_hides_the_survival_widgets() -> None:
    """街机档（纯爽局）：水位条 / 连击 / 集中度 / 台账 / 家道徽标 / 平仓线全藏。"""
    code = _panel_code()
    level = _function_source(code, "const uiLevel = uiLevelFor(", "const shownPositions")
    assert "const showGauge = fullUi || guardedUi || tutorialNeeds(\"gauge\")" in level
    assert "const showCombo = fullUi" in level
    assert "const showConcentration = fullUi" in level
    assert "const showPlanLedger = fullUi" in level
    assert "const showFamilyBadge = !minimalUi" in level
    assert "const showKillLine = !minimalUi" in level
    assert "const showBillAlert = !minimalUi" in level


def test_the_beginner_tier_gets_the_gauge_back() -> None:
    """入门档：恢复水位条（开始教生存了），但台账 / 集中度 / 连击仍藏。"""
    code = _panel_code()
    level = _function_source(code, "const uiLevel = uiLevelFor(", "const shownPositions")
    assert "const showGauge = fullUi || guardedUi || tutorialNeeds(\"gauge\")" in level
    # 藏起来的**不许**在入门档复活。
    assert "const showCombo = fullUi" in level
    assert "const showConcentration = fullUi" in level
    assert "const showPlanLedger = fullUi" in level


# ══════════════════════════════════════════════════════════════════════
# ③ 教程锚点例外
# ══════════════════════════════════════════════════════════════════════


def test_a_hidden_widget_must_stay_visible_for_the_tutorial() -> None:
    """**教程点到哪，那块就必须看得见**。

    街机档默认不显示水位条，而它的教程第 4 步正是「看水位」
    （锚点 ``gauge``）—— 不处理的话，教程圈的就是空气。
    """
    code = _panel_code()
    assert "const tutorialNeeds = (anchor: string): boolean => tutorialAnchor === anchor" in code
    assert 'tutorialNeeds("gauge")' in code


def test_every_tutorial_anchor_has_a_visible_target() -> None:
    """教程用到的每一个锚点，在**它自己的档位**里都得有东西可圈。"""
    from plugins.mock_stock_meow.tutorial import TIER_STEPS

    code = _panel_code()
    # 锚点 → 面板里对应的 className 约定。
    anchors = {
        "market_grid": "rrm-anchor-market-grid",
        "trade_console": "rrm-anchor-trade-console",
        "position_grid": "rrm-anchor-position-grid",
        "gauge": "rrm-anchor-gauge",
        "status_bar": "rrm-anchor-status-bar",
        "loan_button": "rrm-anchor-loan-button",
    }
    for tier, steps in TIER_STEPS.items():
        level = _UI_LEVELS.get(tier, "full")
        for step in steps:
            assert step.anchor in anchors, f"{tier}/{step.key} 的锚点不认识：{step.anchor}"
            assert anchors[step.anchor] in code, anchors[step.anchor]
            # 街机档唯一会被藏起来的、又被教程点到的，就是水位条。
            if level == "minimal" and step.anchor == "gauge":
                assert 'tutorialNeeds("gauge")' in code


# ══════════════════════════════════════════════════════════════════════
# ④ 藏起来的东西不许暗中起作用
# ══════════════════════════════════════════════════════════════════════


def test_the_hidden_ledger_stops_reserving_budget() -> None:
    """前两关藏起台账 ⇒ 台账占掉的额度也必须**归零**。

    否则玩家会撞上一个「滑条莫名其妙拉不满」的鬼现象，
    而他根本看不到原因（台账不在面板上）。
    """
    code = _panel_code()
    assert "const othersBudget = showPlanLedger" in code
    assert ": 0" in code


def test_the_buy_slider_cap_is_the_full_cash_in_the_simple_tiers() -> None:
    """前两关的买入滑条上限 = **全部现金**（就是一个纯粹的「这回掏多少」）。"""
    code = _panel_code()
    block = _function_source(code, "const othersBudget =", "const capBudget")
    assert "showPlanLedger" in block, "额度归零这件事必须由台账可见性决定"
    assert ": 0" in block, "台账藏起来时，其它标的占掉的额度必须归零"


# ══════════════════════════════════════════════════════════════════════
# ⑤ 全屏沉浸
# ══════════════════════════════════════════════════════════════════════


def test_the_kline_height_follows_the_iframe_height() -> None:
    """K 线高度用 **CSS 表达式**（``vh`` = iframe 视口高度），不是写死的 px。"""
    code = _panel_code()
    assert 'const KLINE_HEIGHT_CSS = "clamp(' in code
    assert "vh" in code
    kline = _function_source(code, "function Kline(props", "function ")
    assert "height," in kline
    # 柱子的高度必须是**百分比**（相对容器算）—— 容器长高时柱子自动跟着长高。
    assert "height: `${(ratio * 100).toFixed(2)}%`" in kline


def test_the_core_numbers_scale_with_the_panel_width() -> None:
    """核心数字（盈亏 / 现钱 / 身家）随面板宽度放大（``vw``）。"""
    code = _panel_code()
    assert "size=\"clamp(" in code
    assert "vw" in code
    # BigStat 必须能接受 CSS 表达式（不只是 px 数字）。
    assert "typeof props.size === \"string\" ? props.size" in code


def test_the_background_is_two_layers_and_covers() -> None:
    """底色叠两层 + ``background-size: cover``（全屏下不显单薄）。"""
    css = _panel_css()
    assert "background-size: cover" in css
    root = _function_source(css, ".rrm-root {", "/* ── ②")
    assert root.count("linear-gradient(") >= 2


def test_the_dangerous_css_is_still_banned() -> None:
    """P21.0 加了一堆新样式 —— 崩溃红线一条都不许破。"""
    css = _panel_css()
    for token in (
        "@keyframes",
        "position",
        "filter",
        "opacity",
        "clip-path",
        "inset:",
        "radial-gradient",
        "z-index",
    ):
        assert token not in css, token
    for match in re.finditer(r"animation:\s*([^;\n]+)", css):
        assert match.group(1).strip() in {"none", "none !important"}, match.group(0)
    code = _panel_code()
    for token in (
        'position: "absolute"',
        'position: "fixed"',
        "zIndex",
        "filter:",
        "animation:",
        "clipPath",
        "<div className=",
    ):
        assert token not in code, token


def test_the_panel_code_has_no_hardcoded_chinese() -> None:
    """面板**代码**零硬编码中文（文案必须走 i18n）。"""
    offenders = [
        (number, line.strip())
        for number, line in enumerate(_panel_code().split("\n"), start=1)
        if re.search(r"[\u4e00-\u9fff]", line)
    ]
    assert not offenders, f"面板里有硬编码中文文案：{offenders}"


def test_the_panel_never_references_the_hidden_fields() -> None:
    """面板**不引用**任何局中秘密字段。"""
    code = _panel_code()
    for field in ("heat", "seed", "reversal", "accuracy", "prey_index"):
        assert not re.search(rf"\b{field}\b", code), field


# ══════════════════════════════════════════════════════════════════════
# ⑥ 新手兜底：幽灵提示
# ══════════════════════════════════════════════════════════════════════


def test_the_ghost_hint_fires_on_the_first_hand() -> None:
    """跳过教程的人第一次动滑块 / 第一次成交 ⇒ 飘出一行提示。"""
    code = _panel_code()
    assert "const triggerGhostHint = () =>" in code
    # 买入滑块 / 买入直输 / 卖出滑块 / 买入成交，四条路都触发。
    assert code.count("triggerGhostHint()") >= 4


def test_the_ghost_hint_is_static_ui_copy_not_a_yui_line() -> None:
    """幽灵提示是**静态 UI 文案**，不是 Yui 台词。

    台词仍然 100% 由她生成（P13 契约）—— 这里只负责指路。
    """
    code = _panel_code()
    assert 't("panel.ghost_hint")' in code
    table = _i18n()
    assert "panel.ghost_hint" in table
    assert "呼叫猫娘" in table["panel.ghost_hint"]


def test_the_ghost_hint_stays_away_while_the_tutorial_coaches() -> None:
    """教程正在教 ⇒ 有人盯着，不需要这行提示。"""
    code = _panel_code()
    assert "const tutorialCoaching = Boolean(tutorial.active)" in code
    assert "ghostHintUsed.current || tutorialCoaching" in code


# ══════════════════════════════════════════════════════════════════════
# ⑦ 名字防呆：限 6 字 + 第二条提示
# ══════════════════════════════════════════════════════════════════════


def test_the_name_input_is_capped_at_six_characters() -> None:
    """面板限 6 字，后端也限 6 字（两边一致）。"""
    from plugins.mock_stock_meow.name_easter_eggs import MAX_NAME_LENGTH

    assert MAX_NAME_LENGTH == 6
    code = _panel_code()
    handler = _function_source(code, "const onNameInput = (raw: string) =>", "setNameDraft(cleaned)")
    assert ".slice(0, 6)" in handler, "名字输入框必须截到 6 个字"
    assert ".slice(0, 12)" not in handler, "名字输入框不许还是 12 个字"


def test_the_name_input_says_the_second_hint() -> None:
    """输入框下面两行提示：一行劝退，一行吊胃口。"""
    table = _i18n()
    assert table["panel.name_input_hint"] == "别用真名，丢人"
    assert table["panel.name_input_hint2"] == "名字太嚣张，容易触发特殊彩蛋。"
    code = _panel_code()
    assert 't("panel.name_input_hint2")' in code


# ══════════════════════════════════════════════════════════════════════
# ⑧ 术语去金融化（只管**功能 UI 文案**）
# ══════════════════════════════════════════════════════════════════════


def test_the_panel_copy_has_no_finance_jargon() -> None:
    """面板的**功能文案**彻底去金融化。

    只查 ``panel.*`` —— 猫娘台词（``taunt.*``）里那些行话是**故意**留着
    的：她在用行话**嘲讽**玩家，那是这个游戏的笑点，抹掉就没了。
    """
    table = _i18n()
    for key, value in table.items():
        if not key.startswith("panel."):
            continue
        for banned in ("仓位", "执行买入", "执行卖出", "浮盈亏", "摊薄成本"):
            assert banned not in value, f"{key} 里有金融术语「{banned}」：{value}"


def test_the_trade_buttons_speak_plain_chinese() -> None:
    """按钮与它的副标题都是大白话。"""
    table = _i18n()
    assert table["panel.trade_execute_buy"] == "掏钱买"
    assert table["panel.trade_execute_sell"] == "割肉卖"
    assert table["panel.trade_position"] == "投入比例"
    assert "掏钱" in table["panel.trade_execute_buy_sub"]
    assert "割肉" in table["panel.trade_execute_sell_sub"]


def test_the_guide_describes_the_ui_that_actually_exists() -> None:
    """《玩法说明》第二步不能还在讲 P12 的「一键梭哈 / 一键割肉」。

    按钮早就换成滑块了 —— 说明文字落后于界面，是新手最容易被坑的地方。
    """
    table = _i18n()
    assert "梭哈" in table["guide.step2_body"]
    assert "割肉" in table["guide.step2_body"]
    assert "一键梭哈】" not in table["guide.step2_body"]
    assert "投入比例" in table["guide.step2_body"]
    # 第一步也不能再说「经典模式不收生活费」（入门档及以上都要交）。
    assert "经典模式不收生活费" not in table["guide.step1_body"]
    assert "街机档不收生活费" in table["guide.step1_body"]


# ══════════════════════════════════════════════════════════════════════
# ⑨ i18n
# ══════════════════════════════════════════════════════════════════════


def test_the_new_i18n_keys_exist_and_use_an_allowed_prefix() -> None:
    """新增文案必须进 i18n，且前缀只许那几个。"""
    table = _i18n()
    allowed = (
        "panel.",
        "guide.",
        "meow.",
        "visual.",
        "error.",
        "story.",
        "boom.",
        "daily.",
        "taunt.",
        "director.",
        "welfare.",
        "loan.",
        "kline.",
        "news.",
        "onboarding.",
    )
    for key in (
        "panel.name_input_hint2",
        "panel.ghost_hint",
    ):
        assert key in table, key
        assert key.startswith(allowed), key
        assert table[key].strip(), key
