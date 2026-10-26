"""``rss`` - fetch a feed, parse it, normalize the first N entries."""

from __future__ import annotations

import asyncio

from ..models import BriefItem, SourceConfig
from ..normalize import make_item
from .base import BaseSource, source_timeout
from .feedparse import parse_feed
from .http import FetchError


class RssSource(BaseSource):
    type = "rss"

    async def fetch(self, cfg: SourceConfig) -> list[BriefItem]:
        if not cfg.url:
            raise FetchError("CONFIG_INVALID", "rss source needs url")
        timeout = source_timeout(cfg, 6.0)
        payload = await self.http.get_bytes(cfg.url, headers=cfg.headers or None, timeout=timeout)
        try:
            entries = await asyncio.to_thread(parse_feed, payload)
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


__all__ = ["RssSource"]
