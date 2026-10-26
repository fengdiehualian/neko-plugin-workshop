"""P16.0 守卫：前两关爽感重塑 + Yui 深度情绪感知。

这一版同时改了**两层**，红线各有各的守法方式：

* **任务二（表现层）** —— 街机档的连击徽章 + 盈利数字跳动 +
  前两关弱化水位条与台账。铁律：**纯表现层** ——
  引擎层与模式层的可执行代码一个字节都不许改。
* **任务三（情绪感知）** —— 把 heat / 连败 / 持仓集中度折成**定性**标签
  喂给 Yui。铁律：**标签不带数值**、**局中不进玩家可见载荷**、
  **主动搭话阈值对齐宿主 N.E.K.O 的 Focus 语义**。

这几条都是「说错了就出事」的硬契约，所以逐条钉死。
"""

from __future__ import annotations

import ast
import re
import time
from pathlib import Path
from typing import Any

from conftest import engine_mode_code_changes, run

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_INIT = _PLUGIN_ROOT / "__init__.py"
_EMOTION = _PLUGIN_ROOT / "emotion.py"
_COMBO = _PLUGIN_ROOT / "combo.py"
_MEOW = _PLUGIN_ROOT / "meow.py"

#: 宿主 N.E.K.O 的 Focus / Visit 常量（**只读镜像**）。
#:
#: * ``VISIT_IDLE_TIMEOUT_S`` —— ``config/visit_settings.py``；
#: * ``FOCUS_CHARGE_ENTER`` —— ``config/focus_settings.py``；
#: * ``FOCUS_IDLE_SILENT_RETENTION`` / ``FOCUS_IDLE_REPLIED_RETENTION``
#:   —— 同上（两个都是 0.8）。
#:
#: 插件层不自己拍数字：主动搭话的节奏必须与宿主一致，
#: 否则玩家在两个系统里感受到的是两个「发呆」尺度。
HOST_VISIT_IDLE_TIMEOUT_S = 300
HOST_FOCUS_CHARGE_ENTER = 0.6
HOST_FOCUS_IDLE_RETENTION = 0.8

#: 内部指标名 —— 情绪标签里**一个都不许出现**。
#:
#: 「上头指数」是引擎内部字段，模型要是学会说这个词，
#: 就等于把内部状态透给了玩家（P4 §14 禁止局中泄露）。
_FORBIDDEN_INDICATORS = ("heat", "上头指数", "集中度", "连败")


def _panel_code() -> str:
    """面板源码去掉注释与 CSS 模板字符串后的正文。"""
    source = _PANEL.read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"//[^\n]*", "", source)
    source = re.sub(r"const RRM_CSS = `.*?`", "", source, flags=re.S)
    return source


def _profile_samples() -> list[Any]:
    """构造一堆画像，覆盖各分档（纯函数，好穷举）。"""
    from plugins.mock_stock_meow.emotion import build_profile

    samples: list[Any] = []
    for heat in (0.0, 0.45, 0.95):
        for losses in (0, 1, 4):
            for concentration in (0.0, 0.3, 0.9):
                for trades in (0, 2, 9):
                    for loss_ratio in (0.0, 0.05, 0.3):
                        for count in (0, 1, 3):
                            samples.append(
                                build_profile(
                                    heat=heat,
                                    consecutive_losses=losses,
                                    concentration=concentration,
                                    trades_today=trades,
                                    loss_ratio=loss_ratio,
                                    holding_count=count,
                                    alive=True,
                                )
                            )
    return samples


def _assert_no_numeric_leak(text: str, where: str) -> None:
    """情绪文案不许带任何数值 / 百分比 / 内部指标名。"""
    assert not re.search(r"\d", text), f"{where} 里出现了数字：{text!r}"
    assert "%" not in text, f"{where} 里出现了百分比：{text!r}"
    lowered = text.lower()
    for indicator in _FORBIDDEN_INDICATORS:
        assert indicator.lower() not in lowered, (
            f"{where} 里出现了内部指标名 {indicator!r}：{text!r}"
        )


