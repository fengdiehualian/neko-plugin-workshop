"""统一导出：DatabaseAdapter 接口与四个驱动适配器。"""

from __future__ import annotations

from .base import AdapterError, DatabaseAdapter, TransactionHandle, rowcount_from_pg_status
from .mongo_adapter import MongoDBAdapter
from .mysql_adapter import MySQLAdapter
from .postgres_adapter import PostgreSQLAdapter
from .sqlite_adapter import SQLiteAdapter

#: driver 名 -> 适配器工厂
ADAPTERS: dict[str, type[DatabaseAdapter]] = {
    SQLiteAdapter.driver: SQLiteAdapter,
    PostgreSQLAdapter.driver: PostgreSQLAdapter,
    MySQLAdapter.driver: MySQLAdapter,
    MongoDBAdapter.driver: MongoDBAdapter,
}

__all__ = [
    "ADAPTERS",
    "AdapterError",
    "DatabaseAdapter",
    "MongoDBAdapter",
    "MySQLAdapter",
    "PostgreSQLAdapter",
    "SQLiteAdapter",
    "TransactionHandle",
    "rowcount_from_pg_status",
]
