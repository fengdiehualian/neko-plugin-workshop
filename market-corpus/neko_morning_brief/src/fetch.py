"""Raw fetch capability exposed to the model as a tool.

The plugin decides *when*; the model decides *what* to fetch and how to report
it. This module only turns one URL into compact, model-friendly text.
"""

from __future__ import annotations

import json

from .models import BriefItem, SourceConfig
from .sources.auto import _find_array, build_auto_items, sniff
from .sources.http import FetchError, HttpClient, decode_bytes
from .sources.weather import build_item as build_weather_item

MAX_ITEMS = 5
MAX_JSON_CHARS = 2500


def _items_payload(url: str, kind: str, items: list[BriefItem]) -> dict:
    return {
        "ok": True,
        "url": url,
        "kind": kind,
        "items": [
            {"title": item.title, "summary": item.summary, "url": item.url}
            for item in items
        ],
    }


def render_result(result: dict) -> str:
    """Render one interpreted result as plain text for the model.

    Tool results that are nested JSON are easy for weaker models to
    misread ("ok: true" and nothing else). A flat text block always
    survives the trip and is directly quotable.
    """

    kind = result.get("kind")
    if kind in ("weather", "items"):
        lines: list[str] = []
        for item in result.get("items") or []:
            title = str(item.get("title") or "").strip()
            summary = str(item.get("summary") or "").strip()
            url = item.get("url")
            line = title
            if summary:
                line = f"{line}：{summary}" if line else summary
            if url:
                line = f"{line}（{url}）" if line else str(url)
            if line:
                lines.append(f"- {line}")
        return "\n".join(lines) if lines else "没有解析到内容"

    text = str(result.get("text") or "")
    note = result.get("note")
    if note:
        text = f"[{note}] {text}"
    return text or "没有解析到内容"


def interpret_text(
    text: str,
    *,
    url: str = "",
    label: str = "",
    max_items: int = MAX_ITEMS,
) -> dict:
    """Turn one response body into the compact dict handed to the model."""

    cfg = SourceConfig(
        id="fetch",
        type="auto",
        label=label,
        url=url,
        max_items=max(1, min(int(max_items or MAX_ITEMS), 10)),
    )
    kind = sniff(text)

    if kind == "json":
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise FetchError("PARSE_ERROR", "invalid JSON") from exc

        daily = payload.get("daily") if isinstance(payload, dict) else None

        if isinstance(payload, dict) and payload.get("error"):
            # APIs like Open-Meteo answer 200 with {"error": true, "reason": ...}.
            raise FetchError("API_ERROR", str(payload.get("reason") or "remote api error"))

        if isinstance(daily, dict) and "weather_code" in daily:
            # Open-Meteo style forecast: turn codes into readable weather.
            try:
                items = build_weather_item(cfg, place=label, payload=payload)
            except FetchError:
                items = []
            if items:
                return _items_payload(url, "weather", items)

        if _find_array(payload) is not None:
            # Feed-like list: title/summary/url mapping works for these.
            items = build_auto_items(text, cfg)
            if items:
                return _items_payload(url, "items", items)

        # Everything else (AMap lives/status payloads, single values, exotic
        # structures) goes to the model as raw JSON. Flattening it here is the
        # fastest way to lose the only field the user actually asked for.
        snippet = json.dumps(payload, ensure_ascii=False)
        return {
            "ok": True,
            "url": url,
            "kind": "json",
            "text": snippet[:MAX_JSON_CHARS],
        }

    return _items_payload(url, "items", build_auto_items(text, cfg))


async def fetch_content(
    http: HttpClient,
    url: str,
    *,
    cookie: str = "",
    max_items: int = MAX_ITEMS,
    label: str = "",
    timeout: float = 15.0,
) -> dict:
    """Fetch ``url`` and return ``{kind, items|text}`` for the model."""

    headers = {"Cookie": cookie} if cookie else None
    _, raw, response_headers = await http.request(url, headers=headers, timeout=timeout)
    text = decode_bytes(raw, response_headers)

    try:
        return interpret_text(text, url=url, label=label, max_items=max_items)
    except FetchError as exc:
        # Not a feed/JSON/HTML we understand: hand back a trimmed snippet.
        snippet = " ".join(text.split())[:1200]
        if not snippet:
            raise
        return {"ok": True, "kind": "text", "url": url, "text": snippet, "note": exc.code}


def require_url(url: str) -> str:
    """Small guard so the model cannot ask us to read local files."""

    value = (url or "").strip()
    if not value.lower().startswith(("http://", "https://")):
        raise FetchError("CONFIG_INVALID", "url must be http(s)")
    return value


__all__ = ["fetch_content", "interpret_text", "render_result", "require_url"]
