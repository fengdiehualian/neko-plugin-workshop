"""Tavily search and web-page extraction plugin."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import httpx
from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
)

_SEARCH_URL = "https://api.tavily.com/search"
_EXTRACT_URL = "https://api.tavily.com/extract"
_DEFAULT_MAX_RESULTS = 5
_DEFAULT_TIMEOUT_SECONDS = 30.0


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    """Coerce a configuration value without letting invalid values reach Tavily."""
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def _bounded_float(value: object, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


@neko_plugin
class TavilySearchPlugin(NekoPluginBase):
    """Provide Tavily Search and Extract as agent-callable plugin entries."""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self._api_key = ""
        self._max_results = _DEFAULT_MAX_RESULTS
        self._timeout_seconds = _DEFAULT_TIMEOUT_SECONDS

    async def _load_config(self) -> None:
        config = await self.config.dump(timeout=5.0)
        tavily = config.get("tavily", {}) if isinstance(config, Mapping) else {}
        tavily = tavily if isinstance(tavily, Mapping) else {}

        api_key = tavily.get("api_key", "")
        self._api_key = api_key.strip() if isinstance(api_key, str) else ""
        self._max_results = _bounded_int(
            tavily.get("max_results"),
            default=_DEFAULT_MAX_RESULTS,
            minimum=1,
            maximum=20,
        )
        self._timeout_seconds = _bounded_float(
            tavily.get("timeout_seconds"),
            default=_DEFAULT_TIMEOUT_SECONDS,
            minimum=1.0,
            maximum=120.0,
        )

    @lifecycle(id="startup")
    async def startup(self, **_: object):
        await self._load_config()
        if not self._api_key:
            self.logger.warning("Tavily API key is not configured")
            return Ok({"status": "degraded", "api_key_configured": False})
        self.logger.info("Tavily Search started (max_results={})", self._max_results)
        return Ok({"status": "ready", "api_key_configured": True})

    @lifecycle(id="config_change")
    async def config_change(self, **_: object):
        await self._load_config()
        return Ok({"api_key_configured": bool(self._api_key)})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_: object):
        self._api_key = ""
        return Ok({"status": "stopped"})

    async def _post(self, url: str, payload: dict[str, object]) -> dict[str, Any] | Err:
        if not self._api_key:
            return Err(SdkError("Tavily API Key 未配置，请在插件配置中设置 tavily.api_key"))

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException:
            self.logger.warning("Tavily request timed out")
            return Err(SdkError("Tavily 请求超时，请稍后重试"))
        except httpx.HTTPStatusError as error:
            self.logger.warning("Tavily request failed with status {}", error.response.status_code)
            if error.response.status_code == 401:
                return Err(SdkError("Tavily API Key 无效或已失效"))
            if error.response.status_code == 429:
                return Err(SdkError("Tavily 请求过于频繁，请稍后重试"))
            return Err(SdkError(f"Tavily 请求失败（HTTP {error.response.status_code}）"))
        except httpx.HTTPError as error:
            self.logger.warning("Tavily network request failed: {}", type(error).__name__)
            return Err(SdkError("无法连接到 Tavily 服务"))
        except ValueError:
            self.logger.warning("Tavily returned a non-JSON response")
            return Err(SdkError("Tavily 返回了无法解析的响应"))

        if not isinstance(data, dict):
            return Err(SdkError("Tavily 返回了无效响应"))
        return data

    @plugin_entry(
        id="search",
        name="Tavily 搜索",
        description=(
            "使用 Tavily 搜索互联网。适用于需要最新网页资料、新闻或来源链接的问题；"
            "query 应保留用户原始语言。"
        ),
        llm_result_fields=["answer", "results"],
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词，保留用户原始语言"},
                "topic": {
                    "type": "string",
                    "enum": ["general", "news"],
                    "default": "general",
                    "description": "general 用于一般检索，news 用于实时新闻",
                },
                "search_depth": {
                    "type": "string",
                    "enum": ["basic", "advanced"],
                    "default": "basic",
                    "description": "advanced 相关性更高，但延迟和消耗也更高",
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "返回数量；省略时使用插件配置中的值",
                },
            },
            "required": ["query"],
        },
    )
    async def search(
        self,
        query: str,
        topic: Literal["general", "news"] = "general",
        search_depth: Literal["basic", "advanced"] = "basic",
        max_results: int | None = None,
        **_: object,
    ):
        if not isinstance(query, str) or not query.strip():
            return Err(SdkError("搜索关键词不能为空"))
        if topic not in {"general", "news"}:
            return Err(SdkError("topic 必须是 general 或 news"))
        if search_depth not in {"basic", "advanced"}:
            return Err(SdkError("search_depth 必须是 basic 或 advanced"))

        result_count = _bounded_int(
            max_results,
            default=self._max_results,
            minimum=1,
            maximum=20,
        )
        self.logger.info("Tavily search (query_len={}, topic={}, max_results={})", len(query), topic, result_count)
        response = await self._post(
            _SEARCH_URL,
            {
                "query": query.strip(),
                "topic": topic,
                "search_depth": search_depth,
                "max_results": result_count,
                "include_answer": True,
                "include_raw_content": False,
                "include_images": False,
            },
        )
        if isinstance(response, Err):
            return response

        results = response.get("results", [])
        return Ok({
            "query": response.get("query", query.strip()),
            "answer": response.get("answer"),
            "results": results if isinstance(results, list) else [],
            "response_time": response.get("response_time"),
        })

    @plugin_entry(
        id="extract_webpage",
        name="提取网页内容",
        description="使用 Tavily Extract 提取指定网页的 Markdown 内容。仅在用户明确需要读取给定 URL 的正文时调用。",
        llm_result_fields=["results"],
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "format": "uri", "description": "要提取内容的网页 URL"},
                "query": {"type": "string", "description": "可选：用于按用户意图重排内容片段"},
                "extract_depth": {
                    "type": "string",
                    "enum": ["basic", "advanced"],
                    "default": "basic",
                    "description": "advanced 适合复杂、JavaScript 较多的页面",
                },
            },
            "required": ["url"],
        },
    )
    async def extract_webpage(
        self,
        url: str,
        query: str | None = None,
        extract_depth: Literal["basic", "advanced"] = "basic",
        **_: object,
    ):
        if not isinstance(url, str) or not url.strip():
            return Err(SdkError("网页 URL 不能为空"))
        if extract_depth not in {"basic", "advanced"}:
            return Err(SdkError("extract_depth 必须是 basic 或 advanced"))

        payload: dict[str, object] = {
            "urls": url.strip(),
            "extract_depth": extract_depth,
            "format": "markdown",
            "include_images": False,
        }
        if isinstance(query, str) and query.strip():
            payload["query"] = query.strip()

        self.logger.info("Tavily extract (url_len={}, depth={})", len(url), extract_depth)
        response = await self._post(_EXTRACT_URL, payload)
        if isinstance(response, Err):
            return response

        results = response.get("results", [])
        return Ok({
            "results": results if isinstance(results, list) else [],
            "failed_results": response.get("failed_results", []),
            "response_time": response.get("response_time"),
        })
