"""Pure planning/cache helpers for per-problem metadata backfill.

Luogu's ``/record/list`` payload carries each submission's difficulty but **not**
its tags, so a submission-derived weakness picture starts with no tag signal at
all. The plugin therefore keeps a ``pid -> metadata`` cache and fills the gaps a
few problems at a time from ``/problem/{pid}``.

Everything here is arithmetic over plain values — which problems still need a
fetch, how a fetched page updates the record and the cache, how the cache
survives a round-trip through the plugin store — so it can be unit-tested
without network, SDK, or a clock.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from ._models import SubmissionRecord

# Plugin-store key holding the serialised cache. The version is part of the
# payload: a cache written by an older shape is ignored rather than misread.
CACHE_KEY = "problem_meta"
CACHE_VERSION = 2

# Luogu problem ids look like P1001 / B3624 / CF1234A / SP1234 / UVA100 / AT_dp_a.
# Anything else (a mangled key, an id from a drifted payload) is not worth a
# request, so it is filtered out before planning.
_PID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,23}$")


def is_fetchable_pid(pid: object) -> bool:
    """Whether ``pid`` looks like a real Luogu problem id worth one request.

    Most ids carry digits (``P1001``, ``B3624``, ``CF1234A``); the ``AT_`` family
    is the exception (``AT_dp_a``), so a digit *or* that prefix is required —
    enough to reject a mangled key without spending a request on it.
    """
    text = "" if pid is None else str(pid).strip()
    if not _PID_RE.match(text):
        return False
    return any(char.isdigit() for char in text) or text.upper().startswith("AT_")


@dataclass(frozen=True)
class PidMeta:
    """What one ``/problem/{pid}`` read told us about a problem.

    ``fetched`` records that the read *succeeded*, even if the payload was sparse
    (Luogu legitimately serves unrated problems with no difficulty; re-asking
    those on every run would burn requests for nothing). ``failed_at`` is the
    epoch second of the last failure and drives the retry backoff.
    """

    difficulty: int = 0
    tags: tuple[str, ...] = ()
    title: str = ""
    fetched: bool = False
    failed_at: float = 0.0


def load_cache(raw: Any) -> dict[str, PidMeta]:
    """Rebuild the cache from whatever the store handed back.

    Tolerates a missing / older / corrupt value by returning ``{}`` — a lost
    cache only costs requests, never correctness.
    """
    payload = raw
    if hasattr(payload, "value"):  # store get() wrapper
        payload = payload.value
    if not isinstance(payload, Mapping):
        return {}
    if payload.get("v") != CACHE_VERSION:
        return {}
    items = payload.get("items")
    if not isinstance(items, Mapping):
        return {}
    cache: dict[str, PidMeta] = {}
    for pid, entry in items.items():
        if not isinstance(entry, Mapping):
            continue
        raw_tags = entry.get("tags")
        tags = tuple(str(tag) for tag in raw_tags if str(tag)) if isinstance(raw_tags, list) else ()
        try:
            difficulty = int(entry.get("difficulty") or 0)
        except (TypeError, ValueError):
            difficulty = 0
        try:
            failed_at = float(entry.get("failed_at") or 0.0)
        except (TypeError, ValueError):
            failed_at = 0.0
        cache[str(pid)] = PidMeta(
            difficulty=difficulty,
            tags=tags,
            title=str(entry.get("title") or ""),
            fetched=bool(entry.get("fetched")),
            failed_at=failed_at,
        )
    return cache


def dump_cache(cache: Mapping[str, PidMeta]) -> dict[str, Any]:
    """Serialise the cache for the plugin store (tags stay numeric ids).

    Ids rather than names on purpose: the id → name dictionary can grow or be
    corrected between runs, and cached rows must not freeze an old spelling.
    """
    return {
        "v": CACHE_VERSION,
        "items": {
            pid: {
                "difficulty": meta.difficulty,
                "tags": list(meta.tags),
                "title": meta.title,
                "fetched": meta.fetched,
                "failed_at": meta.failed_at,
            }
            for pid, meta in cache.items()
        },
    }


def needs_meta(record: SubmissionRecord) -> bool:
    """Whether this record is still missing tag or difficulty information."""
    return not record.tags or record.difficulty <= 0


def plan_backfill(
    pids: Iterable[object],
    cache: Mapping[str, PidMeta],
    *,
    limit: int,
    now: float,
    backoff_seconds: float,
) -> list[str]:
    """Pick the pids to fetch this run: unknown, out of backoff, capped.

    Order is preserved (callers pass newest-first records, so the freshest work
    gets enriched first) and duplicates are collapsed — one problem practised
    ten times is still one request.
    """
    if limit <= 0:
        return []
    planned: list[str] = []
    seen: set[str] = set()
    for pid in pids:
        if not is_fetchable_pid(pid):
            continue
        key = str(pid).strip()
        if key in seen:
            continue
        seen.add(key)
        entry = cache.get(key)
        if entry is not None:
            if entry.fetched:
                continue
            if entry.failed_at and (now - entry.failed_at) < backoff_seconds:
                continue
        planned.append(key)
        if len(planned) >= limit:
            break
    return planned


def apply_meta(record: SubmissionRecord, meta: PidMeta | None) -> SubmissionRecord:
    """Return ``record`` with gaps filled from ``meta`` (never overwriting data).

    The record's own difficulty wins when it has one: it comes from the
    submission row itself, while the problem page reports the *current* rating.
    """
    if meta is None:
        return record
    difficulty = record.difficulty if record.difficulty > 0 else meta.difficulty
    tags = record.tags or meta.tags
    if difficulty == record.difficulty and tags == record.tags:
        return record
    return replace(record, difficulty=difficulty, tags=tags)


__all__ = [
    "CACHE_KEY",
    "CACHE_VERSION",
    "PidMeta",
    "apply_meta",
    "dump_cache",
    "is_fetchable_pid",
    "load_cache",
    "needs_meta",
    "plan_backfill",
]
