"""陪玩记忆：宿主 store 持久化 + weight 衰减 + 归档不删除。"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

STORE_KEY = "neko_pawpilot:memory"
DECAY_RATE = 0.05
FLOOR = 0.15
BOOST = 0.1
PROTECTION_FLOOR = 0.5
PROTECTED_KINDS = ["crash", "record", "relationship"]
LIMITS = {"cities": 300, "cargos": 100, "trucks": 50, "events": 100,
          "relationship": 50, "trips": 200}


class MemoryStore:
    """记忆存储：通过宿主 KV（store）持久化，weight 衰减，超限归档。"""

    def __init__(self, kv: Any) -> None:
        # kv 为宿主 store 的适配器（提供 async get/set）
        self._kv = kv
        self._stores: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    async def load(self) -> None:
        raw = await self._kv.get(STORE_KEY)
        if isinstance(raw, dict):
            self._stores = raw
        self._loaded = True

    async def save(self) -> None:
        await self._kv.set(STORE_KEY, self._stores)

    def _store(self, kind: str) -> Dict[str, Any]:
        return self._stores.setdefault(kind, {})

    def remember(self, kind: str, key: str, data: Dict[str, Any],
                 importance: float = 0.5) -> None:
        """写入/更新一条记忆（内存态；由调用方触发 save）。

        再次到访/接货时清除归档标志，让记忆复活。
        """
        now = time.time()
        entry = self._store(kind).get(key, {})
        entry.update(data)
        entry["weight"] = importance
        entry["last_used"] = now
        entry["kind"] = kind
        entry.pop("archived", None)
        self._store(kind)[key] = entry

    def bump(self, kind: str, key: str) -> Optional[Dict[str, Any]]:
        """唤起记忆：boost weight 并刷新 last_used。"""
        entry = self._store(kind).get(key)
        if not entry:
            return None
        entry["last_used"] = time.time()
        if entry.get("kind") not in PROTECTED_KINDS:
            entry["weight"] = min(1.0, entry.get("weight", 0) + BOOST)
        return entry

    def decay(self) -> None:
        """weight 衰减；保护记忆有下限；低于下限归档（不删除）。"""
        for kind, store in self._stores.items():
            for key, entry in list(store.items()):
                if entry.get("archived"):
                    continue
                if entry.get("kind") in PROTECTED_KINDS:
                    entry["weight"] = max(PROTECTION_FLOOR, entry.get("weight", 0))
                    continue
                entry["weight"] = max(0.0, entry.get("weight", 0) - DECAY_RATE)
                if entry["weight"] < FLOOR:
                    entry["archived"] = True

    def query(self, kind: str, key: str = None) -> Any:
        store = self._store(kind)
        if key is not None:
            return store.get(key)
        return store

    def best(self, kind: str, top: int = 3) -> List[Dict[str, Any]]:
        """按 weight 取前 top 条（排除归档）。"""
        store = self._store(kind)
        ranked = sorted(
            (e for e in store.values() if not e.get("archived")),
            key=lambda e: e.get("weight", 0), reverse=True)
        return ranked[:top]

    def snapshot(self) -> Dict[str, Any]:
        return {k: len(v) for k, v in self._stores.items()}
