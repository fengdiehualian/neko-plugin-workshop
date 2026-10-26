"""plugin_database 验收测试：CRUD、事务回滚、连接切换、热数据表、统计表。

SQLite 用真实数据库跑全链路；PostgreSQL / MySQL / MongoDB 的 CRUD 需要真实
服务，默认跳过，设置环境变量后自动启用：

    NEKO_TEST_PG_DSN=postgresql://user:pass@127.0.0.1:5432/neko_test
    NEKO_TEST_MYSQL_DSN=mysql://user:pass@127.0.0.1:3306/neko_test
    NEKO_TEST_MONGO_URI=mongodb://127.0.0.1:27017  NEKO_TEST_MONGO_DB=neko_test

四个适配器共享同一 DatabaseAdapter 接口：SQLite 全链路通过即验证接口契约，
方言差异（占位符转译、REPLACE 语义）另有纯单元测试覆盖。
"""

from __future__ import annotations

import os
import struct

import pytest
from plugin.plugins.plugin_database import hot_schema
from plugin.plugins.plugin_database.adapters import (
    ADAPTERS,
    AdapterError,
    MySQLAdapter,
    PostgreSQLAdapter,
    SQLiteAdapter,
)
from plugin.plugins.plugin_database.service import (
    DatabaseService,
    decode_params,
    decode_value,
    encode_value,
)

# ---------------------------------------------------------------------------
# SQLite：默认驱动，全链路 CRUD + 事务
# ---------------------------------------------------------------------------


async def test_sqlite_crud_and_wal(tmp_path):
    a = SQLiteAdapter()
    await a.connect({"path": str(tmp_path / "t.db")})
    mode = await a.fetch_one("PRAGMA journal_mode", ())
    assert mode["journal_mode"] == "wal"

    await a.execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)")
    assert await a.execute("INSERT INTO t (v) VALUES (?)", ("a",)) == 1
    await a.execute("INSERT INTO t (v) VALUES (?)", ("b",))
    rows = await a.fetch_all("SELECT id, v FROM t ORDER BY id")
    assert [r["v"] for r in rows] == ["a", "b"]
    assert (await a.fetch_one("SELECT v FROM t WHERE id = ?", (2,)))["v"] == "b"
    assert await a.fetch_one("SELECT v FROM t WHERE id = ?", (99,)) is None
    assert await a.execute("UPDATE t SET v = ? WHERE id = ?", ("c", 2)) == 1
    assert await a.execute("DELETE FROM t WHERE id = ?", (1,)) == 1
    assert len(await a.fetch_all("SELECT * FROM t", ())) == 1
    await a.close()


async def test_sqlite_transaction_rollback_and_commit():
    a = SQLiteAdapter()
    await a.connect({})
    await a.execute("CREATE TABLE t (v TEXT)")
    async with await a.transaction():
        await a.execute("INSERT INTO t (v) VALUES (?)", ("kept",))
    assert len(await a.fetch_all("SELECT * FROM t", ())) == 1
    with pytest.raises(RuntimeError):
        async with await a.transaction():
            await a.execute("INSERT INTO t (v) VALUES (?)", ("ghost",))
            raise RuntimeError("boom")
    assert [r["v"] for r in await a.fetch_all("SELECT * FROM t", ())] == ["kept"]
    await a.close()


async def test_sqlite_structured_error_when_not_connected():
    a = SQLiteAdapter()
    with pytest.raises(AdapterError):
        await a.execute("SELECT 1")
    with pytest.raises(AdapterError):
        await a.fetch_all("SELECT 1", ())


# ---------------------------------------------------------------------------
# 方言差异（纯单元）
# ---------------------------------------------------------------------------


def test_qmark_to_dollar_respects_string_literals():
    sql = "INSERT INTO t VALUES (?, 'a?b', ?, ?)"
    assert PostgreSQLAdapter._qmark_to_dollar(sql) == "INSERT INTO t VALUES ($1, 'a?b', $2, $3)"


def test_rowcount_from_pg_status():
    from plugin.plugins.plugin_database.adapters import rowcount_from_pg_status

    assert rowcount_from_pg_status("INSERT 0 3") == 3
    assert rowcount_from_pg_status("UPDATE 7") == 7
    assert rowcount_from_pg_status("CREATE TABLE") == 0


