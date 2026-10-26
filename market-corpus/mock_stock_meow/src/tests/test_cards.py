"""P5 卡片体系单测（``cards.py``）。

守护三条线：

1. **8 种卡片都在**，且都能构造出完整卡片（kind / title / summary / body / html）。
2. **局中隐藏铁律**（P4 §14）：卡片正文 / HTML / summary / payload 一律不得出现
   ``heat`` / ``heat_curve`` / ``seed`` / ``reversal_days`` / ``accuracy`` /
   ``prey_index`` 及其词族。
3. **按钮语义**（P5 契约 §2.4 修正版）：二道贩子卡片只给「拒绝」按钮 ——
   「接受」是默认行为，不需要按钮。
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from plugins.mock_stock_meow.cards import (
    CARD_KINDS,
    build_bill_card,
    build_catgirl_daily_card,
    build_harvest_card,
    build_kill_card,
    build_news_flash_card,
    build_pawnbroker_card,
    build_taunt_card,
    build_welfare_card,
    t,
)

#: 契约 §6.1 的禁止字段。
FORBIDDEN = {
    "heat",
    "heat_curve",
    "seed",
    "reversal_days",
    "accuracy",
    "prey_index",
}

#: 变体守卫：这些词族出现在任何 key 里都不允许。
FORBIDDEN_TOKENS = ("heat", "seed", "reversal", "prey_index")

#: 局中**不能出现的方向词**（细节 5）：玩家只能知道"今天特殊"，
#: 不能知道"特殊在哪" —— 不能从卡片里读出利好 / 利空 / 反转。
FORBIDDEN_DIRECTION_WORDS = (
    "bullish",
    "bearish",
    "反转",
    "利好",
    "利空",
    "偏多",
    "偏空",
    "看涨",
    "看跌",
    "上涨方向",
    "下跌方向",
)


def _walk_keys(payload: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            keys.append(str(key))
            keys.extend(_walk_keys(value))
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            keys.extend(_walk_keys(item))
    return keys


def _all_cards() -> list[Any]:
    """8 种卡片各造一张（覆盖所有分支）。"""
    return [
        build_harvest_card(
            tick=42,
            day=4,
            stock_id="FAKE-003",
            stock_name="环轨科技3号",
            concentration=0.62,
            severity=0.18,
            price_before=12.5,
            price_after=10.25,
            amount=40.0,
            trigger="peak",
        ),
        build_welfare_card(cycle=2, amount=1050.0, days_owed=0),
        build_welfare_card(cycle=2, amount=1050.0, already_claimed=True),
        build_bill_card(
            day=5,
            living_cost=10.0,
            next_cost=10.0,
            cash=3.0,
            gap=7.0,
            missed_bills=1,
        ),
        build_kill_card(
            death_reason="liquidated",
            death_title="资不抵债",
            days_survived=9,
            total_asset=0.0,
            harvest_count=3,
        ),
        build_catgirl_daily_card(
            day=6,
            action="观望",
            cash=1050.0,
            positions_value=980.0,
            days_survived=6,
            position_count=2,
        ),
        build_taunt_card(trigger="heavy_position_delisted", text="你又补仓了。"),
        build_news_flash_card(
            news_id="n-1",
            kind="vague",
            kind_label="含糊",
            headline="环轨科技3号或将获得新订单",
            content="有消息称该公司可能中标，细节尚未公布。",
            day=6,
            stock_id="FAKE-003",
        ),
        build_pawnbroker_card(
            day=7,
            gap=120.0,
            discount=0.2,
            legs=[
                {
                    "stock_id": "FAKE-003",
                    "stock_name": "环轨科技3号",
                    "amount": 12.0,
                    "notional": 123.0,
                }
            ],
            cash_after=3.0,
            window_seconds=45,
        ),
    ]


# ---------------------------------------------------------------- 基本结构


def test_nine_card_kinds_are_declared() -> None:
    """P5 的 8 种 + P5.5 改动 6 的「成就解锁」= 9 种。"""
    assert len(CARD_KINDS) == 9
    assert set(CARD_KINDS) == {
        "harvest",
        "welfare",
        "bill",
        "kill",
        "catgirl_daily",
        "taunt",
        "news_flash",
        "pawnbroker",
        "achievement",
    }


def test_every_builder_produces_a_complete_card() -> None:
    for card in _all_cards():
        assert card.kind in CARD_KINDS
        assert card.title
        assert card.summary
        assert card.body
        assert card.html.startswith("<div")
        assert card.html.endswith("</div>")
        assert isinstance(card.actions, dict)
        assert isinstance(card.payload, dict)


def test_card_titles_come_from_i18n() -> None:
    """卡片标题走 i18n（``card.*`` 命名空间），不是硬编码在渲染层。"""
    assert t("card.harvest.title") == "收割"
    assert t("card.pawnbroker.title") == "二道贩子来敲门"


# ---------------------------------------------------------------- 局中隐藏


def test_cards_never_leak_forbidden_fields() -> None:
    for card in _all_cards():
        for key in _walk_keys(card.payload):
            lowered = key.lower()
            assert lowered not in FORBIDDEN, f"{card.kind} 泄漏了禁止字段：{key}"
            for token in FORBIDDEN_TOKENS:
                assert token not in lowered, f"{card.kind} 泄漏了禁止字段：{key}"


def test_cards_never_leak_forbidden_words_in_text() -> None:
    """正文 / 摘要 / HTML 里也不能出现禁词（不只是 key）。"""
    forbidden_text = ("heat", "热度", "种子", "反转日", "准确率", "真实度", "prey_index")
    for card in _all_cards():
        blob = f"{card.title}\n{card.summary}\n{card.body}\n{card.html}"
        for word in forbidden_text:
            assert word not in blob, f"{card.kind} 的文案里出现了禁词：{word}"


def test_news_flash_card_does_not_leak_truth() -> None:
    """雷新闻卡片只给中性分类，**不给真假、不给原始 type**。"""
    card = build_news_flash_card(
        news_id="n-2",
        kind="vague",
        kind_label="含糊",
        headline="标题",
        content="正文",
        day=3,
    )
    blob = f"{card.title}{card.summary}{card.body}{card.html}{card.payload}"
    for word in ("accuracy", "真新闻", "假新闻", "雷", "true", "fake", "thunder"):
        assert word not in blob, f"新闻卡片泄漏了真假线索：{word}"


def test_cards_never_leak_direction() -> None:
    """细节 5：任何卡片都不得泄漏新闻方向。"""
    for card in _all_cards():
        blob = f"{card.title}{card.summary}{card.body}{card.html}{card.payload}"
        for word in FORBIDDEN_DIRECTION_WORDS:
            assert word not in blob, f"{card.kind} 泄漏了方向：{word}"


# ---------------------------------------------------------------- 按钮语义


def test_pawnbroker_card_only_offers_the_reject_button() -> None:
    """细节 1：**只有「拒绝」按钮**；「接受」是默认行为，不需要按钮。"""
    card = build_pawnbroker_card(
        day=7,
        gap=120.0,
        discount=0.2,
        legs=[],
        cash_after=3.0,
    )
    assert set(card.actions) == {"reject"}
    assert card.actions["reject"]["entry"] == "daily_reject"
    assert "accept" not in card.actions
    assert 'data-neko-action="accept"' not in card.html
    assert 'data-neko-action="reject"' in card.html


def test_pawnbroker_card_lists_the_legs() -> None:
    card = build_pawnbroker_card(
        day=7,
        gap=120.0,
        discount=0.2,
        legs=[
            {
                "stock_id": "FAKE-003",
                "stock_name": "环轨科技3号",
                "amount": 12.0,
                "notional": 123.0,
            }
        ],
        cash_after=3.0,
    )
    assert "环轨科技3号" in card.html
    assert card.payload["legs"][0]["stock_id"] == "FAKE-003"
    assert card.payload["gap"] == 120.0


def test_welfare_card_offers_claim_only_when_not_claimed() -> None:
    unclaimed = build_welfare_card(cycle=1, amount=1050.0)
    assert unclaimed.actions["claim"]["entry"] == "daily_claim"
    assert 'data-neko-action="claim"' in unclaimed.html

    claimed = build_welfare_card(cycle=1, amount=1050.0, already_claimed=True)
    assert claimed.actions == {}
    assert "data-neko-action" not in claimed.html


def test_news_flash_card_detail_action_points_at_news_detail() -> None:
    card = build_news_flash_card(
        news_id="n-9",
        kind="noise",
        kind_label="噪音",
        headline="标题",
        content="正文",
        day=2,
    )
    assert card.actions["detail"]["entry"] == "news_detail"
    assert card.actions["detail"]["args"] == {"news_id": "n-9"}


def test_action_spec_is_a_copy() -> None:
    card = build_welfare_card(cycle=1, amount=1050.0)
    spec = card.action_spec()
    spec["claim"]["entry"] = "tampered"
    assert card.actions["claim"]["entry"] == "daily_claim"


# ---------------------------------------------------------------- 内容细节


def test_harvest_card_contains_the_required_fields() -> None:
    card = build_harvest_card(
        tick=42,
        day=4,
        stock_id="FAKE-003",
        stock_name="环轨科技3号",
        concentration=0.62,
        severity=0.18,
        price_before=12.5,
        price_after=10.25,
        amount=40.0,
        trigger="peak",
    )
    assert card.payload["tick"] == 42
    assert card.payload["stock_id"] == "FAKE-003"
    assert card.payload["concentration"] == 0.62
    assert card.payload["severity"] == 0.18
    assert card.payload["loss"] == pytest.approx(90.0)


def test_bill_card_shows_the_gap() -> None:
    card = build_bill_card(
        day=5, living_cost=10.0, next_cost=10.0, cash=3.0, gap=7.0, missed_bills=1
    )
    assert card.payload["gap"] == 7.0
    assert card.payload["missed_bills"] == 1
    assert "7.00" in card.html


def test_kill_card_carries_reason_and_title() -> None:
    card = build_kill_card(
        death_reason="starved",
        death_title="三餐不继",
        days_survived=4,
        total_asset=0.0,
        harvest_count=1,
    )
    assert card.payload["death_reason"] == "starved"
    assert card.payload["death_title"] == "三餐不继"
    assert "starved" in card.html


def test_taunt_card_keeps_trigger_and_text() -> None:
    card = build_taunt_card(trigger="add_down_3", text="第三次了。", detail="你今天买了 3 次。")
    assert card.payload["trigger"] == "add_down_3"
    assert card.summary == "第三次了。"
    assert "你今天买了 3 次。" in card.html


def test_html_escapes_external_text() -> None:
    """任何外部文本进 HTML 前必须转义。"""
    card = build_harvest_card(
        tick=1,
        day=1,
        stock_id="FAKE-001",
        stock_name="<script>alert(1)</script>",
        concentration=0.5,
        severity=0.1,
        price_before=1.0,
        price_after=0.9,
        amount=1.0,
    )
    assert "<script>" not in card.html
    assert "&lt;script&gt;" in card.html


def test_summary_is_plain_text() -> None:
    """summary 是降级纯文本，不能带 HTML 标签。"""
    for card in _all_cards():
        assert not re.search(r"<[a-zA-Z/]", card.summary), card.kind
