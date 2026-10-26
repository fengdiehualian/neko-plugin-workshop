"""``auto`` - paste a URL, the plugin figures out what it is.

This is the default source type: no paths, no templates, no field mapping.
Detection order: JSON -> feed XML -> HTML -> plain text. Anything that yields a
single value (price, status, page title) is marked as a snapshot, so it is not
swallowed by the duplicate window on the next run.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from ..models import BriefItem, SourceConfig
from ..normalize import clean_text, make_item, strip_html
from .base import BaseSource, source_timeout
from .feedparse import parse_feed
from .http import FetchError, decode_bytes

TITLE_KEYS = ("title", "name", "headline", "subject", "text", "label", "question")
SUMMARY_KEYS = (
    "summary",
    "description",
    "desc",
    "excerpt",
    "content",
    "abstract",
    "digest",
    "body",
)
URL_KEYS = ("url", "link", "href", "permalink", "web_url", "html_url", "webUrl")
TIME_KEYS = (
    "published_at",
    "published",
    "date",
    "created_at",
    "updated_at",
    "updated",
    "time",
    "timestamp",
    "pubDate",
    "datetime",
)

_TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_H1_TAG = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_META_DESC = re.compile(
    r"""<meta[^>]+(?:name|property)=["'](?:description|og:description)["'][^>]+content=["']([^"']+)["']""",
    re.IGNORECASE,
)


def sniff(text: str) -> str:
    head = text.lstrip()[:400].lower()
    if not head:
        return "empty"
    if head.startswith(("{", "[")):
        return "json"
    if head.startswith("<?xml") or re.match(r"<(rss|feed|rdf)", head):
        return "xml"
    if head.startswith("<!doctype html") or "<html" in head or "<body" in head:
        return "html"
    return "text"


# -- JSON -----------------------------------------------------------------


def _pick(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    lowered = {str(k).lower(): v for k, v in row.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value not in (None, ""):
            return value
    return None


def _find_array(payload: Any, depth: int = 0) -> list[Any] | None:
    """Pick the most feed-like array in the payload."""

    if depth > 4:
        return None
    best: list[Any] | None = None
    best_score = 0.0

    if isinstance(payload, list):
        rows = [row for row in payload if isinstance(row, dict)]
        if rows:
            hits = sum(1 for row in rows[:10] if _pick(row, TITLE_KEYS) is not None)
            score = len(rows) + hits * 3
            if score > best_score:
                return payload
        return None

    if isinstance(payload, dict):
        for value in payload.values():
            candidate = _find_array(value, depth + 1)
            if candidate is None:
                continue
            rows = [row for row in candidate if isinstance(row, dict)]
            hits = sum(1 for row in rows[:10] if _pick(row, TITLE_KEYS) is not None)
            score = len(rows) + hits * 3
            if score > best_score:
                best, best_score = candidate, score
    return best


def _flatten(value: Any, prefix: str = "", depth: int = 0) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if depth > 2:
        return pairs
    if isinstance(value, dict):
        for key, item in list(value.items())[:8]:
            name = f"{prefix}.{key}" if prefix else str(key)
            pairs.extend(_flatten(item, name, depth + 1))
            if len(pairs) >= 6:
                break
    elif isinstance(value, list):
        if value and all(not isinstance(item, (dict, list)) for item in value[:5]):
            pairs.append((prefix, "、".join(str(item) for item in value[:5])))
    elif value is not None and value != "":
        pairs.append((prefix, str(value)))
    return pairs


def _scalar_title(cfg: SourceConfig, payload: Any) -> str:
    pairs = _flatten(payload)
    if not pairs:
        raise FetchError("PARSE_ERROR", "no scalar fields in JSON")
    label = cfg.display_label
    rendered = "，".join(f"{name} {value}" for name, value in pairs[:4])
    return f"{label}：{rendered}"


def build_json_items(payload: Any, cfg: SourceConfig) -> list[BriefItem]:
    limit = max(1, int(cfg.max_items or 3))
    rows = _find_array(payload)

    if rows:
        items: list[BriefItem] = []
        for row in rows:
            if len(items) >= limit:
                break
            if not isinstance(row, dict):
                continue
            title = _pick(row, TITLE_KEYS)
            if title is None:
                continue
            item = make_item(
                source_id=cfg.id,
                title=title,
                summary=_pick(row, SUMMARY_KEYS),
                url=_pick(row, URL_KEYS),
                ts=_pick(row, TIME_KEYS),
                weight=cfg.weight,
                tags=tuple(cfg.tags),
            )
            if item is not None:
                items.append(item)
        # Rows exist but none carries a title-like field: return nothing so the
        # caller can hand the raw JSON to the model instead of a status-only
        # summary (e.g. AMap's ``lives`` array).
        return items

    item = make_item(
        source_id=cfg.id,
        title=_scalar_title(cfg, payload),
        weight=cfg.weight,
        tags=tuple(cfg.tags),
        snapshot=True,
    )
    return [item] if item else []


# -- HTML / text ----------------------------------------------------------


def _first(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return strip_html(match.group(1)) if match else ""


def build_html_items(text: str, cfg: SourceConfig) -> list[BriefItem]:
    title = _first(_H1_TAG, text) or _first(_TITLE_TAG, text)
    if not title:
        raise FetchError("PARSE_ERROR", "no title in HTML")
    summary = _first(_META_DESC, text)
    if not summary:
        body = re.search(r"<p[^>]*>(.*?)</p>", text, re.IGNORECASE | re.DOTALL)
        summary = strip_html(body.group(1)) if body else ""
    item = make_item(
        source_id=cfg.id,
        title=title,
        summary=clean_text(summary, 120),
        weight=cfg.weight,
        tags=tuple(cfg.tags),
        snapshot=True,
    )
    return [item] if item else []


def build_text_items(text: str, cfg: SourceConfig) -> list[BriefItem]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise FetchError("PARSE_ERROR", "empty response")
    title = clean_text(lines[0], 80)
    summary = clean_text(" ".join(lines[1:4]), 120)
    item = make_item(
        source_id=cfg.id,
        title=title,
        summary=summary,
        weight=cfg.weight,
        tags=tuple(cfg.tags),
        snapshot=True,
    )
    return [item] if item else []


def build_auto_items(text: str, cfg: SourceConfig) -> list[BriefItem]:
    kind = sniff(text)
    if kind == "json":
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise FetchError("PARSE_ERROR", "invalid JSON") from exc
        return build_json_items(payload, cfg)
    if kind == "xml":
        try:
            entries = parse_feed(text.encode("utf-8"))
        except ValueError as exc:
            raise FetchError("PARSE_ERROR", str(exc)[:120]) from exc
        limit = max(1, int(cfg.max_items or 3))
        items: list[BriefItem] = []
        for entry in entries[:limit]:
            item = make_item(
                source_id=cfg.id,
                title=entry.get("title") or entry.get("link"),
                summary=entry.get("summary"),
                url=entry.get("link"),
                ts=entry.get("published"),
                weight=cfg.weight,
                tags=tuple(cfg.tags),
            )
            if item is not None:
                items.append(item)
        return items
    if kind == "html":
        return build_html_items(text, cfg)
    if kind == "empty":
        raise FetchError("PARSE_ERROR", "empty response")
    return build_text_items(text, cfg)


def host_label(url: str | None) -> str:
    if not url:
        return "自定义源"
    try:
        return urlsplit(url).netloc.lower() or "自定义源"
    except ValueError:
        return "自定义源"


class AutoSource(BaseSource):
    type = "auto"

    async def fetch(self, cfg: SourceConfig) -> list[BriefItem]:
        if not cfg.url:
            raise FetchError("CONFIG_INVALID", "auto source needs url")
        timeout = source_timeout(cfg, 10.0)
        _, raw, headers = await self.http.request(
            cfg.url,
            method=cfg.method or "GET",
            headers=cfg.headers or None,
            body=cfg.body,
            timeout=timeout,
        )
        text = decode_bytes(raw, headers)
        return build_auto_items(text, cfg)


__all__ = [
    "AutoSource",
    "build_auto_items",
    "build_html_items",
    "build_json_items",
    "build_text_items",
    "host_label",
    "sniff",
]
