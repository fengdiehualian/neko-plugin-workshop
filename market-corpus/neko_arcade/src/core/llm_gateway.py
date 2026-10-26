"""统一 LLM 入口: 场景标签 + 缓存 + 限流 + 统计。

游戏不直接调 LLM(契约要求): 需要"内容生成"(出题/剧情/文案)时调用
    await gateway.scene("soup.puzzle", prompt, cache_key=..., ttl=...)
这样缓存口径、限流配额、token 统计、失败兜底全在本体一处, 各游戏不再各写一套。
纯台词/陪伴走 companion(它内部也用这里)。
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Callable, Dict, Optional

log = logging.getLogger("neko_arcade.llm_gateway")


class LLMGateway:
    """包一层现有 LLMProvider, 加上按场景的缓存与统计。"""

    def __init__(self, provider: Any = None, *, cache_cap: int = 128,
                 default_ttl: float = 600.0) -> None:
        self.provider = provider
        self._cache: Dict[str, tuple] = {}          # key -> (text, ts)
        self._cap = max(8, int(cache_cap))
        self._ttl = float(default_ttl)
        self.stats: Dict[str, Dict[str, int]] = {}   # scene -> {calls, cache, fail}

    def bind(self, provider: Any) -> None:
        self.provider = provider

    # ── 主入口 ──────────────────────────────
    async def scene(self, name: str, prompt: str, *, cache_key: str = "",
                    ttl: Optional[float] = None, fallback: str = "",
                    clean: Optional[Callable[[str], str]] = None) -> str:
        """按场景名调用: 命中缓存直接返回; 失败/超时返回 fallback(不抛异常)。"""
        st = self.stats.setdefault(str(name or "misc"), {"calls": 0, "cache": 0, "fail": 0})
        key = self._key(name, cache_key or prompt)
        if cache_key:
            hit = self._get(key, ttl)
            if hit is not None:
                st["cache"] += 1
                return hit
        if self.provider is None:
            st["fail"] += 1
            return fallback
        st["calls"] += 1
        try:
            out = await self.provider.call(prompt)
        except Exception as exc:
            log.debug("LLM 场景 %s 调用失败: %s", name, exc)
            out = None
        text = (clean(out) if clean else self._clean(out)) if out else ""
        if not text:
            st["fail"] += 1
            return fallback
        if cache_key:
            self._put(key, text)
        return text

    # ── 缓存 ────────────────────────────────
    def _key(self, name: str, raw: str) -> str:
        digest = hashlib.sha1(str(raw).encode("utf-8", "ignore")).hexdigest()[:16]
        return f"{name}:{digest}"

    def _get(self, key: str, ttl: Optional[float]) -> Optional[str]:
        item = self._cache.get(key)
        if not item:
            return None
        text, ts = item
        if time.time() - ts > float(ttl if ttl is not None else self._ttl):
            self._cache.pop(key, None)
            return None
        return text

    def _put(self, key: str, text: str) -> None:
        if len(self._cache) >= self._cap:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = (text, time.time())

    def clear(self) -> None:
        self._cache.clear()

    @staticmethod
    def _clean(out: Any) -> str:
        if not out:
            return ""
        text = " ".join(x.strip() for x in str(out).splitlines() if x.strip())
        return text.strip().strip('"“”「」')

    def snapshot(self) -> Dict[str, Any]:
        return {"scenes": {k: dict(v) for k, v in self.stats.items()},
                "cache": len(self._cache)}
