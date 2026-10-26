"""PostgreSQL 适配器（asyncpg 连接池，``?`` 占位符自动转 ``$n``）。

事务语义：``transaction()`` 从池里钉住一条连接并开启事务；事务期间本适配器的
``execute`` / ``fetch_*`` 通过 contextvar 自动路由到该连接——语句真正参与事务，
其它任务/调用方各拿各的上下文，互不污染。
"""

from __future__ import annotations

import asyncio
import contextvars
from typing import Any

from .base import (
    AdapterError,
    DatabaseAdapter,
    TransactionHandle,
    rowcount_from_pg_status,
)


class _PgTransaction(TransactionHandle):
    def __init__(self, adapter: "PostgreSQLAdapter") -> None:
        self._adapter = adapter
        self._timeout = adapter._acquire_timeout
        self._conn: Any = None
        self._tr: Any = None
        self._token: contextvars.Token | None = None

    async def _begin(self) -> None:
        self._conn = await asyncio.wait_for(self._pool_acquire(), self._timeout)
        self._tr = self._conn.transaction()
        await self._tr.start()
        self._token = self._adapter._txn_conn.set(self._conn)

    async def _pool_acquire(self):
        return await self._adapter._require_pool().acquire()

    async def _finish(self, commit: bool) -> None:
        tr, conn, token = self._tr, self._conn, self._token
        self._tr = self._conn = self._token = None
        if token is not None:
            self._adapter._txn_conn.reset(token)
        if tr is None:
            return
        try:
            await (tr.commit() if commit else tr.rollback())
        finally:
            self._adapter._require_pool().release(conn)

    async def _commit(self) -> None:
        await self._finish(commit=True)

    async def _rollback(self) -> None:
        await self._finish(commit=False)


class PostgreSQLAdapter(DatabaseAdapter):
    """asyncpg 连接池。

    config 键：
        dsn: 连接串，如 ``postgresql://user:pass@host:5432/dbname``
        min_size / max_size: 池大小（默认 1 / 5）
        command_timeout: 秒（可选）
        connect_timeout: 建连超时秒数（默认 10，避免启动期挂 60s）
    """

    driver = "postgres"

    #: 事务期间钉住的连接（按任务上下文隔离）
    _txn_conn: contextvars.ContextVar = contextvars.ContextVar("pg_txn_conn", default=None)

    def __init__(self) -> None:
        self._pool: Any = None
        self._acquire_timeout = 10.0

    async def connect(self, config: dict[str, Any]) -> None:
        dsn = str(config.get("dsn") or "").strip()
        if not dsn:
            raise AdapterError("postgres adapter requires [database.postgres] dsn")
        try:
            # 依赖导入必须在保护块内：宿主未安装 asyncpg / vendor 缺失 / ABI 不匹配
            # （vendored 二进制只有 cp311）时，也要返回结构化错误，而不是把裸
            # ModuleNotFoundError 抛给入口层与跨插件调用方。
            import asyncpg

            self._pool = await asyncpg.create_pool(
                dsn=dsn,
                min_size=int(config.get("min_size", 1)),
                max_size=int(config.get("max_size", 5)),
                command_timeout=float(config["command_timeout"])
                if config.get("command_timeout")
                else None,
                timeout=float(config.get("connect_timeout", 10)),
            )
        except Exception as exc:
            self._pool = None
            raise AdapterError(f"postgres connect failed: {exc}") from exc

    def _require_pool(self):
        if self._pool is None:
            raise AdapterError("postgres adapter not connected")
        return self._pool

    def _active_conn(self) -> Any:
        return self._txn_conn.get()

    async def execute(self, sql: str, params: tuple = ()) -> int:
        sql = self._qmark_to_dollar(sql)
        conn = self._active_conn()
        try:
            if conn is not None:
                status = await conn.execute(sql, *params)
            else:
                status = await self._require_pool().execute(sql, *params)
        except Exception as exc:
            raise AdapterError(f"postgres execute failed: {exc}") from exc
        return rowcount_from_pg_status(status)

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        sql = self._qmark_to_dollar(sql)
        conn = self._active_conn()
        try:
            rows = (
                await conn.fetch(sql, *params)
                if conn is not None
                else await self._require_pool().fetch(sql, *params)
            )
        except Exception as exc:
            raise AdapterError(f"postgres fetch failed: {exc}") from exc
        return [dict(r) for r in rows]

    async def fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        sql = self._qmark_to_dollar(sql)
        conn = self._active_conn()
        try:
            row = (
                await conn.fetchrow(sql, *params)
                if conn is not None
                else await self._require_pool().fetchrow(sql, *params)
            )
        except Exception as exc:
            raise AdapterError(f"postgres fetch failed: {exc}") from exc
        return dict(row) if row is not None else None

    async def transaction(self) -> TransactionHandle:
        self._require_pool()
        return _PgTransaction(self)

    async def close(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            try:
                await pool.close()
            except Exception as exc:
                raise AdapterError(f"postgres close failed: {exc}") from exc
