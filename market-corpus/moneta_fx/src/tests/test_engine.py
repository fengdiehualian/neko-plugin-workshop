# isort: skip_file
"""引擎单元测试：下单、保证金、保护单、隔夜费、强平、幂等。

测试以平铺方式导入被测模块（from engine import ...）。CI 把插件目录拷进
N.E.K.O. 树后没有 .git，ruff 无法判定这些是首方模块，会和 pytest 混成一组；
这里的 isort pragma 让导入分组保持人工排好的样子。
"""

from __future__ import annotations

import pytest

from engine import (
    TradeError,
    cancel_order,
    close_position,
    create_account,
    estimate_order,
    metrics,
    modify_position,
    place_order,
    process_account,
)
from market import OFFLINE_SYMBOLS, WATCHLIST, bootstrap_market, digits, pip_size, symbol_name


@pytest.fixture()
def market():
    return bootstrap_market(seed=735191, now_ms=1_700_000_000_000)


@pytest.fixture()
def quotes(market):
    return market.quotes(OFFLINE_SYMBOLS)


@pytest.fixture()
def account():
    return create_account("shared", 1_700_000_000_000, 100000.0)


def test_market_bootstraps_watchlist_bars(market):
    assert set(market.bars) == set(WATCHLIST)
    assert all(len(bars) > 300 for bars in market.bars.values())


def test_symbol_universe_covers_crosses():
    assert "EUR/USD" in OFFLINE_SYMBOLS
    assert "USD/CNY" in OFFLINE_SYMBOLS
    assert "GBP/JPY" in OFFLINE_SYMBOLS
    assert len(OFFLINE_SYMBOLS) > 300


def test_pip_size_and_digits():
    assert pip_size("EUR/USD") == 0.0001
    assert pip_size("USD/JPY") == 0.01
    assert digits("EUR/USD") == 5
    assert digits("USD/JPY") == 3


def test_symbol_name_is_localised():
    assert symbol_name("EUR/USD") == "欧元 / 美元"


def test_market_order_fills_and_charges_commission(account, quotes):
    now = 1_700_000_000_000
    position = place_order(account, {"symbol": "EUR/USD", "side": "buy", "type": "market", "lots": 0.10}, quotes, now)
    assert position["lots"] == 0.10
    assert len(account["positions"]) == 1
    assert account["balance"] < 100000.0
    assert position["margin"] > 0


def test_limit_order_stays_pending(account, quotes):
    now = 1_700_000_000_000
    pending = place_order(
        account,
        {"symbol": "EUR/USD", "side": "buy", "type": "limit", "lots": 0.05, "price": quotes["EUR/USD"]["bid"] * 0.99},
        quotes,
        now,
    )
    assert pending["type"] == "limit"
    assert account["pending"] == [pending]
    assert account["positions"] == []


def test_limit_price_direction_is_validated(account, quotes):
    now = 1_700_000_000_000
    with pytest.raises(TradeError, match="挂单价格方向不正确"):
        place_order(
            account,
            {"symbol": "EUR/USD", "side": "buy", "type": "limit", "lots": 0.05, "price": quotes["EUR/USD"]["ask"] * 1.01},
            quotes,
            now,
        )


def test_stop_order_direction_is_validated(account, quotes):
    now = 1_700_000_000_000
    with pytest.raises(TradeError, match="挂单价格方向不正确"):
        place_order(
            account,
            {"symbol": "EUR/USD", "side": "buy", "type": "stop", "lots": 0.05, "price": quotes["EUR/USD"]["bid"] * 0.99},
            quotes,
            now,
        )


def test_protection_direction_is_validated(account, quotes):
    now = 1_700_000_000_000
    with pytest.raises(TradeError, match="止损价格方向不正确"):
        place_order(
            account,
            {"symbol": "EUR/USD", "side": "buy", "type": "market", "lots": 0.10, "sl": quotes["EUR/USD"]["ask"] * 1.01},
            quotes,
            now,
        )
    with pytest.raises(TradeError, match="止盈价格方向不正确"):
        place_order(
            account,
            {"symbol": "EUR/USD", "side": "buy", "type": "market", "lots": 0.10, "tp": quotes["EUR/USD"]["ask"] * 0.99},
            quotes,
            now,
        )


