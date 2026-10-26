"""Collection pipeline: concurrency, per-source timeout, cache fallback, ranking.

Every failure mode is contained to a single source. The rest of the run always
produces a report list, because "the brief must happen" is a product rule.
"""

from __future__ import annotations

import asyncio
import time
from typing import Iterable, Sequence

from .models import BriefItem, SourceConfig, SourceReport
from .sources.base import BaseSource
from .sources.http import FetchError
from .store import Store

RECENCY_BUCKETS = (
    (3 * 3600, 1.0),
    (12 * 3600, 0.8),
    (36 * 3600, 0.5),
)
UNKNOWN_TS_FACTOR = 0.5


def recency_factor(ts: float | None, now: float) -> float:
    if not ts:
        return UNKNOWN_TS_FACTOR
    age = max(0.0, now - ts)
    for limit, factor in RECENCY_BUCKETS:
        if age < limit:
            return factor
    return 0.2


def score_item(item: BriefItem, now: float) -> float:
    return float(item.weight) * recency_factor(item.ts, now)


def rank_items(items: Iterable[BriefItem], now: float | None = None) -> list[BriefItem]:
    """Sort by weight x freshness, stable on ties (source order preserved)."""

    current = time.time() if now is None else now
    decorated = list(enumerate(items))
    decorated.sort(key=lambda pair: (-score_item(pair[1], current), pair[0]))
    return [item for _, item in decorated]


def select_spoken(
    reports: Sequence[SourceReport],
    *,
    max_items: int = 5,
    per_source: int = 2,
    now: float | None = None,
) -> list[BriefItem]:
    """Pick what the character actually says out loud."""

    current = time.time() if now is None else now
    pool: list[tuple[float, int, BriefItem]] = []
    for order, report in enumerate(reports):
        for item in report.items:
            pool.append((score_item(item, current), order, item))
    pool.sort(key=lambda row: (-row[0], row[1]))

    picked: list[BriefItem] = []
    used: dict[str, int] = {}
    seen_urls: set[str] = set()
    for _, _, item in pool:
        if len(picked) >= max_items:
            break
        if used.get(item.source_id, 0) >= per_source:
            continue
        if item.url and item.url in seen_urls:
            continue
        picked.append(item)
        used[item.source_id] = used.get(item.source_id, 0) + 1
        if item.url:
            seen_urls.add(item.url)
    return picked


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _degrade(
    store: Store,
    cfg: SourceConfig,
    code: str,
    started: float,
    *,
    now: float,
) -> SourceReport:
    cached = store.cached_items(cfg.id, now=now)
    status = "cached" if cached else "error"
    store.save_state(cfg.id, status=status, items=(), error_code=code, fetched_at=now)
    return SourceReport(
        source_id=cfg.id,
        label=cfg.display_label,
        status=status,
        items=list(cached or []),
        fetched_at=now,
        elapsed_ms=_elapsed_ms(started),
        error_code=code,
        hint=cfg.hint,
    )


async def _collect_one(
    cfg: SourceConfig,
    source: BaseSource,
    store: Store,
    *,
    default_timeout: float,
    now: float,
    semaphore: asyncio.Semaphore,
    logger=None,
) -> SourceReport:
    timeout = float(cfg.timeout or default_timeout)
    timeout = min(max(timeout, 1.0), 30.0)
    started = time.monotonic()

    async with semaphore:
        items: list[BriefItem] = []
        for attempt in range(2):
            try:
                items = await asyncio.wait_for(source.fetch(cfg), timeout=timeout)
                break
            except asyncio.TimeoutError:
                if attempt == 1:
                    return _degrade(store, cfg, "TIMEOUT", started, now=now)
                # Cold DNS / first TLS handshake often needs a second chance.
                if logger is not None:
                    logger.warning("source.timeout_retry id={}", cfg.id)
                continue
            except FetchError as exc:
                return _degrade(store, cfg, exc.code, started, now=now)
            except Exception:  # noqa: BLE001 - one bad adapter must not kill the run
                if logger is not None:
                    logger.exception("source.crash id={}", cfg.id)
                return _degrade(store, cfg, "PARSE_ERROR", started, now=now)

    limit = max(1, int(cfg.max_items or 3))
    items = list(items)[:limit]
    if cfg.dedupe:
        candidates = [item for item in items if not item.snapshot]
        fresh_candidates = store.drop_seen(candidates, now=now)
        keep = {id(item) for item in fresh_candidates}
        fresh = [item for item in items if item.snapshot or id(item) in keep]
        store.remember(fresh_candidates, now=now)
    else:
        fresh = items
    store.save_state(cfg.id, status="ok" if fresh else "empty", items=items, fetched_at=now)
    return SourceReport(
        source_id=cfg.id,
        label=cfg.display_label,
        status="ok" if fresh else "empty",
        items=fresh,
        fetched_at=now,
        elapsed_ms=_elapsed_ms(started),
    )


async def collect(
    cfgs: Iterable[SourceConfig],
    sources: dict[str, BaseSource],
    store: Store,
    *,
    default_timeout: float = 6.0,
    concurrency: int = 6,
    now: float | None = None,
    logger=None,
) -> list[SourceReport]:
    """Run all enabled sources concurrently and always return one report each."""

    current = time.time() if now is None else now
    enabled = [cfg for cfg in cfgs if cfg.enabled]
    if not enabled:
        return []

    semaphore = asyncio.Semaphore(max(1, int(concurrency)))
    tasks = []
    for cfg in enabled:
        source = sources.get(cfg.type)
        if source is None:
            started = time.monotonic()
            tasks.append(
                asyncio.sleep(
                    0,
                    result=_degrade(store, cfg, "CONFIG_INVALID", started, now=current),
                )
            )
            continue
        tasks.append(
            _collect_one(
                cfg,
                source,
                store,
                default_timeout=default_timeout,
                now=current,
                semaphore=semaphore,
                logger=logger,
            )
        )
    results = await asyncio.gather(*tasks)
    return list(results)


__all__ = [
    "collect",
    "rank_items",
    "recency_factor",
    "score_item",
    "select_spoken",
]
