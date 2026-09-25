from __future__ import annotations

import hashlib
import time
from typing import Any, Iterable, Mapping


def _candidate(
    source: str, title: str, snippet: str, url: str, query: str
) -> dict[str, Any]:
    identity = url or f"{source}:{title}"
    return {
        "id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
        "source": source,
        "title": title.strip()[:300],
        "snippet": snippet.strip()[:1200],
        "url": url.strip()[:1000],
        "query": query[:120],
        "discovered_at": time.time(),
    }


def normalize_web_results(
    payload: Mapping[str, Any], query: str
) -> list[dict[str, Any]]:
    rows = payload.get("results")
    if not isinstance(rows, list):
        return []
    output = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or row.get("href") or "").strip()
        if title and url:
            output.append(
                _candidate(
                    "web_search",
                    title,
                    str(row.get("snippet") or row.get("body") or ""),
                    url,
                    query,
                )
            )
    return output


def normalize_bilibili_results(
    payload: Mapping[str, Any], query: str
) -> list[dict[str, Any]]:
    wrapped = payload.get("result")
    root = wrapped if isinstance(wrapped, Mapping) else payload
    rows = root.get("videos")
    if not isinstance(rows, list):
        return []
    output = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        bvid = str(row.get("bvid") or "").strip()
        title = str(row.get("title") or "").strip()
        if bvid and title:
            output.append(
                _candidate(
                    "bilibili",
                    title,
                    str(row.get("description") or row.get("desc") or ""),
                    f"https://www.bilibili.com/video/{bvid}",
                    query,
                )
            )
    return output


async def discover_from_plugins(
    plugins: Any,
    queries: Iterable[str],
    *,
    web_search: bool,
    bilibili: bool,
) -> list[dict[str, Any]]:
    from plugin.sdk.plugin import unwrap_or

    candidates: list[dict[str, Any]] = []
    for query in list(dict.fromkeys(q.strip() for q in queries if q.strip()))[:2]:
        if web_search:
            payload = unwrap_or(
                await plugins.call_entry(
                    "web_search:search",
                    {"query": query, "max_results": 6},
                    timeout=20.0,
                ),
                {},
            )
            if isinstance(payload, Mapping):
                candidates.extend(normalize_web_results(payload, query))
        if bilibili:
            payload = unwrap_or(
                await plugins.call_entry(
                    "bilibili_danmaku:bili_search",
                    {"keyword": query, "num": 6, "order": "pubdate"},
                    timeout=20.0,
                ),
                {},
            )
            if isinstance(payload, Mapping):
                candidates.extend(normalize_bilibili_results(payload, query))
    by_id = {str(item["id"]): item for item in candidates}
    return list(by_id.values())
