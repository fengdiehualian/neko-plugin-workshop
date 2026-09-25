"""LLM 客户端：配置自建优先，失败/未配置自动降级（模板兜底）。

参照小游戏插件的三级策略：
1. 配置了自建 LLM（openai/anthropic/gemini 兼容）→ 情感渲染走 LLM
2. 未配置 → 返回 None，调用方用预制模板（EmotionRenderer）
3. LLM 调用失败/限流 → 返回 None，同样降级模板
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

log = logging.getLogger("neko_pawpilot.llm")


class LLMThrottle:
    """每分钟最多 N 次调用的限流器。"""

    def __init__(self, max_calls_per_minute: int = 15) -> None:
        self.max_calls = max(1, max_calls_per_minute)
        self._calls: list = []

    def acquire(self) -> bool:
        now = time.time()
        self._calls = [t for t in self._calls if now - t < 60]
        if len(self._calls) >= self.max_calls:
            return False
        self._calls.append(now)
        return True


class LLMClient:
    """统一 LLM 调用：OpenAI 兼容 / Anthropic / Gemini。"""

    def __init__(self, provider: str, model: str, api_key: str = "",
                 base_url: str = "", timeout: float = 25.0) -> None:
        self.provider = (provider or "").lower()
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout

    async def call(self, prompt: str) -> str:
        import importlib.util
        if importlib.util.find_spec("httpx") is None:
            raise RuntimeError("需要 httpx")
        if self.provider in ("openai", "openai_compatible", "deepseek"):
            url = self.base_url or "https://api.openai.com/v1/chat/completions"
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            body = {"model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.8}
        elif self.provider == "anthropic":
            url = self.base_url or "https://api.anthropic.com/v1/messages"
            headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                       "Content-Type": "application/json"}
            body = {"model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 256}
        elif self.provider == "gemini":
            url = (self.base_url or "https://generativelanguage.googleapis.com/v1beta/models") \
                  + f"/{self.model}:generateContent"
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                url += f"?key={self.api_key}"
            body = {"contents": [{"parts": [{"text": prompt}]}]}
        else:
            raise ValueError(f"不支持的 provider: {self.provider}")
        import httpx as _h
        async with _h.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        if self.provider == "anthropic":
            return "".join(b.get("text", "") for b in data.get("content", [])
                           if b.get("type") == "text").strip()
        if self.provider == "gemini":
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        return data["choices"][0]["message"]["content"].strip()


class LLMProvider:
    """装配自建 LLM，提供限流调用；失败/未配置返回 None（调用方降级）。"""

    def __init__(self, max_calls_per_minute: int = 15) -> None:
        self._client: Optional[LLMClient] = None
        self._throttle = LLMThrottle(max_calls_per_minute)
        self._stats: Dict[str, Any] = {
            "calls": 0, "total_tokens": 0, "last_ts": 0.0,
            "last_error": "",
        }

    @property
    def configured(self) -> bool:
        return self._client is not None

    def set_client(self, provider: str, model: str, api_key: str = "",
                   base_url: str = "") -> None:
        """配置自建客户端；provider/model 缺失则清空（降级）。"""
        if provider and model:
            self._client = LLMClient(provider, model, api_key, base_url)
        else:
            self._client = None

    def snapshot(self) -> Dict[str, Any]:
        return dict(self._stats) | {"configured": self.configured}

    async def call(self, prompt: str) -> Optional[str]:
        """限流内调用 LLM；失败/未配置返回 None（调用方模板兜底）。"""
        if not self.configured:
            return None
        if not self._throttle.acquire():
            self._stats["last_error"] = "rate_limited"
            return None
        try:
            out = await self._client.call(prompt)
            if out:
                self._stats["calls"] += 1
                self._stats["total_tokens"] += max(1, len(out) // 2)
                self._stats["last_ts"] = time.time()
                self._stats["last_error"] = ""
                return out.strip('"').strip()
        except Exception as exc:
            self._stats["last_error"] = str(exc)[:120]
            log.warning("LLM 调用失败: %s", exc)
        return None
