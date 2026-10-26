"""P4 · 结局单测。

两条线：

1. **隐藏结局「无为而治」**：无限模式挂机玩家每周上线一次、点一下领低保，
   就能无限循环 —— 一笔交易都不做（开局强制卖出的那笔基准股票豁免）。
2. **经典模式自动终局**：第 30 个游戏日结束时自动结算，之后再调 ``settle``
   只会拿到 ``no_active_game``，不重复结算。
"""

from __future__ import annotations

import pytest
from conftest import run
from engine.settlement import check_inaction
from plugins.mock_stock_meow.storage.repo import Repo

_TICKS_PER_DAY_CLASSIC = 12
_TICKS_PER_DAY_INFINITE = 48


def _error_code(result) -> str:
    assert result.is_err(), f"期望 Err，实际 {result!r}"
    return result.err().code


def _player_id(repo: Repo) -> str:
    row = run(repo.fetch_one("SELECT player_id FROM market_state WHERE id = 1"))
    return row["player_id"]


def _sell_baseline_stock(plugin, repo: Repo) -> None:
    """把开局发放的基准股票卖掉（挂机玩家必须先这么做才活得下去）。"""
    rows = run(repo.fetch_all("SELECT * FROM positions"))
    assert rows
    stock_id = rows[0]["stock_id"]
    payload = run(plugin.sell(stock_id, rows[0]["amount"])).unwrap()
    assert payload["amount"] == pytest.approx(rows[0]["amount"])


# ---------------------------------------------------------------- 隐藏结局


def test_inaction_check_exempts_the_baseline_sale() -> None:
    """豁免机制本身：卖出基准股票那一笔不算主动交易。"""
    from engine.models import Player

    player = Player(id="p1", name="玩家", cash=0.0, mode="infinite", trade_count=1)
    assert check_inaction(player, max_trades=0, exempt_trades=1) is True
    assert check_inaction(player, max_trades=0, exempt_trades=0) is False


def test_hangman_reaches_the_inaction_ending(plugin) -> None:
    """挂机玩家（每周领一次低保、不交易）→ 隐藏结局「无为而治」。"""
    run(plugin.join(mode="infinite", seed=501))
    repo = Repo(plugin.db)
    _sell_baseline_stock(plugin, repo)

    # 每周上线一次：领一次低保，然后什么也不做
    for _ in range(2):
        run(plugin.daily(action="claim"))
        for _ in range(_TICKS_PER_DAY_INFINITE * 7):
            run(plugin._run_one_tick())
        status = run(plugin.status()).unwrap()
        assert status["alive"] is True, status

    payload = run(plugin.settle()).unwrap()
    assert payload["ending"] == "inaction"
    assert payload["survivor"] is True
    assert payload["trade_count"] == 1  # 只卖过基准股票那一笔


def test_a_trader_does_not_get_the_inaction_ending(plugin) -> None:
    """买了东西就不算「无为而治」。"""
    run(plugin.join(mode="infinite", seed=502))
    repo = Repo(plugin.db)
    _sell_baseline_stock(plugin, repo)
    market = run(plugin.market()).unwrap()
    cheapest = min(market["stocks"], key=lambda item: item["price"])
    bought = run(plugin.buy(cheapest["id"], 1.0)).unwrap()
    assert bought["amount"] == 1.0
    payload = run(plugin.settle()).unwrap()
    assert payload["ending"] is None
    assert payload["trade_count"] == 2  # 卖基准股票 + 买入


def test_inaction_requires_being_alive(plugin) -> None:
    """破产了就没有隐藏结局。"""
    run(plugin.join(mode="infinite", seed=503))
    repo = Repo(plugin.db)
    _sell_baseline_stock(plugin, repo)
    run(
        repo.transaction(
            [
                (
                    "UPDATE players SET alive = 0, death_reason = 'starved' "
                    "WHERE is_catgirl = 0",
                    (),
                )
            ]
        )
    )
    payload = run(plugin.settle()).unwrap()
    assert payload["ending"] is None


# ---------------------------------------------------------------- 经典模式自动终局


def test_classic_auto_finishes_after_thirty_days(plugin) -> None:
    """经典模式第 30 个游戏日结束 → 自动终局并自动结算。

    一条测试同时覆盖：第 29 天还没结束、第 30 天结束后对局关闭、
    之后再调 ``settle`` 只会拿到 ``no_active_game``（不重复结算）。
    """
    run(plugin.join(mode="classic_beginner", seed=504))
    repo = Repo(plugin.db)

    # 第 29 天结束 → 对局继续
    for _ in range(_TICKS_PER_DAY_CLASSIC * 29):
        run(plugin._run_one_tick())
    row = run(repo.fetch_one("SELECT player_id FROM market_state WHERE id = 1"))
    assert row["player_id"] != ""

    # 第 30 天结束 → 自动终局
    for _ in range(_TICKS_PER_DAY_CLASSIC):
        run(plugin._run_one_tick())
    row = run(repo.fetch_one("SELECT player_id FROM market_state WHERE id = 1"))
    assert row["player_id"] == ""  # 对局已结束

    # 自动结算之后玩家再调 settle → no_active_game（结算不重复）
    assert _error_code(run(plugin.settle())) == "no_active_game"


def test_classic_mode_reports_finished_at_total_game_days(plugin) -> None:
    """模式层：第 30 个游戏日结束后 ``is_finished`` 为真。"""
    from engine.config import default_engine_config
    from engine.models import MarketState, Player
    from plugins.mock_stock_meow.modes import get_mode
    from plugins.mock_stock_meow.modes.shared import ModeContext

    config = default_engine_config()
    mode = get_mode("classic_beginner")
    ctx = ModeContext(
        player=Player(id="p", name="玩家", cash=0.0, mode="classic_beginner", day=30),
        market=MarketState(seed=1),
        difficulty=config.difficulties["classic_beginner"],
        engine_config=config,
        mode="classic_beginner",
    )
    assert mode.is_finished(ctx) is False
    ctx.player.day = 31
    assert mode.is_finished(ctx) is True


def test_infinite_mode_never_auto_finishes(plugin) -> None:
    """无限模式没有终点。"""
    run(plugin.join(mode="infinite", seed=507))
    for _ in range(_TICKS_PER_DAY_INFINITE * 3):
        run(plugin._run_one_tick())
    row = run(
        Repo(plugin.db).fetch_one("SELECT player_id FROM market_state WHERE id = 1")
    )
    assert row["player_id"] != ""


def test_settlement_payload_has_the_p4_summaries(plugin) -> None:
    """结算报告里有低保小结、高利贷小结与新闻全记录。"""
    run(plugin.join(mode="infinite", seed=508))
    run(plugin._run_one_tick())
    payload = run(plugin.settle()).unwrap()
    assert "welfare_summary" in payload
    assert "loan_summary" in payload
    assert "news_log" in payload
    assert payload["welfare_summary"]["available"] is True
    assert payload["loan_summary"]["available"] is True