# ══════════════════════════════════════════════════════════════════════
# 任务三 · 情绪标签
# ══════════════════════════════════════════════════════════════════════


def test_the_emotion_tags_carry_no_numbers_and_no_indicator_names() -> None:
    """标签与结算画像**定性、无数值**（P15.0 实测：给了数值模型一定会念）。"""
    from plugins.mock_stock_meow.emotion import summarize

    for profile in _profile_samples():
        for tag in profile.tags:
            _assert_no_numeric_leak(tag, "情绪标签")
        _assert_no_numeric_leak(summarize(profile), "结算页画像")


def test_the_prompt_block_carries_the_five_constraints() -> None:
    """情绪块 = 标签 + **5 条**约束（P15.0 实测：缺一条就出事）。"""
    from plugins.mock_stock_meow.emotion import (
        EMOTION_CONSTRAINTS,
        build_profile,
        render_prompt_block,
    )

    assert len(EMOTION_CONSTRAINTS) == 5, "约束清单必须正好 5 条"
    profile = build_profile(
        heat=0.95,
        consecutive_losses=3,
        concentration=0.9,
        trades_today=6,
        loss_ratio=0.2,
        holding_count=2,
        alive=True,
    )
    block = render_prompt_block(profile)
    assert block, "有标签时必须给出情绪块"
    for index in range(1, 6):
        assert f"{index}. " in block, f"情绪块里缺第 {index} 条约束"
    # 标签部分（约束之前）不许出现数字 / 百分比 / 内部指标名。
    head = block.split("硬性要求")[0]
    _assert_no_numeric_leak(head, "情绪块（标签部分）")


def test_the_emotion_block_is_empty_when_there_is_nothing_to_say() -> None:
    """没画像 ⇒ 情绪块为空串（提示词与旧版一模一样，安全降级）。"""
    from plugins.mock_stock_meow.emotion import build_profile, render_prompt_block

    profile = build_profile(heat=0.0, alive=False)
    assert profile.ended is True
    assert profile.tags == ()
    assert render_prompt_block(profile) == ""


def test_the_midgame_payload_never_carries_the_emotion_profile(plugin: Any) -> None:
    """**局中**不许把情绪画像漏进玩家可见的载荷（P4 §14）。"""
    from plugins.mock_stock_meow.emotion import build_profile

    run(plugin.join(mode="classic_arcade", seed=9601))
    payload = run(plugin.market_context())
    assert isinstance(payload, dict)
    for banned in ("emotion", "emotion_profile", "emotion_summary", "emotion_tags"):
        assert banned not in payload, f"局中载荷不许带 {banned!r}"

    profile = build_profile(
        heat=0.95,
        consecutive_losses=3,
        concentration=0.9,
        trades_today=6,
        loss_ratio=0.2,
        holding_count=2,
        alive=True,
    )
    blob = repr(payload)
    for tag in profile.tags:
        assert tag not in blob, "局中载荷不许出现情绪标签原文"


def test_the_settlement_carries_a_numeric_free_emotion_summary(plugin: Any) -> None:
    """结算页给「本局情绪画像」彩蛋 —— 但同样**不带数值**。"""
    run(plugin.join(mode="classic_arcade", seed=9602))
    result = run(plugin.settle())
    assert result.is_ok()
    summary = result.unwrap().get("emotion_summary")
    assert isinstance(summary, str) and summary, "结算页必须给情绪画像"
    _assert_no_numeric_leak(summary, "结算页情绪画像")


def test_the_emotion_tags_are_only_ever_injected_at_runtime() -> None:
    """静态提示词里不许出现情绪标签 —— 画像只能在运行时拼进去。"""
    from plugins.mock_stock_meow.emotion import build_profile

    source = _MEOW.read_text(encoding="utf-8")
    profile = build_profile(
        heat=0.95,
        consecutive_losses=3,
        concentration=0.9,
        trades_today=6,
        loss_ratio=0.2,
        holding_count=2,
        alive=True,
    )
    for tag in profile.tags:
        assert tag not in source, "情绪标签不许出现在静态提示词里"