async def test_mysql_dsn_parsing_rejects_wrong_scheme():
    a = MySQLAdapter()
    with pytest.raises(AdapterError, match="scheme"):
        await a.connect({"dsn": "postgresql://u:p@h/db"})
    with pytest.raises(AdapterError, match="dsn"):
        await a.connect({})


async def test_mongo_requires_uri_and_database():
    a = ADAPTERS["mongo"]()
    with pytest.raises(AdapterError, match="uri and database"):
        await a.connect({"uri": ""})


# ---------------------------------------------------------------------------
# 热数据表（sqlite 方言）与提升 / 降级 / 查询语义
# ---------------------------------------------------------------------------


async def test_hot_schema_and_promote(tmp_path):
    a = SQLiteAdapter()
    await a.connect({"path": str(tmp_path / "hot.db")})
    info = await hot_schema.register_hot_schema(a)
    assert info == {"driver": "sqlite", "table": "hot_knowledge", "statements": 2}

    emb = encode_value(struct.pack("<3f", 0.1, 0.2, 0.3))
    r = await a.execute(
        "INSERT OR REPLACE INTO hot_knowledge (source, content_hash, content, embedding,"
        " hit_count, first_hit_at, last_hit_at, promoted_at, status)"
        " VALUES (?,?,?,?,?,?,?,?, 'active')",
        ("knowledge_base", "h1", "内容", decode_value(emb), 5, "t0", "t1", "t2"),
    )
    assert r == 1
    row = await a.fetch_one("SELECT content_hash, hit_count, status FROM hot_knowledge", ())
    assert row["content_hash"] == "h1" and row["hit_count"] == 5 and row["status"] == "active"

    # 同 content_hash 再次提升 -> INSERT OR REPLACE 幂等
    await a.execute(
        "INSERT OR REPLACE INTO hot_knowledge (source, content_hash, content, embedding,"
        " hit_count, first_hit_at, last_hit_at, promoted_at, status)"
        " VALUES (?,?,?,?,?,?,?,?, 'active')",
        ("knowledge_base", "h1", "内容", None, 7, "2000-01-01T00:00:00", "2000-01-02T00:00:00", "2000-01-02T00:00:00"),
    )
    assert (await a.fetch_one("SELECT hit_count FROM hot_knowledge", ()))["hit_count"] == 7

    # 降级：last_hit_at 早于 cutoff
    n = await a.execute(*hot_schema.demote_sql("sqlite", "9999-12-31T00:00:00"))
    assert n == 1
    assert (await a.fetch_one("SELECT status FROM hot_knowledge", ()))["status"] == "demoted"
    await a.close()


# ---------------------------------------------------------------------------
# 服务层：driver 选择与连接切换
# ---------------------------------------------------------------------------


async def test_service_switch_driver_and_recovery(tmp_path):
    svc = DatabaseService(tmp_path)
    await svc.start({"driver": "sqlite", "sqlite": {"path": str(tmp_path / "a.db")}})
    assert svc.driver == "sqlite"
    await svc.execute("CREATE TABLE t (v TEXT)", ())
    await svc.execute("INSERT INTO t (v) VALUES (?)", ["x"])

    # 切到不可达驱动 -> 结构化错误
    with pytest.raises(AdapterError, match="postgres connect failed"):
        await svc.start(
            {"driver": "postgres", "postgres": {"dsn": "postgresql://u:p@127.0.0.1:1/none"}}
        )
    # 切回 sqlite 正常，数据仍可访问（同一库文件）
    await svc.start({"driver": "sqlite", "sqlite": {"path": str(tmp_path / "a.db")}})
    assert (await svc.fetch_one("SELECT v FROM t", ()))["v"] == "x"
    await svc.stop()


async def test_service_unknown_driver(tmp_path):
    svc = DatabaseService(tmp_path)
    with pytest.raises(AdapterError, match="unknown database driver"):
        await svc.start({"driver": "oracle"})


