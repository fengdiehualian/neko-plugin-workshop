"""Plugin Store Search plugin.

Fetches plugin listings from the N.E.K.O plugin store API and exposes
an ``@llm_tool`` so the AI can search and discover available plugins.
"""

from __future__ import annotations

from typing import Any, Dict, List

import httpx
from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
)

_SEARCH_PLUGINS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "keyword": {
            "type": "string",
            "description": (
                "搜索关键词，可匹配插件名称、描述、标签、作者名。"
                "留空则返回所有插件。"
            ),
        },
        "sort_by": {
            "type": "string",
            "description": "排序方式：downloads(下载量) / likes(点赞) / rating(评分) / created_at(最新) / name(名称)。默认 downloads。",
            "enum": ["downloads", "likes", "rating", "created_at", "name"],
        },
        "limit": {
            "type": "integer",
            "description": "返回结果数量上限，默认 10。",
        },
    },
    "required": [],
}

_SEARCH_PLUGINS_DESCRIPTION = (
    "搜索 N.E.K.O 插件商店中的可用插件。"
    "可以通过关键词（插件名称、标签、作者、描述）来搜索，也可以不带关键词获取全部插件列表。"
    "返回插件的名称、描述、作者、标签、下载量、评分、仓库地址等信息。"
    "当用户想要查找、安装、了解某个插件时使用此工具。"
)


def _match_keyword(plugin: Dict[str, Any], keyword: str) -> bool:
    """Check if a plugin matches the given keyword."""
    kw = keyword.lower()
    fields = [
        plugin.get("name", ""),
        plugin.get("slug", ""),
        plugin.get("short_description") or "",
        plugin.get("author_name", ""),
        " ".join(plugin.get("tags") or []),
        " ".join(plugin.get("categories") or []),
    ]
    return any(kw in field.lower() for field in fields)


def _sort_key(sort_by: str):
    """Return a sort key function for plugin dicts."""
    if sort_by == "downloads":
        return lambda p: p.get("download_count") or 0
    elif sort_by == "likes":
        return lambda p: p.get("likes") or 0
    elif sort_by == "rating":
        return lambda p: p.get("rating_average") or 0.0
    elif sort_by == "name":
        return lambda p: (p.get("name") or "").lower()
    else:
        return lambda p: p.get("created_at") or ""


def _format_plugin(p: Dict[str, Any]) -> str:
    """Format a single plugin into a readable summary line."""
    parts = [f"【{p.get('name', '?')}】"]
    if p.get("short_description"):
        parts.append(p["short_description"])
    parts.append(f"作者: {p.get('author_name', '未知')}")
    if p.get("tags"):
        parts.append(f"标签: {', '.join(p['tags'])}")
    parts.append(f"下载: {p.get('download_count', 0)} | 点赞: {p.get('likes', 0)}")
    if p.get("rating_average") and p["rating_average"] > 0:
        parts.append(f"评分: {p['rating_average']:.1f} ({p.get('rating_count', 0)} 人评价)")
    if p.get("latest_version"):
        parts.append(f"版本: {p['latest_version']}")
    if p.get("repo_url"):
        parts.append(f"仓库: {p['repo_url']}")
    return " | ".join(parts)


def _build_summary(
    keyword: str,
    plugins: List[Dict[str, Any]],
    total_available: int,
) -> str:
    """Build a human-readable summary for the LLM."""
    if not plugins:
        if keyword:
            return f"在插件商店中未找到与 \"{keyword}\" 匹配的插件。共 {total_available} 个插件可供搜索。"
        return "插件商店当前没有可用插件。"

    lines: list[str] = []
    if keyword:
        lines.append(f"搜索 \"{keyword}\" 找到 {len(plugins)} 个插件（商店共 {total_available} 个）：")
    else:
        lines.append(f"插件商店共 {total_available} 个插件，显示前 {len(plugins)} 个：")
    lines.append("")
    for i, p in enumerate(plugins, 1):
        lines.append(f"{i}. {_format_plugin(p)}")
    return "\n".join(lines)


