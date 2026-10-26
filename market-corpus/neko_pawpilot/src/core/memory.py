"""陪玩记忆：宿主 store 持久化 + weight 衰减 + 归档不删除。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

STORE_KEY = "neko_pawpilot:memory"
DECAY_RATE = 0.05
FLOOR = 0.15
BOOST = 0.1
PROTECTION_FLOOR = 0.5
PROTECTED_KINDS = ["crash", "record", "relationship"]
LIMITS = {"cities": 300, "cargos": 100, "trucks": 50, "events": 100,
          "relationship": 50, "trips": 200, "player_notes": 120}


def unwrap_result(value: Any) -> Any:
    """拆开宿主的 Ok/Err 包装。

    **这是"陪玩记忆永远是空的"的根因**：宿主 `store.get()` 返回
    `Result[JsonValue|None, StoreError]`（`Ok(value)` / `Err(error)`），
    旧代码直接 `isinstance(raw, dict)` —— 永远不成立，于是每次启动记忆都读不出来；
    更糟的是紧接着的 `save()` 会把**空内存态**写回去，把存档覆盖成空
    （实测 store.db 里那条记录只剩 31 字节）。
    """
    if value is None:
        return None
    is_ok = getattr(value, "is_ok", None)
    if callable(is_ok):
        try:
            return value.value if value.is_ok() else None
        except Exception:                   # noqa: BLE001
            return None
    return value


class MemoryStore:
    """记忆存储：宿主 KV（store）持久化 + 本地 JSON 兜底，weight 衰减，超限归档。"""

    def __init__(self, kv: Any, mirror_path: Any = None) -> None:
        # kv 为宿主 store 的适配器（提供 async get/set，返回 Ok/Err 包装）
        self._kv = kv
        self._stores: Dict[str, Dict[str, Any]] = {}
        self._loaded = False
        self._mirror = Path(mirror_path) if mirror_path else None
        self.last_error = ""

    # ── 兜底镜像：宿主 store 出问题时不至于把记忆弄丢 ──
    def _read_mirror(self) -> Optional[Dict[str, Any]]:
        if not self._mirror:
            return None
        try:
            data = json.loads(self._mirror.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def _write_mirror(self) -> None:
        if not self._mirror:
            return
        try:
            self._mirror.parent.mkdir(parents=True, exist_ok=True)
            self._mirror.write_text(
                json.dumps(self._stores, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            self.last_error = f"记忆镜像写入失败: {exc}"

    def is_empty(self) -> bool:
        return not any(bool(v) for v in self._stores.values())

    async def load(self) -> None:
        data = None
        try:
            data = unwrap_result(await self._kv.get(STORE_KEY))
        except Exception as exc:            # noqa: BLE001
            self.last_error = f"宿主 store 读取失败: {exc}"
        if not isinstance(data, dict) or not data:
            mirror = self._read_mirror()
            if isinstance(mirror, dict) and mirror:
                data = mirror
                self.last_error = self.last_error or "宿主 store 无数据，已用本地镜像恢复"
        if isinstance(data, dict) and data:
            self._stores = data
        self._loaded = True

    async def save(self) -> None:
        # 防覆盖：内存是空的不代表存档是空的（加载失败过），先看存档
        if self.is_empty():
            try:
                existing = unwrap_result(await self._kv.get(STORE_KEY))
            except Exception:               # noqa: BLE001
                existing = None
            if not isinstance(existing, dict) or not existing:
                existing = self._read_mirror()
            if isinstance(existing, dict) and existing:
                self._stores = existing
                self.last_error = "内存为空但存档有内容：已恢复存档，拒绝覆盖"
                return
        try:
            result = await self._kv.set(STORE_KEY, self._stores)
            is_err = getattr(result, "is_err", None)
            if callable(is_err) and result.is_err():
                self.last_error = f"宿主 store 写入失败: {getattr(result, 'error', '')}"
        except Exception as exc:            # noqa: BLE001
            self.last_error = f"宿主 store 写入异常: {exc}"
        self._write_mirror()

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
