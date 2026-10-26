"""统一 LLM 客户端：兼容多种 API（OpenAI/Anthropic/Gemini/本地）。"""

import logging
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


class UnifiedLLMClient:
    """统一 LLM 调用接口，支持：OpenAI、Anthropic、Gemini、兼容 OpenAI 的本地模型。"""

    def __init__(self, provider: str, model: str, api_key: str = "",
                 base_url: str = "", timeout: float = 30.0):
        self.provider = provider.lower()
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self._client = None

    async def call(self, prompt: str) -> str:
        """统一调用接口，返回 LLM 文本响应。"""
        if self.provider == "openai":
            return await self._call_openai(prompt)
        elif self.provider == "anthropic":
            return await self._call_anthropic(prompt)
        elif self.provider == "gemini":
            return await self._call_gemini(prompt)
        elif self.provider == "openai_compatible":
            return await self._call_openai_compatible(prompt)
        else:
            raise ValueError(f"不支持的 provider: {self.provider}")

    async def _call_openai(self, prompt: str) -> str:
        """OpenAI API（官方/Azure）。"""
        try:
            import httpx
        except ImportError:
            raise RuntimeError("需要安装 httpx: pip install httpx")

        # base_url 语义：API 根（如 https://api.deepseek.com）或完整端点均可。
        # 只给根/无端点的补 /v1/chat/completions，避免把根路径当端点直接 POST（404）；
        # 已以 /v1 结尾的不再重复加 v1
        url = self.base_url or "https://api.openai.com/v1/chat/completions"
        if "/chat/completions" not in url:
            base = url.rstrip("/")
            url = base if base.endswith("/v1") else f"{base}/v1"
            url = f"{url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    async def _call_anthropic(self, prompt: str) -> str:
        """Anthropic Claude API。"""
        try:
            import httpx
        except ImportError:
            raise RuntimeError("需要安装 httpx: pip install httpx")

        url = self.base_url or "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2048,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            return data["content"][0]["text"]

    async def _call_gemini(self, prompt: str) -> str:
        """Google Gemini API。"""
        try:
            import httpx
        except ImportError:
            raise RuntimeError("需要安装 httpx: pip install httpx")

        url = self.base_url or f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        params = {"key": self.api_key}
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.7},
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, params=params, json=body)
            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]

    async def _call_openai_compatible(self, prompt: str) -> str:
        """兼容 OpenAI API 的本地模型（Ollama/LM Studio/vLLM）。"""
        try:
            import httpx
        except ImportError:
            raise RuntimeError("需要安装 httpx: pip install httpx")

        if not self.base_url:
            raise ValueError("openai_compatible 需要设置 base_url")

        url = self.base_url.rstrip("/")
        if "/chat/completions" not in url:
            url = url if url.endswith("/v1") else f"{url}/v1"
            url = f"{url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]


def create_llm_client(config: Dict[str, Any], prefix: str) -> Optional[UnifiedLLMClient]:
    """从配置创建 LLM 客户端。

    Args:
        config: 插件配置字典
        prefix: "llm_main" 或 "llm_intent"

    Returns:
        UnifiedLLMClient 或 None（未配置）
    """
    provider = config.get(f"{prefix}_provider", "").strip()
    model = config.get(f"{prefix}_model", "").strip()

    if not provider or not model:
        return None

    api_key = config.get(f"{prefix}_api_key", "").strip()
    base_url = config.get(f"{prefix}_base_url", "").strip()

    return UnifiedLLMClient(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout=30.0
    )