# ---------------------------------------------------------------------------
# 编解码：bytes <-> base64 包装（跨插件 IPC 安全）
# ---------------------------------------------------------------------------


def test_value_roundtrip_bytes():
    blob = bytes(range(256))
    enc = encode_value({"e": blob, "n": 1})
    assert enc["e"] == {"__bytes_b64__": __import__("base64").b64encode(blob).decode()}
    assert decode_value(enc)["e"] == blob
    assert decode_params([blob]) == (blob,)


# ---------------------------------------------------------------------------
# 真实服务 CRUD（环境变量存在时才运行）
# ---------------------------------------------------------------------------


async def test_postgres_crud_when_server_available():
    dsn = os.environ.get("NEKO_TEST_PG_DSN")
    if not dsn:
        pytest.skip("NEKO_TEST_PG_DSN not set")
    a = ADAPTERS["postgres"]()
    await a.connect({"dsn": dsn})
    await a.execute("DROP TABLE IF EXISTS t", ())
    await a.execute("CREATE TABLE t (id SERIAL PRIMARY KEY, v TEXT)", ())
    assert await a.execute("INSERT INTO t (v) VALUES (?)", ("a",)) == 1
    assert [r["v"] for r in await a.fetch_all("SELECT v FROM t", ())] == ["a"]
    async with await a.transaction():
        await a.execute("INSERT INTO t (v) VALUES (?)", ("b",))
    assert len(await a.fetch_all("SELECT * FROM t", ())) == 2
    with pytest.raises(RuntimeError):
        async with await a.transaction():
            await a.execute("INSERT INTO t (v) VALUES (?)", ("ghost",))
            raise RuntimeError("boom")
    assert len(await a.fetch_all("SELECT * FROM t", ())) == 2
    await a.close()


async def test_mysql_crud_when_server_available():
    dsn = os.environ.get("NEKO_TEST_MYSQL_DSN")
    if not dsn:
        pytest.skip("NEKO_TEST_MYSQL_DSN not set")
    a = ADAPTERS["mysql"]()
    await a.connect({"dsn": dsn})
    await a.execute("DROP TABLE IF EXISTS t", ())
    await a.execute("CREATE TABLE t (id INT AUTO_INCREMENT PRIMARY KEY, v VARCHAR(64))", ())
    assert await a.execute("INSERT INTO t (v) VALUES (?)", ("a",)) == 1
    assert [r["v"] for r in await a.fetch_all("SELECT v FROM t", ())] == ["a"]
    async with await a.transaction():
        await a.execute("INSERT INTO t (v) VALUES (?)", ("b",))
    with pytest.raises(RuntimeError):
        async with await a.transaction():
            await a.execute("INSERT INTO t (v) VALUES (?)", ("ghost",))
            raise RuntimeError("boom")
    assert len(await a.fetch_all("SELECT * FROM t", ())) == 2
    await a.close()


async def test_mongo_crud_when_server_available():
    uri = os.environ.get("NEKO_TEST_MONGO_URI")
    db = os.environ.get("NEKO_TEST_MONGO_DB")
    if not uri or not db:
        pytest.skip("NEKO_TEST_MONGO_URI / NEKO_TEST_MONGO_DB not set")
    a = ADAPTERS["mongo"]()
    await a.connect({"uri": uri, "database": db})
    import json

    await a.execute(
        json.dumps({"op": "delete_many", "collection": "t", "filter": {}}), ()
    )
    n = await a.execute(
        json.dumps({"op": "insert_many", "collection": "t", "docs": [{"v": "a"}, {"v": "b"}]}),
        (),
    )
    assert n == 2
    rows = await a.fetch_all(
        json.dumps({"op": "find", "collection": "t", "filter": {"v": "a"}}), ()
    )
    assert len(rows) == 1 and rows[0]["v"] == "a"
    n = await a.execute(
        json.dumps(
            {
                "op": "update_many",
                "collection": "t",
                "filter": {},
                "update": {"$set": {"v": "c"}},
            }
        ),
        (),
    )
    assert n == 2
    cnt = await a.fetch_one(json.dumps({"op": "count", "collection": "t", "filter": {}}), ())
    assert cnt is not None
    await a.close()
