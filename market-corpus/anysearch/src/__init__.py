"""AnySearch 联网搜索 v0.1

在 N.E.K.O 里接入 AnySearch（api.anysearch.com）统一搜索 API，让猫娘在聊天里直接联网搜索，
并在面板里可视化检索。支持匿名模式（无需 Key，按 IP 每日免费额度）与 API Key 模式。
纯标准库 urllib 实现，零第三方依赖。
"""

from __future__ import annotations

import time
from typing import Any, Optional

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
)
from plugin.sdk.plugin.ui import action as ui_action
from plugin.sdk.plugin.ui import context as ui_context

from .anysearch_core import AnySearchClient, AnySearchError, format_results

_PLUGIN_ID = "anysearch"

_DEFAULTS: dict[str, Any] = {
    "api_key": "",
    "default_zone": "cn",
    "default_language": "zh-CN",
    "default_max_results": 5,
    "search_timeout": 15,
    "max_recent": 20,
}


def _read_cfg(ctx: Any, key: str, default: Any = None) -> Any:
    """安全读取 plugin.toml 的 [anysearch] 配置段字段。"""
    try:
        section = getattr(ctx.config, _PLUGIN_ID, None)
        if section is not None:
            val = getattr(section, key, None)
            if val is not None:
                return val
    except Exception:
        pass
    return default


@neko_plugin
class AnySearchPlugin(NekoPluginBase):
    """AnySearch 联网搜索 - N.E.K.O 插件入口"""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self.client = self._build_client()
        has_key = bool(self._cfg("api_key"))
        self._state: dict[str, Any] = {
            "configured": has_key,
            "mode": "api_key" if has_key else "anonymous",
            "zone": str(self._cfg("default_zone", "cn")),
            "language": str(self._cfg("default_language", "zh-CN")),
            "max_results": int(self._cfg("default_max_results", 5)),
            "recent": [],          # [{ts, query, count}]
            "last_error": "",
            "last_query": "",
            "last_results": [],    # 给面板渲染
            "last_search_ms": 0,
        }

    # ── 配置 ───────────────────────────────────────────────────
    def _cfg(self, key: str, default: Any = None) -> Any:
        if default is None:
            default = _DEFAULTS.get(key)
        return _read_cfg(self.ctx, key, default)

    def _build_client(self) -> AnySearchClient:
        key = self._cfg("api_key") or None
        return AnySearchClient(
            api_key=str(key) if key else None,
            default_zone=str(self._cfg("default_zone", "cn")),
            default_language=str(self._cfg("default_language", "zh-CN")),
            default_max_results=int(self._cfg("default_max_results", 5)),
            timeout=int(self._cfg("search_timeout", 15)),
        )

    # ── 生命周期 ───────────────────────────────────────────────
    @lifecycle(id="startup")
    async def on_startup(self) -> None:
        self.logger.info("[anysearch] 启动；模式=%s", self._state["mode"])

    @lifecycle(id="shutdown")
    async def on_shutdown(self) -> None:
        self.logger.info("[anysearch] 关闭")

    # ── 内部搜索 ──────────────────────────────────────────────
    def _do_search(
        self,
        query: str,
        max_results: Optional[int] = None,
        zone: Optional[str] = None,
        language: Optional[str] = None,
        content_types: Optional[list[str]] = None,
        domains: Optional[list[str]] = None,
    ) -> Any:
        resp = self.client.search(
            query=query,
            max_results=max_results if max_results is not None else self._state["max_results"],
            zone=zone,
            language=language,
            content_types=content_types,
            domains=domains,
        )
        self._state["last_query"] = query
        self._state["last_results"] = resp.to_list()
        self._state["last_search_ms"] = resp.search_time_ms
        self._state["last_error"] = ""
        self._push_recent(query, resp.total_results)
        return resp

    def _push_recent(self, query: str, count: int) -> None:
        self._state.setdefault("recent", []).append(
            {"ts": int(time.time()), "query": query, "count": count}
        )
        max_recent = int(self._cfg("max_recent", 20))
        if len(self._state["recent"]) > max_recent:
            self._state["recent"] = self._state["recent"][-max_recent:]

    def _run(
        self,
        query: str,
        max_results: Optional[int],
        zone: Optional[str],
        language: Optional[str],
        content_types: Optional[list[str]],
        domains: Optional[list[str]],
    ):
        try:
            resp = self._do_search(query, max_results, zone, language, content_types, domains)
            return Ok(format_results(resp, query))
        except AnySearchError as e:
            self._state["last_error"] = e.message
            return Err(SdkError(f"搜索失败：{e.message}"))
        except Exception as exc:  # noqa: BLE001
            self._state["last_error"] = str(exc)
            return Err(SdkError(f"搜索异常：{exc}"))

    # ── 面板状态 ───────────────────────────────────────────────
    @ui_context(id="main")
    async def get_ui_context(self) -> dict[str, Any]:
        return dict(self._state)

    # ── 动作：搜索（面板 + 聊天共用）──────────────────────────
    @ui_action(
        id="search",
        label="搜索",
        icon="🔍",
        group="search",
        order=10,
        refresh_context=True,
    )
    @plugin_entry(
        id="search",
        name="AnySearch 联网搜索",
        description="使用 AnySearch 统一搜索 API 进行联网搜索，返回结构化结果。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "max_results": {"type": "integer", "description": "返回结果数量（1-100），默认 5"},
                "zone": {"type": "string", "description": "区域：cn（国内）/ intl（国际）", "enum": ["cn", "intl"]},
                "language": {"type": "string", "description": "偏好语言，如 zh-CN / en"},
                "content_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "内容类型过滤：web / news / doc / code / academic",
                },
                "domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "领域过滤，如 tech / academic",
                },
            },
            "required": ["query"],
        },
    )
    async def search(self, **kwargs):
        query = str(kwargs.get("query") or "").strip()
        if not query:
            return Err(SdkError("查询词不能为空"))
        return self._run(
            query,
            kwargs.get("max_results"),
            kwargs.get("zone") or None,
            kwargs.get("language") or None,
            kwargs.get("content_types") or None,
            kwargs.get("domains") or None,
        )

    # ── 入口：猫娘在聊天里直接搜 ─────────────────────────────
    @plugin_entry(
        id="anysearch_search",
        name="联网搜索",
        description="让猫娘直接在对话里用 AnySearch 联网搜索并汇总结果。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "max_results": {"type": "integer", "description": "返回结果数量，默认 5"},
                "zone": {"type": "string", "description": "区域：cn / intl", "enum": ["cn", "intl"]},
            },
            "required": ["query"],
        },
    )
    async def anysearch_search(self, **kwargs):
        query = str(kwargs.get("query") or "").strip()
        if not query:
            return Err(SdkError("查询词不能为空"))
        return self._run(
            query,
            kwargs.get("max_results"),
            kwargs.get("zone") or None,
            None,
            None,
            None,
        )
