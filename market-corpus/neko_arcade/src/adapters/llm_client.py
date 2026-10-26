"""LLM 客户端：宿主注入优先，配置自建兜底。带 token 使用统计。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("neko_arcade.llm")

# token 估算: 中文约 1.5 字符/token, 英文约 4 字符/token; 粗略按 2 字符/token
_CHARS_PER_TOKEN = 2.0


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数(无真实 usage 时兜底)。"""
    if not text:
        return 0
    return max(1, int(len(str(text)) / _CHARS_PER_TOKEN))


class LLMThrottle:
    """每分钟最多 N 次调用的限流器。"""

    def __init__(self, max_calls_per_minute: int = 15) -> None:
        self.max_calls = max(1, max_calls_per_minute)
        self._calls: List[float] = []

    def acquire(self) -> bool:
        now = time.time()
        self._calls = [t for t in self._calls if now - t < 60]
        if len(self._calls) >= self.max_calls:
            return False
        self._calls.append(now)
        return True


class LLMClient:
    """统一 LLM 调用：OpenAI / Anthropic / Gemini / 兼容 OpenAI 本地。"""

    def __init__(self, provider: str, model: str, api_key: str = "",
                 base_url: str = "", timeout: float = 25.0) -> None:
        self.provider = (provider or "").lower()
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout

    async def call(self, prompt: str) -> str:
        try:
            import httpx
        except ImportError:
            raise RuntimeError("需要 httpx")
        if self.provider in ("openai", "openai_compatible"):
            url = self.base_url or "https://api.openai.com/v1/chat/completions"
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            body = {"model": self.model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.8}
        elif self.provider == "anthropic":
            url = self.base_url or "https://api.anthropic.com/v1/messages"
            headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
            body = {"model": self.model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 512}
        elif self.provider == "gemini":
            url = (self.base_url or "https://generativelanguage.googleapis.com/v1beta/models") + f"/{self.model}:generateContent"
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                url += f"?key={self.api_key}"
            body = {"contents": [{"parts": [{"text": prompt}]}]}
        else:
            raise ValueError(f"不支持的 provider: {self.provider}")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        # 提取真实 usage(openai 兼容格式)
        usage = None
        try:
            if isinstance(data, dict) and isinstance(data.get("usage"), dict):
                u = data["usage"]
                usage = {
                    "prompt": int(u.get("prompt_tokens", 0) or 0),
                    "completion": int(u.get("completion_tokens", 0) or 0),
                    "total": int(u.get("total_tokens", 0) or 0),
                }
        except Exception:
            usage = None
        self.last_usage = usage
        if self.provider == "anthropic":
            return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        if self.provider == "gemini":
            return data["candidates"][0]["content"]["parts"][0]["text"]
        return data["choices"][0]["message"]["content"].strip()


class LLMProvider:
    """装配宿主注入或配置自建的 LLM，提供限流调用 + token 统计。"""

    def __init__(self, max_calls_per_minute: int = 15) -> None:
        self._host_call: Optional[Callable[[str], Any]] = None
        self._client: Optional[LLMClient] = None
        self._throttle = LLMThrottle(max_calls_per_minute)
        # token 统计(内存累计 + 定期落盘)
        self._stats: Dict[str, Any] = {
            "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "total_tokens": 0, "by_scene": {}, "first_ts": 0.0, "last_ts": 0.0,
        }
        self._persist: Optional[Callable[[Dict[str, Any]], Any]] = None
        self._scene_override: Optional[str] = None

    def set_host_call(self, call: Callable[[str], Any]) -> None:
        """注入宿主 LLM 回调(旧接口, 保留兼容; 宿主实际不注入 __call_llm,
        官方模式是返回 summary 由宿主演绎, 本插件默认走自建客户端+模板兜底)。"""
        self._host_call = call

    def set_client(self, provider: str, model: str, api_key: str = "", base_url: str = "") -> None:
        """配置自建客户端。"""
        if provider and model:
            self._client = LLMClient(provider, model, api_key, base_url)

    def set_persist(self, fn: Optional[Callable[[Dict[str, Any]], Any]]) -> None:
        """设置统计落盘回调(插件注入, 写 store; 可为 async)。"""
        self._persist = fn

    def set_scene(self, scene: str) -> None:
        """标记当前调用场景(emotion/出题/闲聊等), 由调用方设置。"""
        self._scene_override = scene or None

    def scene_of(self, prompt: str) -> str:
        """根据 prompt 特征判断调用场景(兜底)。"""
        if self._scene_override:
            return self._scene_override
        p = (prompt or "")[:80]
        if "情感" in p or "口吻" in p or "猫娘" in p and "说 1 句话" in p:
            return "emotion"
        if "海龟汤" in p or "出题" in p or "汤面" in p:
            return "soup_generate"
        if "修仙" in p or "道侣" in p or "亲密度" in p:
            return "xiuxian_chat"
        return "game"

    def snapshot(self) -> Dict[str, Any]:
        """返回当前统计快照(供 UI)。"""
        s = self._stats
        by_scene = dict(s.get("by_scene", {}))
        return {
            "calls": s.get("calls", 0),
            "prompt_tokens": s.get("prompt_tokens", 0),
            "completion_tokens": s.get("completion_tokens", 0),
            "total_tokens": s.get("total_tokens", 0),
            "by_scene": by_scene,
            "first_ts": s.get("first_ts", 0),
            "last_ts": s.get("last_ts", 0),
        }

    async def _record(self, prompt: str, output: Optional[str], usage: Optional[Dict]) -> None:
        """记录一次调用。优先真实 usage, 否则按字符估算。"""
        scene = self.scene_of(prompt)
        if usage:
            pt = usage.get("prompt", 0)
            ct = usage.get("completion", 0)
            tt = usage.get("total", pt + ct)
        else:
            pt = _estimate_tokens(prompt)
            ct = _estimate_tokens(output or "")
            tt = pt + ct
        now = time.time()
        s = self._stats
        s["calls"] += 1
        s["prompt_tokens"] += pt
        s["completion_tokens"] += ct
        s["total_tokens"] += tt
        if not s.get("first_ts"):
            s["first_ts"] = now
        s["last_ts"] = now
        bs = s.setdefault("by_scene", {}).setdefault(scene, {"calls": 0, "tokens": 0})
        bs["calls"] += 1
        bs["tokens"] += tt
        try:
            if self._persist:
                res = self._persist(s)
                if asyncio.iscoroutine(res):
                    await res
        except Exception:
            pass

    async def call(self, prompt: str) -> Optional[str]:
        """限流内调用 LLM，失败返回 None。

        优先级：配置自建客户端(新 LLM) → 宿主注入回调(旧接口, 见 set_host_call) →
        无则返回 None(调用方走模板兜底)。
        """
        if not self._throttle.acquire():
            log.warning("LLM 限流，拒绝调用")
            return None
        usage = None
        try:
            if self._client:
                self._scene_override = None  # 自建客户端 scene 由 prompt 判断
                out = await self._client.call(prompt)
                usage = getattr(self._client, "last_usage", None)
                if out is not None:
                    await self._record(prompt, out, usage)
                return out
            if self._host_call:
                result = self._host_call(prompt)
                out = await result if asyncio.iscoroutine(result) else str(result)
                await self._record(prompt, out, None)  # 宿主不返回 usage, 估算
                return out
        except Exception as exc:
            log.warning("LLM 调用失败: %s", exc)
        return None
