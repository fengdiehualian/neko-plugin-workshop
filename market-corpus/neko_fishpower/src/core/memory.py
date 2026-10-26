"""记忆系统：分层记忆 + 权重衰减/遗忘/模糊化（store 持久化）。

每条记忆带 weight（重要性）+ last_used（最后引用）：
- 衰减：周期调用 decay()，未引用的记忆 weight 缓慢下降
- 淘汰：每类超容量淘汰 weight 最低 + 最久未用的
- 保底：importance 高的记忆 weight 有下限，不会忘
- 模糊化：低 weight 记忆被引用时，recall 用「好像/大概」措辞
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

# 每类记忆容量上限（超出淘汰）
CAPACITY = {"trips": 200, "journal": 300, "islands": 50, "gambling": 20,
            "challenge": 20, "achievement": 50, "relationship": 100, "ledger": 50}
# 保底 weight（重要记忆不衰减到 0）
WEIGHT_FLOOR = {"journal": 0.6, "achievement": 0.8, "relationship": 0.7}
DEFAULT_FLOOR = 0.2
DECAY_RATE = 0.02


class MemoryStore:
    """分层记忆：出钓档案/图鉴足迹/岛屿/赌局/挑战/成就/关系/账本。"""

    KEYS = ("trips", "journal", "islands", "gambling", "challenge",
            "achievement", "relationship", "ledger")

    def __init__(self, store: Any) -> None:
        self.store = store  # 插件 store（Ok/Err 包装）
        self._data: Dict[str, Any] = {k: {} for k in self.KEYS}

    def _unwrap(self, val: Any, default: Any = None) -> Any:
        if val is None:
            return default
        if getattr(val, "is_err", None) and callable(val.is_err) and val.is_err():
            return default
        for field in ("value", "data"):
            if hasattr(val, field):
                inner = getattr(val, field)
                return inner if inner is not None else default
        return val

    async def load(self) -> None:
        try:
            raw = self._unwrap(await self.store.get("fishpower:memory"), {})
            if isinstance(raw, dict):
                for k in self.KEYS:
                    if isinstance(raw.get(k), dict):
                        self._data[k] = raw[k]
        except Exception:
            pass

    async def save(self) -> None:
        try:
            await self.store.set("fishpower:memory", self._data)
        except Exception:
            pass

    def remember(self, kind: str, key: str, value: Any,
                 importance: float = 0.5) -> None:
        """写入记忆（自动带 weight/last_used）。"""
        if kind not in self._data:
            return
        entry = dict(value or {})
        entry["weight"] = float(entry.get("weight", importance))
        entry["last_used"] = time.time()
        self._data[kind][key] = entry
        self._evict(kind)

    def query(self, kind: str, key: str) -> Optional[Any]:
        """读记忆并刷新 last_used（引用即更新）。"""
        entry = self._data.get(kind, {}).get(key)
        if entry:
            entry["last_used"] = time.time()
        return entry

    def bump(self, kind: str, key: str, field: str = "count", by: int = 1) -> int:
        """计数 +1 并返回新值（importance 默认 0.7）。"""
        entry = dict(self._data.get(kind, {}).get(key, {}) or {})
        entry[field] = int(entry.get(field, 0)) + by
        entry["last_used"] = time.time()
        entry["ts"] = time.time()  # recall 用 ts 显示"上次来/上次钓到"日期
        entry["weight"] = float(entry.get("weight", 0.7))
        self._data.setdefault(kind, {})[key] = entry
        return int(entry[field])

    def _evict(self, kind: str) -> None:
        """超容量淘汰：weight 最低 + 最久未用。"""
        cap = CAPACITY.get(kind, 100)
        entries = self._data.get(kind, {})
        if len(entries) <= cap:
            return
        ranked = sorted(entries.items(),
                        key=lambda kv: (kv[1].get("weight", 0), kv[1].get("last_used", 0)))
        for key, _ in ranked[: len(entries) - cap]:
            del entries[key]

    def decay(self) -> None:
        """权重衰减：未引用的记忆 weight 下降，保底不下限。"""
        for kind, entries in self._data.items():
            floor = WEIGHT_FLOOR.get(kind, DEFAULT_FLOOR)
            for entry in entries.values():
                w = float(entry.get("weight", 0.5))
                if w > floor:
                    entry["weight"] = max(floor, w - DECAY_RATE)

    def weight_of(self, kind: str, key: str) -> float:
        entry = self._data.get(kind, {}).get(key)
        return float(entry.get("weight", 0.0)) if entry else 0.0

    def snapshot(self) -> Dict[str, Any]:
        counts = {k: len(v) for k, v in self._data.items()}
        return {
            "counts": counts,
            "journal_count": len(self._data.get("journal", {})),
            "trips_count": len(self._data.get("trips", {})),
            "gambling_wins": (self._data.get("gambling", {}).get("wins", {}) or {}).get("count", 0),
            "gambling_losses": (self._data.get("gambling", {}).get("losses", {}) or {}).get("count", 0),
        }
