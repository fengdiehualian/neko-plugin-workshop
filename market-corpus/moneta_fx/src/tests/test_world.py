# isort: skip_file
"""共享市场运行时测试：账户、行情控制、资金调度、存档与停机补绘。

测试以平铺方式导入被测模块（from engine import ...）。CI 把插件目录拷进
N.E.K.O. 树后没有 .git，ruff 无法判定这些是首方模块，会和 pytest 混成一组；
这里的 isort pragma 让导入分组保持人工排好的样子。
"""

from __future__ import annotations

import time

import pytest

from engine import TradeError
from market import OFFLINE_SYMBOLS
from world import MarketRuntime


@pytest.fixture()
def runtime():
    return MarketRuntime(initial_balance=100000.0, tick_interval_ms=10**9, persist_interval_ms=10**9)


@pytest.fixture()
def player(runtime):
    return runtime.world.player(runtime.register("player_one", "password123")["user"]["id"])


@pytest.fixture()
def developer(runtime):
    return runtime.world.developer()


def test_register_creates_player_with_initial_balance(runtime):
    result = runtime.register("new_trader", "password123")
    assert result["user"]["role"] == "player"
    user = runtime.world.player(result["user"]["id"])
    assert user["account"]["balance"] == 100000.0


def test_register_rejects_duplicate_username(runtime):
    runtime.register("dupe", "password123")
    with pytest.raises(TradeError, match="用户名已存在"):
        runtime.register("dupe", "password123")


def test_login_rejects_wrong_password(runtime):
    runtime.register("trader", "password123")
    with pytest.raises(TradeError, match="用户名或密码不正确"):
        runtime.login("trader", "wrong-password")


def test_username_and_password_are_validated(runtime):
    with pytest.raises(TradeError, match="用户名"):
        runtime.register("x", "password123")
    with pytest.raises(TradeError, match="密码"):
        runtime.register("valid_name", "short")


def test_logout_drops_every_session(runtime):
    result = runtime.register("trader", "password123")
    user_id = result["user"]["id"]
    assert user_id in runtime.sessions.active_user_ids()
    runtime.logout_user(user_id)
    assert user_id not in runtime.sessions.active_user_ids()


def test_snapshot_contains_account_quotes_and_bars(runtime, player):
    snapshot = runtime.snapshot(player, "EUR/USD")
    assert snapshot["book"]["account"]["balance"] == 100000.0
    assert "EUR/USD" in snapshot["book"]["quotes"]
    assert snapshot["book"]["bars"]["EUR/USD"]


def test_snapshot_rejects_unknown_symbol(runtime, player):
    with pytest.raises(TradeError, match="不支持的虚构货币对"):
        runtime.snapshot(player, "XXX/YYY")


def test_trade_is_idempotent_per_request_id(runtime, player):
    body = {
        "action": "order",
        "order": {"symbol": "EUR/USD", "side": "buy", "type": "market", "lots": 0.10},
        "requestId": "req-1",
        "accountId": player["id"],
    }
    first = runtime.trade(player, dict(body))
    second = runtime.trade(player, dict(body))
    assert len(first["book"]["account"]["positions"]) == 1
    assert len(second["book"]["account"]["positions"]) == 1


def test_trade_rejects_request_id_reuse_with_different_body(runtime, player):
    body = {
        "action": "order",
        "order": {"symbol": "EUR/USD", "side": "buy", "type": "market", "lots": 0.10},
        "requestId": "req-1",
        "accountId": player["id"],
    }
    runtime.trade(player, dict(body))
    body["order"]["lots"] = 0.20
    with pytest.raises(TradeError, match="同一交易编号不能用于不同指令"):
        runtime.trade(player, body)


def test_trade_rejects_mismatched_account(runtime, player):
    with pytest.raises(TradeError, match="登录账户已变化"):
        runtime.trade(player, {"action": "closeAll", "requestId": "r", "accountId": "someone-else"})


def test_rejected_trade_restores_account(runtime, player):
    body = {
        "action": "order",
        "order": {"symbol": "XXX/YYY", "side": "buy", "type": "market", "lots": 0.10},
        "requestId": "bad-1",
        "accountId": player["id"],
    }
    with pytest.raises(TradeError):
        runtime.trade(player, body)
    assert player["account"]["positions"] == []
    assert player["account"]["balance"] == 100000.0


