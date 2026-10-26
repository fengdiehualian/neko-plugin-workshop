"""二道贩子定价单测。"""

from __future__ import annotations

import pytest
from engine.pawnbroker import (
    forced_buy_price,
    forced_sell_price,
    same_day_penalty,
    urgency,
)


def test_urgency_is_shortfall_over_next_living_cost():
    assert urgency(75.0, 150.0) == pytest.approx(0.5)
    assert urgency(150.0, 150.0) == pytest.approx(1.0)


def test_urgency_is_clamped():
    assert urgency(1000.0, 150.0) == pytest.approx(1.0)
    assert urgency(-10.0, 150.0) == 0.0


def test_urgency_without_living_cost():
    assert urgency(100.0, 0.0) == 0.0


def test_forced_sell_price_normal_and_urgent():
    # 常态约 8 折
    assert forced_sell_price(100.0, 0.0) == pytest.approx(80.0)
    # 极急约 5 折
    assert forced_sell_price(100.0, 1.0) == pytest.approx(50.0)
    # 中间值线性插值
    assert forced_sell_price(100.0, 0.5) == pytest.approx(65.0)


def test_forced_buy_price_normal_and_urgent():
    assert forced_buy_price(100.0, 0.0) == pytest.approx(115.0)
    assert forced_buy_price(100.0, 1.0) == pytest.approx(140.0)


def test_forced_prices_never_negative():
    assert forced_sell_price(10.0, 1.0) >= 0.0
    assert forced_sell_price(10.0, 5.0) >= 0.0


def test_same_day_penalty():
    # 当天买入的标的被强制卖出时再打 4 折 → ×0.6
    assert same_day_penalty(100.0) == pytest.approx(60.0)


def test_same_day_penalty_applies_after_recovery_price():
    recovery = forced_sell_price(100.0, 0.0)
    assert same_day_penalty(recovery) == pytest.approx(48.0)
