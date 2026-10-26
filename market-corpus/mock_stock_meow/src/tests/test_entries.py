"""P2 入口单测：六个入口的 happy path 与错误路径。

对应 P2 契约 §2（入口签名与返回结构）、§5（种子）、§8（错误码清单）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeCtx, make_plugin, run
from storage.repo import Repo

#: 契约 §8.1 的完整错误码清单（12 条）。
CONTRACT_ERROR_CODES = {
    "not_joined",
    "already_joined",
    "invalid_mode",
    "invalid_amount",
    "insufficient_cash",
    "insufficient_position",
    "t_plus_one_locked",
    "stock_not_found",
    "stock_delisted",
    "no_active_game",
    "db_unavailable",
    "engine_not_ready",
}

# ---------------------------------------------------------------- 夹具


@pytest.fixture()
def build_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """按名字在独立数据根目录下造一个已建表的插件实例。"""

    def _factory(name: str) -> Any:
        monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / name))
        instance = make_plugin(FakeCtx(config_path=tmp_path / "plugin.toml"))
        assert run(instance.on_startup()).is_ok()
        return instance

    return _factory


def _value(result: Any) -> dict[str, Any]:
    assert result.is_ok(), f"期望 Ok，实际 {result!r}"
    payload = result.unwrap()
    assert isinstance(payload, dict)
    return payload


def _error_code(result: Any) -> str:
    assert result.is_err(), f"期望 Err，实际 {result!r}"
    error = result.err()
    return str(getattr(error, "code", ""))


def _first_stock(market_payload: dict[str, Any]) -> dict[str, Any]:
    return market_payload["stocks"][0]


def _cheapest_stock(market_payload: dict[str, Any]) -> dict[str, Any]:
    """挑最便宜的一只：初始资金恒 ≥ 105 元，最低价恒 ≤ 100 元，买 1 股一定够。"""
    return min(market_payload["stocks"], key=lambda item: item["price"])


# ---------------------------------------------------------------- join


def test_join_returns_expected_fields(plugin) -> None:
    """join 返回 id / 模式 / 初始资金 / 起始日 / 是否新开局。"""
    payload = _value(run(plugin.join(mode="classic_arcade", seed=12345)))
    assert set(payload) >= {
        "player_id",
        "mode",
        "initial_cash",
        "day",
        "new_game",
    }
    assert payload["player_id"].startswith("p_")
    assert payload["mode"] == "classic_arcade"
    assert payload["day"] == 1
    assert payload["new_game"] is True
    assert payload["initial_cash"] > 0


def test_join_without_seed_still_works(plugin) -> None:
    """不传 seed 时随机开局（种子本身不外泄）。"""
    payload = _value(run(plugin.join()))
    assert payload["mode"] == "classic_arcade"
    assert payload["initial_cash"] > 0


def test_join_rejects_unknown_mode(plugin) -> None:
    """未知模式 → invalid_mode。"""
    assert _error_code(run(plugin.join(mode="classic_nope"))) == "invalid_mode"


def test_join_rejects_second_join(plugin) -> None:
    """已经在局中再 join → already_joined。"""
    _value(run(plugin.join(seed=1)))
    assert _error_code(run(plugin.join(seed=2))) == "already_joined"


def test_i18n_contains_every_contract_error_code() -> None:
    """契约 §8.1 的 12 个错误码必须在 i18n 里都有对应文案。"""
    path = Path(__file__).resolve().parents[1] / "i18n" / "zh-CN.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    missing = sorted(
        code for code in CONTRACT_ERROR_CODES if f"error.{code}" not in data
    )
    assert not missing, f"i18n 缺少错误码文案：{missing}"


# ---------------------------------------------------------------- 未开局


def test_status_before_join(plugin) -> None:
    assert _error_code(run(plugin.status())) == "not_joined"


def test_market_before_join(plugin) -> None:
    assert _error_code(run(plugin.market())) == "not_joined"


def test_buy_before_join(plugin) -> None:
    assert _error_code(run(plugin.buy("FAKE-001", 1))) == "not_joined"


def test_sell_before_join(plugin) -> None:
    assert _error_code(run(plugin.sell("FAKE-001", 1))) == "not_joined"


def test_settle_before_join(plugin) -> None:
    """没有进行中的对局时 settle → no_active_game（与 not_joined 区分）。"""
    assert _error_code(run(plugin.settle())) == "no_active_game"


# ---------------------------------------------------------------- market


def test_market_is_read_only_in_p3(plugin) -> None:
    """P3 起 tick 由心跳自动推进，``market`` 入口**不再**推一格。

    （P2 契约 Q7 是"market 每次调用推进 1 tick"；P3 引入 ``@timer_interval``
    后继续那样做会让"经典 8 秒/tick"的计时承诺彻底失效，所以改成只读。）
    """
    _value(run(plugin.join(seed=99)))
    first = _value(run(plugin.market()))
    second = _value(run(plugin.market()))
    assert first["tick"] == 0
    assert second["tick"] == 0
    assert first["stocks"] == second["stocks"]


def test_market_lists_stocks_with_tags(plugin) -> None:
    """行情返回 id / 名称 / 价格 / 标签，且标的都是 FAKE-xxx。"""
    _value(run(plugin.join(seed=7)))
    payload = _value(run(plugin.market()))
    assert payload["stocks"]
    for stock in payload["stocks"]:
        assert stock["id"].startswith("FAKE-")
        assert set(stock) >= {"id", "name", "price", "tag"}
        assert stock["price"] > 0


# ---------------------------------------------------------------- buy / sell


def test_buy_updates_cash_and_position(plugin) -> None:
    """买入：现金减少、持仓增加、均价含买入手续费。"""
    joined = _value(run(plugin.join(seed=2024)))
    market = _value(run(plugin.market()))
    stock = _cheapest_stock(market)
    price = stock["price"]
    amount = 1.0

    bought = _value(run(plugin.buy(stock["id"], amount)))
    notional = price * amount
    expected_fee = notional * 0.003
    assert bought["stock_id"] == stock["id"]
    assert bought["amount"] == amount
    assert bought["price"] == pytest.approx(price)
    assert bought["fee"] == pytest.approx(round(expected_fee, 6), abs=1e-9)
    assert bought["cash_after"] == pytest.approx(
        joined["initial_cash"] - notional - expected_fee, abs=1e-6
    )
    assert bought["position"]["amount"] == pytest.approx(amount)
    assert bought["position"]["avg_cost"] == pytest.approx(
        (notional + expected_fee) / amount
    )


def test_sell_updates_cash_and_position(plugin) -> None:
    """卖出：现金增加、持仓减少、卖出费含印花税。"""
    joined = _value(run(plugin.join(seed=2025)))
    market = _value(run(plugin.market()))
    stock = _cheapest_stock(market)
    price = stock["price"]
    amount = 1.0

    _value(run(plugin.buy(stock["id"], amount)))
    sold = _value(run(plugin.sell(stock["id"], amount)))
    notional = price * amount
    expected_fee = notional * (0.005 + 0.001)
    assert sold["fee"] == pytest.approx(round(expected_fee, 6), abs=1e-9)
    assert sold["sell_fee"] == pytest.approx(round(notional * 0.005, 6), abs=1e-9)
    assert sold["stamp_tax"] == pytest.approx(round(notional * 0.001, 6), abs=1e-9)
    # 买入 + 卖出两笔手续费都从现钱里扣掉；本金在买卖之间进出相抵。
    assert sold["cash_after"] == pytest.approx(
        joined["initial_cash"] - notional * 0.003 - expected_fee, abs=1e-6
    )
    assert sold["position"] is None


def test_fees_accumulate_in_the_global_fee_pool(plugin) -> None:
    """手续费池只增不减，且与成交同源。"""
    _value(run(plugin.join(seed=2026)))
    market = _value(run(plugin.market()))
    stock = _cheapest_stock(market)
    _value(run(plugin.buy(stock["id"], 1.0)))
    repo = Repo(plugin.db)
    row = run(repo.fetch_one("SELECT fee_pool, total_fees FROM market_state WHERE id = 1"))
    expected = stock["price"] * 1.0 * 0.003
    assert row["fee_pool"] == pytest.approx(expected)
    assert row["total_fees"] == pytest.approx(expected)


def test_buy_rejects_non_positive_amount(plugin) -> None:
    """数量不合法 → invalid_amount。"""
    _value(run(plugin.join(seed=3)))
    assert _error_code(run(plugin.buy("FAKE-001", 0))) == "invalid_amount"
    assert _error_code(run(plugin.buy("FAKE-001", -1))) == "invalid_amount"


def test_buy_rejects_unknown_stock(plugin) -> None:
    """找不到标的 → stock_not_found。"""
    _value(run(plugin.join(seed=4)))
    assert _error_code(run(plugin.buy("FAKE-999", 1))) == "stock_not_found"


def test_buy_rejects_insufficient_cash(plugin) -> None:
    """现钱不足 → insufficient_cash。"""
    _value(run(plugin.join(seed=5)))
    assert _error_code(run(plugin.buy("FAKE-001", 100000))) == "insufficient_cash"


def test_sell_rejects_without_position(plugin) -> None:
    """没有持仓就卖 → insufficient_position。"""
    _value(run(plugin.join(seed=6)))
    assert _error_code(run(plugin.sell("FAKE-001", 1))) == "insufficient_position"


def test_sell_rejects_more_than_held(plugin) -> None:
    """卖超了 → insufficient_position。"""
    _value(run(plugin.join(seed=7)))
    market = _value(run(plugin.market()))
    stock = _cheapest_stock(market)
    _value(run(plugin.buy(stock["id"], 1.0)))
    assert _error_code(run(plugin.sell(stock["id"], 5.0))) == "insufficient_position"


def test_t_plus_one_blocks_same_day_sell(plugin) -> None:
    """classic_advanced 档：当日买入当日卖出 → t_plus_one_locked。"""
    _value(run(plugin.join(mode="classic_advanced", seed=8)))
    market = _value(run(plugin.market()))
    stock = _cheapest_stock(market)
    _value(run(plugin.buy(stock["id"], 1.0)))
    assert _error_code(run(plugin.sell(stock["id"], 1.0))) == "t_plus_one_locked"


# ---------------------------------------------------------------- status


def test_status_reports_assets_and_positions(plugin) -> None:
    """status 给出现金、持仓、总资产、存活天数、集中度。"""
    _value(run(plugin.join(seed=9)))
    market = _value(run(plugin.market()))
    stock = _cheapest_stock(market)
    _value(run(plugin.buy(stock["id"], 1.0)))

    status = _value(run(plugin.status()))
    assert status["joined"] is True
    assert status["alive"] is True
    assert status["day"] == 1
    assert status["trade_count"] == 1
    assert len(status["positions"]) == 1
    position = status["positions"][0]
    assert position["stock_id"] == stock["id"]
    assert position["amount"] == pytest.approx(1.0)
    assert position["avg_cost"] > 0
    assert status["total_asset"] == pytest.approx(
        status["cash"] + position["market_value"], abs=1e-6
    )


def test_buy_rejects_delisted_stock(plugin) -> None:
    """标的退市后不能再买 → stock_delisted。

    退市本身由引擎在价格击穿地板时判定（P3 的定时器会持续触发），
    P2 用「改写状态快照」的方式构造这个前置条件。
    """
    from plugins.mock_stock_meow.storage.session import load_snapshot

    _value(run(plugin.join(seed=21)))
    market = _value(run(plugin.market()))
    victim = _cheapest_stock(market)["id"]

    snapshot = run(load_snapshot(plugin.state))
    assert snapshot is not None
    for item in snapshot.stocks:
        if item["id"] == victim:
            item["alive"] = False
    assert run(plugin.state.save(snapshot)).is_ok()

    assert _error_code(run(plugin.buy(victim, 1.0))) == "stock_delisted"
    assert _error_code(run(plugin.sell(victim, 1.0))) == "stock_delisted"


def test_entries_report_db_unavailable_when_database_is_disabled(plugin) -> None:
    """数据库不可用时统一返回 db_unavailable（而不是抛异常）。

    模拟必须落在**配置级**：只把 ``plugin.db.enabled`` 翻成 False 只是改了一个
    本地标记 —— 插件的降级路径会 ``refresh_runtime_config()`` 重新读宿主的有效
    配置并把它改回来（这正是真机上"宿主晚下发配置"的修复机制）。
    真正的"数据库不可用"必须体现在**有效配置**里。
    """
    plugin.ctx._effective_config["plugin"]["database"]["enabled"] = False
    plugin.db.enabled = False
    assert _error_code(run(plugin.status())) == "db_unavailable"
    assert _error_code(run(plugin.market())) == "db_unavailable"
    assert _error_code(run(plugin.buy("FAKE-001", 1))) == "db_unavailable"
    assert _error_code(run(plugin.sell("FAKE-001", 1))) == "db_unavailable"
    assert _error_code(run(plugin.settle())) == "db_unavailable"


def test_status_reports_distance_to_death_for_living_cost_modes(plugin) -> None:
    """有生活费的档位才给「距离斩杀线」；街机档没有生活成本。"""
    _value(run(plugin.join(mode="classic_beginner", seed=22)))
    status = _value(run(plugin.status()))
    assert status["distance_to_death"] is not None
    assert status["distance_to_death"] < status["cash"]


def test_status_has_no_distance_to_death_in_arcade_mode(plugin) -> None:
    """街机档没有生活成本，斩杀线为 None。"""
    _value(run(plugin.join(mode="classic_arcade", seed=23)))
    status = _value(run(plugin.status()))
    assert status["distance_to_death"] is None


def test_sellable_respects_t_plus_one_per_mode(plugin) -> None:
    """``sellable`` = 考虑 T+1 后的可卖数量（契约 §2.2）。

    * 街机档没有 T+1 → 当日买入的持仓全部可卖；
    * 进阶档有 T+1 → 当日买入的部分锁死。
    """
    _value(run(plugin.join(mode="classic_arcade", seed=31)))
    market = _value(run(plugin.market()))
    stock = _cheapest_stock(market)
    bought = _value(run(plugin.buy(stock["id"], 1.0)))
    assert bought["position"]["sellable"] == pytest.approx(1.0)
    _value(run(plugin.settle()))

    _value(run(plugin.join(mode="classic_advanced", seed=32)))
    market = _value(run(plugin.market()))
    stock = _cheapest_stock(market)
    bought = _value(run(plugin.buy(stock["id"], 1.0)))
    assert bought["position"]["sellable"] == pytest.approx(0.0)


# ---------------------------------------------------------------- settle


def test_settle_returns_report_with_seed(plugin) -> None:
    """settle 是唯一允许出现 seed 与 heat_curve 的入口。"""
    _value(run(plugin.join(seed=4242)))
    run(plugin._run_one_tick())
    _value(run(plugin.buy("FAKE-001", 1.0)))

    report = _value(run(plugin.settle()))
    assert report["seed"] == 4242
    assert isinstance(report["heat_curve"], list)
    assert report["anti_fraud_notice"]
    assert report["ticks_played"] == 1
    assert report["trade_count"] == 1


def test_settle_closes_the_game(plugin) -> None:
    """结算即终局：之后再查状态是 not_joined。"""
    _value(run(plugin.join(seed=11)))
    _value(run(plugin.market()))
    _value(run(plugin.settle()))
    assert _error_code(run(plugin.status())) == "not_joined"


def test_settle_inaction_ending_without_trades(plugin) -> None:
    """一笔没买 → 隐藏结局 inaction。"""
    _value(run(plugin.join(seed=12)))
    _value(run(plugin.market()))
    report = _value(run(plugin.settle()))
    assert report["ending"] == "inaction"


# ---------------------------------------------------------------- 持久化


def test_same_seed_reproduces_same_prices(build_plugin) -> None:
    """同一 seed + 同一操作序列 → 同一结果（契约 §5）。"""
    first = build_plugin("run_a")
    second = build_plugin("run_b")
    _value(run(first.join(seed=20261023)))
    _value(run(second.join(seed=20261023)))
    first_market = _value(run(first.market()))
    second_market = _value(run(second.market()))
    assert first_market["stocks"] == second_market["stocks"]


def test_state_survives_plugin_restart(build_plugin) -> None:
    """插件进程重启后，从 db + state 重建的会话与重启前完全一致。"""
    instance = build_plugin("restart")
    _value(run(instance.join(seed=555)))
    before = _value(run(instance.market()))
    _value(run(instance.buy(before["stocks"][0]["id"], 1.0)))

    reborn = build_plugin("restart")
    after_status = _value(run(reborn.status()))
    assert after_status["trade_count"] == 1
    assert after_status["positions"][0]["amount"] == pytest.approx(1.0)
    assert after_status["positions"][0]["price"] == pytest.approx(
        before["stocks"][0]["price"]
    )


def test_prices_are_recovered_by_replay_when_state_cache_is_lost(build_plugin) -> None:
    """价格不落库：状态缓存丢失时靠 (seed, tick) 重放精确还原。"""
    from plugins.mock_stock_meow.storage.session import build_engine_config, load_session

    instance = build_plugin("replay")
    _value(run(instance.join(seed=777)))
    before = _value(run(instance.market()))

    # 模拟状态文件被清理：删掉 ``.plugin_state`` 缓存。
    state_path = instance.state._state_path
    assert state_path.exists()
    state_path.unlink()

    session = run(
        load_session(
            Repo(instance.db),
            instance.state,
            engine_config=build_engine_config({}),
        )
    )
    assert session is not None
    replayed = {stock.id: stock.price for stock in session.market.stocks.values()}
    expected = {stock["id"]: stock["price"] for stock in before["stocks"]}
    assert replayed == pytest.approx(expected)
    assert session.market.tick == before["tick"]