def test_close_all_flattens_every_position(runtime, player):
    for index, symbol in enumerate(("EUR/USD", "GBP/USD")):
        runtime.trade(player, {
            "action": "order",
            "order": {"symbol": symbol, "side": "buy", "type": "market", "lots": 0.10},
            "requestId": f"open-{index}",
            "accountId": player["id"],
        })
    assert len(player["account"]["positions"]) == 2
    runtime.trade(player, {"action": "closeAll", "requestId": "close-all", "accountId": player["id"]})
    assert player["account"]["positions"] == []


def test_control_requires_developer(runtime, player):
    with pytest.raises(TradeError, match="只有开发者可以操控行情"):
        runtime.control(player, {"kind": "clock", "speed": 5})


def test_price_control_moves_the_pair(runtime, developer):
    before = runtime.world.market.values["EUR"]
    runtime.control(developer, {"kind": "price", "symbol": "EUR/USD", "price": 1.10})
    assert runtime.world.market.values["EUR"] == pytest.approx(1.10)
    assert runtime.world.market.values["EUR"] != pytest.approx(before)


def test_price_control_rejects_out_of_range(runtime, developer):
    with pytest.raises(TradeError, match="50% 至 200%"):
        runtime.control(developer, {"kind": "price", "symbol": "EUR/USD", "price": 99.0})


def test_trend_control_creates_and_cancels_plans(runtime, developer):
    runtime.control(developer, {"kind": "trend", "currency": "GBP", "percent": 2, "seconds": 60})
    assert len(runtime.world.plans) == 1
    runtime.advance(30)
    assert runtime.world.plans[0]["remaining"] < 60
    runtime.control(developer, {"kind": "cancel-trends"})
    assert runtime.world.plans == []


def test_trend_control_validates_bounds(runtime, developer):
    with pytest.raises(TradeError, match="幅度"):
        runtime.control(developer, {"kind": "trend", "currency": "GBP", "percent": 99, "seconds": 60})
    with pytest.raises(TradeError, match="时长"):
        runtime.control(developer, {"kind": "trend", "currency": "GBP", "percent": 1, "seconds": 1})


def test_clock_control_sets_speed_and_pause(runtime, developer):
    runtime.control(developer, {"kind": "clock", "speed": 20, "paused": True})
    assert runtime.world.speed == 20
    assert runtime.world.paused is True


def test_clock_control_rejects_unknown_speed(runtime, developer):
    with pytest.raises(TradeError, match="时钟参数不正确"):
        runtime.control(developer, {"kind": "clock", "speed": 7})


def test_volatility_control_validates_range(runtime, developer):
    runtime.control(developer, {"kind": "volatility", "value": 3.0})
    assert runtime.world.market.volatility == 3.0
    with pytest.raises(TradeError, match="波动强度"):
        runtime.control(developer, {"kind": "volatility", "value": 99})


def test_event_control_accepts_known_scenarios(runtime, developer):
    for event in ("dollar", "euro", "yen", "storm"):
        runtime.control(developer, {"kind": "event", "event": event})
    with pytest.raises(TradeError, match="未知虚构事件"):
        runtime.control(developer, {"kind": "event", "event": "meteor"})


def test_admin_accounts_lists_players_only(runtime):
    runtime.register("alpha", "password123")
    runtime.register("beta", "password123")
    admin = runtime.admin_accounts()
    assert admin["totals"]["accounts"] == 2
    assert {a["username"] for a in admin["accounts"]} == {"alpha", "beta"}


def test_admin_account_detail_requires_a_player(runtime):
    with pytest.raises(TradeError, match="普通账户不存在"):
        runtime.admin_account_detail("developer")


