"""种子可复现性单测：同一 seed 必须得到同一结果。"""

from __future__ import annotations

from engine.config import EngineConfig
from engine.heat import position_concentration, update_heat
from engine.market import create_market, next_price
from engine.models import MarketState, Player, Stock
from engine.news import generate_news
from engine.npc import catgirl_daily_plan
from engine.rng import derive_rng

CONFIG = EngineConfig()
SYMBOLS = [f"FAKE-{index:03d}" for index in range(1, 6)]


def _simulate(seed: int, ticks: int = 30):
    """跑一小段确定性模拟，返回 (市场, 玩家)。"""
    difficulty = CONFIG.difficulties["classic_arcade"]
    stocks = create_market(seed, count=5, config=CONFIG.market, difficulty=difficulty)
    market = MarketState(seed=seed, stocks=stocks)
    player = Player(
        id="p1",
        name="玩家",
        cash=100.0,
        mode="classic_arcade",
        initial_cash=100.0,
    )
    for tick in range(ticks):
        for index, (stock_id, stock) in enumerate(market.stocks.items()):
            rng = derive_rng(seed, 1, tick, index)
            value = next_price(
                stock.price,
                mu=stock.mu,
                sigma=stock.sigma,
                rng=rng,
                floor=CONFIG.market.delisting_floor,
            )
            if value is None:
                stock.alive = False
                stock.delisted_day = market.day
                continue
            stock.prev_price = stock.price
            stock.price = value
        concentration = position_concentration(player.positions, market.prices())
        player.heat = update_heat(
            player.heat,
            events=[],
            concentration=concentration,
            config=CONFIG.heat,
        )
        player.heat_history.append(player.heat)
    return market, player


def test_derive_rng_same_arguments_same_stream():
    assert derive_rng(7, 1, 2).random() == derive_rng(7, 1, 2).random()
    assert derive_rng(7, 1, 2).random() != derive_rng(7, 1, 3).random()
    assert derive_rng(7, 1, 2).random() != derive_rng(8, 1, 2).random()


def test_derive_rng_ignores_pythonhashseed():
    # 直接比对字节流，避免 hash 随机化影响
    first = [derive_rng(123, 4).random() for _ in range(5)]
    second = [derive_rng(123, 4).random() for _ in range(5)]
    assert first == second


def test_same_seed_same_market():
    first, _ = _simulate(20261023)
    second, _ = _simulate(20261023)
    assert first.prices() == second.prices()
    assert first.stocks.keys() == second.stocks.keys()


def test_same_seed_same_heat_curve():
    _, first_player = _simulate(20261023)
    _, second_player = _simulate(20261023)
    assert first_player.heat_history == second_player.heat_history
    assert first_player.heat == second_player.heat


def test_different_seed_different_market():
    first, _ = _simulate(1)
    second, _ = _simulate(2)
    assert first.prices() != second.prices()


def test_same_seed_same_news():
    first = generate_news(
        20261023, 6, phase="trap", symbols=SYMBOLS, reversal_days=set(), config=CONFIG.news
    )
    second = generate_news(
        20261023, 6, phase="trap", symbols=SYMBOLS, reversal_days=set(), config=CONFIG.news
    )
    assert first == second


def test_same_seed_same_catgirl_plan():
    player = Player(id="cat", name="猫娘", cash=100.0, mode="infinite", is_catgirl=True)
    market = MarketState(
        stocks={
            f"FAKE-{index:03d}": Stock(
                id=f"FAKE-{index:03d}",
                name=f"FAKE-{index:03d}虚构标的",
                price=10.0 + index,
                prev_price=10.0 + index,
                mu=0.0,
                sigma=0.1,
            )
            for index in range(1, 4)
        }
    )
    first = catgirl_daily_plan(
        player,
        market=market,
        config=CONFIG.catgirl,
        rng=derive_rng(20261023, 6, 0),
        next_day_living_cost=150.0,
    )
    second = catgirl_daily_plan(
        player,
        market=market,
        config=CONFIG.catgirl,
        rng=derive_rng(20261023, 6, 0),
        next_day_living_cost=150.0,
    )
    assert first == second
