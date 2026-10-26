"""成交、结算与手续费单测。"""

from __future__ import annotations

import pytest
from engine.config import FeeConfig
from engine.errors import InsufficientCashError, InvalidAmountError, TPlusOneError
from engine.models import MarketState, Player, Stock
from engine.settlement import (
    accrue_fee,
    build_settlement_report,
    buy_fee,
    check_inaction,
    execute_buy,
    execute_sell,
    sell_fee,
)

FEES = FeeConfig()


def _player(cash: float = 1000.0) -> Player:
    return Player(
        id="p1",
        name="玩家",
        cash=cash,
        mode="classic_arcade",
        initial_cash=cash,
    )


def _stock(price: float = 10.0) -> Stock:
    return Stock(
        id="FAKE-001",
        name="虚构科技1号",
        price=price,
        prev_price=price,
        mu=0.0,
        sigma=0.1,
    )


def test_buy_fee():
    assert buy_fee(1000.0, FEES) == pytest.approx(3.0)


def test_sell_fee_includes_stamp_tax():
    # 卖出佣金 0.5% + 印花税 0.1% = 0.6%
    assert sell_fee(1000.0, FEES) == pytest.approx(6.0)


def test_stamp_tax_is_one_sided():
    # 买入不收印花税
    assert buy_fee(1000.0, FEES) == pytest.approx(3.0)
    assert sell_fee(1000.0, FEES) > buy_fee(1000.0, FEES)


def test_execute_buy_updates_cash_and_position():
    player = _player(1000.0)
    trade = execute_buy(
        player, _stock(10.0), 10.0, config=FEES, day=1, tick=1, trade_id=1
    )
    assert trade.side == "buy"
    assert trade.fee == pytest.approx(0.3)
    assert player.cash == pytest.approx(1000.0 - 100.0 - 0.3)
    position = player.positions["FAKE-001"]
    assert position.amount == pytest.approx(10.0)
    # 均价含买入手续费
    assert position.avg_cost == pytest.approx(10.03)
    assert player.trade_count == 1
    assert player.total_fee_paid == pytest.approx(0.3)


def test_execute_buy_rejects_insufficient_cash():
    player = _player(10.0)
    with pytest.raises(InsufficientCashError):
        execute_buy(player, _stock(10.0), 10.0, config=FEES, day=1, tick=1, trade_id=1)


def test_execute_buy_rejects_invalid_amount():
    player = _player(1000.0)
    with pytest.raises(InvalidAmountError):
        execute_buy(player, _stock(10.0), 0.0, config=FEES, day=1, tick=1, trade_id=1)


def test_execute_sell_is_blocked_by_t_plus_one():
    player = _player(1000.0)
    stock = _stock(10.0)
    execute_buy(player, stock, 10.0, config=FEES, day=1, tick=1, trade_id=1)
    with pytest.raises(TPlusOneError):
        execute_sell(player, stock, 10.0, config=FEES, day=1, tick=2, trade_id=2)


def test_execute_sell_allowed_next_day():
    player = _player(1000.0)
    stock = _stock(10.0)
    execute_buy(player, stock, 10.0, config=FEES, day=1, tick=1, trade_id=1)
    trade = execute_sell(player, stock, 10.0, config=FEES, day=2, tick=20, trade_id=2)
    assert trade.side == "sell"
    assert player.cash == pytest.approx(1000.0 - 100.3 + 100.0 - 0.6)
    assert "FAKE-001" not in player.positions


def test_execute_sell_can_bypass_t_plus_one():
    player = _player(1000.0)
    stock = _stock(10.0)
    execute_buy(player, stock, 10.0, config=FEES, day=1, tick=1, trade_id=1)
    trade = execute_sell(
        player, stock, 10.0, config=FEES, day=1, tick=2, trade_id=2, t_plus_one=False
    )
    assert trade.amount == pytest.approx(10.0)


def test_forced_sell_uses_recovery_price_as_fee_base():
    player = _player(1000.0)
    stock = _stock(10.0)
    execute_buy(player, stock, 10.0, config=FEES, day=1, tick=1, trade_id=1)
    recovery = 8.0
    trade = execute_sell(
        player,
        stock,
        10.0,
        config=FEES,
        day=2,
        tick=20,
        trade_id=2,
        price=recovery,
        reason="pawnbroker_sell",
    )
    # 手续费按回收价 8.0 × 数量 10 计算，不是市价 10.0
    assert trade.fee == pytest.approx(80.0 * 0.006)
    assert player.cash == pytest.approx(1000.0 - 100.3 + 80.0 - 0.48)


def test_fee_pool_only_accumulates():
    market = MarketState()
    assert market.fee_pool == 0.0
    accrue_fee(market, 3.0)
    accrue_fee(market, 6.0)
    assert market.fee_pool == pytest.approx(9.0)
    assert market.total_fees == pytest.approx(9.0)
    # 手续费池不会因为任何玩家操作而减少
    accrue_fee(market, 0.0)
    assert market.fee_pool == pytest.approx(9.0)


def test_check_inaction():
    player = _player()
    assert check_inaction(player) is True
    player.trade_count = 1
    assert check_inaction(player) is False


def test_build_settlement_report_exposes_heat_and_seed():
    market = MarketState(seed=20261023, fee_pool=12.5)
    player = _player(500.0)
    player.heat_history = [0.0, 10.0, 20.0, 30.0]
    player.trade_count = 3
    player.total_fee_paid = 4.0
    report = build_settlement_report(
        player,
        market=market,
        mode="classic_arcade",
        days_survived=2,
        ticks_played=4,
        ticks_per_day=2,
    )
    assert report.seed == 20261023
    assert report.heat_curve == [0.0, 10.0, 20.0, 30.0]
    assert report.peak_heat == 30.0
    assert report.peak_day == 2
    assert report.fee_pool_total == pytest.approx(12.5)
    assert report.total_fee_paid == pytest.approx(4.0)
    assert report.trade_count == 3
