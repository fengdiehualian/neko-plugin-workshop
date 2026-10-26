"""MySQL 适配器（aiomysql 连接池，DictCursor，``?`` 占位符转 ``%s``）。

事务语义与 PostgreSQL 适配器一致：``transaction()`` 钉住一条连接，
事务期间 ``execute`` / ``fetch_*`` 经 contextvar 路由到该连接，
语句真正参与事务。含 params 的 SQL 中字面 ``%`` 需写成 ``%%``（PyMySQL 规则）。
"""

from __future__ import annotations

import contextvars
from typing import Any

from .base import AdapterError, DatabaseAdapter, TransactionHandle, _scan_sql


def _qmark_to_percent(sql: str) -> str:
    """把 ``?`` 占位符转成 PyMySQL 风格的 ``%s``。

    与 postgres 转译器共用 ``_scan_sql``：跳过单引号字符串（含 ``''`` 转义）、
    双引号标识符/字符串、反引号标识符、``--`` / ``#`` 行注释与 ``/* */`` 块注释。
    """
    return _scan_sql(sql, lambda _idx: "%s", hash_comments=True)


class _MysqlTransaction(TransactionHandle):
    def __init__(self, adapter: "MySQLAdapter") -> None:
        self._adapter = adapter
        self._conn: Any = None
        self._token: contextvars.Token | None = None
        self._done = False

    async def _begin(self) -> None:
        self._conn = await self._adapter._require_pool().acquire()
        cur = await self._conn.cursor()
        try:
            await cur.execute("START TRANSACTION")
        finally:
            await cur.close()
        self._token = self._adapter._txn_conn.set(self._conn)

    async def _finish(self, commit: bool) -> None:
        conn, token = self._conn, self._token
        self._conn = self._token = None
        if token is not None:
            self._adapter._txn_conn.reset(token)
        if conn is None:
            return
        cur = await conn.cursor()
        try:
            if not self._done:
                await cur.execute("COMMIT" if commit else "ROLLBACK")
        finally:
            self._done = True
            await cur.close()
            self._adapter._require_pool().release(conn)

    async def _commit(self) -> None:
        await self._finish(commit=True)

    async def _rollback(self) -> None:
        await self._finish(commit=False)


class MySQLAdapter(DatabaseAdapter):
    """aiomysql 连接池（autocommit=True，事务用显式钉住的连接）。

    config 键：
        dsn: ``mysql://user:pass@host:3306/dbname``
        min_size / max_size: 池大小（默认 1 / 5）
        connect_timeout: 秒（默认 10）
    """

    driver = "mysql"

    #: 事务期间钉住的连接（按任务上下文隔离）
    _txn_conn: contextvars.ContextVar = contextvars.ContextVar("my_txn_conn", default=None)

    def __init__(self) -> None:
        self._pool: Any = None

    async def connect(self, config: dict[str, Any]) -> None:
        from urllib.parse import unquote, urlsplit

        dsn = str(config.get("dsn") or "").strip()
        if not dsn:
            raise AdapterError("mysql adapter requires [database.mysql] dsn")
        u = urlsplit(dsn)
        if u.scheme not in ("mysql", "mariadb"):
            raise AdapterError(f"mysql dsn scheme must be mysql://, got {u.scheme!r}")
        try:
            # 依赖导入放在保护块内：宿主未安装/vendor 缺失时也要给结构化错误，
            # 而不是把裸 ModuleNotFoundError 抛给入口层与跨插件调用方。
            import aiomysql

            self._pool = await aiomysql.create_pool(
                host=u.hostname or "127.0.0.1",
                port=u.port or 3306,
                user=unquote(u.username or ""),
                password=unquote(u.password or ""),
                db=u.path.lstrip("/") or None,
                minsize=int(config.get("min_size", 1)),
                maxsize=int(config.get("max_size", 5)),
                cursorclass=aiomysql.DictCursor,
                autocommit=True,
                connect_timeout=int(config.get("connect_timeout", 10)),
            )
        except Exception as exc:
            self._pool = None
            raise AdapterError(f"mysql connect failed: {exc}") from exc

    def _require_pool(self):
        if self._pool is None:
            raise AdapterError("mysql adapter not connected")
        return self._pool

    def _active_conn(self) -> Any:
        return self._txn_conn.get()

    async def _run(self, conn: Any, sql: str, params: tuple):
        cur = await conn.cursor()
        try:
            await cur.execute(_qmark_to_percent(sql), tuple(params) or None)
            return cur
        except Exception as exc:
            await cur.close()
            raise exc

    async def execute(self, sql: str, params: tuple = ()) -> int:
        conn = self._active_conn()
        if conn is not None:
            try:
                cur = await self._run(conn, sql, params)
            except Exception as exc:
                raise AdapterError(f"mysql execute failed: {exc}") from exc
            rowcount = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
            await cur.close()
            return rowcount
        pool = self._require_pool()
        async with pool.acquire() as conn:
            try:
                cur = await self._run(conn, sql, params)
            except Exception as exc:
                raise AdapterError(f"mysql execute failed: {exc}") from exc
            rowcount = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
            await cur.close()
            return rowcount

    async def _fetch(self, sql: str, params: tuple, many: bool) -> Any:
        conn = self._active_conn()
        if conn is not None:
            rows = await self._fetch_on(conn, sql, params, many)
            return rows
        pool = self._require_pool()
        async with pool.acquire() as conn:
            return await self._fetch_on(conn, sql, params, many)

    async def _fetch_on(self, conn: Any, sql: str, params: tuple, many: bool):
        try:
            cur = await self._run(conn, sql, params)
        except Exception as exc:
            raise AdapterError(f"mysql fetch failed: {exc}") from exc
        try:
            if many:
                rows = await cur.fetchall()
                return [dict(r) for r in rows] if rows else []
            row = await cur.fetchone()
            return dict(row) if row is not None else None
        finally:
            await cur.close()

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        return await self._fetch(sql, params, many=True)  # type: ignore[return-value]

    async def fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        return await self._fetch(sql, params, many=False)  # type: ignore[return-value]

    async def transaction(self) -> TransactionHandle:
        self._require_pool()
        return _MysqlTransaction(self)

    async def close(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            try:
                pool.close()
                await pool.wait_closed()
            except Exception as exc:
                raise AdapterError(f"mysql close failed: {exc}") from exc