@neko_plugin
class PluginStoreSearchPlugin(NekoPluginBase):

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._cfg: Dict[str, Any] = {}

    async def _load_config(self) -> None:
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        self._cfg = cfg.get("store") if isinstance(cfg.get("store"), dict) else {}

    @lifecycle(id="startup")
    async def startup(self, **_):
        await self._load_config()
        self.logger.info("PluginStoreSearch started")
        return Ok({"status": "ready"})

    @lifecycle(id="config_change")
    async def config_change(self, **_):
        await self._load_config()
        self.logger.info("PluginStoreSearch configuration reloaded")
        return Ok({"status": "reloaded"})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        self.logger.info("PluginStoreSearch shutdown")
        return Ok({"status": "shutdown"})

    def _api_url(self) -> str:
        base = self._cfg.get("api_base_url", "http://120.53.24.232/api/v1")
        return f"{base}/plugins"

    def _timeout(self) -> float:
        try:
            return float(self._cfg.get("timeout_seconds", 15))
        except (TypeError, ValueError):
            return 15.0

    def _page_size(self) -> int:
        try:
            return int(self._cfg.get("page_size", 100))
        except (TypeError, ValueError):
            return 100

    async def _fetch_plugins(
        self,
        keyword: str = "",
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> List[Dict[str, Any]]:
        """Fetch plugins from the store API."""
        params: Dict[str, Any] = {
            "page_size": self._page_size(),
            "sort_by": sort_by if sort_by != "downloads" else "download_count",
            "sort_order": sort_order,
        }
        if keyword:
            params["keyword"] = keyword

        timeout = self._timeout()
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(self._api_url(), params=params)
            resp.raise_for_status()
            data = resp.json()

        return data.get("items") or []

    async def _search_and_format(
        self,
        keyword: str = "",
        sort_by: str = "downloads",
        limit: int = 10,
    ) -> Dict[str, Any]:
        """Core search logic shared by llm_tool and plugin_entry.

        Raises on API errors; callers should catch and convert to the
        appropriate return type (``is_error`` dict for LLM tools,
        ``Err()`` for plugin entries).
        """
        all_plugins = await self._fetch_plugins(keyword=keyword)
        total = len(all_plugins)

        if keyword:
            results = [p for p in all_plugins if _match_keyword(p, keyword)]
        else:
            results = all_plugins

        results.sort(key=_sort_key(sort_by), reverse=(sort_by != "name"))
        results = results[:limit]

        summary = _build_summary(keyword, results, total)
        return {
            "keyword": keyword,
            "total_available": total,
            "count": len(results),
            "summary": summary,
            "results": results,
        }

    @llm_tool(
        name="search_plugins",
        description=_SEARCH_PLUGINS_DESCRIPTION,
        parameters=_SEARCH_PLUGINS_SCHEMA,
        timeout=30.0,
    )
    async def search_plugins_tool(
        self,
        *,
        keyword: str = "",
        sort_by: str = "downloads",
        limit: int = 10,
        **_,
    ):
        if limit <= 0:
            limit = 10
        if limit > 50:
            limit = 50
        if sort_by not in ("downloads", "likes", "rating", "created_at", "name"):
            sort_by = "downloads"

        self.logger.info(
            "LLM tool search_plugins: keyword={!r} sort_by={} limit={}",
            keyword, sort_by, limit,
        )
        try:
            return await self._search_and_format(keyword, sort_by, limit)
        except Exception as e:
            return {"is_error": True, "error": str(e)}

    @plugin_entry(
        id="search_plugins",
        name="搜索插件",
        description="从 N.E.K.O 插件商店搜索可用插件。支持按名称、标签、作者等关键词搜索。",
        llm_result_fields=["summary"],
        input_schema=_SEARCH_PLUGINS_SCHEMA,
    )
    async def search_plugins_entry(
        self,
        keyword: str = "",
        sort_by: str = "downloads",
        limit: int = 10,
        **_,
    ):
        if limit <= 0:
            limit = 10
        if limit > 50:
            limit = 50
        if sort_by not in ("downloads", "likes", "rating", "created_at", "name"):
            sort_by = "downloads"

        self.logger.info(
            "Plugin entry search_plugins: keyword={!r} sort_by={} limit={}",
            keyword, sort_by, limit,
        )
        try:
            return Ok(await self._search_and_format(keyword, sort_by, limit))
        except Exception as e:
            self.logger.error("Failed to fetch plugins: {}", e)
            return Err(SdkError(f"获取插件列表失败: {e}"))

    @plugin_entry(
        id="plugin_detail",
        name="插件详情",
        description="获取指定插件的详细信息。",
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "插件名称或 slug",
                },
            },
            "required": ["name"],
        },
    )
    async def plugin_detail(self, name: str = "", **_):
        if not name or not name.strip():
            return Err(SdkError("插件名称不能为空"))

        try:
            all_plugins = await self._fetch_plugins()
        except Exception as e:
            return Err(SdkError(f"获取插件列表失败: {e}"))

        target = name.strip().lower()
        for p in all_plugins:
            if (p.get("name", "").lower() == target
                    or p.get("slug", "").lower() == target):
                lines = [
                    f"插件名称: {p.get('name', '?')}",
                    f"Slug: {p.get('slug', '?')}",
                    f"作者: {p.get('author_name', '未知')}",
                    f"描述: {p.get('short_description') or '无'}",
                    f"标签: {', '.join(p.get('tags') or [])}",
                    f"分类: {', '.join(p.get('categories') or [])}",
                    f"下载量: {p.get('download_count', 0)}",
                    f"点赞数: {p.get('likes', 0)}",
                    f"评分: {p.get('rating_average', 0.0):.1f} ({p.get('rating_count', 0)} 人评价)",
                    f"版本: {p.get('latest_version') or '未发布'}",
                    f"状态: {p.get('status', '?')}",
                    f"仓库: {p.get('repo_url') or '无'}",
                    f"创建时间: {p.get('created_at', '?')}",
                    f"更新时间: {p.get('updated_at', '?')}",
                ]
                summary = "\n".join(lines)
                return Ok({"summary": summary, "plugin": p})

        return Err(SdkError(f"未找到名为 \"{name}\" 的插件"))
