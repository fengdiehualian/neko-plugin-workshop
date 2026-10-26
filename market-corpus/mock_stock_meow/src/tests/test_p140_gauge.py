"""P14.0 守卫：资金水位可视化 + 多标的仓位总限制联动 + 模式引导重构。

这一版是**纯 UI / 交互层**的重构，引擎层与模式层一个字节都不许动。
它要钉死的硬契约：

* **任务一 · 资金水位条**
  - 红区长度 = 明天的生活费（固定值，不随现金变）；
  - 超出生活费的那一段 = 青蓝色（还能花的余量）；
  - 现金跌破生活费 ⇒ 数字与水位条**一起变红并轻微闪烁**；
  - 闪烁只能用 **JS 定时器切颜色**，不许 CSS 动画（0.8.0 的崩溃根因之一）。

* **任务二 · 多标的仓位总限制**
  - 买入 / 卖出是**两条**独立仓位（拖一条不许动另一条）；
  - 台账按**标的 id**记"打算投多少"（绝对金额，不是百分比）；
  - 别的标的占掉的额度会把买入滑条**截断**（上限 = 现金 − 别人占掉的）；
  - 持仓卡必须显示**仓位占比**（钱压在哪儿要看得见）；
  - 买入后可用现金跌破生活费红线 ⇒ **红色高亮**。

* **任务三 · 模式引导**
  - 三档（街机 / 命运挑战 / 无尽）必须**一眼分得开**；
  - 命运挑战点开是**二级选择**（入门 / 进阶 / 地狱）；
  - 每档都要有一句话的剧情提示；
  - 颜色只用约定的青蓝 / 浅绿 / 红，且渐变**不许对角**。

* **红线**：引擎层、模式层零改动；危险 CSS 继续禁用；
  P14 的样式**全走内联** —— CSS 里不许出现 P14 的新类。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from conftest import engine_mode_code_changes

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_TOML = _PLUGIN_ROOT / "plugin.toml"
_I18N_FILE = _PLUGIN_ROOT / "i18n" / "zh-CN.json"

#: P14 新增的类名 —— 它们**只**出现在 JSX 的 className 里（做锚点用），
#: **不许**出现在 RRM_CSS 里：P14 的样式全部走内联。
P14_CLASSES = (
    "rrm-gauge",
    "rrm-gauge-track",
    "rrm-gauge-red",
    "rrm-gauge-hole",
    "rrm-gauge-cyan",
    "rrm-mode-card",
    "rrm-plan",
    "rrm-plan-row",
    "rrm-plan-drop",
    "rrm-trade-sides",
    "rrm-trade-side",
    "rrm-trade-warning",
)


# ══════════════════════════════════════════════════════════════ 取源码


def _panel_text() -> str:
    return _PANEL.read_text(encoding="utf-8")


def _panel_code() -> str:
    """面板源码去掉注释与 CSS 模板字符串后的正文。"""
    source = _panel_text()
    marker = "const RRM_CSS = `"
    if marker in source:
        head, _, rest = source.partition(marker)
        end = rest.find("\n`\n")
        assert end > 0, "找不到 RRM_CSS 的结束反引号"
        source = head + "\n/* <RRM_CSS> */\n" + rest[end + 3 :]
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


def _panel_css() -> str:
    source = _panel_text()
    marker = "const RRM_CSS = `"
    assert marker in source, "找不到 RRM_CSS"
    _, _, rest = source.partition(marker)
    end = rest.find("\n`\n")
    assert end > 0, "找不到 RRM_CSS 的结束反引号"
    body = rest[:end]
    assert "`" not in body, "RRM_CSS 里出现了反引号（会当场截断模板字符串）"
    return body


def _function_source(source: str, start: str, end: str) -> str:
    """取两个标记之间的源码。"""
    assert start in source, f"源码里找不到 {start}"
    _, _, rest = source.partition(start)
    cut = rest.find(end)
    assert cut != -1, f"源码里找不到 {end}"
    return rest[:cut]


def _i18n() -> dict[str, str]:
    return json.loads(_I18N_FILE.read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════════ ① 资金水位条


def test_the_cash_gauge_exists() -> None:
    """面板里必须有一个**资金水位条**组件。"""
    code = _panel_code()
    assert "function CashGauge(" in code
    block = _function_source(code, "function CashGauge(", "\n}\n")
    assert "bill" in block, "水位条必须知道明天的生活费是多少"
    assert "cash" in block


def test_the_red_zone_is_the_living_expense_and_the_rest_is_cyan() -> None:
    """红区长度 = 生活费；超出生活费的那一段 = 青蓝色。

    这是"资金压迫感"的核心：玩家必须一眼看出**红线的位置**。
    """
    code = _panel_code()
    block = _function_source(code, "function CashGauge(", "\n}\n")
    # 红区宽度按**生活费**算（不是按现金算的百分比）。
    assert "Math.min(bill, scale) / scale" in block, "红区长度必须由生活费决定"
    # 余量 = 现金 − 生活费。
    assert "cash - bill" in block, "青蓝区必须是超出生活费的那一段"
    # 颜色：红 = danger（约定红），青蓝 = primary（约定青蓝）。
    assert "var(--danger)" in block, "红区必须是约定的红色"
    assert "var(--primary)" in block, "余量区必须是约定的青蓝色"


def test_the_crisis_state_turns_everything_red() -> None:
    """现金 < 明天的生活费 ⇒ 数字与水位条**全部变红**。"""
    code = _panel_code()
    assert "const cashCrisis = nextBill > 0 && cashNow < nextBill" in code
    block = _function_source(code, "function CashGauge(", "\n}\n")
    assert "crisis" in block, "水位条必须认危机态"
    # 危机态下整条都是红的（亮红 + 暗红的洞），没有青蓝。
    assert "const cyanW = crisis ? 0 :" in block, "危机态不许还有青蓝色"


def test_the_alarm_blinks_with_a_js_timer_not_css() -> None:
    """闪烁 = **JS 定时器切颜色**，不许 CSS 动画。

    0.8.0 的崩溃根因之一就是动画/关键帧；P7.2 的濒临破产数字已经是这个写法，
    P14 的水位条必须**沿用同一套**，不许新造轮子。
    """
    code = _panel_code()
    assert "const [gaugeAlarm, setGaugeAlarm] = useState(false)" in code
    assert "setInterval" in code, "闪烁必须是定时器"
    assert "clearInterval" in code, "定时器必须清理"
    # 关键帧 / 动画名一律不许出现在面板里。
    assert "@keyframes" not in code
    assert "animation:" not in code


def test_the_cash_number_turns_red_in_crisis() -> None:
    """现金那个**大号数字**也要跟着变红（不只是水位条）。"""
    code = _panel_code()
    # P21.0：字号从写死的 30px 改成 clamp()（全屏时跟着面板变大），
    # 所以这里按「现钱那张卡」的范围取。
    block = _function_source(code, "label={cashLabel}", "label={assetsLabel}")
    assert "cashCrisis" in block, "现金数字必须认危机态"
    assert "var(--danger)" in block, "危机态的现金数字必须是红色"


def test_the_gauge_uses_only_inline_styles() -> None:
    """P14 的样式**全走内联** —— CSS 里不许出现 P14 的新类。"""
    css = _panel_css()
    for name in P14_CLASSES:
        assert f".{name}" not in css, f"P14 的 {name} 不许写进 RRM_CSS（样式走内联）"
    code = _panel_code()
    # 水位条的每段都得带 style（宽度与颜色是算出来的）。
    block = _function_source(code, "function CashGauge(", "\n}\n")
    assert block.count("style: {") >= 4, "水位条必须大量用内联 style"


# ══════════════════════════════════════════════════════════════ ② 多标的台账


def test_buy_and_sell_are_two_separate_positions() -> None:
    """买入 / 卖出是**两条**独立仓位（P13 的时候是共用一条）。"""
    code = _panel_code()
    assert 'useLocalState<number>(\n    "rrm.sell_position_pct",' in code or (
        '"rrm.sell_position_pct"' in code
    ), "卖出仓位必须有自己的状态"
    assert "const sellPct = " in code
    assert "const buyPositionPct = " in code
    # 卖出那条滑块的上限永远是 100（不受买入台账影响）。
    block = _function_source(code, 't("panel.trade_sell_side")', 't("panel.trade_sell_hint"')
    assert "max: 100" in block, "卖出滑块的上限必须是 100"
    assert "setSellPositionPct(Number(value))" in block, "卖出滑块必须写回卖出那条"


def test_the_plan_ledger_is_keyed_by_stock_id() -> None:
    """台账按**标的 id**记"打算投多少"（绝对金额）。"""
    code = _panel_code()
    assert '"rrm.plan_budget"' in code, "必须有计划买入台账"
    assert "useLocalState<Record<string, number>>" in code
    assert "[effectiveStockId]" in code, "台账必须按当前标的 id 记账"


def test_the_buy_slider_is_truncated_by_the_other_plans() -> None:
    """别的标的占掉的额度 ⇒ 买入滑条**被截断**（这就是那股拉扯感）。"""
    code = _panel_code()
    # 上限 = 现金 − 别人占掉的。
    assert "const othersBudget = " in code
    assert "const capBudget = Math.max(0, cash - othersBudget)" in code
    assert "const capPct = " in code
    # 买入滑条的上限是 capPct（**不是**写死的 100）。
    block = _function_source(code, 't("panel.trade_buy_side")', 't("panel.trade_cap_hint"')
    assert "max: Math.max(1, Math.floor(capPct))" in block, "买入滑块必须被上限截断"
    assert "setPositionPct(Number(value))" in block, "买入滑块必须写回买入那条"
    # 写进去的时候也要再截一道（直输框也绕不过去）。
    assert "Math.min((cash * wanted) / 100, capBudget)" in code, "写台账时必须受上限约束"


def test_the_total_planned_budget_never_exceeds_the_cash() -> None:
    """不变量：**计划总额 ≤ 可用现金**（不许超买）。"""
    code = _panel_code()
    assert "const currentPlanBudget = Math.max(" in code
    assert "Math.min(capBudget, Number(planBudget[effectiveStockId] || 0))" in code
    # 台账列的是**所有**排了计划的标的（当前标的也在内）。
    assert "const planRows = Object.keys(planBudget)" in code
    assert 'Number(planBudget[key] || 0) > 0.005' in code
    assert "const planTotal = planRows.reduce((sum, row) => sum + row.budget, 0)" in code


def test_the_ledger_is_cleared_when_the_game_ends() -> None:
    """一局结束 / 换局 ⇒ 台账作废（上一局的计划跟这一局没关系）。"""
    code = _panel_code()
    assert 'if (state.stage !== "playing") setPlanBudgetMap({})' in code
    # 买完之后，那只标的的计划必须作废（不然台账会重复计算）。
    assert "delete next[effectiveStockId]" in code


def test_the_buy_warning_mentions_the_cash_left_and_the_bill() -> None:
    """买入后必须告诉玩家"可用现金将只剩 X"，跌破红线要变红。"""
    code = _panel_code()
    assert "const buyCashAfter = " in code
    assert "const buyWarningAlarm = nextBill > 0 && buyCashAfter < nextBill" in code
    assert 't("panel.trade_after_buy"' in code, "必须有'买入后还剩多少'的提示"
    assert 't("panel.trade_below_bill"' in code, "必须提示低于生活费红线"
    assert "buyWarningAlarm" in code
    # 警告条用的是约定的红色。
    assert "rgba(214, 72, 106" in code, "警告的红色必须是约定的那支红"


def test_the_holdings_card_shows_the_position_weight() -> None:
    """持仓卡要显示它占了**总仓位的百分之多少**（钱压在哪儿要看得见）。"""
    code = _panel_code()
    block = _function_source(code, "function PositionTile(", "\n}\n")
    assert "panel.weight" in block
    assert "percent(Number(position.weight || 0))" in block


def test_the_ledger_rows_can_be_dropped() -> None:
    """台账里的每一行都要能**删掉**（玩家得能反悔）。"""
    code = _panel_code()
    assert "const dropPlan = " in code
    assert "delete next[key]" in code
    assert "onClick: () => dropPlan(row.id)" in code


# ══════════════════════════════════════════════════════════════ ③ 模式引导


def test_the_three_tiers_are_present_and_distinct() -> None:
    """三档必须都在，而且**各写各的**（不许三张卡长得一样）。"""
    code = _panel_code()
    for key in (
        "panel.mode_arcade_title",
        "panel.mode_challenge_title",
        "panel.mode_infinite_title",
    ):
        assert f't("{key}")' in code, f"缺少 {key}"
    # 三张卡的渐变不许完全一样。
    bands = re.findall(r'band: "(linear-gradient\([^"]+)"', code)
    assert len(bands) >= 3, "三档卡各自都要有身份色带"
    # 前三张就是街机 / 命运挑战 / 无尽 —— 三者的身份色必须两两不同。
    assert len(set(bands[:3])) == 3, "三档的身份色不许撞车"


def test_the_challenge_tier_opens_a_secondary_picker() -> None:
    """命运挑战点开是**二级选择**（入门 / 进阶 / 地狱）。"""
    code = _panel_code()
    assert "const [challengeOpen, setChallengeOpen] = useState(false)" in code
    assert "onClick: () => setChallengeOpen(true)" in code, "命运挑战卡要点开二级"
    assert "const challengeModal = h(Modal, {" in code
    for mode in ("classic_beginner", "classic_advanced", "classic_hell"):
        assert f'"{mode}"' in code, f"二级选择里必须有 {mode}"


def test_every_tier_has_a_one_line_story_hint() -> None:
    """每一档都要有一句话的剧情提示（街机 / 命运挑战 / 无尽）。"""
    i18n = _i18n()
    for key in (
        "panel.mode_arcade_hint",
        "panel.mode_challenge_hint",
        "panel.mode_infinite_hint",
        "panel.mode_level_beginner_hint",
        "panel.mode_level_advanced_hint",
        "panel.mode_level_hell_hint",
    ):
        assert key in i18n, f"i18n 里缺 {key}"
        text = i18n[key].strip()
        assert text, f"{key} 是空的"
        assert len(text) <= 40, f"{key} 必须是一句话（别写成一段）"


def test_the_tier_colors_stay_inside_the_agreed_palette() -> None:
    """颜色只许用约定的青蓝 / 浅绿 / 红（不许自己发明第五种颜色）。"""
    code = _panel_code()
    block = _function_source(code, "const modeCard = (opts:", "const challengeLevels")
    for hexa in re.findall(r"#([0-9a-fA-F]{6})", block):
        red = int(hexa[0:2], 16)
        green = int(hexa[2:4], 16)
        blue = int(hexa[4:6], 16)
        is_green = green > red + 20 and green > blue + 20
        is_blue = blue > red + 20 and blue > green + 10
        is_red = red > green + 40 and red > blue + 20
        is_warm = red > 120 and green > 80 and blue < 120 and not is_green
        assert (
            is_green or is_blue or is_red or is_warm
        ), f"档位卡的 #{hexa} 不在约定的青蓝 / 浅绿 / 红里"


def test_the_tier_gradients_are_never_diagonal() -> None:
    """渐变**不许对角**（0.8.0 的 X 线条就是对角渐变 + 绝对定位）。"""
    code = _panel_code()
    for match in re.finditer(r"([a-z]+)-gradient\(([^)]*)\)", code):
        kind, body = match.group(1), match.group(2)
        assert kind == "linear", f"只用 linear-gradient，发现 {kind}-gradient"
        angle = re.search(r"(\d+(?:\.\d+)?)deg", body)
        if angle:
            assert float(angle.group(1)) in (0.0, 90.0, 180.0), (
                f"渐变只允许纵向 / 横向（0/90/180deg），发现 {angle.group(1)}deg"
            )


def test_the_join_buttons_go_through_the_global_lock() -> None:
    """开局按钮必须走 P11.1 的**全局请求锁**（狂点不会打爆宿主）。"""
    code = _panel_code()
    assert "const joinMode = (mode: string) =>" in code
    # P20.0：开局时**多带一个** ``player_name``（玩家自报名号）——
    # 走锁这件事本身一个字没变，所以这里只查前缀。
    assert "void callAction(joinActionId, { mode" in code
    assert "if (!joinActionId) return" in code


# ══════════════════════════════════════════════════════════════ ④ 红线


def test_the_dangerous_css_is_still_banned() -> None:
    """危险 CSS 继续禁（0.8.0 的那套写法不许复活）。"""
    css = _panel_css()
    for token in (
        "@keyframes",
        "position: absolute",
        "position: fixed",
        "z-index",
        "filter:",
        "clip-path",
        "inset:",
        "radial-gradient",
    ):
        assert token not in css, f"注入的 CSS 里不许再出现 {token}"
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
        assert token not in code, f"面板代码里不许再出现 {token}"


def test_the_engine_and_modes_are_untouched() -> None:
    """引擎层、模式层**零改动**（数值与公式一个字节都不许动）。"""
    changed = engine_mode_code_changes()
    assert not changed, f"引擎层/模式层的代码不许改动：{changed}"


def test_the_new_i18n_keys_exist() -> None:
    """P14 新增的文案都要在词典里（不许裸奔）。"""
    i18n = _i18n()
    for key in (
        "panel.gauge_title",
        "panel.gauge_bill_amount",
        "panel.gauge_bill",
        "panel.gauge_crisis",
        "panel.gauge_free",
        "panel.trade_buy_side",
        "panel.trade_sell_side",
        "panel.trade_cap_hint",
        "panel.trade_sell_hint",
        "panel.trade_after_buy",
        "panel.trade_below_bill",
        "panel.plan_title",
        "panel.plan_total",
        "panel.plan_empty",
        "panel.mode_arcade_title",
        "panel.mode_arcade_tag",
        "panel.mode_arcade_hint",
        "panel.mode_challenge_title",
        "panel.mode_challenge_tag",
        "panel.mode_challenge_hint",
        "panel.mode_challenge_modal_title",
        "panel.mode_challenge_modal_hint",
        "panel.mode_level_beginner",
        "panel.mode_level_advanced",
        "panel.mode_level_hell",
        "panel.mode_infinite_title",
        "panel.mode_infinite_tag",
        "panel.mode_infinite_hint",
    ):
        assert key in i18n, f"i18n 里缺 {key}"
        assert i18n[key].strip(), f"{key} 是空的"


def test_the_version_is_0_9_8() -> None:
    """版本号 = 0.10.5。"""
    assert 'version = "0.10.5"' in _TOML.read_text(encoding="utf-8")
    init = (_PLUGIN_ROOT / "__init__.py").read_text(encoding="utf-8")
    assert 'PLUGIN_VERSION = "0.10.5"' in init
