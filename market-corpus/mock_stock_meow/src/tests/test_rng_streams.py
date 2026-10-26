"""P3：六条随机流必须互相独立（决策 ⑥）。

「独立」的准确含义：**改动其中一条流的 ID 或用法，另外五条流抽到的数字
一个都不变。** 这是"同 seed 可复现"的地基——否则调一下猫娘策略就会把
行情走势也带跑偏。

六条流的名称与用途：

===========  =============  ==========================================
流名          流 ID          用途
===========  =============  ==========================================
market        100001        行情流：tick 序列的价格推进
catgirl       100002        猫娘流：猫娘每日决策
news          100003        新闻流：新闻生成与反转日判定
pawnbroker    100004        二道贩子流：二道贩子定价
harvest       100005        收割流：收割跳跃的 severity 抖动
delisting     100006        退市流：退市缓冲区判定
===========  =============  ==========================================
"""

from __future__ import annotations

from itertools import combinations

import engine.market as market_module
from engine.config import (
    STREAM_CATGIRL,
    STREAM_DELISTING,
    STREAM_HARVEST,
    STREAM_MARKET,
    STREAM_NEWS,
    STREAM_PAWNBROKER,
    EngineConfig,
)
from engine.models import MarketState
from engine.rng import derive_stream_rng

STREAMS: dict[str, int] = {
    "market": STREAM_MARKET,
    "catgirl": STREAM_CATGIRL,
    "news": STREAM_NEWS,
    "pawnbroker": STREAM_PAWNBROKER,
    "harvest": STREAM_HARVEST,
    "delisting": STREAM_DELISTING,
}

SEED = 20261023


def _draw(stream_id: int, seed: int = SEED, parts: tuple[int, ...] = (1, 2)):
    return [derive_stream_rng(seed, stream_id, *parts).random() for _ in range(4)]


# ---------------------------------------------------------------- 声明


def test_six_streams_are_declared_and_distinct():
    """六条流，流 ID 互不相同。"""
    assert len(STREAMS) == 6
    assert len(set(STREAMS.values())) == 6


def test_stream_ids_are_ints():
    """流 ID 必须是整数（derive_rng 只接受 int，不能传字符串）。"""
    for stream_id in STREAMS.values():
        assert isinstance(stream_id, int)


# ---------------------------------------------------------------- 独立性


def test_streams_are_pairwise_different():
    """任意两条流抽到的数字都不同。"""
    for left, right in combinations(STREAMS.values(), 2):
        assert _draw(left) != _draw(right)


def test_changing_one_stream_does_not_affect_the_others():
    """改一条流，其余五条的数字**一个都不变**。"""
    modified = dict(STREAMS)
    modified["news"] = STREAM_NEWS + 1000
    for name, stream_id in STREAMS.items():
        if name == "news":
            continue
        assert _draw(stream_id) == _draw(modified[name]), name


def test_same_stream_and_seed_gives_same_numbers():
    """同一条流 + 同一个 seed → 永远同一串数字。"""
    assert _draw(STREAM_MARKET) == _draw(STREAM_MARKET)
    assert _draw(STREAM_MARKET, seed=1) != _draw(STREAM_MARKET, seed=2)


def test_different_parts_give_different_numbers():
    """同一条流的不同上下文也是互相独立的。"""
    assert _draw(STREAM_MARKET, parts=(1, 2)) != _draw(STREAM_MARKET, parts=(1, 3))


# ---------------------------------------------------------------- 引擎确实在用各自的流


def _first_tick_prices(seed: int = SEED) -> dict[str, float]:
    config = EngineConfig()
    difficulty = config.difficulties["classic_arcade"]
    stocks = market_module.create_market(
        seed, count=20, config=config.market, difficulty=difficulty
    )
    market = MarketState(seed=seed, stocks=stocks)
    market_module.advance_market(
        market, seed=seed, ticks_per_day=12, config=config.market
    )
    return market.prices()


def test_delisting_stream_does_not_change_prices(monkeypatch):
    """退市流只管"要不要退市"，不参与价格计算。"""
    baseline = _first_tick_prices()
    monkeypatch.setattr(
        market_module, "STREAM_DELISTING", market_module.STREAM_DELISTING + 777
    )
    assert _first_tick_prices() == baseline


def test_market_stream_does_change_prices(monkeypatch):
    """行情流参与价格计算——改了它价格就变。"""
    baseline = _first_tick_prices()
    monkeypatch.setattr(
        market_module, "STREAM_MARKET", market_module.STREAM_MARKET + 777
    )
    assert _first_tick_prices() != baseline