# ══════════════════════════════════════════════════════════════════════
# 任务三 · 主动搭话（对齐宿主 Focus 语义）
# ══════════════════════════════════════════════════════════════════════


def test_the_proactive_idle_thresholds_match_the_host() -> None:
    """主动搭话的阈值必须**逐个对齐**宿主 N.E.K.O 的 Focus 语义。"""
    from plugins.mock_stock_meow import (
        IDLE_PROACTIVE_SECONDS,
        PROACTIVE_CHARGE_ENTER,
        PROACTIVE_CHARGE_RETENTION,
    )

    assert IDLE_PROACTIVE_SECONDS == float(HOST_VISIT_IDLE_TIMEOUT_S)
    assert PROACTIVE_CHARGE_ENTER == HOST_FOCUS_CHARGE_ENTER
    assert PROACTIVE_CHARGE_RETENTION == HOST_FOCUS_IDLE_RETENTION

    source = _INIT.read_text(encoding="utf-8")
    for host_name in (
        "VISIT_IDLE_TIMEOUT_S",
        "FOCUS_CHARGE_ENTER",
        "FOCUS_IDLE_SILENT_RETENTION",
        "FOCUS_IDLE_REPLIED_RETENTION",
    ):
        assert host_name in source, f"源码里必须写明对齐的是宿主的 {host_name}"


def test_the_idle_watchdog_waits_for_the_full_threshold(plugin: Any) -> None:
    """发呆不足 300 秒 ⇒ **不许**搭话（宿主 ``VISIT_IDLE_TIMEOUT_S``）。"""
    run(plugin.join(mode="classic_arcade", seed=9603))
    plugin._idle_meow_at = 0.0
    plugin._last_player_action_at = time.time() - 100.0
    plugin._proactive_charge = 0.1
    plugin._proactive_charge_at = time.time() - 100.0
    run(plugin._idle_meow_watchdog())
    assert plugin._idle_meow_at == 0.0, "没到阈值就不许主动搭话"


def test_the_idle_watchdog_speaks_once_the_player_has_gone_quiet(plugin: Any) -> None:
    """发呆够久 ⇒ 主动搭话（宿主接不住也不许崩）。"""
    run(plugin.join(mode="classic_arcade", seed=9604))
    plugin._idle_meow_at = 0.0
    plugin._last_player_action_at = time.time() - 400.0
    plugin._proactive_charge = 0.1
    plugin._proactive_charge_at = time.time() - 400.0
    run(plugin._idle_meow_watchdog())
    assert plugin._idle_meow_at > 0.0, "发呆够久就该搭话了"


def test_the_idle_watchdog_respects_the_charge_gate(plugin: Any) -> None:
    """charge 还高着（玩家刚互动过）⇒ **不许**打扰。"""
    run(plugin.join(mode="classic_arcade", seed=9605))
    plugin._idle_meow_at = 0.0
    plugin._last_player_action_at = time.time() - 400.0
    plugin._proactive_charge = 0.9
    # 衰减时钟**不往前推** ⇒ charge 保持 0.9（高于 0.6 门槛）。
    plugin._proactive_charge_at = time.time()
    run(plugin._idle_meow_watchdog())
    assert plugin._idle_meow_at == 0.0, "charge 高于门槛就不该打扰"


