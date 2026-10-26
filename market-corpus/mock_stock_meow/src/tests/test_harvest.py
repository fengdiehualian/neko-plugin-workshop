"""P3：收割（触发、去重、落库、通知白名单）。

对应 P3 契约 §4（收割）、§13（局中隐藏规则的延续）、
§17 验收标准 3 / 4 / 5。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from conftest import run
from storage.repo import Repo


def _value(result: Any) -> dict[str, Any]:
    assert result.is_ok(), f"期望 Ok，实际 {result!r}"
    payload = result.unwrap()
    assert isinstance(payload, dict)
    return payload


# ---------------------------------------------------------------- 引擎层


def test_select_harvest_target_picks_the_largest_position() -> None:
    """收割目标 = 持仓市值占比最高的那一只。"""
    from engine.harvest import select_harvest_target
    from engine.models import Player, Position

    player = Player(id="p1", name="玩家", cash=0.0, mode="classic_arcade")
    player.positions["FAKE-001"] = Position("FAKE-001", amount=1.0, avg_cost=10.0)
    player.positions["FAKE-002"] = Position("FAKE-002", amount=1.0, avg_cost=90.0)
    prices = {"FAKE-001": 10.0, "FAKE-002": 90.0}
    assert select_harvest_target(player, prices) == "FAKE-002"


def test_select_harvest_target_returns_none_without_positions() -> None:
    """空仓收割不了。"""
    from engine.harvest import select_harvest_target
    from engine.models import Player

    player = Player(id="p1", name="玩家", cash=0.0, mode="classic_arcade")
    assert select_harvest_target(player, {}) is None


def test_execute_harvest_skips_low_concentration_peak() -> None:
    """峰值触发的收割要求集中度达到门槛。"""
    from engine.harvest import TRIGGER_PEAK, execute_harvest
    from engine.models import MarketState, Player, Position, Stock

    stock = Stock(id="FAKE-001", name="虚构标的", price=100.0, prev_price=100.0, mu=0.0, sigma=0.1)
    other = Stock(id="FAKE-002", name="虚构标的", price=100.0, prev_price=100.0, mu=0.0, sigma=0.1)
    market = MarketState(seed=1, stocks={stock.id: stock, other.id: other})
    player = Player(id="p1", name="玩家", cash=0.0, mode="classic_beginner")
    player.positions[stock.id] = Position(stock.id, amount=1.0, avg_cost=100.0)
    player.positions["FAKE-002"] = Position("FAKE-002", amount=1.0, avg_cost=100.0)
    # 两只等大 → 集中度 0.5 < 门槛 0.6
    result = execute_harvest(
        player, market, trigger=TRIGGER_PEAK, phase="honeymoon", threshold=0.6
    )
    assert result is None
    assert stock.price == 100.0


def test_execute_harvest_drops_the_price_and_records_concentration() -> None:
    """收割 = 向下跳跃，力度与集中度挂钩。"""
    from engine.harvest import TRIGGER_PEAK, execute_harvest
    from engine.models import MarketState, Player, Position, Stock

    stock = Stock(id="FAKE-001", name="虚构标的", price=100.0, prev_price=100.0, mu=0.0, sigma=0.1)
    market = MarketState(seed=1, stocks={stock.id: stock})
    player = Player(id="p1", name="玩家", cash=0.0, mode="classic_beginner", heat=88.0)
    player.positions[stock.id] = Position(stock.id, amount=1.0, avg_cost=100.0)
    result = execute_harvest(
        player, market, trigger=TRIGGER_PEAK, phase="honeymoon", threshold=0.5
    )
    assert result is not None
    assert result.stock_id == "FAKE-001"
    assert result.concentration == pytest.approx(1.0)
    assert result.heat_at_harvest == 88.0
    assert stock.price < 100.0
    assert result.price_after == pytest.approx(stock.price)


# ---------------------------------------------------------------- 集成层


def _setup_two_positions(plugin, repo: Repo, ratio: float = 0.68) -> tuple[str, str]:
    """造出两个持仓，集中度约等于 ``ratio``。返回 (stock_a, stock_b)。"""
    market = _value(run(plugin.market()))
    stocks = sorted(market["stocks"], key=lambda item: item["price"])
    first, second = stocks[0], stocks[1]
    # (amount_a * p_a) / (amount_a * p_a + amount_b * p_b) = ratio
    amount_b = 1.0
    amount_a = (ratio / (1.0 - ratio)) * second["price"] / first["price"]
    row = run(repo.fetch_one("SELECT player_id FROM market_state WHERE id = 1"))
    player_id = row["player_id"]
    run(
        repo.transaction(
            [
                (
                    "INSERT INTO positions (player_id, stock_id, amount, avg_cost, "
                    "today_bought, today_bought_day, opened_day) VALUES (?, ?, ?, ?, 0, 0, 1)",
                    (player_id, first["id"], amount_a, first["price"]),
                ),
                (
                    "INSERT INTO positions (player_id, stock_id, amount, avg_cost, "
                    "today_bought, today_bought_day, opened_day) VALUES (?, ?, ?, ?, 0, 0, 1)",
                    (player_id, second["id"], amount_b, second["price"]),
                ),
            ]
        )
    )
    return first["id"], second["id"]


def test_phase_triggered_harvest_writes_liquidation_log(plugin) -> None:
    """阶段切到收割期 → 收割 → 写一行 ``liquidation_log``。"""
    _value(run(plugin.join(mode="classic_arcade", seed=31)))
    repo = Repo(plugin.db)
    _setup_two_positions(plugin, repo)
    row = run(repo.fetch_one("SELECT player_id FROM market_state WHERE id = 1"))
    player_id = row["player_id"]

    # 先把阶段推到 trap
    run(repo.transaction([("UPDATE players SET trade_count = 30 WHERE id = ?", (player_id,))]))
    run(plugin._run_one_tick())
    assert _value(run(plugin.market()))["phase"] == "trap"

    # 再推到 slaughter → 触发收割
    run(repo.transaction([("UPDATE players SET trade_count = 60 WHERE id = ?", (player_id,))]))
    run(plugin._run_one_tick())
    assert _value(run(plugin.market()))["phase"] == "slaughter"

    rows = run(
        repo.fetch_all("SELECT * FROM liquidation_log WHERE player_id = ?", (player_id,))
    )
    assert len(rows) == 1
    assert rows[0]["trigger"] == "phase"
    assert rows[0]["severity"] > 0
    assert rows[0]["concentration_at_harvest"] > 0
    assert "player_heat_at_harvest" in rows[0].keys()


def test_harvest_happens_at_most_once_per_tick(plugin) -> None:
    """两个触发源去重：一个 tick 最多一条收割流水。"""
    _value(run(plugin.join(mode="classic_arcade", seed=32)))
    repo = Repo(plugin.db)
    _setup_two_positions(plugin, repo)
    row = run(repo.fetch_one("SELECT player_id FROM market_state WHERE id = 1"))
    player_id = row["player_id"]
    run(repo.transaction([("UPDATE players SET trade_count = 60 WHERE id = ?", (player_id,))]))
    run(plugin._run_one_tick())
    run(plugin._run_one_tick())
    rows = run(
        repo.fetch_all("SELECT * FROM liquidation_log WHERE player_id = ?", (player_id,))
    )
    # 第一个 tick 从 honeymoon 直跳 slaughter（街机档的 fixed_trades 判定）
    # → 一次收割；第二个 tick 阶段已是 slaughter，不再重复收割。
    assert len(rows) == 1


def test_harvest_notification_whitelist_has_no_heat(plugin) -> None:
    """收割通知只带 tick / 股票 / 集中度 / 力度，**不带 heat**。"""
    _value(run(plugin.join(mode="classic_arcade", seed=33)))
    repo = Repo(plugin.db)
    _setup_two_positions(plugin, repo)
    row = run(repo.fetch_one("SELECT player_id FROM market_state WHERE id = 1"))
    player_id = row["player_id"]
    # 街机档：honeymoon → trap → slaughter 两步走
    run(repo.transaction([("UPDATE players SET trade_count = 30 WHERE id = ?", (player_id,))]))
    run(plugin._run_one_tick())
    run(repo.transaction([("UPDATE players SET trade_count = 60 WHERE id = ?", (player_id,))]))
    run(plugin._run_one_tick())

    status = _value(run(plugin.status()))
    event = status["pending_event"]
    assert event is not None
    assert set(event) == {"kind", "tick", "stock_id", "concentration", "severity"}
    assert event["kind"] == "harvest"
    # 通知里不能出现 heat 数值
    assert "heat" not in json.dumps(event, ensure_ascii=False).lower()


def test_peak_triggered_harvest_fires_when_heat_falls_above_equilibrium(
    plugin,
) -> None:
    """峰值驱动的收割在真实游戏里**可达**。

    机制：玩家先重仓把 heat 顶到高位（集中度 1.0 → 平衡点 266.7），
    随后开始分散（集中度降到 0.6 → 平衡点 66.7），heat 于是**掉头向下**——
    这就是"上头到顶、开始想事"的瞬间，峰值成立，收割落下。
    """
    _value(run(plugin.join(mode="classic_beginner", seed=35)))
    repo = Repo(plugin.db)
    _setup_two_positions(plugin, repo, ratio=0.68)
    row = run(repo.fetch_one("SELECT player_id FROM market_state WHERE id = 1"))
    player_id = row["player_id"]

    # 重仓期把 heat 顶到 250（高于 0.68 集中度下的平衡点 66.7）
    run(
        repo.transaction(
            [
                (
                    "INSERT INTO heat_history (player_id, tick, day, heat) VALUES (?, 0, 1, 50)",
                    (player_id,),
                ),
                (
                    "INSERT INTO heat_history (player_id, tick, day, heat) VALUES (?, 1, 1, 250)",
                    (player_id,),
                ),
            ]
        )
    )
    run(plugin._run_one_tick())

    rows = run(
        repo.fetch_all("SELECT * FROM liquidation_log WHERE player_id = ?", (player_id,))
    )
    assert len(rows) == 1
    assert rows[0]["trigger"] == "peak"
    assert rows[0]["concentration_at_harvest"] >= 0.5
    assert rows[0]["player_heat_at_harvest"] > 0


def test_peak_triggered_harvest_requires_concentration(plugin) -> None:
    """峰值驱动收割：集中度不到门槛就不收割。"""
    _value(run(plugin.join(mode="classic_beginner", seed=34)))
    repo = Repo(plugin.db)
    # 只放一只持仓 → 集中度 1.0，但 heat 峰值需要老值足够大
    market = _value(run(plugin.market()))
    stock = market["stocks"][0]
    row = run(repo.fetch_one("SELECT player_id FROM market_state WHERE id = 1"))
    player_id = row["player_id"]
    run(
        repo.transaction(
            [
                (
                    "INSERT INTO positions (player_id, stock_id, amount, avg_cost, "
                    "today_bought, today_bought_day, opened_day) VALUES (?, ?, ?, ?, 0, 0, 1)",
                    (player_id, stock["id"], 1.0, stock["price"]),
                )
            ]
        )
    )
    # 造一条 heat 曲线：老值 50 → 70（老值 > 峰值阈值 40）
    run(
        repo.transaction(
            [
                (
                    "INSERT INTO heat_history (player_id, tick, day, heat) VALUES (?, 0, 1, 50)",
                    (player_id,),
                ),
                (
                    "INSERT INTO heat_history (player_id, tick, day, heat) VALUES (?, 1, 1, 70)",
                    (player_id,),
                ),
            ]
        )
    )
    run(plugin._run_one_tick())
    rows = run(
        repo.fetch_all("SELECT * FROM liquidation_log WHERE player_id = ?", (player_id,))
    )
    # 集中度 1.0 → heat 状态分 40 → 新值 70*0.85+40 = 99.5 > 70 → 不是峰值
    assert rows == []
