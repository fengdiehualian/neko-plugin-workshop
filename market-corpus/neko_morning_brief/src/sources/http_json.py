"""``http.json`` - the escape hatch: any JSON API, no code required.

Two modes:

``list``
    ``items_path`` locates an array; ``map`` projects each row into
    title/summary/url/ts through the path resolver.

``single``
    The whole payload feeds a text template, producing exactly one item.
    This is how price/status sources work without writing an adapter.
"""

from __future__ import annotations

import json
from typing import Any

from ..models import BriefItem, SourceConfig
from ..normalize import make_item
from ..paths import MISSING, resolve, resolve_str
from ..template import TemplateError, render
from .base import BaseSource, source_timeout
from .http import FetchError


def extract_rows(payload: Any, cfg: SourceConfig) -> list[Any]:
    path = cfg.items_path or ""
    if not path:
        raise FetchError("CONFIG_INVALID", "list mode needs items_path")
    rows = resolve(payload, path)
    if rows is MISSING or not isinstance(rows, list):
        raise FetchError("PARSE_ERROR", f"items_path not an array: {path}")
    return rows


def build_list_items(payload: Any, cfg: SourceConfig) -> list[BriefItem]:
    rows = extract_rows(payload, cfg)
    title_path = cfg.map.get("title") or "$.title"
    summary_path = cfg.map.get("summary")
    url_path = cfg.map.get("url")
    ts_path = cfg.map.get("ts")

    limit = max(1, int(cfg.max_items or 3))
    items: list[BriefItem] = []
    for row in rows:
        if len(items) >= limit:
            break
        if row is None:
            continue
        title = resolve_str(row, title_path)
        if not title:
            continue
        summary = resolve_str(row, summary_path) if summary_path else ""
        url = resolve_str(row, url_path) if url_path else ""
        ts_value = resolve(row, ts_path) if ts_path else None
        item = make_item(
            source_id=cfg.id,
            title=title,
            summary=summary,
            url=url or None,
            ts=None if ts_value is MISSING else ts_value,
            weight=cfg.weight,
            tags=tuple(cfg.tags),
        )
        if item is not None:
            items.append(item)
    return items


def build_single_item(payload: Any, cfg: SourceConfig) -> list[BriefItem]:
    if not cfg.text:
        raise FetchError("CONFIG_INVALID", "single mode needs text template")
    try:
        line = render(cfg.text, payload)
    except TemplateError as exc:
        raise FetchError("PARSE_ERROR", str(exc)[:120]) from exc
    item = make_item(
        source_id=cfg.id,
        title=line,
        summary="",
        weight=cfg.weight,
        tags=tuple(cfg.tags),
        snapshot=True,
    )
    return [item] if item else []


def build_items(payload: Any, cfg: SourceConfig) -> list[BriefItem]:
    mode = (cfg.mode or "single").lower()
    if mode == "list":
        return build_list_items(payload, cfg)
    if mode == "single":
        return build_single_item(payload, cfg)
    raise FetchError("CONFIG_INVALID", f"unknown mode: {cfg.mode}")


class HttpJsonSource(BaseSource):
    type = "http.json"

    async def fetch(self, cfg: SourceConfig) -> list[BriefItem]:
        if not cfg.url:
            raise FetchError("CONFIG_INVALID", "http.json source needs url")
        timeout = source_timeout(cfg, 6.0)
        _, raw, _ = await self.http.request(
            cfg.url,
            method=cfg.method or "GET",
            headers=cfg.headers or None,
            body=cfg.body,
            timeout=timeout,
        )
        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError as exc:
            raise FetchError("PARSE_ERROR", "response is not valid JSON") from exc
        return build_items(data, cfg)


__all__ = [
    "HttpJsonSource",
    "build_items",
    "build_list_items",
    "build_single_item",
    "extract_rows",
]
