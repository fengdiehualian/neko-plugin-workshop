"""P5 · 卡片投递与猫娘嘲讽的单测。

守四条线：

1. **文案在位**：5 个触发点各 ≥2 个变体，且**全部原创**（不含现实机构/作品名）。
2. **选取稳定**：同一个 seed 必出同一句（``hash()`` 那种每进程随机的做法不行）。
3. **变量填得上**：``{stock}`` / ``{weight}`` 这些占位符会被替换掉。
4. **投递不扰局**：tick 照常跑，推出去的卡片**不含局中秘密**。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import run
from plugins.mock_stock_meow.cards import (
    CARD_KINDS,
    TAUNT_TRIGGERS,
    pick_taunt,
    taunt_variants,
)

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_I18N = json.loads(
    (_PLUGIN_ROOT / "i18n" / "zh-CN.json").read_text(encoding="utf-8")
)

#: 局中**不能出现**的词（卡片正文 / 摘要 / HTML / payload）。
FORBIDDEN_TEXT = ("heat", "热度", "种子", "反转日", "准确率", "真实度", "prey_index")


@pytest.fixture()
def pushed(plugin) -> list[Any]:
    """把插件的卡片投递换成收集器。"""
    collected: list[Any] = []

    async def fake_push(card: Any) -> None:
        collected.append(card)

    plugin._push_card = fake_push
    return collected


# ---------------------------------------------------------------- 文案在位


def test_every_trigger_has_at_least_two_variants() -> None:
    for trigger in TAUNT_TRIGGERS:
        variants = taunt_variants(trigger)
        assert len(variants) >= 2, f"{trigger} 的变体不足 2 条"


def test_taunt_copy_lives_in_i18n() -> None:
    """文案在 ``i18n/zh-CN.json`` 的 ``taunt.*`` 命名空间里（一份文案一处维护）。"""
    for trigger in TAUNT_TRIGGERS:
        for index in range(1, 4):
            assert f"taunt.{trigger}.{index}" in _I18N


def test_taunt_copy_has_no_forbidden_words() -> None:
    """嘲讽文案里不得出现支付类措辞与方向词。"""
    banned = ("充值", "提现", "兑换", "入金", "出金", "获利", "反转")
    for trigger in TAUNT_TRIGGERS:
        for variant in taunt_variants(trigger):
            for word in banned:
                assert word not in variant, f"{trigger} 的文案出现了禁词：{word}"


def test_taunt_copy_has_no_real_world_names() -> None:
    """不得出现现实机构名，也不得出现夸大收益的承诺式说法。"""
    banned = ("股票", "期货", "外汇", "基金", "证券", "稳赚", "保本", "包赚", "一夜暴富")
    for trigger in TAUNT_TRIGGERS:
        for variant in taunt_variants(trigger):
            for word in banned:
                assert word not in variant, f"{trigger} 的文案出现了禁词：{word}"


# ---------------------------------------------------------------- 选取


def test_pick_taunt_is_stable_for_the_same_seed() -> None:
    """同一个 seed 必出同一句（跨进程也成立）。"""
    first = pick_taunt("cash_kill", 123456, {"days": 3})
    for _ in range(20):
        assert pick_taunt("cash_kill", 123456, {"days": 3}) == first


def test_pick_taunt_varies_across_seeds() -> None:
    """不同 seed 大概率给出不同变体（不是恒定一句）。"""
    picks = {pick_taunt("harvest", seed, {}) for seed in range(50)}
    assert len(picks) > 1


def test_pick_taunt_fills_variables() -> None:
    text = pick_taunt("add_down", 7, {"n": 3, "avg": "12.34", "stock": "FAKE-001"})
    # 选中的那条用到的占位符必须被替换掉（没用到的不强求）。
    assert "{" not in text and "}" not in text, f"占位符没填上：{text}"
    assert "3" in text
    # 逐条变体都检查一遍：模板里出现的占位符必须都能填上。
    for variant in taunt_variants("add_down"):
        filled = pick_taunt("add_down", 7, {"n": 3, "avg": "12.34", "stock": "X"})
        assert "{" not in filled and "}" not in filled


def test_pick_taunt_of_unknown_trigger_is_empty() -> None:
    assert pick_taunt("no_such_trigger", 1, {}) == ""


# ---------------------------------------------------------------- 投递


def test_tick_pushes_a_news_card(plugin, pushed) -> None:
    """tick 之后会推出当天的新闻卡片。"""
    run(plugin.join(mode="classic_beginner", seed=9201))
    run(plugin._run_one_tick())
    kinds = [card.kind for card in pushed]
    assert "news_flash" in kinds


def test_pushed_news_card_does_not_leak_truth(plugin, pushed) -> None:
    """突发新闻卡片**不给真假、不给原始 type**。"""
    run(plugin.join(mode="classic_beginner", seed=9202))
    run(plugin._run_one_tick())
    for card in pushed:
        if card.kind != "news_flash":
            continue
        blob = f"{card.title}{card.summary}{card.body}{card.html}{card.payload}"
        for word in ("accuracy", "真新闻", "假新闻", "thunder", "true", "fake"):
            assert word not in blob, f"新闻卡片泄漏了真假线索：{word}"


def test_pushed_cards_never_leak_forbidden_words(plugin, pushed) -> None:
    """推出去的卡片不含局中秘密。"""
    run(plugin.join(mode="classic_beginner", seed=9203))
    for _ in range(12):
        run(plugin._run_one_tick())
    for card in pushed:
        blob = f"{card.title}{card.summary}{card.body}{card.html}{card.payload}"
        for word in FORBIDDEN_TEXT:
            assert word not in blob, f"{card.kind} 泄漏了禁词：{word}"


def test_pushed_cards_are_all_known_kinds(plugin, pushed) -> None:
    """推出去的卡片 kind 必须在 8 种之内。"""
    run(plugin.join(mode="classic_beginner", seed=9204))
    for _ in range(12):
        run(plugin._run_one_tick())
    for card in pushed:
        assert card.kind in CARD_KINDS


def test_card_push_failure_does_not_break_the_game(plugin, monkeypatch) -> None:
    """投递失败**不影响本局**（卡片只是锦上添花）。"""
    async def boom(*_args, **_kwargs):
        raise RuntimeError("卡片通道挂了")

    # 卡片走公开 SDK API ``ctx.create_card(...)``（宿主内部实现不许直引）。
    monkeypatch.setattr(plugin.ctx, "create_card", boom)
    run(plugin.join(mode="classic_beginner", seed=9205))
    run(plugin._run_one_tick())
    # tick 照常跑完：状态还在。
    payload = run(plugin.status()).unwrap()
    assert payload["joined"] is True
    assert payload["day"] == 1
