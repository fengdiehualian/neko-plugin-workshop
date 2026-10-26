"""价格引擎单测。"""

from __future__ import annotations

import pytest
from engine.config import DifficultyConfig, MarketConfig
from engine.market import (
    apply_jump,
    classify_sigma,
    create_market,
    grow_sigma,
    next_price,
)

CONFIG = MarketConfig()


class _FixedRng:
    """返回固定值的伪 RNG，用于精确验证公式。"""

    def __init__(self, gauss_value: float = 0.0, random_value: float = 0.5) -> None:
        self._gauss = gauss_value
        self._random = random_value

    def gauss(self, mu: float, sigma: float) -> float:
        return self._gauss

    def random(self) -> float:
        return self._random


def test_next_price_formula():
    # 100 × (1 + 0.01 + 0.1 × 1.0) = 111
    price = next_price(100.0, mu=0.01, sigma=0.1, rng=_FixedRng(1.0))
    assert price == pytest.approx(111.0)


def test_next_price_never_negative():
    price = next_price(100.0, mu=-0.99, sigma=0.0, rng=_FixedRng(0.0))
    assert price is not None and price >= 0.0


def test_next_price_returns_none_at_or_below_floor():
    assert next_price(0.005, mu=0.0, sigma=0.0, rng=_FixedRng(0.0), floor=0.01) is None
    assert next_price(0.01, mu=0.0, sigma=0.0, rng=_FixedRng(0.0), floor=0.01) is None


def test_next_price_returns_none_after_crash():
    # 0.02 × (1 - 1.0) = 0 → 触及地板
    assert next_price(0.02, mu=0.0, sigma=1.0, rng=_FixedRng(-1.0), floor=0.01) is None


def test_next_price_is_pure():
    rng_a = _FixedRng(0.5)
    rng_b = _FixedRng(0.5)
    assert next_price(10.0, mu=0.0, sigma=0.2, rng=rng_a) == next_price(
        10.0, mu=0.0, sigma=0.2, rng=rng_b
    )


def test_apply_jump_formula():
    assert apply_jump(100.0, severity=0.5) == pytest.approx(50.0)
    assert apply_jump(100.0, severity=1.0) == pytest.approx(0.0)


def test_apply_jump_clamps_severity():
    assert apply_jump(100.0, severity=2.0) == pytest.approx(0.0)
    assert apply_jump(100.0, severity=-1.0) == pytest.approx(100.0)


def test_apply_jump_jitter_within_twenty_percent():
    low = apply_jump(100.0, severity=0.5, rng=_FixedRng(random_value=0.0))
    high = apply_jump(100.0, severity=0.5, rng=_FixedRng(random_value=1.0))
    assert low == pytest.approx(60.0)
    assert high == pytest.approx(40.0)


def test_classify_sigma_bands():
    assert classify_sigma(0.05) == "stable"
    assert classify_sigma(0.10) == "stable"
    assert classify_sigma(0.15) == "neutral"
    assert classify_sigma(0.20) == "explosive"
    assert classify_sigma(0.40) == "explosive"


def test_grow_sigma_caps():
    assert grow_sigma(0.05, 0, CONFIG) == pytest.approx(0.05)
    assert grow_sigma(0.05, 10, CONFIG) == pytest.approx(0.15)
    assert grow_sigma(0.05, 1000, CONFIG) == pytest.approx(CONFIG.sigma_max)


def test_create_market_uses_fictional_ids():
    difficulty = DifficultyConfig(mode="classic_arcade")
    stocks = create_market(42, count=20, config=CONFIG, difficulty=difficulty)
    assert len(stocks) == 20
    assert "FAKE-001" in stocks
    assert all(stock_id.startswith("FAKE-") for stock_id in stocks)
    assert all(stock.alive for stock in stocks.values())


def test_create_market_is_reproducible():
    difficulty = DifficultyConfig(mode="classic_arcade")
    first = create_market(7, count=10, config=CONFIG, difficulty=difficulty)
    second = create_market(7, count=10, config=CONFIG, difficulty=difficulty)
    assert {k: (v.price, v.name, v.sigma) for k, v in first.items()} == {
        k: (v.price, v.name, v.sigma) for k, v in second.items()
    }


def test_create_market_differs_by_seed():
    difficulty = DifficultyConfig(mode="classic_arcade")
    first = create_market(7, count=10, config=CONFIG, difficulty=difficulty)
    second = create_market(8, count=10, config=CONFIG, difficulty=difficulty)
    assert [s.price for s in first.values()] != [s.price for s in second.values()]