def test_the_idle_watchdog_throttles_itself_through_the_charge(plugin: Any) -> None:
    """搭话完 charge 打满 ⇒ 下一轮要等它自然衰减下去（不会连着刷屏）。"""
    run(plugin.join(mode="classic_arcade", seed=9610))
    plugin._idle_meow_at = 0.0
    plugin._last_player_action_at = time.time() - 400.0
    plugin._proactive_charge = 0.1
    plugin._proactive_charge_at = time.time() - 400.0
    run(plugin._idle_meow_watchdog())
    assert plugin._idle_meow_at > 0.0, "发呆够久就该搭话了"
    assert plugin._proactive_charge == 1.0, "搭话完宿主要重新上心（charge 打满）"

    # 紧接着再调一次 ⇒ charge 刚打满，不该再搭话。
    fired_at = plugin._idle_meow_at
    plugin._last_player_action_at = time.time() - 400.0
    plugin._proactive_charge_at = time.time()
    run(plugin._idle_meow_watchdog())
    assert plugin._idle_meow_at == fired_at, "charge 没衰减下去就不该再搭话"


def test_every_player_action_resets_the_idle_timer(plugin: Any) -> None:
    """玩家一动手 ⇒ 计时归零、charge 打满（不许在操作间隙搭话）。"""
    run(plugin.join(mode="classic_arcade", seed=9606))
    calls = (
        lambda: plugin.buy("FAKE-001", 1.0),
        lambda: plugin.sell("FAKE-001", 1.0),
        lambda: plugin.analyze(),
        lambda: plugin.skip_day(),
        lambda: plugin.advance_tick(),
    )
    for call in calls:
        plugin._last_player_action_at = 0.0
        plugin._proactive_charge = 0.2
        run(call())
        assert plugin._last_player_action_at > 0.0, "操作必须重置发呆计时"
        assert plugin._proactive_charge == 1.0, "操作必须把 charge 打满"


# ══════════════════════════════════════════════════════════════════════
# 任务二 · 连击（纯表现层）
# ══════════════════════════════════════════════════════════════════════


def test_the_combo_is_only_for_the_arcade_level() -> None:
    """连击是**新手村的糖** —— 后面的档位不给。"""
    from plugins.mock_stock_meow.combo import COMBO_MODES

    assert COMBO_MODES == ("classic_arcade",)


def test_the_panel_payload_reports_the_combo(plugin: Any) -> None:
    """面板载荷带 ``combo``，且只有街机档开着。"""
    run(plugin.join(mode="classic_arcade", seed=9607))
    combo = run(plugin.market_context())["combo"]
    assert combo["enabled"] is True
    assert combo["streak"] == 0
    assert combo["best"] == 0

    run(plugin.settle())
    run(plugin.join(mode="classic_beginner", seed=9608))
    assert run(plugin.market_context())["combo"]["enabled"] is False


def test_the_combo_judge_counts_the_fees() -> None:
    """「买中」= 按现价卖出**净赚**（涨幅不够覆盖手续费就不算命中）。"""
    from plugins.mock_stock_meow.combo import judge_entry
    from plugins.mock_stock_meow.engine.config import FeeConfig

    fees = FeeConfig()
    assert judge_entry(
        entry_price=10.0, amount=10.0, current_price=10.01, fees=fees
    ) is False
    assert judge_entry(
        entry_price=10.0, amount=10.0, current_price=11.0, fees=fees
    ) is True


def test_the_combo_module_never_writes_engine_state() -> None:
    """连击**不许**写引擎状态（现金 / heat / 持仓一个都不许动）。"""
    tree = ast.parse(_COMBO.read_text(encoding="utf-8"))
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
    assert not offenders, f"连击模块不许写引擎状态：{sorted(set(offenders))}"


def test_the_combo_only_touches_the_presentation_fields() -> None:
    """连击的钩子只许动 ``self._combo``，不许顺手改别的。"""
    source = _INIT.read_text(encoding="utf-8")
    assert "self._combo.add_entry(" in source
    assert "self._combo.resolve(" in source
    assert "self._combo.reset()" in source


# ══════════════════════════════════════════════════════════════════════
# 任务二 · 前两关的简化面板
# ══════════════════════════════════════════════════════════════════════


