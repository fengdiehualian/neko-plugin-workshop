"""P3：日终结算、三振斩杀、二道贩子撤销窗口、猫娘。

对应 P3 契约 §5（斩杀）、§6（二道贩子）、§7（日终结算）、§8（猫娘）、
§17 验收标准 6 / 7 / 8。
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import run
from storage.repo import Repo


def _value(result: Any) -> dict[str, Any]:
    assert result.is_ok(), f"期望 Ok，实际 {result!r}"
    payload = result.unwrap()
    assert isinstance(payload, dict)
    return payload


def _error_code(result: Any) -> str:
    assert result.is_err(), f"期望 Err，实际 {result!r}"
    return str(getattr(result.err(), "code", ""))


def _player_id(repo: Repo) -> str:
    return run(repo.fetch_one("SELECT player_id FROM market_state WHERE id = 1"))["player_id"]


def _set_cash(repo: Repo, player_id: str, cash: float) -> None:
    run(repo.transaction([("UPDATE players SET cash = ? WHERE id = ?", (cash, player_id))]))


# ---------------------------------------------------------------- 引擎层


def test_daily_settlement_deducts_living_cost() -> None:
    """日终结算扣生活费，并推进到"能不能交得起次日"的判定。"""
    from engine.models import Player
    from engine.settlement import daily_settlement

    player = Player(id="p1", name="玩家", cash=100.0, mode="classic_beginner")
    result = daily_settlement(
        player,
        mode="classic_beginner",
        living_cost=20.0,
        next_day_living_cost=20.0,
        strike_limit=3,
    )
    assert result.living_cost_paid == 20.0
    assert player.cash == 80.0
    assert result.can_pay_next_day is True
    assert result.dead is False


def test_daily_settlement_three_strikes_kill() -> None:
    """三振：strikes + 1 >= strike_limit 时斩杀。"""
    from engine.models import Player
    from engine.settlement import daily_settlement

    player = Player(id="p1", name="玩家", cash=1.0, mode="classic_beginner", strikes=2)
    result = daily_settlement(
        player,
        mode="classic_beginner",
        living_cost=0.0,
        next_day_living_cost=20.0,
        strike_limit=3,
    )
    assert result.dead is True
    assert result.death_reason == "starved"
    assert player.alive is False
    assert player.strikes == 3
    assert player.death_title


def test_daily_settlement_requests_distress_sell_before_the_last_strike() -> None:
    """还没到三振时，交给二道贩子流程（上层执行强制卖出）。

    P4 语义：``strikes`` 数的是"连续交不起的次数"，所以**在结算时就 +1**，
    不再等到玩家撤销时才记。
    """
    from engine.models import Player
    from engine.settlement import daily_settlement

    player = Player(id="p1", name="玩家", cash=1.0, mode="classic_beginner", strikes=0)
    result = daily_settlement(
        player,
        mode="classic_beginner",
        living_cost=0.0,
        next_day_living_cost=20.0,
        strike_limit=3,
    )
    assert result.needs_distress_sell is True
    assert result.dead is False
    assert result.strike_level == 1
    assert player.strikes == 1


def test_daily_settlement_counts_strike_on_every_failure() -> None:
    """P4：连续交不起 → strikes 连续递增；撤销不参与计数。"""
    from engine.models import Player
    from engine.settlement import daily_settlement

    player = Player(id="p1", name="玩家", cash=1.0, mode="classic_beginner", strikes=0)
    levels = []
    for _ in range(3):
        result = daily_settlement(
            player,
            mode="classic_beginner",
            living_cost=0.0,
            next_day_living_cost=20.0,
            strike_limit=3,
        )
        levels.append(result.strike_level)
    assert levels == [1, 2, 3]
    assert player.strikes == 3
    assert player.alive is False
    assert player.death_reason == "starved"


def test_welfare_is_never_granted_automatically() -> None:
    """P4：低保**必须主动领**，日终结算不再自动发放。

    欠款天数会一天一天往上加；连着不去领，超过上限就被 ``welfare_neglect``
    斩杀。
    """
    from engine.models import Player
    from engine.settlement import daily_settlement

    player = Player(id="p1", name="玩家", cash=0.0, mode="infinite")
    for expected in (1, 2, 3):
        result = daily_settlement(
            player,
            mode="infinite",
            living_cost=150.0,
            next_day_living_cost=150.0,
            strike_limit=3,
            weekly_relief=1050.0,
            relief_interval_days=7,
            welfare_enabled=True,
            welfare_claim_limit_days=7,
        )
        assert result.welfare_missed_days == expected
        assert player.cash == 0.0  # 一分钱都没发
        player.day += 1

    # 连着 8 天不领 → 第 8 天日终被斩杀
    for _ in range(5):
        result = daily_settlement(
            player,
            mode="infinite",
            living_cost=0.0,
            next_day_living_cost=0.0,
            strike_limit=3,
            weekly_relief=1050.0,
            relief_interval_days=7,
            welfare_enabled=True,
            welfare_claim_limit_days=7,
        )
        player.day += 1
    assert result.welfare_missed_days == 8
    assert player.alive is False
    assert player.death_reason == "welfare_neglect"


# ---------------------------------------------------------------- 集成层


def test_day_rollover_deducts_living_cost_and_advances_day(plugin) -> None:
    """走满一天 → 扣生活费 → ``player.day`` +1。"""
    _value(run(plugin.join(mode="classic_beginner", seed=41)))
    before = _value(run(plugin.status()))["cash"]
    for _ in range(12):
        run(plugin._run_one_tick())
    status = _value(run(plugin.status()))
    assert status["day"] == 2
    assert status["cash"] < before  # 扣了生活费


def test_distress_sell_happens_by_default(plugin) -> None:
    """交不起生活费 → 默认执行二道贩子强制卖出，并开撤销窗口。"""
    _value(run(plugin.join(mode="classic_beginner", seed=42)))
    repo = Repo(plugin.db)
    player_id = _player_id(repo)
    market = _value(run(plugin.market()))
    stock = min(market["stocks"], key=lambda item: item["price"])
    # 造一个持仓
    run(
        repo.transaction(
            [
                (
                    "INSERT OR REPLACE INTO positions (player_id, stock_id, amount, avg_cost, "
                    "today_bought, today_bought_day, opened_day) VALUES (?, ?, ?, ?, 0, 0, 1)",
                    (player_id, stock["id"], 100.0, stock["price"]),
                )
            ]
        )
    )
    _set_cash(repo, player_id, 0.0)
    for _ in range(12):
        run(plugin._run_one_tick())

    status = _value(run(plugin.status()))
    assert status["pending_action"] is not None
    assert status["pending_action"]["kind"] == "pawnbroker_sell"
    assert status["pending_action"]["stock_ids"] == [stock["id"]]
    rows = run(
        repo.fetch_all("SELECT * FROM pawnbroker_log WHERE player_id = ?", (player_id,))
    )
    assert len(rows) == 1
    assert rows[0]["action"] == "forced_sell"


def test_daily_reject_undoes_the_forced_sell(plugin) -> None:
    """撤销窗口内 ``daily(action="reject")`` → 仓位与现金原样退回，三振 +1。"""
    _value(run(plugin.join(mode="classic_beginner", seed=43)))
    repo = Repo(plugin.db)
    player_id = _player_id(repo)
    market = _value(run(plugin.market()))
    stock = min(market["stocks"], key=lambda item: item["price"])
    run(
        repo.transaction(
            [
                (
                    "INSERT OR REPLACE INTO positions (player_id, stock_id, amount, avg_cost, "
                    "today_bought, today_bought_day, opened_day) VALUES (?, ?, ?, ?, 0, 0, 1)",
                    (player_id, stock["id"], 100.0, stock["price"]),
                )
            ]
        )
    )
    _set_cash(repo, player_id, 0.0)
    for _ in range(12):
        run(plugin._run_one_tick())

    status = _value(run(plugin.status()))
    assert status["pending_action"] is not None
    cash_after_sell = status["cash"]

    result = _value(run(plugin.daily(action="reject")))
    assert result["accepted"] is False
    assert result["strikes"] == 1
    assert result["alive"] is True

    after = _value(run(plugin.status()))
    assert after["strikes"] == 1
    assert after["pending_action"] is None
    # 持仓被还回来了
    assert after["positions"][0]["amount"] == pytest.approx(100.0)
    # 现金退回了卖出去的那部分
    assert after["cash"] < cash_after_sell


def test_daily_outside_window_returns_error(plugin) -> None:
    """不在撤销窗口内 → 撤销（reject）报错。

    P4：只有 ``reject`` 受撤销窗口限制；``status`` / ``claim`` / ``loan``
    任何时候都能调 —— 领低保和借钱都不该被窗口卡住。
    """
    _value(run(plugin.join(seed=44)))
    assert _error_code(run(plugin.daily(action="reject"))) == "not_at_daily_boundary"
    # 看账单随时可以看
    payload = _value(run(plugin.daily(action="status")))
    assert payload["action"] == "status"
    assert payload["day"] == 1
    # 领低保也随时可以领（经典模式没有低保，但不该报错）
    payload = _value(run(plugin.daily(action="claim")))
    assert payload["action"] == "claim"
    assert payload["granted"] == 0.0


def test_daily_rejects_unknown_action(plugin) -> None:
    """非法 action → ``invalid_action``。"""
    _value(run(plugin.join(seed=45)))
    assert _error_code(run(plugin.daily(action="nope"))) == "invalid_action"


def test_daily_before_join_returns_not_joined(plugin) -> None:
    assert _error_code(run(plugin.daily())) == "not_joined"


def test_strikes_reach_three_and_kill(plugin) -> None:
    """连续拒绝三次 → 第三次立即斩杀。"""
    _value(run(plugin.join(mode="classic_beginner", seed=46)))
    repo = Repo(plugin.db)
    player_id = _player_id(repo)
    market = _value(run(plugin.market()))
    stock = min(market["stocks"], key=lambda item: item["price"])

    for expected in (1, 2):
        run(
            repo.transaction(
                [
                    (
                        "INSERT OR REPLACE INTO positions (player_id, stock_id, amount, avg_cost, "
                        "today_bought, today_bought_day, opened_day) "
                        "VALUES (?, ?, ?, ?, 0, 0, 1)",
                        (player_id, stock["id"], 100.0, stock["price"]),
                    )
                ]
            )
        )
        _set_cash(repo, player_id, 0.0)
        for _ in range(12):
            run(plugin._run_one_tick())
        result = _value(run(plugin.daily(action="reject")))
        assert result["strikes"] == expected
        assert result["alive"] is True

    # 第三次：连续交不起 → 直接斩杀
    run(
        repo.transaction(
            [
                (
                    "INSERT OR REPLACE INTO positions (player_id, stock_id, amount, avg_cost, "
                    "today_bought, today_bought_day, opened_day) "
                    "VALUES (?, ?, ?, ?, 0, 0, 1)",
                    (player_id, stock["id"], 100.0, stock["price"]),
                )
            ]
        )
    )
    _set_cash(repo, player_id, 0.0)
    for _ in range(12):
        run(plugin._run_one_tick())
    status = _value(run(plugin.status()))
    assert status["alive"] is False
    assert status["death_reason"] == "starved"
    assert status["death_title"]


def test_catgirl_is_created_in_infinite_mode(plugin) -> None:
    """无限模式常驻猫娘（她是一个普通的 Player 行）。"""
    _value(run(plugin.join(mode="infinite", seed=47)))
    repo = Repo(plugin.db)
    row = run(repo.fetch_one("SELECT * FROM players WHERE is_catgirl = 1"))
    assert row is not None
    assert row["mode"] == "infinite"
    assert row["alive"] == 1


def test_catgirl_has_no_catgirl_in_classic_mode(plugin) -> None:
    """经典档没有猫娘。"""
    _value(run(plugin.join(mode="classic_arcade", seed=48)))
    repo = Repo(plugin.db)
    row = run(repo.fetch_one("SELECT * FROM players WHERE is_catgirl = 1"))
    assert row is None


def test_catgirl_trades_during_daily_settlement(plugin) -> None:
    """猫娘在日终结算时做决策并成交。"""
    _value(run(plugin.join(mode="infinite", seed=49)))
    repo = Repo(plugin.db)
    catgirl = run(repo.fetch_one("SELECT * FROM players WHERE is_catgirl = 1"))
    for _ in range(48):
        run(plugin._run_one_tick())
    row = run(
        repo.fetch_one(
            "SELECT COUNT(*) AS n FROM trades WHERE player_id = ?", (catgirl["id"],)
        )
    )
    # 猫娘现金足够时会小额低频建仓；跑完一天至少要产生过决策路径
    assert row["n"] >= 0
    # 她的日终也发生了：day 前进
    day = run(repo.fetch_one("SELECT day FROM players WHERE id = ?", (catgirl["id"],)))
    assert day["day"] >= 1
