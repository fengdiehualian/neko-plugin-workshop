"""P2 持久化层单测：schema 幂等、七表读写、事务回滚、外键约束。

对应 P2 契约 §1（SQLite schema）与 §9（``self.db`` 权威 / ``self.state`` 缓存）。
"""

from __future__ import annotations

import sqlite3

import pytest
from conftest import run
from storage.repo import SCHEMA_STATEMENTS, DbUnavailableError, Repo

_EXPECTED_TABLES = {
    "players",
    "positions",
    "trades",
    "heat_history",
    "market_state",
    "news_log",
    "pawnbroker_log",
}


@pytest.fixture()
def repo(plugin) -> Repo:
    """建表完成的 Repo（plugin 夹具已跑过 startup）。"""
    return Repo(plugin.db)


def _table_names(repo: Repo) -> set[str]:
    rows = run(repo.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table'"))
    return {row["name"] for row in rows}


def test_schema_creates_all_seven_tables(repo: Repo) -> None:
    """七张业务表全部建好。"""
    assert _EXPECTED_TABLES <= _table_names(repo)


def test_ensure_schema_is_idempotent(repo: Repo) -> None:
    """重复建表不报错、不丢数据（每次 startup 都跑一遍）。"""
    run(repo.fetch_all("SELECT 1"))  # 预热
    run(repo.ensure_schema())
    run(repo.ensure_schema())
    assert _EXPECTED_TABLES <= _table_names(repo)


def test_schema_statements_are_all_idempotent_sql(repo: Repo) -> None:
    """所有 DDL 都是 CREATE ... IF NOT EXISTS 形式。"""
    for statement in SCHEMA_STATEMENTS:
        assert "IF NOT EXISTS" in statement.upper(), statement


def test_market_state_keeps_single_row(repo: Repo) -> None:
    """market_state 是单行表（id 恒为 1）。"""
    run(
        repo.transaction(
            [
                (
                    "INSERT OR REPLACE INTO market_state "
                    "(id, tick, day, phase, seed, player_id, fee_pool, total_fees, updated_at) "
                    "VALUES (1, 0, 1, 'honeymoon', 42, 'p_1', 0, 0, 0)",
                    (),
                )
            ]
        )
    )
    row = run(repo.fetch_one("SELECT * FROM market_state WHERE id = 1"))
    assert row is not None
    assert row["seed"] == 42
    assert row["player_id"] == "p_1"


def test_write_and_read_all_seven_tables(repo: Repo) -> None:
    """七张表都能写入并读回。"""
    run(
        repo.transaction(
            [
                (
                    "INSERT INTO players (id, name, cash, mode, day, alive, initial_cash, "
                    "trade_count, heat, total_fee_paid, tokens, strikes, loan_debt, "
                    "created_at, updated_at) VALUES "
                    "('p_1', '玩家', 100.0, 'classic_arcade', 1, 1, 100.0, 0, 0, 0, 0, 0, 0, 0, 0)",
                    (),
                ),
                (
                    "INSERT INTO positions (player_id, stock_id, amount, avg_cost, "
                    "today_bought, today_bought_day, opened_day) "
                    "VALUES ('p_1', 'FAKE-001', 2.0, 10.0, 2.0, 1, 1)",
                    (),
                ),
                (
                    "INSERT INTO trades (player_id, stock_id, side, amount, price, fee, tick, "
                    "day, reason, created_at) VALUES "
                    "('p_1', 'FAKE-001', 'buy', 2.0, 10.0, 0.06, 3, 1, 'manual', 0)",
                    (),
                ),
                (
                    "INSERT INTO heat_history (player_id, tick, day, heat) "
                    "VALUES ('p_1', 3, 1, 12.5)",
                    (),
                ),
                (
                    "INSERT OR REPLACE INTO market_state (id, tick, day, phase, seed, "
                    "player_id, fee_pool, total_fees, updated_at) "
                    "VALUES (1, 3, 1, 'honeymoon', 42, 'p_1', 0.06, 0.06, 0)",
                    (),
                ),
                (
                    "INSERT INTO news_log (day, news_id, news_type, stock_id, content, accuracy) "
                    "VALUES (1, 'n_1', 'true', 'FAKE-001', '虚构快讯', 0.7)",
                    (),
                ),
                (
                    "INSERT INTO pawnbroker_log (player_id, tick, day, action, stock_id, price, "
                    "reason, created_at) VALUES "
                    "('p_1', 3, 1, 'forced_sell', 'FAKE-001', 8.0, 'forced', 0)",
                    (),
                ),
            ]
        )
    )

    assert run(repo.fetch_one("SELECT * FROM players WHERE id = 'p_1'"))["cash"] == 100.0
    assert run(repo.fetch_one("SELECT * FROM positions WHERE stock_id = 'FAKE-001'"))["amount"] == 2.0
    assert run(repo.fetch_one("SELECT * FROM trades"))["side"] == "buy"
    assert run(repo.fetch_one("SELECT * FROM heat_history"))["heat"] == 12.5
    assert run(repo.fetch_one("SELECT * FROM market_state WHERE id = 1"))["tick"] == 3
    assert run(repo.fetch_one("SELECT * FROM news_log"))["news_type"] == "true"
    assert run(repo.fetch_one("SELECT * FROM pawnbroker_log"))["action"] == "forced_sell"


def test_transaction_rolls_back_on_failure(repo: Repo) -> None:
    """事务中任一语句失败 → 整体回滚，先前语句不落库。"""
    with pytest.raises(sqlite3.Error):
        run(
            repo.transaction(
                [
                    (
                        "INSERT INTO players (id, name, cash, mode, day, alive, initial_cash, "
                        "trade_count, heat, total_fee_paid, tokens, strikes, loan_debt, "
                        "created_at, updated_at) VALUES "
                        "('p_rollback', '玩家', 1.0, 'classic_arcade', 1, 1, 1.0, 0, 0, 0, 0, 0, 0, 0, 0)",
                        (),
                    ),
                    ("INSERT INTO no_such_table (x) VALUES (1)", ()),
                ]
            )
        )
    assert run(repo.fetch_one("SELECT * FROM players WHERE id = 'p_rollback'")) is None


def test_foreign_keys_are_enforced(repo: Repo) -> None:
    """SDK 已开启 PRAGMA foreign_keys，positions 的外键必须真的生效。"""
    with pytest.raises(sqlite3.Error):
        run(
            repo.transaction(
                [
                    (
                        "INSERT INTO positions (player_id, stock_id, amount, avg_cost, "
                        "today_bought, today_bought_day, opened_day) "
                        "VALUES ('p_missing', 'FAKE-001', 1.0, 1.0, 0, 0, 1)",
                        (),
                    )
                ]
            )
        )


def test_fetch_one_returns_none_when_missing(repo: Repo) -> None:
    """查不到就是 None，不是异常。"""
    assert run(repo.fetch_one("SELECT * FROM players WHERE id = 'nope'")) is None


def test_disabled_database_raises_db_unavailable(plugin) -> None:
    """数据库没开时统一抛 DbUnavailableError（插件层据此返回 db_unavailable）。"""
    plugin.db.enabled = False
    disabled = Repo(plugin.db)
    assert disabled.enabled is False
    with pytest.raises(DbUnavailableError):
        run(disabled.fetch_all("SELECT 1"))
