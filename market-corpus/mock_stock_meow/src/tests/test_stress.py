"""压力测试：5000 只股票跑 100 tick 不崩。"""

from __future__ import annotations

import time

from engine.config import EngineConfig
from engine.market import next_price
from engine.rng import derive_rng

CONFIG = EngineConfig()
SEED = 20261023
SYMBOL_COUNT = 5000
TICKS = 100


def test_five_thousand_symbols_hundred_ticks():
    prices = {f"FAKE-{index:04d}": 10.0 for index in range(SYMBOL_COUNT)}
    delisted = 0

    started = time.perf_counter()
    for tick in range(TICKS):
        rng = derive_rng(SEED, 1, tick)
        for stock_id, price in prices.items():
            value = next_price(
                price,
                mu=0.0,
                sigma=0.2,
                rng=rng,
                floor=CONFIG.market.delisting_floor,
            )
            if value is None:
                # 退市：保留最后一次有效价，停止参与后续行情
                prices[stock_id] = price
                delisted += 1
            else:
                prices[stock_id] = value
    elapsed = time.perf_counter() - started

    assert len(prices) == SYMBOL_COUNT
    assert all(value >= CONFIG.market.delisting_floor for value in prices.values())
    assert elapsed < 60.0, f"5000 股 × 100 tick 耗时 {elapsed:.2f}s，过慢"


def test_five_thousand_symbols_survive_many_rounds():
    """连续多轮推进不抛异常，且价格始终有限。"""
    prices = {f"FAKE-{index:04d}": 10.0 for index in range(SYMBOL_COUNT)}
    for tick in range(10):
        rng = derive_rng(SEED, 2, tick)
        for stock_id in prices:
            value = next_price(
                prices[stock_id],
                mu=0.01,
                sigma=0.3,
                rng=rng,
                floor=CONFIG.market.delisting_floor,
            )
            if value is not None:
                prices[stock_id] = value
    assert all(0.0 < value < 1e6 for value in prices.values())