def test_lots_must_be_a_cent_multiple(account, quotes):
    now = 1_700_000_000_000
    with pytest.raises(TradeError, match="手数"):
        place_order(account, {"symbol": "EUR/USD", "side": "buy", "type": "market", "lots": 0.005}, quotes, now)


def test_unknown_symbol_is_rejected(account, quotes):
    now = 1_700_000_000_000
    with pytest.raises(TradeError):
        place_order(account, {"symbol": "XXX/YYY", "side": "buy", "type": "market", "lots": 0.10}, quotes, now)


def test_estimate_reports_margin_commission_and_spread(account, quotes):
    estimate = estimate_order(account, {"symbol": "EUR/USD", "side": "buy", "type": "market", "lots": 0.10}, quotes)
    assert estimate["margin"] > 0
    assert estimate["commission"] == pytest.approx(0.10 * account["settings"]["commission"])
    assert estimate["spreadCost"] >= 0


def test_close_position_books_history(account, quotes):
    now = 1_700_000_000_000
    position = place_order(account, {"symbol": "EUR/USD", "side": "buy", "type": "market", "lots": 0.10}, quotes, now)
    closed = close_position(account, position["id"], quotes, now + 60000)
    assert closed["reason"] == "手动平仓"
    assert account["positions"] == []
    assert account["history"] == [closed]


def test_partial_close_keeps_remainder(account, quotes):
    now = 1_700_000_000_000
    position = place_order(account, {"symbol": "EUR/USD", "side": "buy", "type": "market", "lots": 0.20}, quotes, now)
    close_position(account, position["id"], quotes, now + 60000, 0.05)
    assert len(account["positions"]) == 1
    assert account["positions"][0]["lots"] == pytest.approx(0.15)
    assert len(account["history"]) == 1


def test_modify_position_updates_protection(account, quotes):
    now = 1_700_000_000_000
    position = place_order(account, {"symbol": "EUR/USD", "side": "buy", "type": "market", "lots": 0.10}, quotes, now)
    bid = quotes["EUR/USD"]["bid"]
    modify_position(account, position["id"], quotes, sl=bid * 0.99, tp=bid * 1.01)
    assert account["positions"][0]["sl"] == pytest.approx(bid * 0.99)
    assert account["positions"][0]["tp"] == pytest.approx(bid * 1.01)


def test_cancel_order_removes_pending(account, quotes):
    now = 1_700_000_000_000
    pending = place_order(
        account,
        {"symbol": "EUR/USD", "side": "buy", "type": "limit", "lots": 0.05, "price": quotes["EUR/USD"]["bid"] * 0.99},
        quotes,
        now,
    )
    cancel_order(account, pending["id"], now + 1000)
    assert account["pending"] == []


def test_metrics_reports_null_margin_level_when_flat(account, quotes):
    result = metrics(account, quotes)
    assert result["margin"] == 0
    assert result["marginLevel"] is None
    assert result["equity"] == pytest.approx(100000.0)


def test_process_account_triggers_stop_loss(account, quotes, market):
    now = market.time
    position = place_order(account, {"symbol": "EUR/USD", "side": "buy", "type": "market", "lots": 0.10}, quotes, now)
    bid = quotes["EUR/USD"]["bid"]
    # 多单止损必须低于当前可平仓价，先把报价推高，再在下方设止损。
    quotes["EUR/USD"]["bid"] = bid * 1.01
    quotes["EUR/USD"]["ask"] = bid * 1.011
    modify_position(account, position["id"], quotes, sl=bid * 1.005)
    # 价格回落到止损之下，触发多单止损。
    quotes["EUR/USD"]["bid"] = bid * 1.002
    quotes["EUR/USD"]["ask"] = bid * 1.003
    process_account(account, quotes, now + 1000)
    assert account["positions"] == []
    assert account["history"][-1]["reason"] == "止损"


def test_margin_call_liquidates_worst_position(account, quotes):
    now = 1_700_000_000_000
    position = place_order(account, {"symbol": "EUR/USD", "side": "buy", "type": "market", "lots": 1.0}, quotes, now)
    # 直接构造资不抵债：巨额浮动亏损 + 极低余额，保证金水平远低于 50%。
    position["lastPnl"] = -90000.0
    account["balance"] = 100.0
    process_account(account, quotes, now + 1000)
    assert account["positions"] == []
    assert any(t["reason"] == "强制平仓" for t in account["history"])
