"""AnySearch 搜索核心（纯标准库，零第三方依赖）

对接 AnySearch 统一搜索 API（https://api.anysearch.com/v1/search）。
支持匿名模式（无需 Key，按客户端 IP 每日免费额度）与 API Key 模式。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

API_BASE = "https://api.anysearch.com/v1/search"

# 常见内容类型与区域
CONTENT_TYPES = ["web", "news", "doc", "code", "academic"]
ZONES = ["cn", "intl"]


class AnySearchError(Exception):
    """搜索过程中的错误，携带状态码与 request_id 便于排查。"""

    def __init__(self, message: str, status: Optional[int] = None, request_id: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.request_id = request_id


@dataclass
class SearchResult:
    title: str
    url: str
    description: str = ""
    content: str = ""
    source: str = ""
    score: float = 0.0
    quality_score: float = 0.0
    published_at: Optional[str] = None

    def to_card(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "description": self.description,
            "content": self.content,
            "source": self.source,
            "score": self.score,
            "quality_score": self.quality_score,
            "published_at": self.published_at,
        }


@dataclass
class SearchResponse:
    results: list[SearchResult]
    total_results: int = 0
    search_time_ms: int = 0
    request_id: str = ""
    cached: bool = False

    def to_list(self) -> list[dict[str, Any]]:
        return [r.to_card() for r in self.results]


def _coerce_float(v: Any) -> float:
    try:
        return float(v)
    except Exception:
        return 0.0


def parse_payload(raw: dict[str, Any]) -> SearchResponse:
    """解析 AnySearch /v1/search 的 JSON 响应。可在无网络下做单元测试。"""
    results: list[SearchResult] = []
    for item in raw.get("results", []) or []:
        if not isinstance(item, dict):
            continue
        results.append(
            SearchResult(
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
                description=str(item.get("description") or ""),
                content=str(item.get("content") or ""),
                source=str(item.get("source") or ""),
                score=_coerce_float(item.get("score")),
                quality_score=_coerce_float(item.get("quality_score")),
                published_at=item.get("published_at"),
            )
        )
    meta = raw.get("metadata", {}) or {}
    total = meta.get("total_results")
    if total is None:
        total = len(results)
    return SearchResponse(
        results=results,
        total_results=int(total or 0),
        search_time_ms=int(meta.get("search_time_ms", 0) or 0),
        request_id=str(meta.get("request_id") or ""),
        cached=bool(meta.get("cached", False)),
    )


class AnySearchClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        default_zone: str = "cn",
        default_language: str = "zh-CN",
        default_max_results: int = 5,
        timeout: int = 15,
    ):
        self.api_key = api_key
        self.default_zone = default_zone
        self.default_language = default_language
        self.default_max_results = default_max_results
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def search(
        self,
        query: str,
        max_results: Optional[int] = None,
        zone: Optional[str] = None,
        language: Optional[str] = None,
        content_types: Optional[list[str]] = None,
        domains: Optional[list[str]] = None,
    ) -> SearchResponse:
        if not query or not str(query).strip():
            raise AnySearchError("查询词不能为空", status=400)

        body: dict[str, Any] = {"query": str(query).strip()}
        body["max_results"] = int(max_results or self.default_max_results)
        if zone:
            body["zone"] = zone
        elif self.default_zone:
            body["zone"] = self.default_zone
        if language:
            body["language"] = language
        elif self.default_language:
            body["language"] = self.default_language
        if content_types:
            body["content_types"] = list(content_types)
        if domains:
            body["domains"] = list(domains)

        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(API_BASE, data=data, method="POST", headers=self._headers())

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body_text = ""
            try:
                body_text = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            msg = ""
            rid = ""
            try:
                obj = json.loads(body_text) if body_text else {}
                msg = obj.get("message", "") or e.reason or ""
                rid = obj.get("request_id", "")
            except Exception:
                msg = e.reason or ""
            raise AnySearchError(f"搜索失败（{e.code}）：{msg}", status=e.code, request_id=rid)
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            raise AnySearchError(f"网络错误：{reason}")
        return parse_payload(raw)


def format_results(resp: SearchResponse, query: str, max_show: int = 5) -> str:
    """把搜索结果格式化成猫娘聊天可用的文本。"""
    if not resp.results:
        return f"🔍 没有找到关于「{query}」的结果。"
    lines = [f"🔍 关于「{query}」找到 {resp.total_results} 条结果（用时 {resp.search_time_ms}ms）：", ""]
    for i, r in enumerate(resp.results[:max_show], 1):
        lines.append(f"{i}. {r.title}")
        lines.append(f"   {r.url}")
        if r.description:
            desc = r.description.strip()
            if len(desc) > 220:
                desc = desc[:220] + "…"
            lines.append(f"   {desc}")
        if r.source:
            extra = f"来源: {r.source}"
            if r.quality_score:
                extra += f" · 质量分 {r.quality_score:.2f}"
            lines.append(f"   {extra}")
        lines.append("")
    return "\n".join(lines).strip()
