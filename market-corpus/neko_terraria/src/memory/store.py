"""SQLite 记忆存储：facts 表 + 重要性 + 时间衰减。

- remember(key, value, category, importance)：upsert 一条记忆
- recall(query, limit)：关键词匹配 + 重要性加权 + 半衰期衰减（7 天）排序
- forget(key)：删除
- close()：关闭连接（shutdown 时调用）
"""

import re
import sqlite3
import threading
import time
from typing import Any, Dict, List

# 半衰期：7 天（秒）
_HALF_LIFE = 7 * 24 * 3600


class MemoryStore:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.RLock()
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS facts ("
            " key TEXT PRIMARY KEY,"
            " value TEXT,"
            " category TEXT DEFAULT 'fact',"
            " importance REAL DEFAULT 1.0,"
            " created_at REAL,"
            " updated_at REAL,"
            " access_count INTEGER DEFAULT 0)")
        self._conn.commit()

    def remember(self, key: str, value: str,
                 category: str = "fact", importance: float = 1.0) -> None:
        """记住/更新一条记忆（upsert）。"""
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO facts(key,value,category,importance,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET"
                " value=excluded.value, category=excluded.category,"
                " importance=excluded.importance, updated_at=excluded.updated_at",
                (key, str(value), category, float(importance), now, now))
            self._conn.commit()

    def recall(self, query: str = "", limit: int = 10) -> List[Dict[str, Any]]:
        """按关键词匹配 + 重要性加权 + 半衰期衰减排序返回记忆。

        查询拆词（空白/标点分隔），任一词命中即匹配——"你还记得我喜欢什么吗"
        拆出"喜欢"也能召回"主人偏好-喜欢挖矿"这类记忆。
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT key,value,category,importance,created_at,updated_at,access_count"
                " FROM facts").fetchall()
        now = time.time()
        words: List[str] = []
        if query:
            # 中文连续文本没有空格，直接按标点切分会把整句当成一个词，
            # 导致“你还记得我喜欢什么吗”无法命中“喜欢挖矿”。
            # 保留完整片段，同时加入 2～4 字短语；英文/数字仍按词匹配。
            for part in re.split(r"[\s,，。！？!?、；;：:]+", query.casefold()):
                if not part:
                    continue
                words.append(part)
                for chunk in re.findall(r"[\u4e00-\u9fff]+|[a-z0-9_]+", part):
                    if re.fullmatch(r"[\u4e00-\u9fff]+", chunk):
                        for size in (4, 3, 2):
                            words.extend(chunk[i:i + size]
                                         for i in range(max(0, len(chunk) - size + 1)))
                    elif chunk != part:
                        words.append(chunk)
            words = list(dict.fromkeys(w for w in words if w))
        scored: List[tuple] = []
        for key, value, category, importance, _created, updated, _ac in rows:
            age = max(0.0, now - updated)
            decay = 0.5 ** (age / _HALF_LIFE)   # 越久没更新权重越低
            score = float(importance) * decay
            if words:
                hay = f"{key} {value} {category}".lower()
                if not any(w in hay for w in words):
                    continue
                score += 2.0
            scored.append((score, key, value, category))
            with self._lock:
                self._conn.execute(
                    "UPDATE facts SET access_count=access_count+1 WHERE key=?", (key,))
        with self._lock:
            self._conn.commit()
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"key": k, "value": v, "category": c}
                for _s, k, v, c in scored[:limit]]

    def forget(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM facts WHERE key=?", (key,))
            self._conn.commit()

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM facts").fetchone()
        return int(row[0]) if row else 0

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
