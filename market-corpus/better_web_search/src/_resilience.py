"""Per-backend caching, spacing, and failure backoff.

A keyless search route is a shared public resource: hammering it gets the user's
IP rate-limited or challenged. Every backend therefore runs through a coordinator
that (a) serves repeated queries from cache, (b) collapses identical concurrent
queries into one upstream request, (c) enforces a minimum interval, and (d) stops
asking a backend that just refused for a while — with exponential growth so a
persistent block backs off instead of flapping.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping


class SearchProviderError(RuntimeError):
    """A backend failed in a way that should let the next backend try."""

    is_search_provider_error = True


class ApiKeyRejectedError(SearchProviderError):
    """The upstream rejected the supplied API key."""

    is_api_key_rejected = True


class QuotaExhaustedError(SearchProviderError):
    """The keyed upstream is out of quota or temporarily rate limited."""

    is_quota_exhausted = True

    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class BlockedError(SearchProviderError):
    """The upstream challenged or rate-limited us (403/429/202/captcha page)."""

    is_search_block = True

    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class BusyError(RuntimeError):
    """Coordinator is saturated; the plugin is healthy but refused this call."""


class CooldownError(RuntimeError):
    """The backend is in a self-imposed penalty box."""


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise


@dataclass
class _BackendState:
    min_interval_seconds: float
    cooldown_seconds: float
    max_cooldown_seconds: float
    next_allowed_at: float = 0.0
    cooldown_until: float = 0.0
    block_count: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SearchCoordinator:
    """Cache + single-flight + per-backend throttle for one search backend."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 300.0,
        stale_seconds: float = 1800.0,
        max_entries: int = 128,
        min_interval_seconds: float = 1.0,
        cooldown_seconds: float = 60.0,
        max_cooldown_seconds: float = 900.0,
        queue_wait_seconds: float = 2.0,
    ) -> None:
        self.ttl_seconds = _as_float(ttl_seconds)
        self.stale_seconds = _as_float(stale_seconds)
        self.max_entries = max(1, _as_int(max_entries))
        self.min_interval_seconds = _as_float(min_interval_seconds)
        self.cooldown_seconds = _as_float(cooldown_seconds)
        self.max_cooldown_seconds = _as_float(max_cooldown_seconds)
        self.queue_wait_seconds = _as_float(queue_wait_seconds)
        self._cache: OrderedDict[tuple[str, str, int], tuple[float, float, dict]] = OrderedDict()
        self._inflight: dict[tuple[str, str, int], asyncio.Task[dict]] = {}
        self._waiters: dict[tuple[str, str, int], int] = {}
        self._state = _BackendState(min_interval_seconds, cooldown_seconds, max_cooldown_seconds)

    # -- cache ------------------------------------------------------------

    def _key(self, query: str, limit: int) -> tuple[str, str, int]:
        return (self._name, " ".join(query.split()).casefold(), _as_int(limit))

    _name = "default"

    def bind(self, name: str) -> "SearchCoordinator":
        self._name = name
        return self

    def _cached(self, key: tuple, *, fresh: bool = True) -> dict | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        fresh_until, stale_until, value = entry
        now = time.monotonic()
        if now >= stale_until:
            self._cache.pop(key, None)
            return None
        if fresh and now >= fresh_until:
            return None
        if not fresh and not value.get("results"):
            return None
        self._cache.move_to_end(key)
        return _copy(value)

    def _store(self, key: tuple, value: dict) -> None:
        now = time.monotonic()
        self._cache[key] = (now + self.ttl_seconds, now + self.ttl_seconds + self.stale_seconds,
                            _copy(value))
        self._cache.move_to_end(key)
        while len(self._cache) > self.max_entries:
            self._cache.popitem(last=False)

    def invalidate_cold_start(self) -> None:
        """Drop cached entries and penalty state (used on config change)."""
        self._cache.clear()
        self._state.next_allowed_at = 0.0
        self._state.cooldown_until = 0.0
        self._state.block_count = 0

    # -- failure bookkeeping ---------------------------------------------

    def _note_block(self, error: BaseException) -> None:
        state = self._state
        declared = getattr(error, "retry_after_seconds", None)
        state.block_count += 1
        backoff = min(state.max_cooldown_seconds,
                      state.cooldown_seconds * (2 ** (state.block_count - 1)))
        if isinstance(declared, (int, float)) and declared > 0:
            backoff = min(state.max_cooldown_seconds, max(backoff, _as_float(declared)))
        state.cooldown_until = time.monotonic() + backoff

    def _note_success(self) -> None:
        self._state.block_count = 0

    # -- execution --------------------------------------------------------

    async def execute(
        self,
        query: str,
        limit: int,
        fetch: Callable[[], Awaitable[list[dict[str, str]]]],
    ) -> dict[str, Any]:
        """Return ``{"results": [...], "backend": name, "cached": bool}``.

        ``fetch`` must return a list of ``{title,url,snippet}`` dicts, or raise
        :class:`BlockedError` / :class:`SearchProviderError`.
        """
        key = self._key(query, limit)
        cached = self._cached(key)
        if cached is not None:
            return {**_copy(cached), "cached": True}

        task = self._inflight.get(key)
        owner = task is None
        if owner:
            task = asyncio.create_task(self._run(query, limit, key, fetch))
            self._inflight[key] = task

            def finish(done: asyncio.Task) -> None:
                if self._inflight.get(key) is done:
                    self._inflight.pop(key, None)

            task.add_done_callback(finish)

        self._waiters[key] = self._waiters.get(key, 0) + 1
        try:
            return _copy(await asyncio.shield(task))
        except asyncio.CancelledError:
            raise
        finally:
            left = self._waiters.get(key, 1) - 1
            if left > 0:
                self._waiters[key] = left
            else:
                self._waiters.pop(key, None)
                if owner or not task.done():
                    if self._inflight.get(key) is task:
                        self._inflight.pop(key, None)
                    task.cancel()

    async def _run(self, query: str, limit: int, key: tuple, fetch) -> dict[str, Any]:
        now = time.monotonic()
        if now < self._state.cooldown_until:
            stale = self._cached(key, fresh=False)
            if stale is not None:
                return {**_copy(stale), "cached": True}
            raise CooldownError(f"{self._name} 后端处于失败冷却期")

        async def produce() -> list[dict[str, str]]:
            state = self._state
            try:
                await asyncio.wait_for(state.lock.acquire(), timeout=max(0.05, self.queue_wait_seconds))
            except (asyncio.TimeoutError, TimeoutError) as error:
                raise BusyError(f"{self._name} 后端繁忙") from error
            try:
                wait = state.next_allowed_at - time.monotonic()
                if wait > min(self.queue_wait_seconds, 3.0):
                    raise BusyError(f"{self._name} 后端请求过于频繁")
                if wait > 0:
                    remaining = min(wait, 3.0)
                    try:
                        await asyncio.wait_for(asyncio.sleep(remaining), timeout=3.5)
                    except (asyncio.TimeoutError, TimeoutError) as error:
                        raise BusyError(f"{self._name} 后端请求过于频繁") from error
                try:
                    results = await fetch()
                except (BlockedError, SearchProviderError) as error:
                    if isinstance(error, BlockedError):
                        self._note_block(error)
                    raise
                self._note_success()
                return results
            finally:
                state.next_allowed_at = time.monotonic() + self.min_interval_seconds
                state.lock.release()

        stale = self._cached(key, fresh=False)
        try:
            results = await produce()
        except (BusyError, CooldownError):
            if stale is not None:
                return {**_copy(stale), "cached": True}
            raise
        except Exception:
            if stale is not None:
                return {**_copy(stale), "cached": True}
            raise

        payload = {"results": _only_results(results, limit), "backend": self._name, "cached": False}
        if payload["results"]:
            self._store(key, payload)
        return payload


def _copy(value: Mapping[str, Any]) -> dict[str, Any]:
    copied = dict(value)
    copied["results"] = [dict(item) for item in value.get("results", []) if isinstance(item, dict)]
    return copied


def _only_results(results: Any, limit: int) -> list[dict[str, str]]:
    if not isinstance(results, list):
        return []
    out: list[dict[str, str]] = []
    for item in results[: max(1, _as_int(limit))]:
        if isinstance(item, dict):
            out.append(dict(item))
    return out
