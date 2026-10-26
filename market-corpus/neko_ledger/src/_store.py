"""记账猫娘 · 存储层（SQLite，仅标准库）。

数据落在插件自己的目录里，不碰角色的角色卡 —— 记账是插件自己的数据，
没有理由写进用户的人设。

异步包装：所有磁盘操作都走 `asyncio.to_thread`；sqlite3 连接是 thread-local，
每个线程开自己的连接，避免跨线程复用。
"""

from __future__ import annotations

import asyncio
import gc
import os
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,          -- ISO8601，本地时间
    day         TEXT    NOT NULL,          -- YYYY-MM-DD，便于按天聚合
    amount      REAL    NOT NULL,
    item        TEXT    NOT NULL,
    category    TEXT    NOT NULL DEFAULT '其他',
    raw         TEXT    NOT NULL DEFAULT '',
    tone        TEXT    NOT NULL DEFAULT 'cute',
    note        TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_expenses_day ON expenses(day);
CREATE INDEX IF NOT EXISTS idx_expenses_category ON expenses(category);
"""


def default_data_dir() -> Path:
    """默认数据目录：%LOCALAPPDATA%\\N.E.K.O\\plugins\\neko_ledger"""
    local = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(local) / "N.E.K.O" / "plugins" / "neko_ledger"


class LedgerStore:
    """极简账本存储。"""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        # 记录所有线程开过的连接：to_thread 会换线程，只关当前线程那个不够，
        # 其余连接会一直占着 db/wal 文件（Windows 上连临时目录都删不掉）。
        self._all_conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    # ------------------------------------------------------------- 连接管理

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
            with self._conns_lock:
                self._all_conns.append(conn)
        return conn

    # --------------------------------------------------------------- 写入

    def _add_sync(
        self,
        *,
        amount: float,
        item: str,
        category: str,
        raw: str = "",
        tone: str = "cute",
        note: str = "",
        when: Optional[datetime] = None,
    ) -> dict[str, Any]:
        moment = when or datetime.now()
        conn = self._conn()
        cur = conn.execute(
            "INSERT INTO expenses (ts, day, amount, item, category, raw, tone, note)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                moment.isoformat(timespec="seconds"),
                moment.strftime("%Y-%m-%d"),
                round(float(amount), 2),
                item,
                category,
                raw,
                tone,
                note,
            ),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "amount": round(float(amount), 2),
            "item": item,
            "category": category,
            "ts": moment.isoformat(timespec="seconds"),
        }

    async def add(self, **kwargs: Any) -> dict[str, Any]:
        return await asyncio.to_thread(self._add_sync, **kwargs)

    # --------------------------------------------------------------- 查询

    def _category_stats_sync(self, category: str, days: int = 90) -> tuple[Optional[float], int]:
        """返回 (该类别近期均值, 样本数)。"""
        since = (date.today() - timedelta(days=max(1, days))).strftime("%Y-%m-%d")
        conn = self._conn()
        row = conn.execute(
            "SELECT AVG(amount) AS avg_amount, COUNT(*) AS n FROM expenses"
            " WHERE category = ? AND day >= ?",
            (category, since),
        ).fetchone()
        if not row or not row["n"]:
            return None, 0
        return float(row["avg_amount"]), int(row["n"])

    async def category_stats(self, category: str, days: int = 90) -> tuple[Optional[float], int]:
        return await asyncio.to_thread(self._category_stats_sync, category, days)

    def _recent_sync(self, limit: int = 10) -> list[dict[str, Any]]:
        conn = self._conn()
        rows = conn.execute(
            "SELECT id, ts, amount, item, category, tone FROM expenses"
            " ORDER BY id DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
        return [dict(r) for r in rows]

    async def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._recent_sync, limit)

    def _summary_sync(self, *, start: str, end: str, category: Optional[str] = None) -> dict[str, Any]:
        conn = self._conn()
        where = "day >= ? AND day <= ?"
        params: list[Any] = [start, end]
        if category:
            where += " AND category = ?"
            params.append(category)

        total_row = conn.execute(
            f"SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS n FROM expenses WHERE {where}",
            params,
        ).fetchone()
        by_cat = conn.execute(
            f"SELECT category, SUM(amount) AS total, COUNT(*) AS n FROM expenses"
            f" WHERE {where} GROUP BY category ORDER BY total DESC",
            params,
        ).fetchall()

        return {
            "range": [start, end],
            "total": round(float(total_row["total"]), 2),
            "count": int(total_row["n"]),
            "by_category": [
                {"category": r["category"], "total": round(float(r["total"]), 2), "count": int(r["n"])}
                for r in by_cat
            ],
        }

    async def summary(
        self, *, start: str, end: str, category: Optional[str] = None
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self._summary_sync, start=start, end=end, category=category)

    def _latest_sync(self) -> Optional[dict[str, Any]]:
        conn = self._conn()
        row = conn.execute(
            "SELECT id, ts, amount, item, category, tone FROM expenses ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    async def latest(self) -> Optional[dict[str, Any]]:
        return await asyncio.to_thread(self._latest_sync)

    def _delete_sync(self, entry_id: int) -> Optional[dict[str, Any]]:
        conn = self._conn()
        row = conn.execute(
            "SELECT id, amount, item, category FROM expenses WHERE id = ?", (entry_id,)
        ).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM expenses WHERE id = ?", (entry_id,))
        conn.commit()
        return dict(row)

    async def delete(self, entry_id: int) -> Optional[dict[str, Any]]:
        return await asyncio.to_thread(self._delete_sync, entry_id)

    def _count_sync(self) -> int:
        conn = self._conn()
        row = conn.execute("SELECT COUNT(*) AS n FROM expenses").fetchone()
        return int(row["n"]) if row else 0

    async def count(self) -> int:
        return await asyncio.to_thread(self._count_sync)

    # --------------------------------------------------------------- 清理

    def close(self) -> None:
        """关掉**所有**线程开过的连接，并确保文件句柄真的释放。

        Windows 上不关连接会一直占着 db / wal 文件（连临时目录都删不掉）。
        而且 `Connection.close()` 只标记关闭：sqlite3 连接对象之间存在
        循环引用，引用计数不会立刻归零，句柄要等到 GC 才释放。
        所以这里显式置空引用并跑一次 `gc.collect()`，
        让「close 之后文件就可以删」成为一个真正成立的保证。
        """
        with self._conns_lock:
            conns, self._all_conns = self._all_conns, []
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        conns.clear()
        self._local.conn = None

        # 打断循环引用并回收，确保句柄马上释放
        gc.collect()