def test_the_first_two_levels_get_the_simple_panel() -> None:
    """前两关（街机 / 入门）⇒ 面板**更简单**。

    P21.0：P16.0 的两档模型升级成**三档**（街机 / 入门 / 后面三档）——
    街机是纯爽局，入门开始教生存，两者的诉求不一样。

    这里守的不变式：**前两关不吃复杂信息**（持仓只铺前 3 只、水位条压成
    一行、集中度警告不出现），后面三档才火力全开。
    """
    code = _panel_code()
    assert 'const MINIMAL_UI_MODES = ["classic_arcade"]' in code
    assert 'const GUARDED_UI_MODES = ["classic_beginner"]' in code
    assert "SIMPLE_POSITION_LIMIT" in code
    assert "compact: simpleUi" in code, "资金水位条必须按前两关切紧凑模式"
    assert "shownPositions.map" in code, "持仓台账必须按前两关裁剪"
    # 集中度警告只在**后面三档**出现（前两关不该有 —— 那是教学指标，不是爽感）。
    assert "const showConcentration = fullUi" in code
    assert "showConcentration && (state.concentration || 0) > 0.6" in code


def test_the_new_effects_use_js_timers_not_css_animation() -> None:
    """新增动效**不许**用 CSS 动画（0.8.0 的崩溃根因）。"""
    code = _panel_code()
    assert "function ComboBadge(" in code
    assert "setInterval" in code
    assert "clearInterval" in code
    assert "@keyframes" not in code
    assert "animation:" not in code


def test_the_new_copy_all_goes_through_i18n() -> None:
    """新增文案必须走 i18n 词典（面板代码里不许有裸中文）。"""
    import json

    table = json.loads((_PLUGIN_ROOT / "i18n" / "zh-CN.json").read_text(encoding="utf-8"))
    for key in (
        "panel.combo_badge",
        "panel.combo_hint",
        "panel.combo_best",
        "panel.positions_more",
    ):
        assert key in table, f"i18n 缺 {key}"


# ══════════════════════════════════════════════════════════════════════
# 任务二 · 新手期放水（只给前两关）
# ══════════════════════════════════════════════════════════════════════


def test_the_beginner_level_starts_with_a_bigger_bankroll(plugin: Any) -> None:
    """入门档初始资金 **×1.2**（引擎的算法没动，只是插件层乘了一下）。"""
    from plugins.mock_stock_meow import BEGINNER_CASH_MULTIPLIER
    from plugins.mock_stock_meow.engine.rng import derive_rng
    from plugins.mock_stock_meow.modes.shared import (
        classic_initial_days,
        classic_living_cost_range,
    )
    from plugins.mock_stock_meow.storage.session import build_engine_config

    assert BEGINNER_CASH_MULTIPLIER == 1.2
    seed = 9609
    engine_config = build_engine_config({})
    difficulty = engine_config.difficulties["classic_beginner"]
    low, high = classic_living_cost_range(difficulty, engine_config)
    days = classic_initial_days(difficulty, engine_config)
    base = derive_rng(seed, 999).uniform(low, high)
    expected = round(base * days * 1.2, 2)
    assert plugin._initial_cash("classic_beginner", seed, engine_config) == expected


def test_the_softening_only_applies_to_the_first_two_levels() -> None:
    """放水**只给前两关** —— 后面的收割场不许被削弱。"""
    from plugins.mock_stock_meow import SOFTENED_MODES
    from plugins.mock_stock_meow.storage.session import build_engine_config

    assert SOFTENED_MODES == ("classic_arcade", "classic_beginner")
    engine_config = build_engine_config({})
    assert engine_config.news.true_ratio_honeymoon == 0.60, "引擎默认值不许动"
    assert engine_config.news.truth_ratio_honeymoon == 0.70, "引擎默认值不许动"


# ══════════════════════════════════════════════════════════════════════
# 红线：引擎层 / 模式层的可执行代码零改动
# ══════════════════════════════════════════════════════════════════════


def test_the_engine_and_mode_layers_are_untouched() -> None:
    """引擎层 / 模式层的**可执行代码**一个字节都不许改。"""
    assert engine_mode_code_changes() == []
