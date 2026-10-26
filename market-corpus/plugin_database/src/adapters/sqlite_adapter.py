"""SQLite 适配器 —— 默认驱动，零配置（aiosqlite，单连接 + WAL）。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .base import AdapterError, DatabaseAdapter, TransactionHandle


class _SqliteTransaction(TransactionHandle):
    """单连接 SQLite 事务。

    单连接上 ``BEGIN IMMEDIATE`` 不能嵌套，因此显式拒绝嵌套事务（返回结构化
    ``AdapterError``），而不是先 ``BEGIN`` 失败、再把 ``_txn_depth`` 计错。
    ``_txn_depth`` 只在 ``BEGIN`` 成功后自增，失败路径不会泄漏计数。
    """

    def __init__(self, adapter: "SQLiteAdapter") -> None:
        self._adapter = adapter
        self._finished = False
        self._rollback_pending = False

    async def _begin(self) -> None:
        if self._adapter._txn_depth:
            raise AdapterError(
                "sqlite adapter does not support nested transactions "
                "(single connection, BEGIN IMMEDIATE is already active)"
            )
        try:
            await self._adapter._raw("BEGIN IMMEDIATE")
        except AdapterError:
            raise
        except Exception as exc:  # sqlite3 异常 -> 结构化错误
            raise AdapterError(f"sqlite begin transaction failed: {exc}") from exc
        self._adapter._txn_depth += 1

    async def _finish(self) -> None:
        if not self._finished:
            self._finished = True
            self._adapter._txn_depth -= 1
            try:
                await self._adapter._raw("ROLLBACK" if self._rollback_pending else "COMMIT")
            except AdapterError:
                raise
            except Exception as exc:
                raise AdapterError(f"sqlite {'rollback' if self._rollback_pending else 'commit'} failed: {exc}") from exc

    async def _commit(self) -> None:
        await self._finish()

    async def _rollback(self) -> None:
        self._rollback_pending = True
        await self._finish()


class SQLiteAdapter(DatabaseAdapter):
    """单连接 SQLite。零配置：不传路径时自动创建数据库文件。

    config 键：
        path: 数据库文件路径（默认 ``<plugin data>/plugin_database.db``，
              由入口层补全；本适配器在 path 缺省时用内存库）。
    """

    driver = "sqlite"

    def __init__(self) -> None:
        self._conn: Any = None
        self._lock = asyncio.Lock()
        self._txn_depth = 0

    async def _raw(self, sql: str) -> None:
        await self._conn.execute(sql)

    async def _discard_conn(self) -> None:
        """尽力关闭并丢弃连接（不抛出，用于失败清理路径）。"""
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass

    async def connect(self, config: dict[str, Any]) -> None:
        if self._conn is not None:
            return
        raw = config.get("path")
        path: str | None = str(raw) if raw else None
        try:
            import aiosqlite

            if path and path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(path or ":memory:", isolation_level=None)
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA foreign_keys=ON")
            await self._conn.execute("PRAGMA busy_timeout=5000")
        except AdapterError:
            raise
        except Exception as exc:  # aiosqlite/sqlite3/导入异常统一转结构化错误
            await self._discard_conn()
            raise AdapterError(f"sqlite connect failed: {exc}") from exc
        self._vec0_error: str | None = None
        vec0_path = str(config.get("vec0_path") or "").strip()
        require_vec0 = bool(config.get("require_vec0", False))
        if vec0_path.lower() == "none":
            # 显式禁用（"none"），其余空值/缺失 = 自动
            vec0_path = ""
        if vec0_path and not Path(vec0_path).exists():
            if require_vec0:
                raise AdapterError(f"vec0 extension not found: {vec0_path}")
            self._vec0_error = f"extension file not found: {vec0_path}"
            return
        if vec0_path:
            try:
                await self._conn.enable_load_extension(True)
                await self._conn.load_extension(vec0_path)
                await self._conn.enable_load_extension(False)
            except Exception as exc:
                # 尽力而为：宿主 sqlite 可能未编译 loadable extension 支持
                # （AttributeError）或 ABI 不匹配。能力探测会如实汇报 vec0=False，
                # 知识库自动走 numpy 余弦扫描；仅显式 require_vec0 才视为致命。
                if require_vec0:
                    raise AdapterError(f"sqlite load vec0 extension failed: {exc}") from exc
                self._vec0_error = f"{type(exc).__name__}: {exc}"

    async def _require_conn(self):
        if self._conn is None:
            raise AdapterError("sqlite adapter not connected")
        return self._conn

    async def execute(self, sql: str, params: tuple = ()) -> int:
        conn = await self._require_conn()
        async with self._lock:
            try:
                cur = await conn.execute(sql, tuple(params))
                if self._txn_depth == 0:
                    await conn.commit()
            except AdapterError:
                raise
            except Exception as exc:  # sqlite3 异常 -> 结构化错误（入口层不抛）
                raise AdapterError(f"sqlite execute failed: {exc}") from exc
            return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        conn = await self._require_conn()
        async with self._lock:
            try:
                cur = await conn.execute(sql, tuple(params))
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description or ()]
            except AdapterError:
                raise
            except Exception as exc:
                raise AdapterError(f"sqlite fetch failed: {exc}") from exc
        return [dict(zip(cols, row)) for row in rows]

    async def fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        conn = await self._require_conn()
        async with self._lock:
            try:
                cur = await conn.execute(sql, tuple(params))
                row = await cur.fetchone()
                cols = [d[0] for d in cur.description or ()]
            except AdapterError:
                raise
            except Exception as exc:
                raise AdapterError(f"sqlite fetch failed: {exc}") from exc
        return dict(zip(cols, row)) if row is not None else None

    async def transaction(self) -> TransactionHandle:
        await self._require_conn()
        return _SqliteTransaction(self)

    async def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            await conn.close()
        except Exception as exc:
            raise AdapterError(f"sqlite close failed: {exc}") from exc
