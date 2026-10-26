"""上头指数（heat）单测。"""

from __future__ import annotations

import pytest
from engine.config import HeatConfig
from engine.heat import (
    classify_concentration,
    concentration_score,
    heat_events_for_trade,
    is_at_peak,
    position_concentration,
    update_heat,
)
from engine.models import HeatEvent, HeatEventContext, Position

CONFIG = HeatConfig()


def test_decay_only_without_events():
    assert update_heat(100.0, events=[], concentration=0.0, config=CONFIG) == pytest.approx(85.0)


def test_event_scores_are_added():
    assert update_heat(0.0, events=[HeatEvent.LOSS_AVERAGING], concentration=0.0, config=CONFIG) == 15.0
    assert update_heat(0.0, events=[HeatEvent.CHASE_HIGH], concentration=0.0, config=CONFIG) == 10.0
    assert update_heat(0.0, events=[HeatEvent.LOSS_LEVERAGE], concentration=0.0, config=CONFIG) == 30.0
    assert update_heat(0.0, events=[HeatEvent.HIGH_FREQUENCY], concentration=0.0, config=CONFIG) == 5.0
    assert update_heat(0.0, events=[HeatEvent.FAKE_NEWS_CHASE], concentration=0.0, config=CONFIG) == 10.0
    assert update_heat(
        0.0, events=[HeatEvent.THUNDER_NEWS_AVERAGING], concentration=0.0, config=CONFIG
    ) == 20.0
    assert update_heat(
        0.0, events=[HeatEvent.LIVING_COST_AVERAGING], concentration=0.0, config=CONFIG
    ) == 20.0


def test_heat_never_negative():
    assert update_heat(0.0, events=[], concentration=0.0, config=CONFIG) >= 0.0


def test_concentration_bands():
    assert concentration_score(0.49, CONFIG) == 0.0
    assert concentration_score(0.50, CONFIG) == 10.0
    assert concentration_score(0.69, CONFIG) == 10.0
    assert concentration_score(0.70, CONFIG) == 25.0
    assert concentration_score(0.89, CONFIG) == 25.0
    assert concentration_score(0.90, CONFIG) == 40.0


def test_concentration_weight_scales_state_score():
    weighted = HeatConfig(concentration_weight=0.5)
    assert update_heat(0.0, events=[], concentration=0.9, config=weighted) == pytest.approx(20.0)
    assert update_heat(0.0, events=[], concentration=0.9, config=CONFIG) == pytest.approx(40.0)


def test_classify_concentration():
    assert classify_concentration(0.0) == "none"
    assert classify_concentration(0.5) == "low"
    assert classify_concentration(0.7) == "mid"
    assert classify_concentration(0.95) == "high"


def test_position_concentration():
    positions = {
        "FAKE-001": Position(stock_id="FAKE-001", amount=10.0, avg_cost=5.0),
        "FAKE-002": Position(stock_id="FAKE-002", amount=10.0, avg_cost=5.0),
    }
    prices = {"FAKE-001": 10.0, "FAKE-002": 10.0}
    assert position_concentration(positions, prices) == pytest.approx(0.5)
    prices = {"FAKE-001": 30.0, "FAKE-002": 10.0}
    assert position_concentration(positions, prices) == pytest.approx(0.75)
    assert position_concentration({}, {}) == 0.0


def test_is_at_peak_true():
    assert is_at_peak([0.0, 50.0, 40.0], threshold=40.0, window=5) is True


def test_is_at_peak_requires_threshold_strictly():
    assert is_at_peak([0.0, 40.0, 30.0], threshold=40.0, window=5) is False


def test_is_at_peak_requires_fall():
    assert is_at_peak([0.0, 50.0, 60.0], threshold=40.0, window=5) is False


def test_is_at_peak_requires_window_highest():
    # 更早的 tick 曾经更高 → 不是近窗口最高点
    assert is_at_peak([100.0, 10.0, 50.0, 40.0], threshold=40.0, window=5) is False


def test_is_at_peak_allows_tie():
    assert is_at_peak([50.0, 50.0, 40.0], threshold=40.0, window=5) is True


def test_is_at_peak_short_history():
    assert is_at_peak([], threshold=40.0, window=5) is False
    assert is_at_peak([50.0], threshold=40.0, window=5) is False


def test_is_at_peak_window_boundary():
    # 窗口含 H_old 自身，取最近 5 个值：更早的第 7 个值（90）不算数
    assert is_at_peak([90.0, 0.0, 10.0, 20.0, 30.0, 50.0, 40.0], threshold=40.0, window=5) is True


def test_heat_events_for_trade():
    context = HeatEventContext(
        is_loss_position=True,
        is_leverage=True,
        is_chase_high=True,
        high_frequency=True,
        after_fake_news=True,
        after_thunder_news=True,
        living_cost_averaging=True,
    )
    events = heat_events_for_trade(context, CONFIG)
    assert set(events) == {
        HeatEvent.LOSS_AVERAGING,
        HeatEvent.CHASE_HIGH,
        HeatEvent.LOSS_LEVERAGE,
        HeatEvent.HIGH_FREQUENCY,
        HeatEvent.FAKE_NEWS_CHASE,
        HeatEvent.THUNDER_NEWS_AVERAGING,
        HeatEvent.LIVING_COST_AVERAGING,
    }


def test_heat_events_for_trade_plain_trade():
    assert heat_events_for_trade(HeatEventContext(), CONFIG) == []


def test_living_cost_averaging_needs_loss_position():
    context = HeatEventContext(living_cost_averaging=True)
    events = heat_events_for_trade(context, CONFIG)
    assert HeatEvent.LIVING_COST_AVERAGING in events
    assert HeatEvent.LOSS_AVERAGING not in events
