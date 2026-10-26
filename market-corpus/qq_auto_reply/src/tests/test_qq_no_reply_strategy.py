# -*- coding: utf-8 -*-
"""「回复策略」删除契约 + 顺带修复的可达性守卫。

背景（2026-09-27 使用者要求）：`strategy_mode` 在模式合并后只剩 `neko_dynamic` 一个
取值 —— 界面上永远选不动、代码里恒真，是个假旋钮。整套删掉：配置键、归一化、
两个页面的「回复策略」卡片、策略分支、i18n 文案。

删除过程中撞出三处**可达性**问题，一并修掉，并由本文件钉住：

1. **`cfg-normal-prob`（全局普通转发概率）原来住在 `scene-prob-card` 里**，而那个
   card 的 `display:none` 是硬编码的（原本靠策略下拉切换，而策略永远只有单值）
   ⇒ 这个**活**旋钮在界面上不可达。现在搬进「普通群转发」卡片，正常显示。
2. **群聊弹窗里的「普通转发概率」输入框只在 `isScene` 分支里渲染**（`state.strategy
   === 'neko_scene'`），策略删除后该分支恒假 ⇒ 按群覆盖概率**永远填不了**。
   现在无条件渲染。
3. **开放平台的「机器人账本」（`#bind-bots`）原来也挤在 `page-config-strategy` 里**，
   而那个页面既不在 topbar 标签、又是 `display:none` ⇒ `loadBots()` 明明在页面加载时
   就调用，用户永远看不到账本。现在搬进可见的「连接」页。
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest
from _ui_source import code_of, fn_body, read

PLUGIN = pathlib.Path(__file__).resolve().parents[1]
PAGES = ("napcat.html", "open_platform.html")

#: 已删除的 i18n 键（两个 bundle 都不该再有）
DEAD_I18N_KEYS = (
    "ui.shared.card.strategy",
    "ui.shared.card.strategy_mode",
    "ui.shared.card.strategy_neko_dynamic",
    "ui.shared.card.scene_prob",
    "ui.shared.topbar.tab_strategy",
    "ui.napcat.prompts.overview_strategy",
)


def _bundle(locale: str) -> dict:
    return json.loads((PLUGIN / "i18n" / f"{locale}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("page", PAGES)
def test_no_reply_strategy_control_left_in_the_pages(page):
    """页面里不能再有策略下拉、策略卡片、或 `state.strategy` 这种残留。"""
    text = read(page)
    for needle in ("cfg-strategy-mode", "scene-prob-card", "state.strategy", "page-config-strategy"):
        assert needle not in text, f"{page} 还留着「回复策略」的痕迹: {needle}"


def test_napcat_has_no_strategy_tab_or_shortcut():
    """策略页删了，topbar 标签与状态页快捷入口也得跟着删（否则点进去是空白）。"""
    text = read("napcat.html")
    assert "tab_strategy" not in text, "策略标签还在 topbar 里"
    assert "gotoConfig('strategy')" not in text, "状态页还有指向已删除页面的快捷入口"
    assert "switchSub('strategy')" not in text, "还有别的入口跳到已删除的策略页"


@pytest.mark.parametrize("locale", ("zh-CN", "en"))
def test_dead_i18n_keys_are_gone(locale):
    bundle = _bundle(locale)
    left = [k for k in DEAD_I18N_KEYS if k in bundle]
    assert not left, f"{locale} 还留着已删除的文案键: {left}"


def test_i18n_bundles_stay_in_sync_after_the_removal():
    """两个 bundle 的键集合必须一致（删键时最容易只删一边）。"""
    zh, en = _bundle("zh-CN"), _bundle("en")
    assert set(zh) == set(en), f"键集合不一致: 仅中文 {sorted(set(zh) - set(en))}，仅英文 {sorted(set(en) - set(zh))}"


# ── 顺带修复 1：全局普通转发概率必须可达 ─────────────────────────────

def test_global_normal_relay_input_is_reachable():
    """输入框在 + 它所在的卡片没有 display:none（原来被硬编码藏起来）。"""
    text = read("napcat.html")
    assert 'id="cfg-normal-prob"' in text, "全局普通转发概率的输入框没了"
    card = re.search(r'<div class="card" id="normal-relay-card"[^>]*>', text)
    assert card, "没有一个承载它的「普通群转发」卡片"
    assert "display:none" not in card.group(0), "卡片又被藏起来了 —— 这个旋钮会变成不可达"
    assert "ui.shared.card.normal_relay" in text, "卡片标题的 i18n 键丢了"


def test_global_normal_relay_input_is_still_saved():
    """可达还不够 —— 它必须真的进 doSave 的 payload。"""
    body = fn_body(code_of("napcat.html"), "doSave")
    assert body, "找不到 doSave"
    assert "normal_relay_probability:floatVal('cfg-normal-prob',.1)" in body, (
        "全局转发概率没有进保存 payload —— 界面能改、存不下来"
    )
    assert "strategy_mode" not in body, "doSave 还在提交已删除的 strategy_mode"


# ── 顺带修复 2：按群覆盖概率必须能填 ─────────────────────────────────

def test_group_modal_always_offers_the_relay_probability_field():
    body = fn_body(code_of("napcat.html"), "showGroupModal")
    assert body, "找不到 showGroupModal"
    assert "mg-nrp" in body, "群聊弹窗里没有按群转发概率的输入框"
    assert "isScene" not in body and "neko_scene" not in body, (
        "按群转发概率又被策略条件挡住了 —— 那个条件恒假，字段等于不存在"
    )
    assert "normal_relay_probability" in body, "弹窗提交时没带上按群概率"


# ── 顺带修复 3：开放平台机器人账本必须在可见页里 ─────────────────────

def test_open_platform_bot_ledger_lives_in_a_visible_page():
    text = read("open_platform.html")
    assert 'id="bind-bots"' in text, "账本容器没了（loadBots() 会抛空引用）"

    # 它必须在 page-config-connection 里，且那个页面**没有** display:none
    page = re.search(r'<div class="page" id="page-config-connection"[^>]*>(.*?)\n</div>\n<div class="page"',
                     text, flags=re.S)
    assert page, "页面结构变了，无法定位连接页"
    assert 'id="bind-bots"' in page.group(1), "账本不在连接页里（可能又被挪进隐藏页）"
    assert "display:none" not in page.group(0).split(">", 1)[0], "账本所在的页被藏起来了"

    # 连接页必须是 topbar 里真实存在的标签（否则用户点不到）
    switch = code_of("open_platform.html")
    assert "id:'connection'" in switch, "topbar 里没有连接标签"


def test_open_platform_loadbot_still_called_on_load():
    """账本可见的前提是它真的被渲染 —— 别只搬了容器、忘了调用。"""
    code = code_of("open_platform.html")
    assert re.search(r"^\s*loadBots\(\);\s*$", code, flags=re.M), "页面加载时不再渲染账本"
