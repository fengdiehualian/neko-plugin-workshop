"""DatabaseAdapter 统一接口与错误类型。

四个驱动适配器（SQLite / PostgreSQL / MySQL / MongoDB）都实现这里定义的
``DatabaseAdapter``。约定：

* ``connect(config)`` 建立连接/连接池；``config`` 由 plugin.toml 的 ``[database.*]``
  段展开而来，键名见各适配器 docstring。
* ``execute(sql, params)`` 返回受影响行数（DML）；SELECT 不保证有意义的返回值。
* ``fetch_all`` / ``fetch_one`` 返回 ``dict``（列名 -> 值）。
* ``transaction()`` 是 ``async`` 方法，返回一个异步上下文管理器，
  用法：``async with await adapter.transaction(): ...``。
  异常时回滚，正常退出时提交。
* MongoDB 不支持 SQL：``sql`` 参数传 JSON 字符串编码的文档操作，
  见 :mod:`.mongo_adapter`。

占位符风格：SQLite/MySQL 用 ``?``，PostgreSQL 也接受 ``?``（内部转译成 ``$n``），
MongoDB 忽略 params。这样上层可以用同一份 SQL。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from types import TracebackType
from typing import Any


class AdapterError(Exception):
    """适配器层结构化错误。宿主与跨插件调用方据此返回 Err 而不是崩溃。"""


class TransactionHandle:
    """通用异步事务上下文管理器骨架。"""

    async def __aenter__(self) -> "TransactionHandle":
        await self._begin()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            await self._commit()
        else:
            await self._rollback()

    async def _begin(self) -> None:  # pragma: no cover - 由子类覆写
        raise NotImplementedError

    async def _commit(self) -> None:  # pragma: no cover - 由子类覆写
        raise NotImplementedError

    async def _rollback(self) -> None:  # pragma: no cover - 由子类覆写
        raise NotImplementedError


class DatabaseAdapter(ABC):
    """统一数据库适配器接口（全部异步，禁止阻塞调用）。"""

    #: 驱动标识，与 plugin.toml 的 [database] driver 一致
    driver: str = "abstract"

    @abstractmethod
    async def connect(self, config: dict[str, Any]) -> None:
        """建立连接或连接池。config 来自 plugin.toml。"""

    @abstractmethod
    async def execute(self, sql: str, params: tuple = ()) -> int:
        """执行 DML，返回受影响行数。"""

    @abstractmethod
    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        """执行查询，返回全部行（dict 列表）。"""

    @abstractmethod
    async def fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        """执行查询，返回第一行或 None。"""

    @abstractmethod
    async def transaction(self) -> TransactionHandle:
        """返回异步事务上下文管理器：``async with await a.transaction():``。"""

    @abstractmethod
    async def close(self) -> None:
        """关闭连接/连接池，释放资源。"""

    # ------------------------------------------------------------------
    # 占位符转译工具
    # ------------------------------------------------------------------

    @staticmethod
    def _qmark_to_dollar(sql: str) -> str:
        """把 ``?`` 占位符转成 asyncpg 的 ``$1..$n``。

        跳过：单引号字符串（含 ``E'...'`` 转义串与 ``''`` 转义）、双引号标识符
        （``"we?ird"``）、``--`` 行注释、``/* */`` 块注释、``$$ ... $$`` 美元引用。
        """
        return _scan_sql(sql, lambda idx: f"${idx}")


def _scan_sql(
    sql: str, emit: Callable[[int], str], *, hash_comments: bool = False
) -> str:
    """按 SQL 词法扫描 ``sql``，把占位符位置的 ``?`` 交给 ``emit(index)``。

    统一实现各适配器共用的"跳过引号/注释"规则，避免每个转译器各写一份
    （此前 ``base`` 与 ``mysql_adapter`` 各有一份，且都只认单引号）。

    跳过：单引号字符串（``''`` 转义）、``E'...'`` 转义串、双引号标识符、
    反引号标识符、``--`` 行注释、``/* */`` 块注释（支持嵌套）、``$tag$`` 美元引用。
    ``hash_comments`` 只在 MySQL 打开（``#`` 在 PG 里是按位异或运算符，不是注释）。
    """
    out: list[str] = []
    idx = 0
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        # --- 行注释：-- 到行尾 ---
        if ch == "-" and sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j < 0 else j + 1
            out.append(sql[i:j])
            i = j
            continue
        # --- MySQL 的 # 行注释 ---
        if hash_comments and ch == "#":
            j = sql.find("\n", i)
            j = n if j < 0 else j + 1
            out.append(sql[i:j])
            i = j
            continue
        # --- 块注释：/* ... */（PG 支持嵌套） ---
        if ch == "/" and sql.startswith("/*", i):
            depth = 0
            j = i
            while j < n:
                if sql.startswith("/*", j):
                    depth += 1
                    j += 2
                elif sql.startswith("*/", j):
                    depth -= 1
                    j += 2
                    if depth == 0:
                        break
                else:
                    j += 1
            out.append(sql[i:j])
            i = j
            continue
        # --- 美元引用：$tag$ ... $tag$（仅 PG；$$ 也算） ---
        if ch == "$":
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            if j < n and sql[j] == "$":
                tag = sql[i : j + 1]
                end = sql.find(tag, j + 1)
                end = n if end < 0 else end + len(tag)
                out.append(sql[i:end])
                i = end
                continue
        # --- 单引号字符串：E'...' 与普通 '...'，'' 为转义 ---
        if ch == "'" or (
            ch in "Ee" and i + 1 < n and sql[i + 1] == "'" and _is_ident_boundary(sql, i)
        ):
            prefix = ""
            if ch in "Ee":
                prefix = ch
                i += 1
            out.append(prefix + "'")
            i += 1
            while i < n:
                if sql[i] == "\\" and prefix:  # E'...' 里反斜杠转义
                    out.append(sql[i : i + 2])
                    i += 2
                    continue
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        out.append("''")
                        i += 2
                        continue
                    out.append("'")
                    i += 1
                    break
                out.append(sql[i])
                i += 1
            continue
        # --- 双引号标识符（"we?ird"）；MySQL 里也可能是字符串，同样跳过 ---
        if ch == '"':
            out.append(ch)
            i += 1
            while i < n:
                if sql[i] == '"':
                    if i + 1 < n and sql[i + 1] == '"':
                        out.append('""')
                        i += 2
                        continue
                    out.append('"')
                    i += 1
                    break
                out.append(sql[i])
                i += 1
            continue
        # --- 反引号标识符（MySQL） ---
        if ch == "`":
            j = sql.find("`", i + 1)
            j = n if j < 0 else j + 1
            out.append(sql[i:j])
            i = j
            continue
        if ch == "?":
            idx += 1
            out.append(emit(idx))
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _is_ident_boundary(sql: str, i: int) -> bool:
    """``E`` 前面是标识符字符时不算 ``E'...'`` 前缀（如 ``CASE'`` 不存在，但 ``NAME'`` 可能）。"""
    if i == 0:
        return True
    prev = sql[i - 1]
    return not (prev.isalnum() or prev == "_")


def rowcount_from_pg_status(status: str) -> int:
    """asyncpg 的 execute 返回形如 ``INSERT 0 3`` 的状态串，解析出行数。"""
    parts = (status or "").split()
    for part in reversed(parts):
        if part.isdigit():
            return int(part)
    return 0
