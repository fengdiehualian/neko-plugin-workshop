"""猫娘策略单测。

重点验证三件事：
1. 现金充足时她只做小额低频交易；
2. 现金不足时她先卖浮亏最少的，救不回来就穷困潦倒；
3. **引擎里不存在猫娘豁免分支**——猫娘与人类玩家走同一套 heat 逻辑。
"""

from __future__ import annotations

import pathlib

import pytest
from engine.config import CatgirlConfig
from engine.models import MarketState, Player, Position, Stock
from engine.npc import catgirl_daily_plan

CONFIG = CatgirlConfig()
ENGINE_DIR = pathlib.Path(__file__).resolve().parents[1] / "engine"


class _FakeRng:
    """可控 RNG：random() 固定值，shuffle 不改变顺序。"""

    def __init__(self, random_value: float = 0.0) -> None:
        self._value = random_value

    def random(self) -> float:
        return self._value

    def shuffle(self, items):
        return items


def _market(prices: dict[str, float]) -> MarketState:
    stocks = {
        stock_id: Stock(
            id=stock_id,
            name=f"{stock_id}虚构标的",
            price=price,
            prev_price=price,
            mu=0.0,
            sigma=0.1,
        )
        for stock_id, price in prices.items()
    }
    return MarketState(stocks=stocks)


def _catgirl(cash: float) -> Player:
    return Player(
        id="cat",
        name="猫娘",
        cash=cash,
        mode="infinite",
        day=2,
        is_catgirl=True,
    )


def test_enough_cash_buys_small_and_few():
    player = _catgirl(1000.0)
    market = _market({"FAKE-001": 10.0, "FAKE-002": 20.0, "FAKE-003": 30.0})
    actions = catgirl_daily_plan(
        player,
        market=market,
        config=CONFIG,
        rng=_FakeRng(0.0),
        next_day_living_cost=150.0,
    )
    assert actions, "现金充足时猫娘应该会小额交易"
    assert all(action.kind == "buy" for action in actions)
    assert len(actions) <= CONFIG.max_positions
    for action in actions:
        price = market.stocks[action.stock_id].price
        assert action.amount * price <= player.cash * CONFIG.trade_cash_ratio + 1e-9


def test_enough_cash_does_not_chase_recent_high():
    player = _catgirl(1000.0)
    market = _market({"FAKE-001": 100.0})
    market.stocks["FAKE-001"].kline = [(1, 10.0), (2, 20.0)]
    actions = catgirl_daily_plan(
        player,
        market=market,
        config=CONFIG,
        rng=_FakeRng(0.0),
        next_day_living_cost=150.0,
    )
    assert actions == []


def test_short_cash_sells_smallest_loss_first():
    player = _catgirl(10.0)
    player.positions = {
        "FAKE-001": Position(stock_id="FAKE-001", amount=10.0, avg_cost=10.0),
        "FAKE-002": Position(stock_id="FAKE-002", amount=10.0, avg_cost=20.0),
    }
    market = _market({"FAKE-001": 10.0, "FAKE-002": 10.0})
    actions = catgirl_daily_plan(
        player,
        market=market,
        config=CONFIG,
        rng=_FakeRng(0.0),
        next_day_living_cost=150.0,
    )
    sells = [action for action in actions if action.kind == "sell"]
    assert sells, "现金不足时猫娘应该卖持仓"
    # FAKE-001 浮亏 0，FAKE-002 浮亏 100 → 先卖 FAKE-001
    assert sells[0].stock_id == "FAKE-001"


def test_short_cash_uses_pawnbroker_when_t_plus_one_locks():
    player = _catgirl(10.0)
    player.positions = {
        "FAKE-001": Position(
            stock_id="FAKE-001",
            amount=10.0,
            avg_cost=10.0,
            today_bought=10.0,
            today_bought_day=2,
        )
    }
    market = _market({"FAKE-001": 10.0})
    actions = catgirl_daily_plan(
        player,
        market=market,
        config=CONFIG,
        rng=_FakeRng(0.0),
        next_day_living_cost=150.0,
    )
    forced = [action for action in actions if action.kind == "pawnbroker_sell"]
    assert forced, "T+1 锁死时应该走二道贩子"
    assert forced[0].price is not None and forced[0].price < 10.0
    # 回收价按极急折扣：市价 10 × 0.5
    assert forced[0].price == pytest.approx(5.0)


def test_starves_when_nothing_can_help():
    player = _catgirl(0.0)
    market = _market({"FAKE-001": 10.0})
    actions = catgirl_daily_plan(
        player,
        market=market,
        config=CONFIG,
        rng=_FakeRng(0.0),
        next_day_living_cost=150.0,
    )
    assert any(action.kind == "starve" for action in actions)


def test_catgirl_does_not_sell_the_same_position_twice():
    player = _catgirl(10.0)
    player.positions = {
        "FAKE-001": Position(stock_id="FAKE-001", amount=10.0, avg_cost=10.0),
    }
    market = _market({"FAKE-001": 10.0})
    actions = catgirl_daily_plan(
        player,
        market=market,
        config=CONFIG,
        rng=_FakeRng(0.0),
        next_day_living_cost=150.0,
    )
    stock_ids = [action.stock_id for action in actions if action.stock_id]
    assert len(stock_ids) == len(set(stock_ids))


def test_no_catgirl_exemption_branch_anywhere_in_engine():
    """猫娘豁免分支是**结构性禁止**的。

    除 ``npc.py``（策略实现）、``config.py``（参数）与 ``models.py``（数据字段）
    之外，引擎其他模块不得出现 ``is_catgirl`` 分支判断。
    """
    offenders = []
    for path in sorted(ENGINE_DIR.glob("*.py")):
        if path.name in {"npc.py", "config.py", "models.py", "__init__.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        if "is_catgirl" in text:
            offenders.append(path.name)
    assert offenders == [], f"这些模块出现了猫娘豁免分支：{offenders}"


def test_settlement_has_no_is_catgirl_branch():
    text = (ENGINE_DIR / "settlement.py").read_text(encoding="utf-8")
    assert "is_catgirl" not in text
