"""热数据表（hot_knowledge）多方言 DDL 与常用操作 SQL。

由知识库插件在初始化时通过数据库插件的 ``register_hot_knowledge_schema``
入口调用；本模块按驱动生成对应方言，并对 MongoDB 建集合 + 索引。

相对任务书 schema 的一处显式扩展：新增 ``status`` 列（默认 ``'active'``），
用于冷数据降级标记（``'demoted'``）——任务书要求降级后“数据保留但不再参与
热数据查询”，需要一个可查询的状态字段。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .adapters import AdapterError

#: 各驱动建表语句（SQLite 为任务书原版 schema）
DDL: dict[str, list[str]] = {
    "sqlite": [
        """
        CREATE TABLE IF NOT EXISTS hot_knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            content_hash TEXT NOT NULL UNIQUE,
            content TEXT NOT NULL,
            embedding BLOB,
            hit_count INTEGER DEFAULT 0,
            first_hit_at TEXT,
            last_hit_at TEXT,
            promoted_at TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            embedder TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_hot_status ON hot_knowledge (status, last_hit_at)",
    ],
    "postgres": [
        """
        CREATE TABLE IF NOT EXISTS hot_knowledge (
            id BIGSERIAL PRIMARY KEY,
            source TEXT NOT NULL,
            content_hash TEXT NOT NULL UNIQUE,
            content TEXT NOT NULL,
            embedding BYTEA,
            hit_count INTEGER DEFAULT 0,
            first_hit_at TEXT,
            last_hit_at TEXT,
            promoted_at TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            embedder TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_hot_status ON hot_knowledge (status, last_hit_at)",
    ],
    "mysql": [
        """
        CREATE TABLE IF NOT EXISTS hot_knowledge (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            source VARCHAR(64) NOT NULL,
            content_hash VARCHAR(64) NOT NULL UNIQUE,
            content LONGTEXT NOT NULL,
            embedding LONGBLOB,
            hit_count INT DEFAULT 0,
            first_hit_at VARCHAR(40),
            last_hit_at VARCHAR(40),
            promoted_at VARCHAR(40),
            status VARCHAR(16) NOT NULL DEFAULT 'active',
            embedder VARCHAR(128) NOT NULL DEFAULT '',
            created_at VARCHAR(40) DEFAULT NULL
        )
        """,
        "CREATE INDEX idx_hot_status ON hot_knowledge (status, last_hit_at)",
    ],
}

#: MongoDB 的集合名与索引定义（文档模型，不建关系表）
MONGO_COLLECTION = "hot_knowledge"


def now_iso_text() -> str:
    """统一时间戳格式（本地时间 ISO，秒精度，字典序可比）。"""
    return datetime.now().isoformat(timespec="seconds")


def cutoff_iso(days: int) -> str:
    """days 天前的 ISO 时间戳（降级扫描的 cutoff）。"""
    return (datetime.now() - timedelta(days=max(0, int(days)))).isoformat(timespec="seconds")


MONGO_INDEXES: list[dict] = [
    {"keys": {"content_hash": 1}, "unique": True},
    {"keys": {"status": 1, "last_hit_at": 1}, "unique": False},
]


async def register_hot_schema(adapter) -> dict:
    """在给定适配器上注册 hot_knowledge 存储结构，返回描述信息。"""
    driver = adapter.driver
    if driver == "mongo":
        await adapter.execute(
            json_op_create_collection(), params=()
        )
        for spec in MONGO_INDEXES:
            import json as _json

            await adapter.execute(_json.dumps({"op": "create_index", "collection": MONGO_COLLECTION, **spec}), ())
        return {"driver": driver, "collection": MONGO_COLLECTION, "indexes": len(MONGO_INDEXES)}
    statements = DDL.get(driver)
    if statements is None:
        raise AdapterError(f"hot_knowledge schema: unknown driver {driver!r}")
    for stmt in statements:
        await adapter.execute(" ".join(stmt.split()), ())
    return {"driver": driver, "table": "hot_knowledge", "statements": len(statements)}


def json_op_create_collection() -> str:
    import json as _json

    return _json.dumps({"op": "create_collection", "collection": MONGO_COLLECTION})


# ---------------------------------------------------------------------------
# 降级（demotion）：last_hit_at 超过 demotion_days 的热记录标记为 demoted。
# ---------------------------------------------------------------------------

def demote_sql(driver: str, cutoff_iso: str) -> tuple[str, tuple]:
    """返回 (sql, params)。MongoDB 由入口层走 update_many 文档操作。"""
    if driver == "mongo":
        raise AdapterError("mongo demotion uses the JSON update_many op, not SQL")
    return (
        "UPDATE hot_knowledge SET status = 'demoted' "
        "WHERE status = 'active' AND last_hit_at IS NOT NULL AND last_hit_at < ?",
        (cutoff_iso,),
    )


def mongo_demote_op(cutoff_iso: str) -> str:
    import json as _json

    return _json.dumps(
        {
            "op": "update_many",
            "collection": MONGO_COLLECTION,
            "filter": {"status": "active", "last_hit_at": {"$lt": cutoff_iso, "$ne": None}},
            "update": {"$set": {"status": "demoted"}},
        }
    )
