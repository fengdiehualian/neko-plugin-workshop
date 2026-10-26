"""数据库**懒建表自愈**的回归测试（真机事故）。

事故现象（Steam 版宿主 0.9.0 实测）：

* 插件进程启动时宿主**还没把有效配置下发进来** → ``db.enabled`` 是 False
  → ``on_startup`` 建表失败（"plugin database is disabled"）；
* 宿主稍后补发配置，DB 能开会话了；
* 但表**从未建成**（0 字节的 .db 文件），之后每次查询都撞
  ``no such table`` → 插件永远卡在"数据库不可用"的降级状态出不来。

修法：schema 改成**懒建表**（``Repo.ensure_schema``）—— 第一次真正用数据库时
补建，失败也不放弃。

本文件锁死两条性质：

1. 数据库**晚**可用时，schema 会在第一次真正用数据库时自动补建；
2. 补建之后插件不再报"数据库不可用"。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from conftest import make_plugin, run


def _db_path(instance: Any) -> Path:
    """插件的 SQLite 文件路径。"""
    return Path(str(getattr(instance.db, "_db_path")))


def _tables(instance: Any) -> set[str]:
    path = _db_path(instance)
    if not path.exists():
        return set()
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {row[0] for row in rows.fetchall()}
    finally:
        connection.close()


def test_database_disabled_at_startup_does_not_crash_the_plugin(ctx) -> None:
    """数据库关着时启动：降级但**不崩**（返回 Ok）。"""
    ctx._effective_config["plugin"]["database"]["enabled"] = False
    instance = make_plugin(ctx)
    assert instance.db.enabled is False

    result = run(instance.on_startup())
    assert result.is_ok()


def test_schema_is_built_lazily_once_the_database_becomes_available(ctx) -> None:
    """宿主晚下发配置 → 第一次真正用数据库时**自动补建 schema**。"""
    # ① 配置还没到：数据库关着
    ctx._effective_config["plugin"]["database"]["enabled"] = False
    instance = make_plugin(ctx)
    assert instance.db.enabled is False

    # ② 启动时建表失败（降级）
    result = run(instance.on_startup())
    assert result.is_ok()

    # ③ 宿主补发有效配置 → SDK 刷新运行时能力
    ctx._effective_config["plugin"]["database"]["enabled"] = True
    instance.refresh_runtime_config()
    assert instance.db.enabled is True

    # ④ 此时还没有任何表（启动时的建表失败了）
    assert "players" not in _tables(instance)

    # ⑤ 第一次真正用数据库 → schema 自动补建
    payload = run(instance.market_context())
    assert isinstance(payload, dict)
    assert payload.get("notice") != "数据库不可用。", payload

    # ⑥ 表真的建出来了
    tables = _tables(instance)
    assert "players" in tables
    assert "market_state" in tables


def test_plugin_recovers_after_the_database_arrives_late(ctx) -> None:
    """补建之后，插件从降级状态**完全恢复**（不再是 p2-persistence）。"""
    ctx._effective_config["plugin"]["database"]["enabled"] = False
    instance = make_plugin(ctx)
    run(instance.on_startup())

    ctx._effective_config["plugin"]["database"]["enabled"] = True
    instance.refresh_runtime_config()

    payload = run(instance.market_context())
    assert isinstance(payload, dict)
    assert payload.get("stage") != "p2-persistence", payload
    assert "数据库不可用" not in str(payload.get("notice") or ""), payload
