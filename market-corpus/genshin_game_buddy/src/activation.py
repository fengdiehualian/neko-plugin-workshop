"""激活状态管理：术语的按需激活 / TTL 过期 / 上限淘汰。

内部无锁，由调用方（__init__.py 的 _state_lock）统一保护。
"""
from __future__ import annotations

import time
from typing import Iterable

#: 同时最多激活的术语数（含核心层）。拍板 2.0.56：核心层典型 20-35 条，留 40 作缓冲。
MAX_ACTIVE = 40

#: 场景激活的 TTL（秒）。
SCENE_TTL_SECONDS = 120.0

#: 场景连续未命中多少 tick 后清除该场景激活。
SCENE_MISS_THRESHOLD = 3


class ActivationState:
    """运行时术语激活状态。

    * ``_active``: term_key → (expiry_monotonic, source)
    * ``_scene_miss``: scene_name → 连续未命中 tick 数
    * source 取值: ``"core_layer"`` / ``"query"`` / ``"scene:<name>"``
    """

    def __init__(self) -> None:
        self._active: dict[str, tuple[float, str]] = {}
        self._scene_miss: dict[str, int] = {}
        self._order: dict[str, int] = {}
        self._seq = 0

    def activate_terms(self, keys: list[str], source: str, ttl_seconds: float) -> None:
        now = time.monotonic()
        for key in keys:
            if not key:
                continue
            expiry = float("inf") if ttl_seconds == float("inf") else now + ttl_seconds
            self._active[key] = (expiry, source)
            self._seq += 1
            self._order[key] = self._seq
        self._evict()

    def activate_scene(self, scene_name: str, term_keys: list[str]) -> None:
        self._scene_miss[scene_name] = 0
        self.activate_terms(
            term_keys, source=f"scene:{scene_name}", ttl_seconds=SCENE_TTL_SECONDS
        )

    def tick_scene(self, scene_name: str, hit: bool) -> None:
        if hit:
            self._scene_miss[scene_name] = 0
            return
        count = self._scene_miss.get(scene_name, 0) + 1
        self._scene_miss[scene_name] = count
        if count >= SCENE_MISS_THRESHOLD:
            self.clear_scene(scene_name)

    def get_active_keys(self) -> list[str]:
        self._expire()
        return list(self._active.keys())

    def has_scene_active(self) -> bool:
        self._expire()
        return any(src.startswith("scene:") for _, src in self._active.values())

    def clear_all(self) -> None:
        self._active.clear()
        self._scene_miss.clear()
        self._order.clear()

    def clear_by_sources(self, sources: "Iterable[str]") -> list[str]:
        """拍板 2.0.64：移除指定 source 的激活，返回被移除的 keys 列表。

        转场清空用——从"有游戏"变成"没游戏"或游戏变了时，清掉屏幕/场景激活
        但保留 query（用户主动问过的，LLM 可能还在聊）+ core_layer（常驻锚点）。
        "scene" 作为前缀通配——匹配 f"scene:<name>" 形式的所有场景激活
        （activation.py 里 scene:<name> 是 activate_scene 的 source 命名）。
        """
        sources = tuple(sources)
        to_del: list[str] = []
        for key, (_exp, src) in self._active.items():
            if src in sources:
                to_del.append(key)
                continue
            if "scene" in sources and src.startswith("scene:"):
                to_del.append(key)
        for key in to_del:
            del self._active[key]
            self._order.pop(key, None)
        return to_del

    def clear_scene(self, scene_name: str) -> None:
        self._scene_miss.pop(scene_name, None)
        prefix = f"scene:{scene_name}"
        to_del = [k for k, (_, src) in self._active.items() if src == prefix]
        for k in to_del:
            del self._active[k]
            self._order.pop(k, None)

    def _expire(self) -> None:
        now = time.monotonic()
        expired = [k for k, (exp, _) in self._active.items() if exp < now]
        for k in expired:
            del self._active[k]
            self._order.pop(k, None)

    def _evict(self) -> None:
        self._expire()
        if len(self._active) <= MAX_ACTIVE:
            return

        def sort_key(key: str) -> tuple[int, int]:
            src = self._active[key][1]
            if src == "core_layer":
                return (3, 0)
            if src == "query":
                return (2, 0)
            return (1, self._order.get(key, 0))

        sorted_keys = sorted(self._active.keys(), key=sort_key)
        to_evict = len(self._active) - MAX_ACTIVE
        for key in sorted_keys:
            if to_evict <= 0:
                break
            if self._active[key][1] == "core_layer":
                continue
            del self._active[key]
            self._order.pop(key, None)
            to_evict -= 1