def test_funding_credit_debit_and_transfer(runtime, developer):
    source = runtime.world.player(runtime.register("source", "password123")["user"]["id"])
    target = runtime.world.player(runtime.register("target", "password123")["user"]["id"])

    credit = runtime.fund(developer, {
        "requestId": "f-credit", "kind": "credit", "accountId": source["id"],
        "amount": 5000, "reason": "测试入金",
    })
    assert credit["entry"]["changes"][0]["deltaCents"] == 500000
    assert source["account"]["balance"] == pytest.approx(105000.0)

    debit = runtime.fund(developer, {
        "requestId": "f-debit", "kind": "debit", "accountId": source["id"],
        "amount": 1000, "reason": "测试扣款",
    })
    assert debit["entry"]["changes"][0]["deltaCents"] == -100000
    assert source["account"]["balance"] == pytest.approx(104000.0)

    transfer = runtime.fund(developer, {
        "requestId": "f-transfer", "kind": "transfer", "accountId": source["id"],
        "destinationId": target["id"], "amount": 2000, "reason": "测试转账",
    })
    assert len(transfer["entry"]["changes"]) == 2
    assert source["account"]["balance"] == pytest.approx(102000.0)
    assert target["account"]["balance"] == pytest.approx(102000.0)
    assert runtime.net_funding(target["id"]) == pytest.approx(2000.0)


def test_funding_is_idempotent_per_request_id(runtime, developer):
    player = runtime.world.player(runtime.register("player_p", "password123")["user"]["id"])
    body = {
        "requestId": "f-1", "kind": "credit", "accountId": player["id"],
        "amount": 100, "reason": "重复提交",
    }
    runtime.fund(developer, dict(body))
    again = runtime.fund(developer, dict(body))
    assert again["duplicate"] is True
    assert player["account"]["balance"] == pytest.approx(100100.0)


def test_funding_validates_amount_and_reason(runtime, developer):
    player = runtime.world.player(runtime.register("player_p", "password123")["user"]["id"])
    with pytest.raises(TradeError, match="金额"):
        runtime.fund(developer, {"requestId": "a", "kind": "credit", "accountId": player["id"], "amount": 1.005, "reason": "ok"})
    with pytest.raises(TradeError, match="原因"):
        runtime.fund(developer, {"requestId": "b", "kind": "credit", "accountId": player["id"], "amount": 10, "reason": "x"})


def test_funding_rejects_same_source_and_destination(runtime, developer):
    player = runtime.world.player(runtime.register("player_p", "password123")["user"]["id"])
    with pytest.raises(TradeError, match="转出和收款账户不能相同"):
        runtime.fund(developer, {
            "requestId": "self", "kind": "transfer", "accountId": player["id"],
            "destinationId": player["id"], "amount": 10, "reason": "自转",
        })


def test_admin_ledger_paginates(runtime, developer):
    player = runtime.world.player(runtime.register("player_p", "password123")["user"]["id"])
    for index in range(3):
        runtime.fund(developer, {
            "requestId": f"led-{index}", "kind": "credit", "accountId": player["id"],
            "amount": 10, "reason": f"第 {index} 笔",
        })
    page = runtime.admin_ledger(limit=2)
    assert page["total"] == 3
    assert len(page["entries"]) == 2
    assert page["nextBefore"] is not None


def test_state_round_trips(runtime):
    runtime.register("round_trip", "password123")
    state = runtime.snapshot_state()
    restored = MarketRuntime(initial_balance=100000.0, tick_interval_ms=10**9, persist_interval_ms=10**9)
    restored.load(state)
    assert len(restored.world.users) == len(runtime.world.users)
    assert restored.world.player(restored.world.users[1]["id"])["account"]["balance"] == pytest.approx(100000.0)


def test_corrupt_state_is_rejected(runtime):
    with pytest.raises(TradeError, match="存档"):
        type(runtime.world).load({"version": 99})


def test_resume_backfills_charts_without_trading(runtime, player):
    before = player["account"]["balance"]
    runtime.advance(5)
    gap = runtime.world.resume(int(time.time() * 1000) - 3_600_000)
    assert gap is not None
    assert gap["seconds"] == pytest.approx(3600.0)
    assert player["account"]["balance"] == pytest.approx(before)
    assert runtime.world.gaps[-1]["to"] > runtime.world.gaps[-1]["from"]


def test_reference_lists_every_symbol(runtime):
    reference = runtime.reference()
    assert reference["count"] == len(OFFLINE_SYMBOLS)
    assert "EUR/USD" in reference["symbols"]
